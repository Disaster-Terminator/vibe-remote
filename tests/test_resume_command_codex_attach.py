import unittest
from unittest.mock import AsyncMock

from core.handlers.command_handlers import CommandHandlers
from modules.agents.native_sessions.types import NativeResumeSession
from modules.im import MessageContext


class _StubSettingsManager:
    pass


class _StubIMClient:
    def __init__(self):
        self.messages = []
        self.resume_calls = []

    async def send_message(self, context, text, parse_mode=None):
        ts = f"T{len(self.messages) + 1}"
        self.messages.append((context.channel_id, context.thread_id, text, ts))
        return ts

    async def open_resume_session_modal(self, trigger_id, sessions, channel_id, thread_id, host_message_ts, working_path=None):
        self.resume_calls.append((trigger_id, sessions, channel_id, thread_id, host_message_ts, working_path))

    async def run_on_client_loop(self, coro):
        return await coro


class _StubNativeSessionService:
    def __init__(self, sessions=None):
        self.sessions = sessions or []
        self.calls = []

    def list_recent_sessions(self, working_path: str, limit: int = 100):
        self.calls.append((working_path, limit))
        return list(self.sessions)


class _StubSessionHandler:
    def __init__(self):
        self.handle_resume_session_submission = AsyncMock()


class _StubConfig:
    def __init__(self, platform="slack"):
        self.platform = platform
        self.language = "en"


class _StubController:
    def __init__(self, *, platform: str, sessions: list[NativeResumeSession]):
        self.config = _StubConfig(platform=platform)
        self.im_client = _StubIMClient()
        self.settings_manager = _StubSettingsManager()
        self.sessions = self.settings_manager
        self.session_manager = None
        self.native_session_service = _StubNativeSessionService(sessions)
        self.agent_service = type("AgentService", (), {"agents": {"claude": object(), "codex": object(), "opencode": object()}})()
        self.session_handler = _StubSessionHandler()
        self.command_handler = CommandHandlers(self)

    def _get_settings_key(self, context: MessageContext) -> str:
        return context.user_id if (context.platform_specific or {}).get("is_dm") else context.channel_id

    def _get_session_key(self, context: MessageContext) -> str:
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.config.platform
        return f"{platform}::{self._get_settings_key(context)}"

    def get_cwd(self, context: MessageContext) -> str:
        return "/Users/cyh/vibe-remote"


def _codex_attach_session() -> NativeResumeSession:
    return NativeResumeSession(
        agent="codex",
        agent_prefix="cx",
        native_session_id="thread_attach_123",
        working_path="/Users/cyh/vibe-remote",
        created_at=None,
        updated_at=None,
        sort_ts=100.0,
        last_agent_message="Inspect this external Codex thread before binding.",
        last_agent_tail="...inspect before binding",
        locator={
            "title": "External Codex Thread",
            "codex_thread_id": "thread_attach_123",
            "validation_status": "valid",
            "workspace_match": True,
            "materialized_history_available": True,
            "allowed_actions": ["resume", "fork"],
        },
    )


def _claude_session() -> NativeResumeSession:
    return NativeResumeSession(
        agent="claude",
        agent_prefix="cc",
        native_session_id="claude_sess_456",
        working_path="/Users/cyh/vibe-remote",
        created_at=None,
        updated_at=None,
        sort_ts=90.0,
        last_agent_message="Regular Claude resume entry.",
        last_agent_tail="...claude unchanged",
        locator={"title": "Claude Session"},
    )


