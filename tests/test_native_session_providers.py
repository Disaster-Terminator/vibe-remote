import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
from typing import Any, cast
from unittest.mock import AsyncMock

from modules.agents.codex.attach_service import (
    CodexThreadListResult,
    CodexThreadSummary,
    CodexWorkspaceIdentity,
    CodexWorkspaceValidationResult,
)
from modules.agents.native_sessions.base import build_resume_preview, build_tail_preview
from modules.agents.native_sessions import claude as claude_module
from modules.agents.native_sessions.claude import ClaudeNativeSessionProvider, encode_project_path
from modules.agents.native_sessions import codex as codex_module
from modules.agents.native_sessions.codex import CodexNativeSessionProvider
from modules.agents.native_sessions import service as service_module
from modules.agents.native_sessions.service import AgentNativeSessionService
from modules.agents.native_sessions.types import AgentName, AgentPrefix, NativeResumeSession


def _codex_workspace_identity(cwd: str) -> CodexWorkspaceIdentity:
    return CodexWorkspaceIdentity(
        cwd=cwd,
        realpath=cwd,
        repo_root=cwd,
        workspace_fingerprint=f"repo:{cwd}",
    )


def _codex_validation(
    requested_workspace: CodexWorkspaceIdentity,
    *,
    candidate_workspace: CodexWorkspaceIdentity | None,
    status: str,
    is_valid: bool,
    message: str,
) -> CodexWorkspaceValidationResult:
    return CodexWorkspaceValidationResult(
        requested_workspace=requested_workspace,
        candidate_workspace=candidate_workspace,
        status=status,
        is_valid=is_valid,
        message=message,
    )


def _write_codex_threads_db(db_path: Path, working_path: str, rows: list[tuple]) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                created_at REAL,
                updated_at REAL,
                title TEXT,
                first_user_message TEXT,
                rollout_path TEXT,
                cwd TEXT,
                archived INTEGER DEFAULT 0
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO threads (id, created_at, updated_at, title, first_user_message, rollout_path, cwd, archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(session_id, created_at, updated_at, title, first_user_message, rollout_path, working_path, archived) for session_id, created_at, updated_at, title, first_user_message, rollout_path, archived in rows],
        )


