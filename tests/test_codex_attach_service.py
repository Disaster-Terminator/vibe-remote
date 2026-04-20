import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import paths
from config.v2_sessions import SessionsStore
from modules.agents.codex.attach_service import CodexAttachService
from modules.sessions_facade import SessionsFacade


class TestCodexAttachService(unittest.IsolatedAsyncioTestCase):
    async def test_list_threads_dispatches_transport_request_with_normalized_cwd(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            alias = root / "repo-link"
            alias.symlink_to(repo, target_is_directory=True)

            transport = cast(
                Any,
                SimpleNamespace(
                send_request=AsyncMock(
                    return_value={
                        "data": [
                            {"id": "thread-1", "cwd": str(repo)},
                        ],
                        "nextCursor": "cursor-2",
                    }
                )
                ),
            )

            async def provide_transport(cwd: str):
                self.assertEqual(cwd, str(alias))
                return transport

            service = CodexAttachService(provide_transport)
            result = await service.list_threads(str(alias))

            transport.send_request.assert_awaited_once_with("thread/list", {"cwd": str(repo.resolve())})
            self.assertEqual(result.next_cursor, "cursor-2")
            self.assertEqual(result.threads[0].thread_id, "thread-1")
            self.assertEqual(result.threads[0].workspace_validation.status, "valid")
            self.assertEqual(result.threads[0].workspace_validation.requested_workspace.workspace_fingerprint, f"repo:{repo.resolve()}")

    async def test_read_vs_resume_read_thread_is_preview_only(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            vibe_dir = root / ".vibe_remote"

            with patch.object(paths, "get_vibe_remote_dir", return_value=vibe_dir):
                sessions = SessionsFacade(SessionsStore())
                sessions.set_agent_session_mapping("slack::C123", "codex", "base-session-1", "native-thread-1")
                before = sessions.sessions_store.sessions_path.read_text(encoding="utf-8")

                transport = cast(
                    Any,
                    SimpleNamespace(
                    send_request=AsyncMock(
                        return_value={
                            "thread": {"id": "external-thread-1", "cwd": str(repo)},
                            "turns": [{"id": "turn-1"}],
                        }
                    )
                    ),
                )

                async def provide_transport(_cwd: str):
                    return transport

                service = CodexAttachService(provide_transport)
                result = await service.read_thread(str(repo), "external-thread-1", include_turns=True)

                transport.send_request.assert_awaited_once_with(
                    "thread/read",
                    {"threadId": "external-thread-1", "includeTurns": True},
                )
                after = sessions.sessions_store.sessions_path.read_text(encoding="utf-8")
                self.assertEqual(before, after)
                self.assertEqual(result.attach_metadata.attach_mode, "read")
                self.assertEqual(result.thread_id, "external-thread-1")
                self.assertEqual(result.turns, [{"id": "turn-1"}])
                self.assertIsNone(sessions.get_codex_external_attachment("slack::C123", "base-session-1"))
                self.assertEqual(
                    sessions.get_agent_session_id("slack::C123", "base-session-1", "codex"),
                    "native-thread-1",
                )

    async def test_read_vs_resume_resume_thread_dispatches_transport_request(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()

            transport = cast(
                Any,
                SimpleNamespace(
                    send_request=AsyncMock(return_value={"thread": {"id": "thread-9", "cwd": str(repo)}})
                ),
            )

            async def provide_transport(_cwd: str):
                return transport

            service = CodexAttachService(provide_transport)
            result = await service.resume_thread(str(repo), "thread-9")

            transport.send_request.assert_awaited_once_with("thread/resume", {"threadId": "thread-9"})
            self.assertEqual(result.thread_id, "thread-9")
            self.assertEqual(result.attach_metadata.attach_mode, "resume")
            self.assertTrue(result.workspace_validation.is_valid)

    async def test_fork_thread_dispatches_transport_request(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()

            transport = cast(
                Any,
                SimpleNamespace(
                    send_request=AsyncMock(return_value={"thread": {"id": "thread-10", "cwd": str(repo)}})
                ),
            )

            async def provide_transport(_cwd: str):
                return transport

            service = CodexAttachService(provide_transport)
            result = await service.fork_thread(str(repo), "thread-1")

            transport.send_request.assert_awaited_once_with("thread/fork", {"threadId": "thread-1"})
            self.assertEqual(result.thread_id, "thread-10")
            self.assertEqual(result.attach_metadata.attach_mode, "fork")
            self.assertEqual(result.attach_metadata.forked_from_thread_id, "thread-1")

    async def test_resume_thread_fails_when_app_server_returns_no_thread_payload(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()

            transport = cast(Any, SimpleNamespace(send_request=AsyncMock(return_value={})))

            async def provide_transport(_cwd: str):
                return transport

            service = CodexAttachService(provide_transport)

            with self.assertRaisesRegex(RuntimeError, "thread/resume returned no thread payload"):
                await service.resume_thread(str(repo), "thread-missing")

            transport.send_request.assert_awaited_once_with("thread/resume", {"threadId": "thread-missing"})

    async def test_list_threads_marks_missing_workspace_metadata_unverifiable(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()

            transport = cast(
                Any,
                SimpleNamespace(send_request=AsyncMock(return_value={"data": [{"id": "thread-1", "name": "No cwd"}]})),
            )

            async def provide_transport(_cwd: str):
                return transport

            service = CodexAttachService(provide_transport)
            result = await service.list_threads(str(repo))

            self.assertEqual(len(result.threads), 1)
            self.assertEqual(result.threads[0].thread_id, "thread-1")
            self.assertIsNone(result.threads[0].workspace)
            self.assertEqual(result.threads[0].workspace_validation.status, "unverifiable")
            self.assertFalse(result.threads[0].workspace_validation.is_valid)

    def test_workspace_validation_accepts_matching_realpath_and_repo_fingerprint(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            alias = root / "repo-link"
            alias.symlink_to(repo, target_is_directory=True)

            service = CodexAttachService(AsyncMock())
            validation = service.validate_workspace(
                str(alias),
                expected_realpath=str(repo.resolve()),
                expected_repo_root=str(repo.resolve()),
                expected_fingerprint=f"repo:{repo.resolve()}",
            )

            self.assertTrue(validation.is_valid)
            self.assertEqual(validation.status, "valid")
            self.assertEqual(validation.requested_workspace.realpath, str(repo.resolve()))
            self.assertEqual(validation.requested_workspace.workspace_fingerprint, f"repo:{repo.resolve()}")

    def test_workspace_validation_rejects_realpath_mismatch(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_a = root / "repo-a"
            repo_b = root / "repo-b"
            repo_a.mkdir()
            repo_b.mkdir()
            (repo_a / ".git").mkdir()
            (repo_b / ".git").mkdir()

            service = CodexAttachService(AsyncMock())
            validation = service.validate_workspace(
                str(repo_a),
                expected_realpath=str(repo_b.resolve()),
                expected_repo_root=str(repo_b.resolve()),
                expected_fingerprint=f"repo:{repo_b.resolve()}",
            )

            self.assertFalse(validation.is_valid)
            self.assertEqual(validation.status, "realpath_mismatch")
            self.assertIsNotNone(validation.candidate_workspace)
            candidate_workspace = validation.candidate_workspace
            assert candidate_workspace is not None
            self.assertEqual(candidate_workspace.realpath, str(repo_b.resolve()))
            self.assertEqual(validation.requested_workspace.workspace_fingerprint, f"repo:{repo_a.resolve()}")


if __name__ == "__main__":
    unittest.main()
