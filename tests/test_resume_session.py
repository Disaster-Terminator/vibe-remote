import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.handlers.command_handlers import CommandHandlers
from core.handlers.session_handler import SessionHandler
from modules.agents.native_sessions.types import NativeResumeSession
from modules.im import MessageContext
from config.v2_config import SlackConfig

try:
    from modules.im.slack import SlackBot
except ModuleNotFoundError:
    SlackBot = None


class _StubSettingsManager:
    def __init__(self):
        self.set_calls = []
        self.mark_calls = []
        self.routing_calls = []
        self.codex_attachment_upserts = []
        self.codex_attachments = {}

    def set_agent_session_mapping(self, settings_key, agent_name, thread_id, session_id):
        self.set_calls.append((settings_key, agent_name, thread_id, session_id))

    def mark_thread_active(self, user_id, channel_id, thread_ts):
        self.mark_calls.append((user_id, channel_id, thread_ts))

    def list_all_agent_sessions(self, user_id):
        return {}

    def get_channel_routing(self, settings_key):
        return None

    def set_channel_routing(self, settings_key, routing):
        self.routing_calls.append((settings_key, routing))

    def upsert_codex_external_attachment(self, session_key, base_session_id, **kwargs):
        record = SimpleNamespace(**kwargs)
        self.codex_attachments[(str(session_key), base_session_id)] = record
        self.codex_attachment_upserts.append((str(session_key), base_session_id, record))

    def get_codex_external_attachment(self, session_key, base_session_id):
        return self.codex_attachments.get((str(session_key), base_session_id))

    def find_codex_external_attachments_by_thread_id(self, codex_thread_id):
        matches = []
        for (session_key, base_session_id), attachment in self.codex_attachments.items():
            if getattr(attachment, "codex_thread_id", None) != codex_thread_id:
                continue
            matches.append(
                SimpleNamespace(
                    session_key=session_key,
                    base_session_id=base_session_id,
                    attachment=attachment,
                )
            )
        return matches


class _StubIMClient:
    def __init__(self):
        self.messages = []
        self.resume_calls = []
        self.prepared_context: MessageContext | None = None
        self.should_use_thread_for_reply = lambda: True
        self.should_use_message_id_for_channel_session = lambda context=None: True
        self.should_use_thread_for_dm_session = lambda: False

    async def send_message(self, context, text, parse_mode=None):
        ts = f"T{len(self.messages) + 1}"
        self.messages.append((context.channel_id, context.thread_id, text, ts))
        return ts

    async def open_resume_session_modal(self, trigger_id, sessions, channel_id, thread_id, host_message_ts, working_path=None):
        self.resume_calls.append((trigger_id, sessions, channel_id, thread_id, host_message_ts, working_path))

    async def run_on_client_loop(self, coro):
        return await coro

    async def prepare_resume_context(self, context, host_message_ts=None, is_dm=False):
        return self.prepared_context or context


class _StubNativeSessionService:
    def __init__(self, sessions=None):
        self.sessions = sessions or []
        self.calls = []

    def list_recent_sessions(self, working_path: str, limit: int = 100):
        self.calls.append((working_path, limit))
        return list(self.sessions)

    def get_session(self, working_path: str, agent: str, native_session_id: str):
        for item in self.sessions:
            if item.agent == agent and item.native_session_id == native_session_id:
                return item
        return None


class _StubConfig:
    def __init__(self, platform="slack"):
        self.platform = platform
        self.language = "en"
        self.claude = type("ClaudeCfg", (), {"cwd": "/tmp"})()


class _StubController:
    def __init__(self):
        # Bypass base __init__ to avoid wiring everything
        pass

    def init_minimal(self, im_client, settings_manager, config, session_manager=None):
        self.im_client = im_client
        self.settings_manager = settings_manager
        self.sessions = settings_manager
        self.config = config
        self.session_manager = session_manager
        self.claude_sessions = {}
        self.receiver_tasks = {}
        self.stored_session_mappings = {}
        self.agent_service = SimpleNamespace(agents={"claude": object(), "codex": object()})
        self.native_session_service = _StubNativeSessionService()
        self.command_handler = CommandHandlers(self)
        self.session_handler = SessionHandler(self)

    def _get_settings_key(self, context: MessageContext) -> str:
        return context.user_id if (context.platform_specific or {}).get("is_dm") else context.channel_id

    def _get_session_key(self, context: MessageContext) -> str:
        return f"{getattr(context, 'platform', None) or 'test'}::{self._get_settings_key(context)}"

    def get_cwd(self, context: MessageContext) -> str:
        return "/Users/cyh/vibe-remote"


def _codex_workspace(cwd: str = "/Users/cyh/vibe-remote"):
    return SimpleNamespace(
        cwd=cwd,
        realpath=cwd,
        repo_root=cwd,
        workspace_fingerprint=f"repo:{cwd}",
    )