def test_claude_provider_falls_back_to_history_jsonl(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    history_path = tmp_path / "history.jsonl"
    projects_root.mkdir(parents=True, exist_ok=True)

    working_path = "/Users/cyh/vibe-remote"
    history_path.write_text(
        "\n".join(
            [
                '{"display":"old prompt","timestamp":1766078000000,"project":"/Users/cyh/vibe-remote","sessionId":"sess_a"}',
                '{"display":"latest prompt","timestamp":1766079000000,"project":"/Users/cyh/vibe-remote","sessionId":"sess_a"}',
                '{"display":"other project","timestamp":1766079100000,"project":"/Users/cyh/other","sessionId":"sess_b"}',
            ]
        ),
        encoding="utf-8",
    )

    provider = ClaudeNativeSessionProvider(root=str(projects_root), history_path=str(history_path))

    items = provider.list_metadata(working_path)

    assert [item.native_session_id for item in items] == ["sess_a"]
    hydrated = provider.hydrate_preview(items[0])
    assert hydrated.last_agent_message == "latest prompt"
    assert hydrated.last_agent_tail == "latest prompt"


def test_claude_provider_does_not_scan_unrelated_project_jsonl(tmp_path: Path, monkeypatch) -> None:
    projects_root = tmp_path / "projects"
    history_path = tmp_path / "history.jsonl"
    projects_root.mkdir(parents=True, exist_ok=True)
    history_path.write_text("", encoding="utf-8")

    working_path = "/Users/cyh/vibe-remote"
    candidate_dir = projects_root / encode_project_path(working_path)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    target_jsonl = candidate_dir / "sess_target.jsonl"
    target_jsonl.write_text(
        '{"type":"assistant","timestamp":"2026-03-27T10:00:00Z","message":{"content":"done"}}\n',
        encoding="utf-8",
    )

    unrelated_dir = projects_root / "-Users-cyh-other"
    unrelated_dir.mkdir(parents=True, exist_ok=True)
    unrelated_jsonl = unrelated_dir / "sess_other.jsonl"
    unrelated_jsonl.write_text(
        '{"type":"assistant","timestamp":"2026-03-27T10:00:00Z","message":{"content":"should not read"}}\n',
        encoding="utf-8",
    )

    read_paths: list[Path] = []
    original_read_json_lines = claude_module.read_json_lines

    def _tracking_read_json_lines(path: Path) -> list[dict]:
        read_paths.append(Path(path))
        return original_read_json_lines(path)

    monkeypatch.setattr(claude_module, "read_json_lines", _tracking_read_json_lines)
    provider = ClaudeNativeSessionProvider(root=str(projects_root), history_path=str(history_path))

    items = provider.list_metadata(working_path)

    assert [item.native_session_id for item in items] == ["sess_target"]
    assert target_jsonl in read_paths
    assert unrelated_jsonl not in read_paths


def test_claude_provider_uses_global_index_fallback_without_scanning_all_jsonl(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    history_path = tmp_path / "history.jsonl"
    projects_root.mkdir(parents=True, exist_ok=True)
    history_path.write_text("", encoding="utf-8")

    working_path = "/Users/cyh/vibe-remote"
    indexed_dir = projects_root / "-Users-cyh"
    indexed_dir.mkdir(parents=True, exist_ok=True)
    session_jsonl = indexed_dir / "sess_idx.jsonl"
    session_jsonl.write_text(
        "\n".join(
            [
                '{"type":"user","timestamp":"2026-03-27T09:59:00Z","cwd":"/Users/cyh/vibe-remote","message":{"content":"hello"}}',
                '{"type":"assistant","timestamp":"2026-03-27T10:00:00Z","message":{"content":"reply from indexed session"}}',
            ]
        ),
        encoding="utf-8",
    )
    (indexed_dir / "sessions-index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "sessionId": "sess_idx",
                        "projectPath": "/Users/cyh/vibe-remote",
                        "created": "2026-03-27T09:59:00Z",
                        "modified": "2026-03-27T10:00:00Z",
                        "firstPrompt": "hello",
                        "fullPath": str(session_jsonl),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provider = ClaudeNativeSessionProvider(root=str(projects_root), history_path=str(history_path))

    items = provider.list_metadata(working_path)

    assert [item.native_session_id for item in items] == ["sess_idx"]
    hydrated = provider.hydrate_preview(items[0])
    assert hydrated.last_agent_message == "reply from indexed session"
    assert hydrated.last_agent_tail.startswith("...")
    assert "indexed session" in hydrated.last_agent_tail


def test_codex_provider_skips_empty_rollout_path(monkeypatch) -> None:
    provider = CodexNativeSessionProvider(db_path="/tmp/does-not-matter.sqlite")
    item = NativeResumeSession(
        agent="codex",
        agent_prefix="cx",
        native_session_id="thread_1",
        working_path="/tmp/project",
        created_at=None,
        updated_at=None,
        sort_ts=1.0,
        locator={"title": "Fallback title", "rollout_path": ""},
    )

    called = False

    def _unexpected_read_json_lines(_path: Path) -> list[dict]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(codex_module, "read_json_lines", _unexpected_read_json_lines)

    hydrated = provider.hydrate_preview(item)

    assert called is False
    assert hydrated.last_agent_message == "Fallback title"
    assert hydrated.last_agent_tail == "Fallback title"


def test_codex_attach_provider_emits_attach_aware_metadata(tmp_path: Path) -> None:
    working_path = tmp_path / "repo"
    working_path.mkdir()
    (working_path / ".git").mkdir()
    rollout_path = working_path / "thread-1.jsonl"
    rollout_path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Attach-aware preview from rollout"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    requested_workspace = _codex_workspace_identity(str(working_path))
    summary = CodexThreadSummary(
        thread_id="thread-1",
        thread={
            "id": "thread-1",
            "name": "Investigate flaky test",
            "preview": "Why is the test flaky?",
            "createdAt": 10,
            "updatedAt": 20,
            "path": str(rollout_path),
            "cwd": str(working_path),
            "forkedFromId": None,
            "ephemeral": False,
        },
        workspace=requested_workspace,
        workspace_validation=_codex_validation(
            requested_workspace,
            candidate_workspace=requested_workspace,
            status="valid",
            is_valid=True,
            message="Workspace validation succeeded.",
        ),
    )
    attach_service = SimpleNamespace(
        list_threads=AsyncMock(return_value=CodexThreadListResult(threads=[summary], next_cursor=None, raw_result={}))
    )
    provider = CodexNativeSessionProvider(
        db_path=str(tmp_path / "missing.sqlite"),
        attach_service=cast(Any, attach_service),
    )

    async def _collect() -> list[NativeResumeSession]:
        return provider.list_metadata(str(working_path))

    items = asyncio.run(_collect())

    attach_service.list_threads.assert_awaited_once_with(str(working_path), timeout_seconds=5.0)
    assert [item.native_session_id for item in items] == ["thread-1"]
    assert items[0].locator["thread_id"] == "thread-1"
    assert items[0].locator["title"] == "Investigate flaky test"
    assert items[0].locator["preview"] == "Why is the test flaky?"
    assert items[0].locator["validation_status"] == "valid"
    assert items[0].locator["workspace_match"] is True
    assert items[0].locator["materialized_history"] is True
    assert items[0].locator["last_activity_ts"] == 20
    assert items[0].locator["allowed_actions"] == ["resume", "fork"]

    hydrated = provider.hydrate_preview(items[0])

    assert hydrated.last_agent_message == "Attach-aware preview from rollout"
    assert hydrated.last_agent_tail.startswith("...")


def test_codex_attach_provider_keeps_sqlite_only_threads_when_catalog_is_partial(tmp_path: Path) -> None:
    working_path = tmp_path / "repo"
    working_path.mkdir()
    (working_path / ".git").mkdir()
    db_path = tmp_path / "state_5.sqlite"
    _write_codex_threads_db(
        db_path,
        str(working_path),
        [
            ("thread-app", 10, 25, "App title", "App first prompt", "", 0),
            ("thread-sqlite-only", 11, 30, "SQLite title", "SQLite first prompt", "", 0),
        ],
    )
    requested_workspace = _codex_workspace_identity(str(working_path))
    attach_service = SimpleNamespace(
        list_threads=AsyncMock(
            return_value=CodexThreadListResult(
                threads=[
                    CodexThreadSummary(
                        thread_id="thread-app",
                        thread={
                            "id": "thread-app",
                            "name": "App title",
                            "preview": "App preview",
                            "createdAt": 10,
                            "updatedAt": 25,
                            "path": None,
                            "cwd": str(working_path),
                            "ephemeral": False,
                        },
                        workspace=requested_workspace,
                        workspace_validation=_codex_validation(
                            requested_workspace,
                            candidate_workspace=requested_workspace,
                            status="valid",
                            is_valid=True,
                            message="Workspace validation succeeded.",
                        ),
                    )
                ],
                next_cursor=None,
                raw_result={},
            )
        )
    )
    provider = CodexNativeSessionProvider(db_path=str(db_path), attach_service=cast(Any, attach_service))

    items = provider.list_metadata(str(working_path))

    assert [item.native_session_id for item in items] == ["thread-sqlite-only", "thread-app"]
    sqlite_only = items[0]
    assert sqlite_only.locator["attach_source"] == "sqlite_fallback"
    assert sqlite_only.locator["attach_service_available"] is False
    assert sqlite_only.locator["allowed_actions"] == ["inspect_only"]
    attach_item = items[1]
    assert attach_item.locator["attach_source"] == "app_server"
    assert attach_item.locator["allowed_actions"] == ["resume", "fork"]


def test_codex_attach_fallback_when_attach_service_unavailable(tmp_path: Path) -> None:
    working_path = str(tmp_path / "repo")
    Path(working_path).mkdir()
    db_path = tmp_path / "state_5.sqlite"
    _write_codex_threads_db(
        db_path,
        working_path,
        [("thread-sqlite", 10, 25, "Fallback title", "Fallback first prompt", "", 0)],
    )
    attach_service = SimpleNamespace(list_threads=AsyncMock(side_effect=RuntimeError("app server offline")))
    provider = CodexNativeSessionProvider(db_path=str(db_path), attach_service=cast(Any, attach_service))

    items = provider.list_metadata(working_path)

    attach_service.list_threads.assert_awaited_once_with(working_path, timeout_seconds=5.0)
    assert [item.native_session_id for item in items] == ["thread-sqlite"]
    assert items[0].locator["attach_source"] == "sqlite_fallback"
    assert items[0].locator["attach_service_available"] is False
    assert items[0].locator["validation_status"] == "attach_unavailable"
    assert items[0].locator["allowed_actions"] == ["inspect_only"]

    hydrated = provider.hydrate_preview(items[0])

    assert hydrated.last_agent_message == "Fallback title"
    assert hydrated.last_agent_tail == "Fallback title"


def test_codex_attach_materialized_history_policy_marks_restricted_candidates(tmp_path: Path) -> None:
    working_path = tmp_path / "repo"
    other_path = tmp_path / "other"
    working_path.mkdir()
    other_path.mkdir()
    (working_path / ".git").mkdir()
    (other_path / ".git").mkdir()
    requested_workspace = _codex_workspace_identity(str(working_path))
    other_workspace = _codex_workspace_identity(str(other_path))
    materialized_path = working_path / "thread-materialized.jsonl"
    materialized_path.write_text("", encoding="utf-8")
    attach_service = SimpleNamespace(
        list_threads=AsyncMock(
            return_value=CodexThreadListResult(
                threads=[
                    CodexThreadSummary(
                        thread_id="thread-materialized",
                        thread={
                            "id": "thread-materialized",
                            "name": "Safe attach",
                            "preview": "safe",
                            "createdAt": 10,
                            "updatedAt": 30,
                            "path": str(materialized_path),
                            "cwd": str(working_path),
                            "ephemeral": False,
                        },
                        workspace=requested_workspace,
                        workspace_validation=_codex_validation(
                            requested_workspace,
                            candidate_workspace=requested_workspace,
                            status="valid",
                            is_valid=True,
                            message="Workspace validation succeeded.",
                        ),
                    ),
                    CodexThreadSummary(
                        thread_id="thread-unmaterialized",
                        thread={
                            "id": "thread-unmaterialized",
                            "name": "Needs inspect",
                            "preview": "inspect",
                            "createdAt": 11,
                            "updatedAt": 31,
                            "path": None,
                            "cwd": str(working_path),
                            "ephemeral": True,
                        },
                        workspace=requested_workspace,
                        workspace_validation=_codex_validation(
                            requested_workspace,
                            candidate_workspace=requested_workspace,
                            status="valid",
                            is_valid=True,
                            message="Workspace validation succeeded.",
                        ),
                    ),
                    CodexThreadSummary(
                        thread_id="thread-mismatch",
                        thread={
                            "id": "thread-mismatch",
                            "name": "Wrong workspace",
                            "preview": "mismatch",
                            "createdAt": 12,
                            "updatedAt": 32,
                            "path": str(materialized_path),
                            "cwd": str(other_path),
                            "ephemeral": False,
                        },
                        workspace=other_workspace,
                        workspace_validation=_codex_validation(
                            requested_workspace,
                            candidate_workspace=other_workspace,
                            status="realpath_mismatch",
                            is_valid=False,
                            message="Normalized working directory does not match the target thread workspace.",
                        ),
                    ),
                ],
                next_cursor=None,
                raw_result={},
            )
        )
    )
    provider = CodexNativeSessionProvider(
        db_path=str(tmp_path / "missing.sqlite"),
        attach_service=cast(Any, attach_service),
    )

    items = provider.list_metadata(str(working_path))
    items_by_id = {item.native_session_id: item for item in items}

    assert items_by_id["thread-materialized"].locator["allowed_actions"] == ["resume", "fork"]
    assert items_by_id["thread-materialized"].locator["materialized_history"] is True
    assert items_by_id["thread-unmaterialized"].locator["allowed_actions"] == ["inspect_only"]
    assert items_by_id["thread-unmaterialized"].locator["materialized_history"] is False
    assert items_by_id["thread-mismatch"].locator["validation_status"] == "realpath_mismatch"
    assert items_by_id["thread-mismatch"].locator["workspace_match"] is False
    assert items_by_id["thread-mismatch"].locator["allowed_actions"] == ["inspect_only"]


def test_codex_attach_provider_marks_unverifiable_candidates_inspect_only(tmp_path: Path) -> None:
    working_path = tmp_path / "repo"
    working_path.mkdir()
    (working_path / ".git").mkdir()
    materialized_path = working_path / "thread-unverifiable.jsonl"
    materialized_path.write_text("", encoding="utf-8")
    requested_workspace = _codex_workspace_identity(str(working_path))
    attach_service = SimpleNamespace(
        list_threads=AsyncMock(
            return_value=CodexThreadListResult(
                threads=[
                    CodexThreadSummary(
                        thread_id="thread-unverifiable",
                        thread={
                            "id": "thread-unverifiable",
                            "name": "Missing cwd",
                            "preview": "inspect first",
                            "createdAt": 10,
                            "updatedAt": 20,
                            "path": str(materialized_path),
                            "ephemeral": False,
                        },
                        workspace=None,
                        workspace_validation=_codex_validation(
                            requested_workspace,
                            candidate_workspace=None,
                            status="unverifiable",
                            is_valid=False,
                            message="Target workspace metadata is missing, so attach safety cannot be verified.",
                        ),
                    )
                ],
                next_cursor=None,
                raw_result={},
            )
        )
    )
    provider = CodexNativeSessionProvider(
        db_path=str(tmp_path / "missing.sqlite"),
        attach_service=cast(Any, attach_service),
    )

    items = provider.list_metadata(str(working_path))

    assert [item.native_session_id for item in items] == ["thread-unverifiable"]
    assert items[0].locator["validation_status"] == "unverifiable"
    assert items[0].locator["workspace_match"] is False
    assert items[0].locator["workspace_realpath"] == ""
    assert items[0].locator["allowed_actions"] == ["inspect_only"]


def test_codex_attach_provider_uses_short_catalog_timeout_for_listing(tmp_path: Path) -> None:
    working_path = tmp_path / "repo"
    working_path.mkdir()
    (working_path / ".git").mkdir()
    attach_service = SimpleNamespace(
        list_threads=AsyncMock(side_effect=TimeoutError("timed out"))
    )
    provider = CodexNativeSessionProvider(
        db_path=str(tmp_path / "missing.sqlite"),
        attach_service=cast(Any, attach_service),
        attach_catalog_timeout_seconds=0.05,
    )

    items = provider.list_metadata(str(working_path))

    attach_service.list_threads.assert_awaited_once_with(str(working_path), timeout_seconds=0.05)
    assert items == []


def test_native_session_service_preserves_agent_visibility_when_limited() -> None:
    def _item(agent: AgentName, prefix: AgentPrefix, session_id: str, sort_ts: float) -> NativeResumeSession:
        return NativeResumeSession(
            agent=agent,
            agent_prefix=prefix,
            native_session_id=session_id,
            working_path="/tmp/project",
            created_at=None,
            updated_at=None,
            sort_ts=sort_ts,
            last_agent_message=session_id,
            last_agent_tail=f"...{session_id[-4:]}",
        )

    oc_provider = SimpleNamespace(
        agent_name="opencode",
        list_metadata=lambda working_path: [_item("opencode", "oc", f"oc_{i}", 200 - i) for i in range(5)],
        hydrate_preview=lambda item: item,
    )
    cc_provider = SimpleNamespace(
        agent_name="claude",
        list_metadata=lambda working_path: [_item("claude", "cc", "cc_1", 50)],
        hydrate_preview=lambda item: item,
    )
    cx_provider = SimpleNamespace(
        agent_name="codex",
        list_metadata=lambda working_path: [_item("codex", "cx", f"cx_{i}", 100 - i) for i in range(5)],
        hydrate_preview=lambda item: item,
    )

    service = AgentNativeSessionService(providers=cast(Any, [oc_provider, cc_provider, cx_provider]))

    items = service.list_recent_sessions("/tmp/project", limit=5)

    assert len(items) == 5
    assert {item.agent for item in items} == {"opencode", "claude", "codex"}


def test_native_session_service_loads_default_providers_lazily(monkeypatch) -> None:
    calls: list[str] = []

    class _StubProvider:
        agent_name = "claude"

        def list_metadata(self, working_path: str) -> list[NativeResumeSession]:
            return []

        def hydrate_preview(self, item: NativeResumeSession) -> NativeResumeSession:
            return item

    def _fake_import_module(module_path: str):
        calls.append(module_path)
        return SimpleNamespace(ClaudeNativeSessionProvider=_StubProvider)

    monkeypatch.setattr(service_module.importlib, "import_module", _fake_import_module)
    service = AgentNativeSessionService(
        provider_specs=(
            service_module.NativeSessionProviderSpec(
                agent_name="claude",
                module_path="modules.agents.native_sessions.claude",
                class_name="ClaudeNativeSessionProvider",
            ),
        )
    )

    assert calls == []

    assert service.list_recent_sessions("/tmp/project", limit=5) == []
    assert calls == ["modules.agents.native_sessions.claude"]


def test_native_session_lightweight_imports_do_not_require_sqlite() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    script = """
import importlib.abc

class BlockSqlite(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "sqlite3" or fullname.startswith("sqlite3.") or fullname == "_sqlite3":
            raise ImportError("blocked sqlite for test")
        return None

import sys
sys.meta_path.insert(0, BlockSqlite())

for module_name in [
    "modules.agents.native_sessions",
    "core.handlers.command_handlers",
    "core.handlers.session_handler",
]:
    __import__(module_name)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_build_tail_preview_strips_edge_symbols() -> None:
    assert build_tail_preview("前文很多很多很多。**最后一句话？？？**") == "...很多。**最后一句话"


def test_build_resume_preview_preserves_line_breaks() -> None:
    text = "第一段第一行\n第二行\n\n第三行\n---\n[button]"

    assert build_resume_preview(text, limit=200) == "第一段第一行\n第二行\n\n第三行"