class ResumeCommandCodexAttachTests(unittest.IsolatedAsyncioTestCase):
    def _context_for_platform(self, platform: str) -> MessageContext:
        if platform == "slack":
            return MessageContext(
                user_id="U1",
                channel_id="C1",
                thread_id="TH1",
                message_id="TS1",
                platform="slack",
                platform_specific={"trigger_id": "TRIG"},
            )
        if platform == "discord":
            return MessageContext(
                user_id="U1",
                channel_id="C1",
                thread_id="TH1",
                message_id="TS1",
                platform="discord",
                platform_specific={"interaction": object()},
            )
        if platform == "telegram":
            return MessageContext(
                user_id="U1",
                channel_id="TG1",
                thread_id="TOPIC1",
                message_id="MSG1",
                platform="telegram",
                platform_specific={"is_dm": False},
            )
        if platform == "lark":
            return MessageContext(
                user_id="U1",
                channel_id="LK1",
                thread_id="OT1",
                message_id="MSG1",
                platform="lark",
                platform_specific={"is_dm": False},
            )
        raise AssertionError(f"Unsupported platform {platform}")

    def _assert_codex_attach_payload(self, item: NativeResumeSession) -> None:
        attach = item.locator.get("codex_attach")
        assert isinstance(attach, dict)
        assert attach["inspect_first"] is True
        assert attach["validation_status"] == "valid"
        assert attach["allowed_actions"] == ["resume", "fork"]
        assert attach["inspect_payload"] == {
            "agent": "codex",
            "session_id": "thread_attach_123",
            "codex_thread_id": "thread_attach_123",
        }
        assert attach["submission_payloads"]["resume"]["action_intent"] == "resume"
        assert attach["submission_payloads"]["fork"]["action_intent"] == "fork"

    async def test_resume_lists_codex_attach_actions_without_affecting_other_backends(self):
        for platform in ("slack", "discord", "telegram", "lark"):
            with self.subTest(platform=platform):
                controller = _StubController(platform=platform, sessions=[_codex_attach_session(), _claude_session()])

                await controller.command_handler.handle_resume(self._context_for_platform(platform))

                self.assertEqual(len(controller.im_client.resume_calls), 1)
                _, sessions, _, _, _, _ = controller.im_client.resume_calls[0]
                self.assertEqual([item.agent for item in sessions], ["codex", "claude"])
                self._assert_codex_attach_payload(sessions[0])
                self.assertEqual(sessions[1].locator, {"title": "Claude Session"})
                self.assertNotIn("codex_attach", sessions[1].locator)

    async def test_wechat_resume_handles_codex_attach_candidates(self):
        controller = _StubController(platform="wechat", sessions=[_codex_attach_session()])
        controller.config.language = "en"
        context = MessageContext(
            user_id="wx-user",
            channel_id="wx-chat",
            message_id="WX1",
            platform="wechat",
            platform_specific={"is_dm": True, "platform": "wechat"},
        )

        await controller.command_handler.handle_resume(context)
        await controller.command_handler.handle_resume(context, "1")

        controller.session_handler.handle_resume_session_submission.assert_not_awaited()
        self.assertEqual(len(controller.im_client.messages), 2)
        self.assertIn("interactive inspect + Resume/Fork flow", controller.im_client.messages[0][2])
        self.assertIn("interactive inspect + Resume/Fork flow", controller.im_client.messages[1][2])
        self.assertEqual(controller.im_client.resume_calls, [])

    async def test_explicit_action_payloads_are_carried_for_codex_entries(self):
        controller = _StubController(platform="slack", sessions=[_codex_attach_session()])
        context = self._context_for_platform("slack")

        await controller.command_handler.handle_resume(context)

        _, sessions, _, _, _, _ = controller.im_client.resume_calls[0]
        codex_attach = sessions[0].locator["codex_attach"]
        self.assertEqual(codex_attach["action_intents"], ["resume", "fork"])
        self.assertEqual(
            codex_attach["submission_payloads"]["resume"],
            {
                "agent": "codex",
                "session_id": "thread_attach_123",
                "codex_thread_id": "thread_attach_123",
                "action_intent": "resume",
            },
        )
        self.assertEqual(
            codex_attach["submission_payloads"]["fork"],
            {
                "agent": "codex",
                "session_id": "thread_attach_123",
                "codex_thread_id": "thread_attach_123",
                "action_intent": "fork",
            },
        )


if __name__ == "__main__":
    unittest.main()
