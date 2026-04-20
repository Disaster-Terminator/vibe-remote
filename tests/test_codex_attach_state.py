import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import paths
from config.v2_sessions import SessionsStore
from modules.sessions_facade import SessionsFacade


class TestCodexAttachState:
    def test_round_trip_external_attachment(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
        store = SessionsStore()
        sessions = SessionsFacade(store)

        sessions.set_agent_session_mapping("slack::C123", "codex", "base-session-1", "native-thread-1")
        sessions.upsert_codex_external_attachment(
            "slack::C123",
            "base-session-1",
            binding_origin="external_attached",
            codex_thread_id="codex-thread-123",
            attach_mode="resume",
            workspace_realpath="/tmp/project",
            workspace_repo_root="/tmp/project",
            workspace_fingerprint="repo:/tmp/project",
            forked_from_thread_id=None,
            attached_at="2026-04-19T12:00:00Z",
            last_validated_at="2026-04-19T12:05:00Z",
            validation_status="valid",
        )
        sessions.upsert_codex_external_attachment(
            "slack::C123",
            "base-session-1",
            binding_origin="external_attached",
            codex_thread_id="codex-thread-123",
            attach_mode="fork",
            workspace_realpath="/tmp/project",
            workspace_repo_root="/tmp/project",
            workspace_fingerprint="repo:/tmp/project",
            forked_from_thread_id="codex-thread-123",
            attached_at="2026-04-19T12:00:00Z",
            last_validated_at="2026-04-19T12:10:00Z",
            validation_status="forked",
        )

        reloaded = SessionsStore()
        reloaded.load()
        reloaded_sessions = SessionsFacade(reloaded)

        attachment = reloaded_sessions.get_codex_external_attachment("slack::C123", "base-session-1")
        assert attachment is not None
        assert attachment.codex_thread_id == "codex-thread-123"
        assert attachment.attach_mode == "fork"
        assert attachment.workspace_fingerprint == "repo:/tmp/project"
        assert attachment.forked_from_thread_id == "codex-thread-123"
        assert attachment.last_validated_at == "2026-04-19T12:10:00Z"
        assert attachment.validation_status == "forked"
        assert reloaded_sessions.get_agent_session_id("slack::C123", "base-session-1", "codex") == "native-thread-1"

    def test_duplicate_lookup_reverse_lookup_by_thread_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
        sessions = SessionsFacade(SessionsStore())

        sessions.upsert_codex_external_attachment(
            "slack::C123",
            "base-session-1",
            binding_origin="external_attached",
            codex_thread_id="codex-thread-dup",
            attach_mode="resume",
            workspace_realpath="/tmp/project-a",
            workspace_repo_root="/tmp/project-a",
            workspace_fingerprint="repo:/tmp/project-a",
            forked_from_thread_id=None,
            attached_at="2026-04-19T12:00:00Z",
            last_validated_at="2026-04-19T12:05:00Z",
            validation_status="valid",
        )
        sessions.upsert_codex_external_attachment(
            "discord::D456",
            "base-session-2",
            binding_origin="external_attached",
            codex_thread_id="codex-thread-dup",
            attach_mode="resume",
            workspace_realpath="/tmp/project-b",
            workspace_repo_root="/tmp/project-b",
            workspace_fingerprint="repo:/tmp/project-b",
            forked_from_thread_id=None,
            attached_at="2026-04-19T13:00:00Z",
            last_validated_at="2026-04-19T13:05:00Z",
            validation_status="valid",
        )
        sessions.upsert_codex_external_attachment(
            "slack::C123",
            "base-session-3",
            binding_origin="external_attached",
            codex_thread_id="codex-thread-other",
            attach_mode="resume",
            workspace_realpath="/tmp/project-c",
            workspace_repo_root="/tmp/project-c",
            workspace_fingerprint="repo:/tmp/project-c",
            forked_from_thread_id=None,
            attached_at="2026-04-19T14:00:00Z",
            last_validated_at="2026-04-19T14:05:00Z",
            validation_status="valid",
        )

        matches = sessions.find_codex_external_attachments_by_thread_id("codex-thread-dup")

        assert [(match.session_key, match.base_session_id) for match in matches] == [
            ("discord::D456", "base-session-2"),
            ("slack::C123", "base-session-1"),
        ]
        assert {match.attachment.workspace_fingerprint for match in matches} == {
            "repo:/tmp/project-a",
            "repo:/tmp/project-b",
        }

    def test_migration_safe_legacy_load_defaults_missing_attachment_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
        store = SessionsStore()
        store.sessions_path.parent.mkdir(parents=True, exist_ok=True)
        store.sessions_path.write_text(
            json.dumps(
                {
                    "session_mappings": {"slack::C123": {"codex": {"base-session-1": "native-thread-1"}}},
                    "active_slack_threads": {},
                    "active_polls": {},
                    "processed_message_ts": {},
                    "last_activity": "2026-04-19T12:00:00Z",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        reloaded = SessionsStore()
        reloaded.load()
        reloaded_sessions = SessionsFacade(reloaded)

        assert reloaded.state.codex_external_attachments == {}
        assert reloaded_sessions.get_codex_external_attachment("slack::C123", "base-session-1") is None
        assert reloaded_sessions.find_codex_external_attachments_by_thread_id("native-thread-1") == []
        assert reloaded_sessions.get_agent_session_id("slack::C123", "base-session-1", "codex") == "native-thread-1"

    def test_migration_safe_legacy_attachment_record_defaults_missing_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
        store = SessionsStore()
        store.sessions_path.parent.mkdir(parents=True, exist_ok=True)
        store.sessions_path.write_text(
            json.dumps(
                {
                    "session_mappings": {"slack::C123": {"codex": {"base-session-1": "native-thread-1"}}},
                    "codex_external_attachments": {
                        "slack::C123": {
                            "base-session-1": {
                                "binding_origin": "external_attached",
                                "codex_thread_id": "external-thread-legacy",
                                "workspace_realpath": "/tmp/project",
                            }
                        }
                    },
                    "active_slack_threads": {},
                    "active_polls": {},
                    "processed_message_ts": {},
                    "last_activity": "2026-04-19T12:00:00Z",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        reloaded = SessionsStore()
        reloaded.load()
        reloaded_sessions = SessionsFacade(reloaded)

        attachment = reloaded_sessions.get_codex_external_attachment("slack::C123", "base-session-1")

        assert attachment is not None
        assert attachment.binding_origin == "external_attached"
        assert attachment.codex_thread_id == "external-thread-legacy"
        assert attachment.attach_mode == ""
        assert attachment.workspace_realpath == "/tmp/project"
        assert attachment.workspace_repo_root is None
        assert attachment.workspace_fingerprint == ""
        assert attachment.forked_from_thread_id is None
        assert attachment.attached_at is None
        assert attachment.last_validated_at is None
        assert attachment.validation_status == ""

    def test_clear_external_attachment(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
        sessions = SessionsFacade(SessionsStore())

        sessions.upsert_codex_external_attachment(
            "slack::C123",
            "base-session-1",
            binding_origin="external_attached",
            codex_thread_id="codex-thread-123",
            attach_mode="resume",
            workspace_realpath="/tmp/project",
            workspace_repo_root="/tmp/project",
            workspace_fingerprint="repo:/tmp/project",
            forked_from_thread_id=None,
            attached_at="2026-04-19T12:00:00Z",
            last_validated_at="2026-04-19T12:05:00Z",
            validation_status="valid",
        )

        assert sessions.clear_codex_external_attachment("slack::C123", "base-session-1")
        assert not sessions.clear_codex_external_attachment("slack::C123", "base-session-1")

        reloaded = SessionsStore()
        reloaded.load()
        reloaded_sessions = SessionsFacade(reloaded)

        assert reloaded_sessions.get_codex_external_attachment("slack::C123", "base-session-1") is None
        assert reloaded_sessions.find_codex_external_attachments_by_thread_id("codex-thread-123") == []
