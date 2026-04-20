from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Coroutine, TypeVar

from modules.agents.codex.attach_service import CodexAttachService
from modules.agents.codex.transport import CodexTransport

from .base import NativeSessionProvider, build_tail_preview, dt_from_ts, read_json_lines
from .types import NativeResumeSession

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CodexNativeSessionProvider(NativeSessionProvider):
    agent_name = "codex"

    def __init__(
        self,
        db_path: str | None = None,
        *,
        attach_service: CodexAttachService | None = None,
        codex_binary: str | None = None,
        attach_catalog_timeout_seconds: float = 5.0,
    ):
        self.db_path = Path(db_path or Path.home() / ".codex" / "state_5.sqlite")
        self._attach_service = attach_service
        self._codex_binary = codex_binary or os.getenv("CODEX_CLI_PATH") or "codex"
        self._attach_catalog_timeout_seconds = attach_catalog_timeout_seconds

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def list_metadata(self, working_path: str) -> list[NativeResumeSession]:
        sqlite_items = self._list_sqlite_metadata(working_path)
        attach_items = self._list_attach_metadata(working_path, sqlite_items)
        if attach_items is not None:
            return attach_items
        return [self._mark_attach_unavailable(item) for item in sqlite_items]

    def _list_sqlite_metadata(self, working_path: str) -> list[NativeResumeSession]:
        if not self.db_path.exists():
            return []
        items: list[NativeResumeSession] = []
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    SELECT id, created_at, updated_at, title, first_user_message, rollout_path
                    FROM threads
                    WHERE cwd = ? AND archived = 0
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (working_path,),
                )
                for session_id, created_ts, updated_ts, title, first_user_message, rollout_path in cursor.fetchall():
                    created_at = dt_from_ts(created_ts)
                    updated_at = dt_from_ts(updated_ts)
                    sort_basis = updated_at or created_at
                    items.append(
                        NativeResumeSession(
                            agent="codex",
                            agent_prefix="cx",
                            native_session_id=session_id,
                            working_path=working_path,
                            created_at=created_at,
                            updated_at=updated_at,
                            sort_ts=sort_basis.timestamp() if sort_basis else 0.0,
                            locator={
                                "title": title or "",
                                "first_user_message": first_user_message or "",
                                "rollout_path": rollout_path or "",
                            },
                        )
                    )
        except Exception as exc:
            logger.warning("Failed to list Codex sessions for %s: %s", working_path, exc)
        return items

    def _list_attach_metadata(
        self,
        working_path: str,
        sqlite_items: list[NativeResumeSession],
    ) -> list[NativeResumeSession] | None:
        sqlite_by_id = {item.native_session_id: item for item in sqlite_items}
        try:
            result = self._run_attach_operation_sync(
                working_path,
                lambda service: service.list_threads(
                    working_path,
                    timeout_seconds=self._attach_catalog_timeout_seconds,
                ),
            )
        except Exception as exc:
            logger.info("Codex attach catalog unavailable for %s, falling back to sqlite: %s", working_path, exc)
            return None

        items: list[NativeResumeSession] = []
        for summary in result.threads:
            thread = summary.thread
            sqlite_item = sqlite_by_id.get(summary.thread_id)
            created_at = dt_from_ts(thread.get("createdAt")) or (sqlite_item.created_at if sqlite_item else None)
            updated_at = dt_from_ts(thread.get("updatedAt")) or (sqlite_item.updated_at if sqlite_item else None)
            title = self._coalesce_text(
                thread.get("name"),
                sqlite_item.locator.get("title") if sqlite_item else None,
            )
            preview = self._coalesce_text(
                thread.get("preview"),
                sqlite_item.locator.get("first_user_message") if sqlite_item else None,
                title,
            )
            rollout_path = self._resolve_rollout_path(thread, sqlite_item)
            materialized_history = bool(rollout_path)
            allowed_actions = self._build_allowed_actions(
                workspace_valid=summary.workspace_validation.is_valid,
                materialized_history=materialized_history,
            )
            sort_basis = updated_at or created_at
            items.append(
                NativeResumeSession(
                    agent="codex",
                    agent_prefix="cx",
                    native_session_id=summary.thread_id,
                    working_path=working_path,
                    created_at=created_at,
                    updated_at=updated_at,
                    sort_ts=sort_basis.timestamp() if sort_basis else 0.0,
                    locator={
                        "title": title,
                        "preview": preview,
                        "first_user_message": preview,
                        "rollout_path": rollout_path,
                        "thread_id": summary.thread_id,
                        "attach_source": "app_server",
                        "attach_service_available": True,
                        "validation_status": summary.workspace_validation.status,
                        "validation_message": summary.workspace_validation.message,
                        "workspace_match": summary.workspace_validation.is_valid,
                        "workspace_realpath": summary.workspace.realpath if summary.workspace else "",
                        "workspace_repo_root": summary.workspace.repo_root if summary.workspace else "",
                        "workspace_fingerprint": (
                            summary.workspace.workspace_fingerprint if summary.workspace else ""
                        ),
                        "requested_workspace_realpath": summary.workspace_validation.requested_workspace.realpath,
                        "requested_workspace_repo_root": summary.workspace_validation.requested_workspace.repo_root or "",
                        "requested_workspace_fingerprint": (
                            summary.workspace_validation.requested_workspace.workspace_fingerprint
                        ),
                        "materialized_history": materialized_history,
                        "last_activity_ts": thread.get("updatedAt") if thread.get("updatedAt") is not None else "",
                        "forked_from_thread_id": thread.get("forkedFromId") or "",
                        "ephemeral": bool(thread.get("ephemeral")),
                        "allowed_actions": allowed_actions,
                    },
                )
            )
        return items

    def hydrate_preview(self, item: NativeResumeSession) -> NativeResumeSession:
        preview = ""
        rollout_path_raw = str(item.locator.get("rollout_path") or "").strip()
        rollout_path = Path(rollout_path_raw) if rollout_path_raw else None
        if rollout_path and rollout_path.is_file():
            rows = read_json_lines(rollout_path)
            for row in reversed(rows):
                if row.get("type") != "response_item":
                    continue
                payload = row.get("payload") or {}
                if payload.get("type") != "message" or payload.get("role") != "assistant":
                    continue
                parts = payload.get("content") or []
                texts: list[str] = []
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict) and part.get("type") == "output_text":
                            text = str(part.get("text") or "").strip()
                            if text:
                                texts.append(text)
                if texts:
                    preview = "\n".join(texts)
                    break
        if not preview:
            preview = str(item.locator.get("preview") or item.locator.get("title") or item.locator.get("first_user_message") or "")
        item.last_agent_message = preview
        item.last_agent_tail = build_tail_preview(preview or item.native_session_id)
        return item

    def _run_attach_operation_sync(
        self,
        working_path: str,
        callback: Callable[[CodexAttachService], Coroutine[Any, Any, T]],
    ) -> T:
        return self._run_awaitable_sync(self._run_attach_operation(working_path, callback))

    async def _run_attach_operation(
        self,
        working_path: str,
        callback: Callable[[CodexAttachService], Coroutine[Any, Any, T]],
    ) -> T:
        if self._attach_service is not None:
            return await callback(self._attach_service)

        transport: CodexTransport | None = None

        async def _provide_transport(cwd: str) -> CodexTransport:
            nonlocal transport
            if transport is not None and transport.is_initialized:
                return transport
            transport = CodexTransport(binary=self._codex_binary, cwd=cwd)
            await transport.start(timeout_seconds=self._attach_catalog_timeout_seconds)
            return transport

        service = CodexAttachService(_provide_transport)
        try:
            return await callback(service)
        finally:
            if transport is not None:
                await transport.stop()

    @staticmethod
    def _run_awaitable_sync(awaitable: Coroutine[Any, Any, T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        result: dict[str, T] = {}
        error: dict[str, BaseException] = {}

        def _runner() -> None:
            try:
                result["value"] = asyncio.run(awaitable)
            except BaseException as exc:  # pragma: no cover - re-raised in caller thread
                error["value"] = exc

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        if "value" in error:
            raise error["value"]
        return result["value"]

    @staticmethod
    def _coalesce_text(*values: Any) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _build_allowed_actions(*, workspace_valid: bool, materialized_history: bool) -> list[str]:
        if workspace_valid and materialized_history:
            return ["resume", "fork"]
        return ["inspect_only"]

    @staticmethod
    def _resolve_rollout_path(thread: dict[str, Any], sqlite_item: NativeResumeSession | None) -> str:
        attach_path = thread.get("path")
        if isinstance(attach_path, str) and attach_path.strip():
            return attach_path.strip()
        if attach_path is None and sqlite_item is not None:
            return str(sqlite_item.locator.get("rollout_path") or "").strip()
        return ""

    @staticmethod
    def _mark_attach_unavailable(item: NativeResumeSession) -> NativeResumeSession:
        item.locator.update(
            {
                "thread_id": item.native_session_id,
                "attach_source": "sqlite_fallback",
                "attach_service_available": False,
                "validation_status": "attach_unavailable",
                "validation_message": "Codex attach catalog is unavailable, so the candidate cannot be validated for direct attach.",
                "workspace_match": False,
                "workspace_realpath": "",
                "workspace_repo_root": "",
                "workspace_fingerprint": "",
                "requested_workspace_realpath": item.working_path,
                "requested_workspace_repo_root": "",
                "requested_workspace_fingerprint": "",
                "materialized_history": bool(str(item.locator.get("rollout_path") or "").strip()),
                "last_activity_ts": item.updated_at.timestamp() if item.updated_at else "",
                "forked_from_thread_id": "",
                "ephemeral": False,
                "allowed_actions": ["inspect_only"],
            }
        )
        return item