def _codex_validation(
    *,
    status: str = "valid",
    is_valid: bool = True,
    message: str = "Workspace validation succeeded.",
    requested_workspace=None,
    candidate_workspace=None,
):
    requested = requested_workspace or _codex_workspace()
    candidate = candidate_workspace if candidate_workspace is not None else requested
    return SimpleNamespace(
        requested_workspace=requested,
        candidate_workspace=candidate,
        status=status,
        is_valid=is_valid,
        message=message,
    )


def _codex_attach_result(
    *,
    thread_id: str,
    attach_mode: str,
    source_thread_id: str | None = None,
    workspace=None,
):
    resolved_workspace = workspace or _codex_workspace()
    validation = _codex_validation(requested_workspace=resolved_workspace, candidate_workspace=resolved_workspace)
    return SimpleNamespace(
        thread_id=thread_id,
        workspace=resolved_workspace,
        workspace_validation=validation,
        attach_metadata=SimpleNamespace(
            candidate_workspace=resolved_workspace,
            requested_workspace=resolved_workspace,
            workspace_validation=validation,
            attach_mode=attach_mode,
            requested_thread_id=source_thread_id or thread_id,
            resolved_thread_id=thread_id,
            forked_from_thread_id=source_thread_id,
        ),
        raw_result={},
    )


def _codex_attach_session(
    *,
    thread_id: str,
    allowed_actions: list[str],
    validation_status: str = "valid",
    workspace_match: bool = True,
):
    return NativeResumeSession(
        agent="codex",
        agent_prefix="cx",
        native_session_id=thread_id,
        working_path="/Users/cyh/vibe-remote",
        created_at=None,
        updated_at=None,
        sort_ts=100.0,
        last_agent_message="Inspect this external Codex thread before binding.",
        last_agent_tail="...inspect before binding",
        locator={
            "title": "External Codex Thread",
            "preview": "Inspect this external Codex thread before binding.",
            "thread_id": thread_id,
            "codex_thread_id": thread_id,
            "attach_source": "app_server",
            "attach_service_available": True,
            "validation_status": validation_status,
            "workspace_match": workspace_match,
            "workspace_realpath": "/Users/cyh/vibe-remote",
            "workspace_repo_root": "/Users/cyh/vibe-remote",
            "workspace_fingerprint": "repo:/Users/cyh/vibe-remote",
            "materialized_history": True,
            "allowed_actions": allowed_actions,
        },
    )


def _codex_local_session(*, session_id: str, preview: str = "Local Codex session preview."):
    return NativeResumeSession(
        agent="codex",
        agent_prefix="cx",
        native_session_id=session_id,
        working_path="/Users/cyh/vibe-remote",
        created_at=None,
        updated_at=None,
        sort_ts=95.0,
        last_agent_message=preview,
        last_agent_tail="...local codex",
        locator={"title": "Local Codex Session"},
    )


class _StubCodexAttachService:
    def __init__(self, *, validation, resume_result=None, fork_result=None):
        self.validation = validation
        self.resume_thread = AsyncMock(return_value=resume_result)
        self.fork_thread = AsyncMock(return_value=fork_result)
        self.validation_calls = []

    def validate_workspace(
        self,
        cwd: str,
        *,
        candidate_cwd=None,
        expected_realpath=None,
        expected_repo_root=None,
        expected_fingerprint=None,
    ):
        self.validation_calls.append(
            {
                "cwd": cwd,
                "candidate_cwd": candidate_cwd,
                "expected_realpath": expected_realpath,
                "expected_repo_root": expected_repo_root,
                "expected_fingerprint": expected_fingerprint,
            }
        )
        return self.validation


class ResumeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_resume_session_submission_threads(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.native_session_service = _StubNativeSessionService(
            [
                NativeResumeSession(
                    agent="claude",
                    agent_prefix="cc",
                    native_session_id="sess_abc",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=10.0,
                    last_agent_message="The latest Claude answer ends with a concise handoff.",
                    last_agent_tail="...concise handoff",
                )
            ]
        )

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="claude",
            session_id="sess_abc",
        )

        self.assertEqual(
            settings.set_calls,
            [("slack::C111", "claude", "slack_169999.123", "sess_abc")],
        )
        self.assertEqual(settings.mark_calls, [("U123", "C111", "169999.123")])
        self.assertEqual(len(im_client.messages), 2)
        self.assertIn("sess_abc", im_client.messages[0][2])
        self.assertIn("The latest Claude answer ends with a concise handoff", im_client.messages[0][2])
        self.assertIn("Reply in this thread", im_client.messages[1][2])

    async def test_handle_resume_session_submission_dm_falls_back_to_channel(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.native_session_service = _StubNativeSessionService([_codex_local_session(session_id="sess_dm")])

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U999",
            channel_id="DXYZ",
            thread_id=None,
            agent="codex",
            session_id="sess_dm",
        )

        # No thread provided -> new confirmation message anchor used
        self.assertEqual(settings.set_calls, [("slack::DXYZ", "codex", "slack_T1", "sess_dm")])
        self.assertEqual(settings.mark_calls, [("U999", "DXYZ", "T1")])
        self.assertEqual(len(im_client.messages), 2)
        self.assertIn("sess_dm", im_client.messages[0][2])
        self.assertIn("Send your next message directly", im_client.messages[1][2])

    async def test_handle_resume_session_submission_prepares_resume_binding(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        ctrl.native_session_service = _StubNativeSessionService([_codex_local_session(session_id="sess_abc")])
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock())
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U999",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="sess_abc",
        )

        codex_agent.prepare_resume_binding.assert_awaited_once_with(
            base_session_id="slack_169999.123",
            session_key="slack::C111",
            working_path="/Users/cyh/vibe-remote",
        )

    async def test_codex_manual_unresolved_plain_session_id_fails_closed(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock())
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="unresolved-manual-id",
        )

        codex_agent.prepare_resume_binding.assert_not_awaited()
        self.assertEqual(settings.routing_calls, [])
        self.assertEqual(settings.set_calls, [])
        self.assertEqual(settings.mark_calls, [])
        self.assertEqual(settings.codex_attachment_upserts, [])
        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("interactive inspect + Resume/Fork flow", im_client.messages[0][2])

    async def test_codex_manual_unresolved_attach_submission_fails_closed(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(),
            resume_result=_codex_attach_result(thread_id="should-not-run", attach_mode="resume"),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="unresolved-manual-id",
            action_intent="resume",
            codex_thread_id="unresolved-manual-id",
        )

        attach_service.resume_thread.assert_not_awaited()
        attach_service.fork_thread.assert_not_awaited()
        codex_agent.prepare_resume_binding.assert_not_awaited()
        self.assertEqual(settings.routing_calls, [])
        self.assertEqual(settings.set_calls, [])
        self.assertEqual(settings.mark_calls, [])
        self.assertEqual(settings.codex_attachment_upserts, [])
        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("interactive inspect + Resume/Fork flow", im_client.messages[0][2])

    async def test_handle_resume_session_submission_prepares_claude_binding(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        claude_agent = SimpleNamespace(prepare_resume_binding=AsyncMock())
        ctrl.agent_service = SimpleNamespace(agents={"claude": claude_agent, "codex": object()})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="claude",
            session_id="sess_abc",
        )

        claude_agent.prepare_resume_binding.assert_awaited_once_with(
            base_session_id="slack_169999.123",
            session_key="slack::C111",
            working_path="/Users/cyh/vibe-remote",
        )

    async def test_codex_external_attach_resumes_and_persists_provenance(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        ctrl.native_session_service = _StubNativeSessionService(
            [_codex_attach_session(thread_id="external-thread-1", allowed_actions=["resume", "fork"])]
        )
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(),
            resume_result=_codex_attach_result(thread_id="external-thread-1", attach_mode="resume"),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-1",
            action_intent="resume",
            codex_thread_id="external-thread-1",
        )

        attach_service.resume_thread.assert_awaited_once_with("/Users/cyh/vibe-remote", "external-thread-1")
        attach_service.fork_thread.assert_not_awaited()
        codex_agent.prepare_resume_binding.assert_awaited_once_with(
            base_session_id="slack_169999.123",
            session_key="slack::C111",
            working_path="/Users/cyh/vibe-remote",
        )
        self.assertEqual(settings.routing_calls[0][0], "C111")
        self.assertEqual(
            settings.set_calls,
            [("slack::C111", "codex", "slack_169999.123", "external-thread-1")],
        )
        self.assertEqual(settings.mark_calls, [("U123", "C111", "169999.123")])
        attachment = settings.get_codex_external_attachment("slack::C111", "slack_169999.123")
        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.binding_origin, "external_attached")
        self.assertEqual(attachment.codex_thread_id, "external-thread-1")
        self.assertEqual(attachment.attach_mode, "resume")
        self.assertEqual(attachment.workspace_fingerprint, "repo:/Users/cyh/vibe-remote")
        self.assertEqual(attachment.validation_status, "valid")
        self.assertEqual(len(im_client.messages), 2)
        self.assertIn("external-thread-1", im_client.messages[0][2])
        self.assertIn("external Codex thread", im_client.messages[0][2])

    async def test_codex_external_attach_forks_and_persists_provenance(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        ctrl.native_session_service = _StubNativeSessionService(
            [_codex_attach_session(thread_id="external-thread-2", allowed_actions=["resume", "fork"])]
        )
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(),
            fork_result=_codex_attach_result(
                thread_id="forked-thread-2",
                attach_mode="fork",
                source_thread_id="external-thread-2",
            ),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-2",
            action_intent="fork",
            codex_thread_id="external-thread-2",
        )

        attach_service.resume_thread.assert_not_awaited()
        attach_service.fork_thread.assert_awaited_once_with("/Users/cyh/vibe-remote", "external-thread-2")
        self.assertEqual(
            settings.set_calls,
            [("slack::C111", "codex", "slack_169999.123", "forked-thread-2")],
        )
        attachment = settings.get_codex_external_attachment("slack::C111", "slack_169999.123")
        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.codex_thread_id, "forked-thread-2")
        self.assertEqual(attachment.attach_mode, "fork")
        self.assertEqual(attachment.forked_from_thread_id, "external-thread-2")
        self.assertIn("forked-thread-2", im_client.messages[0][2])
        self.assertIn("external-thread-2", im_client.messages[0][2])

    async def test_codex_external_attach_fork_confirmation_preview_uses_effective_session_id(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        ctrl.native_session_service = _StubNativeSessionService(
            [
                _codex_attach_session(thread_id="external-thread-preview", allowed_actions=["resume", "fork"]),
                NativeResumeSession(
                    agent="codex",
                    agent_prefix="cx",
                    native_session_id="forked-thread-preview",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=101.0,
                    last_agent_message="Forked thread preview should appear in confirmation.",
                    last_agent_tail="...forked preview",
                ),
            ]
        )
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(),
            fork_result=_codex_attach_result(
                thread_id="forked-thread-preview",
                attach_mode="fork",
                source_thread_id="external-thread-preview",
            ),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-preview",
            action_intent="fork",
            codex_thread_id="external-thread-preview",
        )

        self.assertIn("forked-thread-preview", im_client.messages[0][2])
        self.assertIn("Forked thread preview should appear in confirmation.", im_client.messages[0][2])
        self.assertNotIn("Inspect this external Codex thread before binding.", im_client.messages[0][2])

    async def test_codex_external_attach_validation_failure_uses_localized_status_reason(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        config = _StubConfig()
        config.language = "zh"
        ctrl.init_minimal(im_client, settings, config)
        ctrl.native_session_service = _StubNativeSessionService(
            [_codex_attach_session(thread_id="external-thread-3", allowed_actions=["resume", "fork"])]
        )
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(
                status="realpath_mismatch",
                is_valid=False,
                message="Normalized working directory does not match the target thread workspace.",
            ),
            resume_result=_codex_attach_result(thread_id="external-thread-3", attach_mode="resume"),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-3",
            action_intent="resume",
            codex_thread_id="external-thread-3",
        )

        attach_service.resume_thread.assert_not_awaited()
        attach_service.fork_thread.assert_not_awaited()
        codex_agent.prepare_resume_binding.assert_not_awaited()
        self.assertEqual(settings.routing_calls, [])
        self.assertEqual(settings.set_calls, [])
        self.assertEqual(settings.mark_calls, [])
        self.assertEqual(settings.codex_attachment_upserts, [])
        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("realpath_mismatch", im_client.messages[0][2])
        self.assertIn("该线程属于另一个工作目录", im_client.messages[0][2])
        self.assertNotIn("Normalized working directory does not match", im_client.messages[0][2])

    async def test_codex_external_attach_validation_failure_unknown_status_uses_fallback_reason(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.native_session_service = _StubNativeSessionService(
            [_codex_attach_session(thread_id="external-thread-unknown", allowed_actions=["resume", "fork"])]
        )
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(
                status="mystery_status",
                is_valid=False,
                message="mystery backend message",
            ),
            resume_result=_codex_attach_result(thread_id="external-thread-unknown", attach_mode="resume"),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-unknown",
            action_intent="resume",
            codex_thread_id="external-thread-unknown",
        )

        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("status: mystery_status", im_client.messages[0][2])
        self.assertIn("Workspace validation failed.", im_client.messages[0][2])
        self.assertNotIn("mystery backend message", im_client.messages[0][2])

    async def test_codex_manual_attach_candidate_requires_interactive_picker(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.native_session_service = _StubNativeSessionService(
            [_codex_attach_session(thread_id="external-thread-manual", allowed_actions=["resume", "fork"])]
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock())
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-manual",
        )

        self.assertEqual(settings.routing_calls, [])
        self.assertEqual(settings.set_calls, [])
        self.assertEqual(settings.mark_calls, [])
        self.assertEqual(settings.codex_attachment_upserts, [])
        codex_agent.prepare_resume_binding.assert_not_awaited()
        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("interactive inspect + Resume/Fork flow", im_client.messages[0][2])

    async def test_codex_external_attach_missing_result_thread_id_mutates_nothing(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.native_session_service = _StubNativeSessionService(
            [_codex_attach_session(thread_id="external-thread-4", allowed_actions=["resume", "fork"])]
        )
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(),
            resume_result=_codex_attach_result(thread_id="", attach_mode="resume"),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-4",
            action_intent="resume",
            codex_thread_id="external-thread-4",
        )

        attach_service.resume_thread.assert_awaited_once_with("/Users/cyh/vibe-remote", "external-thread-4")
        attach_service.fork_thread.assert_not_awaited()
        codex_agent.prepare_resume_binding.assert_not_awaited()
        self.assertEqual(settings.routing_calls, [])
        self.assertEqual(settings.set_calls, [])
        self.assertEqual(settings.mark_calls, [])
        self.assertEqual(settings.codex_attachment_upserts, [])
        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("missing the target thread id", im_client.messages[0][2])

    async def test_codex_external_attach_duplicate_without_fork_permission_fails_closed(self):
        settings = _StubSettingsManager()
        settings.upsert_codex_external_attachment(
            "slack::OTHER",
            "slack_existing",
            binding_origin="external_attached",
            codex_thread_id="external-thread-resume-only",
            attach_mode="resume",
            workspace_realpath="/Users/cyh/vibe-remote",
            workspace_repo_root="/Users/cyh/vibe-remote",
            workspace_fingerprint="repo:/Users/cyh/vibe-remote",
            forked_from_thread_id=None,
            attached_at="2026-04-19T12:00:00Z",
            last_validated_at="2026-04-19T12:00:00Z",
            validation_status="valid",
        )
        seeded_attachment_writes = len(settings.codex_attachment_upserts)
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.native_session_service = _StubNativeSessionService(
            [_codex_attach_session(thread_id="external-thread-resume-only", allowed_actions=["resume"])]
        )
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(),
            resume_result=_codex_attach_result(thread_id="external-thread-resume-only", attach_mode="resume"),
            fork_result=_codex_attach_result(
                thread_id="forked-thread-resume-only",
                attach_mode="fork",
                source_thread_id="external-thread-resume-only",
            ),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-resume-only",
            action_intent="resume",
            codex_thread_id="external-thread-resume-only",
        )

        attach_service.resume_thread.assert_not_awaited()
        attach_service.fork_thread.assert_not_awaited()
        codex_agent.prepare_resume_binding.assert_not_awaited()
        self.assertEqual(settings.routing_calls, [])
        self.assertEqual(settings.set_calls, [])
        self.assertEqual(settings.mark_calls, [])
        self.assertEqual(len(settings.codex_attachment_upserts), seeded_attachment_writes)
        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("action `fork`", im_client.messages[0][2])

    async def test_codex_external_attach_fork_on_ambiguity_when_duplicate_binding_exists(self):
        settings = _StubSettingsManager()
        settings.upsert_codex_external_attachment(
            "slack::OTHER",
            "slack_existing",
            binding_origin="external_attached",
            codex_thread_id="external-thread-dup",
            attach_mode="resume",
            workspace_realpath="/Users/cyh/vibe-remote",
            workspace_repo_root="/Users/cyh/vibe-remote",
            workspace_fingerprint="repo:/Users/cyh/vibe-remote",
            forked_from_thread_id=None,
            attached_at="2026-04-19T12:00:00Z",
            last_validated_at="2026-04-19T12:00:00Z",
            validation_status="valid",
        )
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        ctrl.native_session_service = _StubNativeSessionService(
            [_codex_attach_session(thread_id="external-thread-dup", allowed_actions=["resume", "fork"])]
        )
        attach_service = _StubCodexAttachService(
            validation=_codex_validation(),
            resume_result=_codex_attach_result(thread_id="external-thread-dup", attach_mode="resume"),
            fork_result=_codex_attach_result(
                thread_id="forked-thread-dup",
                attach_mode="fork",
                source_thread_id="external-thread-dup",
            ),
        )
        codex_agent = SimpleNamespace(prepare_resume_binding=AsyncMock(), _attach_service=attach_service)
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": codex_agent})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="codex",
            session_id="external-thread-dup",
            action_intent="resume",
            codex_thread_id="external-thread-dup",
        )

        attach_service.resume_thread.assert_not_awaited()
        attach_service.fork_thread.assert_awaited_once_with("/Users/cyh/vibe-remote", "external-thread-dup")
        self.assertEqual(
            settings.set_calls,
            [("slack::C111", "codex", "slack_169999.123", "forked-thread-dup")],
        )
        attachment = settings.get_codex_external_attachment("slack::C111", "slack_169999.123")
        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.attach_mode, "fork")
        self.assertEqual(attachment.codex_thread_id, "forked-thread-dup")
        self.assertEqual(attachment.forked_from_thread_id, "external-thread-dup")

    async def test_handle_resume_session_submission_skips_resume_prepare_when_backend_has_no_hook(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())
        ctrl.agent_service = SimpleNamespace(agents={"claude": object(), "codex": object()})

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="claude",
            session_id="sess_abc",
        )

        self.assertEqual(len(settings.set_calls), 1)
        self.assertEqual(settings.set_calls[0][0], "slack::C111")
        self.assertEqual(settings.set_calls[0][1], "claude")
        self.assertEqual(settings.set_calls[0][3], "sess_abc")

    async def test_handle_resume_session_submission_discord_dm_uses_channel_session_key(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="discord"))
        ctrl.im_client.should_use_thread_for_dm_session = lambda: False
        ctrl.native_session_service = _StubNativeSessionService([_codex_local_session(session_id="sess_dm")])

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U999",
            channel_id="DMCHAN",
            thread_id=None,
            agent="codex",
            session_id="sess_dm",
            is_dm=True,
        )

        self.assertEqual(settings.set_calls, [("discord::U999", "codex", "discord_DMCHAN", "sess_dm")])
        self.assertEqual(settings.mark_calls, [("U999", "DMCHAN", "T1")])

    async def test_handle_resume_session_submission_lark_dm_uses_thread_session_key(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="lark"))
        ctrl.im_client.should_use_thread_for_dm_session = lambda: True

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U888",
            channel_id="DMCHAT",
            thread_id="root_123",
            agent="claude",
            session_id="sess_lark_dm",
            is_dm=True,
        )

        self.assertEqual(settings.set_calls, [("lark::U888", "claude", "lark_root_123", "sess_lark_dm")])
        self.assertEqual(settings.mark_calls, [("U888", "DMCHAT", "root_123")])
        self.assertEqual(len(im_client.messages), 2)
        self.assertIn("Reply to this message", im_client.messages[1][2])

    async def test_handle_resume_session_submission_uses_prepared_thread_context(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="discord"))
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        ctrl.native_session_service = _StubNativeSessionService([_codex_local_session(session_id="sess_sub")])
        im_client.prepared_context = MessageContext(
            user_id="U777",
            channel_id="C777",
            platform="discord",
            thread_id="SUB123",
            message_id="HOST1",
            platform_specific={"is_dm": False},
        )

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U777",
            channel_id="C777",
            thread_id=None,
            agent="codex",
            session_id="sess_sub",
            host_message_ts="HOST1",
            is_dm=False,
            platform="discord",
        )

        self.assertEqual(settings.set_calls, [("discord::C777", "codex", "discord_SUB123", "sess_sub")])
        self.assertEqual(settings.mark_calls, [("U777", "C777", "SUB123")])
        self.assertEqual(len(im_client.messages), 2)
        self.assertEqual(im_client.messages[0][1], None)
        self.assertEqual(im_client.messages[1][1], "SUB123")
        self.assertIn("Reply in the subchannel", im_client.messages[1][2])

    async def test_handle_resume_session_submission_telegram_group_keeps_channel_mapping(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="telegram"))
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        ctrl.im_client.should_use_message_id_for_channel_session = lambda context=None: False
        ctrl.native_session_service = _StubNativeSessionService([_codex_local_session(session_id="sess_telegram_group")])

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U777",
            channel_id="-100123",
            thread_id=None,
            agent="codex",
            session_id="sess_telegram_group",
            host_message_ts="HOST1",
            is_dm=False,
            platform="telegram",
        )

        self.assertEqual(
            settings.set_calls,
            [("telegram::-100123", "codex", "telegram_-100123", "sess_telegram_group")],
        )
        self.assertEqual(settings.mark_calls, [("U777", "-100123", "T1")])
        self.assertEqual(len(im_client.messages), 2)
        self.assertEqual(im_client.messages[0][1], None)
        self.assertEqual(im_client.messages[1][1], None)
        self.assertIn("Send your next message directly", im_client.messages[1][2])

    async def test_handle_resume_session_submission_telegram_forum_keeps_topic_mapping(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="telegram"))
        ctrl.im_client.should_use_thread_for_reply = lambda: True
        ctrl.im_client.should_use_message_id_for_channel_session = lambda context=None: False
        ctrl.native_session_service = _StubNativeSessionService([_codex_local_session(session_id="sess_telegram_topic")])

        await ctrl.session_handler.handle_resume_session_submission(
            user_id="U777",
            channel_id="-100123",
            thread_id="99",
            agent="codex",
            session_id="sess_telegram_topic",
            host_message_ts="HOST1",
            is_dm=False,
            platform="telegram",
        )

        self.assertEqual(
            settings.set_calls,
            [("telegram::-100123", "codex", "telegram_99", "sess_telegram_topic")],
        )
        self.assertEqual(settings.mark_calls, [("U777", "-100123", "99")])
        self.assertEqual(len(im_client.messages), 2)
        self.assertEqual(im_client.messages[0][1], "99")
        self.assertEqual(im_client.messages[1][1], "99")
        self.assertIn("Reply in this thread", im_client.messages[1][2])

    async def test_command_handlers_handle_resume_opens_modal(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="slack"))
        ctrl.native_session_service = _StubNativeSessionService(
            [
                NativeResumeSession(
                    agent="codex",
                    agent_prefix="cx",
                    native_session_id="thread_123",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=100.0,
                    last_agent_message="done",
                    last_agent_tail="...done",
                )
            ]
        )

        ctx = MessageContext(
            user_id="U1",
            channel_id="CCHAN",
            thread_id="TH1",
            message_id="TS1",
            platform_specific={"trigger_id": "TRIG"},
        )

        await ctrl.command_handler.handle_resume(ctx)

        self.assertEqual(im_client.messages, [])
        self.assertEqual(len(im_client.resume_calls), 1)
        trigger_id, sessions, channel_id, thread_id, host_ts, working_path = im_client.resume_calls[0]
        self.assertEqual((trigger_id, channel_id, thread_id, host_ts), ("TRIG", "CCHAN", "TH1", "TS1"))
        self.assertEqual(working_path, "/Users/cyh/vibe-remote")
        self.assertEqual([item.native_session_id for item in sessions], ["thread_123"])
        self.assertEqual(ctrl.native_session_service.calls, [("/Users/cyh/vibe-remote", 100)])

    async def test_command_handlers_handle_resume_filters_disabled_backends_before_modal(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="slack"))
        ctrl.native_session_service = _StubNativeSessionService(
            [
                NativeResumeSession(
                    agent="opencode",
                    agent_prefix="oc",
                    native_session_id="oc_disabled",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=200.0,
                    last_agent_message="done",
                    last_agent_tail="...done",
                ),
                NativeResumeSession(
                    agent="codex",
                    agent_prefix="cx",
                    native_session_id="cx_enabled",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=100.0,
                    last_agent_message="done",
                    last_agent_tail="...done",
                ),
            ]
        )

        ctx = MessageContext(
            user_id="U1",
            channel_id="CCHAN",
            thread_id="TH1",
            message_id="TS1",
            platform="slack",
            platform_specific={"trigger_id": "TRIG"},
        )

        await ctrl.command_handler.handle_resume(ctx)

        self.assertEqual(len(im_client.resume_calls), 1)
        _, sessions, _, _, _, _ = im_client.resume_calls[0]
        self.assertEqual([item.native_session_id for item in sessions], ["cx_enabled"])

    async def test_command_handlers_handle_resume_without_trigger_sends_menu_prompt(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="slack"))
        ctrl.command_handler.handle_start = AsyncMock()

        ctx = MessageContext(
            user_id="U1",
            channel_id="CCHAN",
            thread_id="TH1",
            message_id="TS1",
            platform="slack",
            platform_specific={},
        )

        await ctrl.command_handler.handle_resume(ctx)

        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("menu message", im_client.messages[0][2])
        self.assertEqual(ctrl.native_session_service.calls, [])
        ctrl.command_handler.handle_start.assert_awaited_once()

    async def test_command_handlers_handle_resume_telegram_uses_native_sessions(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="telegram"))
        ctrl.native_session_service = _StubNativeSessionService(
            [
                NativeResumeSession(
                    agent="codex",
                    agent_prefix="cx",
                    native_session_id="session_telegram_123",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=100.0,
                    last_agent_message="done",
                    last_agent_tail="...done",
                )
            ]
        )

        ctx = MessageContext(
            user_id="U1",
            channel_id="TGCHAN",
            thread_id="TOPIC1",
            message_id="MSG1",
            platform="telegram",
            platform_specific={"is_dm": False},
        )

        await ctrl.command_handler.handle_resume(ctx)

        self.assertEqual(im_client.messages, [])
        self.assertEqual(len(im_client.resume_calls), 1)
        trigger_id, sessions, channel_id, thread_id, host_ts, working_path = im_client.resume_calls[0]
        self.assertEqual(trigger_id, ctx)
        self.assertEqual((channel_id, thread_id, host_ts), ("TGCHAN", "TOPIC1", "MSG1"))
        self.assertEqual(working_path, "/Users/cyh/vibe-remote")
        self.assertEqual([item.native_session_id for item in sessions], ["session_telegram_123"])
        self.assertEqual(ctrl.native_session_service.calls, [("/Users/cyh/vibe-remote", 25)])

    async def test_command_handlers_handle_resume_wechat_lists_recent_sessions(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="wechat"))
        ctrl.config.language = "zh"
        ctrl.native_session_service = _StubNativeSessionService(
            [
                NativeResumeSession(
                    agent="claude",
                    agent_prefix="cc",
                    native_session_id="claude-1",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=datetime(2026, 3, 27, 14, 32),
                    sort_ts=10.0,
                    last_agent_message="",
                    last_agent_tail="...修好了 Claude fallback 列表",
                ),
                NativeResumeSession(
                    agent="codex",
                    agent_prefix="cx",
                    native_session_id="codex-1",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=datetime(2026, 3, 27, 14, 10),
                    sort_ts=9.0,
                    last_agent_message="",
                    last_agent_tail="...继续在子区里回复这条消息",
                ),
            ]
        )

        ctx = MessageContext(
            user_id="wx-user",
            channel_id="wx-chat",
            platform="wechat",
            platform_specific={"is_dm": True, "platform": "wechat"},
        )

        await ctrl.command_handler.handle_resume(ctx)

        self.assertEqual(len(im_client.messages), 1)
        text = im_client.messages[0][2]
        self.assertIn("当前工作目录下最近的 Agent 会话", text)
        self.assertIn("1. cc ...修好了 Claude fallback 列表", text)
        self.assertIn("2. cx ...继续在子区里回复这条消息", text)
        self.assertIn("/resume 1 - 恢复当前列表中的第 1 条", text)
        self.assertIn("/resume more - 查看下一页", text)

    async def test_command_handlers_handle_resume_wechat_numeric_selection_uses_snapshot(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="wechat"))
        ctrl.native_session_service = _StubNativeSessionService(
            [
                NativeResumeSession(
                    agent="opencode",
                    agent_prefix="oc",
                    native_session_id="oc-1",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=10.0,
                    last_agent_message="",
                    last_agent_tail="...第一条",
                ),
                NativeResumeSession(
                    agent="claude",
                    agent_prefix="cc",
                    native_session_id="cc-2",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=9.0,
                    last_agent_message="",
                    last_agent_tail="...第二条",
                ),
            ]
        )
        ctrl.session_handler.handle_resume_session_submission = AsyncMock()
        ctx = MessageContext(
            user_id="wx-user",
            channel_id="wx-chat",
            platform="wechat",
            message_id="MSG1",
            platform_specific={"is_dm": True, "platform": "wechat"},
        )

        await ctrl.command_handler.handle_resume(ctx)
        await ctrl.command_handler.handle_resume(ctx, "1")

        ctrl.session_handler.handle_resume_session_submission.assert_awaited_once_with(
            user_id="wx-user",
            channel_id="wx-chat",
            thread_id=None,
            agent="claude",
            session_id="cc-2",
            host_message_ts="MSG1",
            is_dm=True,
            platform="wechat",
        )

    async def test_command_handlers_handle_resume_wechat_manual_backend_session_id(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="wechat"))
        ctrl.session_handler.handle_resume_session_submission = AsyncMock()
        ctx = MessageContext(
            user_id="wx-user",
            channel_id="wx-chat",
            platform="wechat",
            message_id="MSG1",
            platform_specific={"is_dm": True, "platform": "wechat"},
        )

        await ctrl.command_handler.handle_resume(ctx, "cc 59adbb74-ce14-418f-b176-28210e21b6ae")

        ctrl.session_handler.handle_resume_session_submission.assert_awaited_once_with(
            user_id="wx-user",
            channel_id="wx-chat",
            thread_id=None,
            agent="claude",
            session_id="59adbb74-ce14-418f-b176-28210e21b6ae",
            host_message_ts="MSG1",
            is_dm=True,
            platform="wechat",
        )

    async def test_command_handlers_handle_resume_wechat_latest_skips_disabled_backends(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="wechat"))
        ctrl.native_session_service = _StubNativeSessionService(
            [
                NativeResumeSession(
                    agent="opencode",
                    agent_prefix="oc",
                    native_session_id="oc_disabled",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=200.0,
                    last_agent_message="",
                    last_agent_tail="...latest disabled",
                ),
                NativeResumeSession(
                    agent="codex",
                    agent_prefix="cx",
                    native_session_id="cx_enabled",
                    working_path="/Users/cyh/vibe-remote",
                    created_at=None,
                    updated_at=None,
                    sort_ts=100.0,
                    last_agent_message="",
                    last_agent_tail="...latest enabled",
                ),
            ]
        )
        ctrl.session_handler.handle_resume_session_submission = AsyncMock()
        ctx = MessageContext(
            user_id="wx-user",
            channel_id="wx-chat",
            platform="wechat",
            message_id="MSG1",
            platform_specific={"is_dm": True, "platform": "wechat"},
        )

        await ctrl.command_handler.handle_resume(ctx, "latest")

        ctrl.session_handler.handle_resume_session_submission.assert_awaited_once_with(
            user_id="wx-user",
            channel_id="wx-chat",
            thread_id=None,
            agent="codex",
            session_id="cx_enabled",
            host_message_ts="MSG1",
            is_dm=True,
            platform="wechat",
        )

    async def test_resume_modal_manual_session_uses_manual_agent(self):
        if SlackBot is None:
            self.skipTest("Slack dependencies not installed in this environment")
        cfg = SlackConfig(bot_token="xoxb-test")
        slack = SlackBot(cfg)
        received = {}

        async def _on_resume(user_id, channel_id, thread_id, agent, session, host_ts):
            received["args"] = (user_id, channel_id, thread_id, agent, session, host_ts)

        slack._on_resume_session = _on_resume

        payload = {
            "type": "view_submission",
            "user": {"id": "U1"},
            "view": {
                "callback_id": "resume_session_modal",
                "state": {
                    "values": {
                        "agent_block": {"agent_select": {"selected_option": {"value": "codex"}}},
                        "manual_block": {"manual_input": {"value": "manual_sess"}},
                        "session_block": {"session_select": {"selected_option": {"value": "claude|sess_drop"}}},
                    }
                },
                "private_metadata": ('{"channel_id":"C1","thread_id":"TH1","host_message_ts":"TS1"}'),
            },
        }

        await slack._handle_view_submission(payload)

        self.assertEqual(
            received["args"],
            ("U1", "C1", "TH1", "codex", "manual_sess", "TS1"),
        )


if __name__ == "__main__":
    unittest.main()
