import unittest
import sys
import types
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from modules.agents.native_sessions.types import NativeResumeSession
from modules.im import MessageContext


def _install_slack_stubs() -> None:
    if "slack_sdk" in sys.modules:
        return

    slack_sdk = types.ModuleType("slack_sdk")
    web_module = types.ModuleType("slack_sdk.web")
    async_client_module = types.ModuleType("slack_sdk.web.async_client")
    socket_mode_module = types.ModuleType("slack_sdk.socket_mode")
    socket_mode_aiohttp_module = types.ModuleType("slack_sdk.socket_mode.aiohttp")
    socket_mode_request_module = types.ModuleType("slack_sdk.socket_mode.request")
    socket_mode_response_module = types.ModuleType("slack_sdk.socket_mode.response")
    errors_module = types.ModuleType("slack_sdk.errors")

    class _AsyncWebClient:
        pass

    class _SocketModeClient:
        pass

    class _SocketModeRequest:
        pass

    class _SocketModeResponse:
        pass

    class _SlackApiError(Exception):
        pass

    setattr(async_client_module, "AsyncWebClient", _AsyncWebClient)
    setattr(socket_mode_aiohttp_module, "SocketModeClient", _SocketModeClient)
    setattr(socket_mode_request_module, "SocketModeRequest", _SocketModeRequest)
    setattr(socket_mode_response_module, "SocketModeResponse", _SocketModeResponse)
    setattr(errors_module, "SlackApiError", _SlackApiError)

    sys.modules["slack_sdk"] = slack_sdk
    sys.modules["slack_sdk.web"] = web_module
    sys.modules["slack_sdk.web.async_client"] = async_client_module
    sys.modules["slack_sdk.socket_mode"] = socket_mode_module
    sys.modules["slack_sdk.socket_mode.aiohttp"] = socket_mode_aiohttp_module
    sys.modules["slack_sdk.socket_mode.request"] = socket_mode_request_module
    sys.modules["slack_sdk.socket_mode.response"] = socket_mode_response_module
    sys.modules["slack_sdk.errors"] = errors_module

    markdown_module = types.ModuleType("markdown_to_mrkdwn")

    class _SlackMarkdownConverter:
        pass

    setattr(markdown_module, "SlackMarkdownConverter", _SlackMarkdownConverter)
    sys.modules["markdown_to_mrkdwn"] = markdown_module


def _install_discord_stubs() -> None:
    if "discord" in sys.modules:
        return

    discord_module = types.ModuleType("discord")

    class _Intents:
        message_content = False
        guilds = False
        messages = False
        dm_messages = False
        reactions = False

        @staticmethod
        def default():
            return _Intents()

    class _Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

    class _Interaction:
        pass

    class _SelectOption:
        def __init__(self, label: str, value: str, description: str | None = None, default: bool = False):
            self.label = label
            self.value = value
            self.description = description
            self.default = default

    class _View:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.children = []

        def add_item(self, item):
            self.children.append(item)

        def clear_items(self):
            self.children = []

    class _Select:
        def __init__(self, *, placeholder: str, options, min_values: int, max_values: int):
            self.placeholder = placeholder
            self.options = options
            self.min_values = min_values
            self.max_values = max_values
            self._values = []
            self.callback = None

        @property
        def values(self):
            return list(self._values)

    class _Button:
        def __init__(self, *, label: str, style):
            self.label = label
            self.style = style
            self.callback = None

    class _Modal:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def add_item(self, item):
            del item

    class _TextInput:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.value = ""

    class _Embed:
        def __init__(self, *args, **kwargs):
            del args, kwargs

    class _File:
        def __init__(self, *args, **kwargs):
            del args, kwargs

    setattr(discord_module, "Intents", _Intents)
    setattr(discord_module, "Client", _Client)
    setattr(discord_module, "Interaction", _Interaction)
    setattr(discord_module, "SelectOption", _SelectOption)
    setattr(discord_module, "Embed", _Embed)
    setattr(discord_module, "File", _File)
    setattr(discord_module, "Message", type("Message", (), {}))
    setattr(discord_module, "Thread", type("Thread", (), {}))
    setattr(discord_module, "DMChannel", type("DMChannel", (), {}))
    setattr(discord_module, "ButtonStyle", SimpleNamespace(primary="primary", secondary="secondary"))
    setattr(discord_module, "abc", SimpleNamespace(Messageable=object, GuildChannel=object))
    setattr(
        discord_module,
        "ui",
        SimpleNamespace(
            View=_View,
            Select=_Select,
            Button=_Button,
            Modal=_Modal,
            TextInput=_TextInput,
        ),
    )

    sys.modules["discord"] = discord_module

    aiohttp_socks_module = types.ModuleType("aiohttp_socks")

    class _ProxyConnector:
        pass

    setattr(aiohttp_socks_module, "ProxyConnector", _ProxyConnector)
    sys.modules["aiohttp_socks"] = aiohttp_socks_module


_install_slack_stubs()
_install_discord_stubs()

from modules.im import discord as discord_module
from modules.im.discord import DiscordBot
from modules.im.feishu import FeishuBot
from modules.im.resume_picker import build_resume_picker_entries
from modules.im.resume_picker import build_codex_attach_summary_lines
from modules.im.slack import SlackBot
from modules.im.telegram import TelegramBot, _TelegramResumeSessionState


_TRANSLATIONS: dict[str, str] = {
    "common.resume": "Resume",
    "common.fork": "Fork",
    "common.none": "None",
    "common.back": "Back",
    "common.cancel": "Cancel",
    "modal.resume.title": "Resume Session",
    "modal.resume.description": "Select a recent session.",
    "modal.resume.pickExisting": "Pick a recent session",
    "modal.resume.selectSession": "Select a session",
    "modal.resume.pasteId": "Or paste a session ID",
    "modal.resume.pasteIdPlaceholder": "ses_123",
    "modal.resume.agentBackend": "Agent backend",
    "modal.resume.selectAgentBackend": "Select agent backend",
    "modal.resume.showingFirst100": "Showing first 100",
    "modal.resume.noSessionsFound": "No sessions found",
    "modal.resume.chooseOneOf": "Choose one.",
    "modal.resume.discordPickOrPaste": "Pick or paste.",
    "modal.resume.manualInputButton": "OR: Paste Session ID",
    "modal.resume.manualInputTitle": "Paste Session ID",
    "modal.resume.sessionIdLabel": "Session ID",
    "modal.resume.manualCaptured": "Captured.",
    "modal.resume.noRecentSessionsOption": "No recent sessions",
    "modal.resume.codexInspectTitle": "Codex thread preview",
    "modal.resume.codexInspectPrompt": "Choose Resume or Fork.",
    "modal.resume.codexActionLabel": "Codex attach action",
    "modal.resume.codexActionPlaceholder": "Choose Resume or Fork",
    "modal.resume.codexActionInspectOnly": "Inspect only",
    "modal.resume.codexUnsupportedHint": "This Codex candidate is preview-only here. Direct Resume/Fork actions are unavailable for this thread.",
    "modal.resume.codexSummaryThreadLabel": "Thread:",
    "modal.resume.codexSummaryValidationLabel": "Validation:",
    "modal.resume.codexSummaryAllowedActionsLabel": "Allowed actions:",
    "modal.resume.codexSummaryPreviewLabel": "Preview:",
    "modal.resume.codexWorkspaceMatch": "workspace match",
    "modal.resume.codexWorkspaceMismatch": "workspace mismatch",
    "error.codexAttachValidationReason.valid": "validation ok",
    "error.codexAttachValidationReason.realpath_mismatch": "validation mismatch",
    "error.codexAttachValidationReason.attach_unavailable": "catalog unavailable",
    "error.codexAttachValidationReason.unknown": "validation unknown",
    "telegram.resumeTitle": "Resume a saved session",
    "telegram.resumeBody": "Pick a saved session.",
    "telegram.resumeNoStoredSessions": "No stored sessions.",
    "error.resumeFailed": "Resume failed",
}


def _t(key: str, *args, **kwargs) -> str:
    del args, kwargs
    return _TRANSLATIONS.get(key, key)


class _SlackHarness(SlackBot):
    def _t(self, key: str, channel_id: str | None = None, **kwargs) -> str:
        del channel_id, kwargs
        return _t(key)


class _DiscordHarness(DiscordBot):
    def _t(self, key: str, channel_id: str | None = None, **kwargs) -> str:
        del channel_id, kwargs
        return _t(key)


class _TelegramHarness(TelegramBot):
    def _t(self, key: str, **kwargs) -> str:
        del kwargs
        return _t(key)


class _FeishuHarness(FeishuBot):
    def _t(self, key: str, channel_id: str | None = None, **kwargs) -> str:
        del channel_id, kwargs
        return _t(key)


def _codex_attach_session(*, allowed_actions=None, submission_payloads=None) -> NativeResumeSession:
    allowed_actions = allowed_actions or ["resume", "fork"]
    if submission_payloads is None:
        submission_payloads = {
            "resume": {
                "agent": "codex",
                "session_id": "thread_attach_123",
                "codex_thread_id": "thread_attach_123",
                "action_intent": "resume",
            },
            "fork": {
                "agent": "codex",
                "session_id": "thread_attach_123",
                "codex_thread_id": "thread_attach_123",
                "action_intent": "fork",
            },
        }
    return NativeResumeSession(
        agent="codex",
        agent_prefix="cx",
        native_session_id="thread_attach_123",
        working_path="/tmp/worktree",
        created_at=None,
        updated_at=None,
        sort_ts=10.0,
        last_agent_message="Inspect this external Codex thread before binding.",
        last_agent_tail="...inspect before binding",
        locator={
            "codex_attach": {
                "inspect_first": True,
                "codex_thread_id": "thread_attach_123",
                "title": "External Codex Thread",
                "preview": "Inspect this external Codex thread before binding.",
                "validation_status": "valid",
                "workspace_match": True,
                "allowed_actions": allowed_actions,
                "submission_payloads": submission_payloads,
            }
        },
    )


def _flattened_codex_attach_session(*, allowed_actions=None) -> NativeResumeSession:
    allowed_actions = allowed_actions or ["resume", "fork"]
    return NativeResumeSession(
        agent="codex",
        agent_prefix="cx",
        native_session_id="thread_attach_123",
        working_path="/tmp/worktree",
        created_at=None,
        updated_at=None,
        sort_ts=10.0,
        last_agent_message="Inspect this external Codex thread before binding.",
        last_agent_tail="...inspect before binding",
        locator={
            "codex_thread_id": "thread_attach_123",
            "title": "External Codex Thread",
            "preview": "Inspect this external Codex thread before binding.",
            "validation_status": "valid",
            "workspace_match": True,
            "allowed_actions": allowed_actions,
        },
    )


def _claude_session() -> NativeResumeSession:
    return NativeResumeSession(
        agent="claude",
        agent_prefix="cc",
        native_session_id="claude_session_1",
        working_path="/tmp/worktree",
        created_at=None,
        updated_at=None,
        sort_ts=8.0,
        last_agent_message="Regular Claude session.",
        last_agent_tail="...claude unchanged",
        locator={"title": "Claude Session"},
    )


class _FakeDiscordChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content, view):
        self.sent.append((content, view))


class _FakeDiscordResponse:
    def __init__(self, done=False):
        self.defer = AsyncMock()
        self.edit_message = AsyncMock()
        self.send_message = AsyncMock()
        self._done = done

    def is_done(self):
        return self._done


class _FakeDiscordFollowup:
    def __init__(self):
        self.send = AsyncMock()


class _FakeDiscordInteraction:
    def __init__(self, user_id="U1", response_done=False):
        self.user = SimpleNamespace(id=user_id)
        self.guild = None
        self.response = _FakeDiscordResponse(done=response_done)
        self.followup = _FakeDiscordFollowup()


class CodexAttachUITests(unittest.IsolatedAsyncioTestCase):
    def test_codex_attach_summary_uses_translator_instead_of_hardcoded_english(self):
        attach = build_resume_picker_entries([_codex_attach_session()])[0].codex_attach
        assert attach is not None
        lines = build_codex_attach_summary_lines(
            attach,
            t=_t,
        )

        self.assertEqual(
            lines,
            [
                "Thread: External Codex Thread",
                "Validation: validation ok / workspace match",
                "Allowed actions: Resume, Fork",
                "Preview: Inspect this external Codex thread before binding.",
            ],
        )

        mismatch_entry = build_resume_picker_entries([_codex_attach_session(allowed_actions=["inspect_only"])])[0]
        mismatch_attach = mismatch_entry.codex_attach
        assert mismatch_attach is not None
        mismatch_attach = mismatch_attach.__class__(
            codex_thread_id=mismatch_attach.codex_thread_id,
            title=mismatch_attach.title,
            preview=mismatch_attach.preview,
            validation_status="realpath_mismatch",
            workspace_match=False,
            allowed_actions=("inspect_only",),
            actionable_actions=(),
            submission_payloads={},
            inspect_first=True,
        )
        mismatch_lines = build_codex_attach_summary_lines(mismatch_attach, t=_t)
        self.assertIn("Validation: validation mismatch / workspace mismatch", mismatch_lines)
        self.assertIn("Allowed actions: Inspect only", mismatch_lines)

        no_action_attach = mismatch_attach.__class__(
            codex_thread_id=mismatch_attach.codex_thread_id,
            title=mismatch_attach.title,
            preview="",
            validation_status="attach_unavailable",
            workspace_match=None,
            allowed_actions=(),
            actionable_actions=(),
            submission_payloads={},
            inspect_first=True,
        )
        no_action_lines = build_codex_attach_summary_lines(no_action_attach, t=_t)
        self.assertIn("Validation: catalog unavailable", no_action_lines)
        self.assertIn("Allowed actions: None", no_action_lines)
        self.assertEqual(len(no_action_lines), 3)

    def test_resume_picker_entries_leave_non_codex_items_unchanged(self):
        entries = build_resume_picker_entries([_claude_session()])
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0].codex_attach)
        self.assertEqual(entries[0].item.locator, {"title": "Claude Session"})

    def test_slack_modal_renders_resume_and_fork_actions_for_codex_attach(self):
        bot = _SlackHarness.__new__(_SlackHarness)
        entry = build_resume_picker_entries([_codex_attach_session()])[0]

        view = bot._build_resume_modal_view(
            metadata={"channel_id": "C1"},
            session_options=[
                {
                    "text": {"type": "plain_text", "text": entry.label, "emoji": True},
                    "value": entry.selection_value,
                    "description": {"type": "plain_text", "text": entry.description, "emoji": True},
                }
            ],
            agent_options=[{"text": {"type": "plain_text", "text": "Codex"}, "value": "codex"}],
            show_agent=False,
            selected_session_value=entry.selection_value,
            selected_attach=entry.codex_attach,
            selected_action="fork",
        )

        action_block = next(block for block in view["blocks"] if block.get("block_id") == "action_block")
        self.assertEqual([option["value"] for option in action_block["element"]["options"]], ["resume", "fork"])
        self.assertEqual(action_block["element"]["initial_option"]["value"], "fork")

    def test_slack_modal_shows_fallback_text_for_preview_only_codex_attach(self):
        bot = _SlackHarness.__new__(_SlackHarness)
        entry = build_resume_picker_entries(
            [
                _codex_attach_session(
                    allowed_actions=["inspect_only"],
                    submission_payloads={},
                )
            ]
        )[0]

        view = bot._build_resume_modal_view(
            metadata={"channel_id": "C1"},
            session_options=[
                {
                    "text": {"type": "plain_text", "text": entry.label, "emoji": True},
                    "value": entry.selection_value,
                    "description": {"type": "plain_text", "text": entry.description, "emoji": True},
                }
            ],
            agent_options=[{"text": {"type": "plain_text", "text": "Codex"}, "value": "codex"}],
            show_agent=False,
            selected_session_value=entry.selection_value,
            selected_attach=entry.codex_attach,
        )

        self.assertFalse(any(block.get("block_id") == "action_block" for block in view["blocks"]))
        self.assertTrue(any(_TRANSLATIONS["modal.resume.codexUnsupportedHint"] in str(block) for block in view["blocks"]))

    def test_flattened_codex_locator_rebuilds_explicit_attach_payloads(self):
        entry = build_resume_picker_entries([_flattened_codex_attach_session()])[0]

        self.assertIsNotNone(entry.codex_attach)
        assert entry.codex_attach is not None
        self.assertEqual(entry.codex_attach.actionable_actions, ("resume", "fork"))
        self.assertEqual(
            entry.codex_attach.submission_payloads["resume"],
            {
                "agent": "codex",
                "session_id": "thread_attach_123",
                "codex_thread_id": "thread_attach_123",
                "action_intent": "resume",
            },
        )
        self.assertEqual(
            entry.codex_attach.submission_payloads["fork"],
            {
                "agent": "codex",
                "session_id": "thread_attach_123",
                "codex_thread_id": "thread_attach_123",
                "action_intent": "fork",
            },
        )

    async def test_slack_static_select_session_selection_updates_modal_for_rehydrated_codex_attach(self):
        bot = _SlackHarness.__new__(_SlackHarness)
        session = _flattened_codex_attach_session()
        entry = build_resume_picker_entries([session])[0]
        bot.settings_manager = None
        bot._controller = SimpleNamespace(
            native_session_service=SimpleNamespace(get_session=lambda working_path, agent, session_id: session)
        )
        web_client = SimpleNamespace(views_update=AsyncMock())
        bot.web_client = web_client

        payload = {
            "type": "block_actions",
            "user": {"id": "U1"},
            "channel": {"id": "C1"},
            "actions": [
                {
                    "type": "static_select",
                    "action_id": "session_select",
                    "selected_option": {"value": entry.selection_value},
                }
            ],
            "view": {
                "id": "VIEW1",
                "hash": "HASH1",
                "callback_id": "resume_session_modal",
                "private_metadata": json.dumps(
                    {
                        "channel_id": "C1",
                        "thread_id": "TH1",
                        "host_message_ts": "TS1",
                        "working_path": "/tmp/worktree",
                        "agent_options": [{"text": {"type": "plain_text", "text": "Codex"}, "value": "codex"}],
                    }
                ),
                "state": {
                    "values": {
                        "manual_block": {"manual_input": {"value": ""}},
                        "session_block": {"session_select": {"selected_option": {"value": entry.selection_value}}},
                    }
                },
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "session_block",
                        "element": {
                            "type": "static_select",
                            "options": [
                                {
                                    "text": {"type": "plain_text", "text": entry.label, "emoji": True},
                                    "value": entry.selection_value,
                                    "description": {
                                        "type": "plain_text",
                                        "text": entry.description,
                                        "emoji": True,
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        }

        await bot._handle_interactive(payload)

        web_client.views_update.assert_awaited_once()
        updated_view = web_client.views_update.await_args_list[0].kwargs["view"]
        action_block = next(block for block in updated_view["blocks"] if block.get("block_id") == "action_block")
        self.assertEqual([option["value"] for option in action_block["element"]["options"]], ["resume", "fork"])
        self.assertTrue(
            any(
                block.get("block_id") == "attach_preview_block"
                and _TRANSLATIONS["modal.resume.codexInspectTitle"] in block["text"]["text"]
                for block in updated_view["blocks"]
            )
        )

    async def test_slack_preview_only_rehydrated_selection_fails_closed_on_submit(self):
        bot = _SlackHarness.__new__(_SlackHarness)
        preview_only_session = _flattened_codex_attach_session(allowed_actions=["inspect_only"])
        bot._controller = SimpleNamespace(
            native_session_service=SimpleNamespace(
                get_session=lambda working_path, agent, session_id: preview_only_session
            )
        )
        bot._on_resume_session = AsyncMock()
        bot.send_message = AsyncMock()

        payload = {
            "type": "view_submission",
            "user": {"id": "U1"},
            "view": {
                "callback_id": "resume_session_modal",
                "state": {
                    "values": {
                        "agent_block": {"agent_select": {"selected_option": {"value": "codex"}}},
                        "manual_block": {"manual_input": {"value": ""}},
                        "session_block": {"session_select": {"selected_option": {"value": "codex|thread_attach_123"}}},
                    }
                },
                "private_metadata": json.dumps(
                    {
                        "channel_id": "C1",
                        "thread_id": "TH1",
                        "host_message_ts": "TS1",
                        "working_path": "/tmp/worktree",
                    }
                ),
            },
        }

        await bot._handle_view_submission(payload)

        bot._on_resume_session.assert_not_awaited()
        bot.send_message.assert_awaited_once()
        sent_text = bot.send_message.await_args_list[0].args[1]
        self.assertIn(_TRANSLATIONS["modal.resume.codexUnsupportedHint"], sent_text)

    async def test_discord_attach_flow_exposes_explicit_resume_and_fork_actions(self):
        bot = _DiscordHarness.__new__(_DiscordHarness)
        bot._controller = SimpleNamespace(agent_service=SimpleNamespace(agents={"codex": object(), "claude": object(), "opencode": object()}))
        bot._dismiss_interaction_message = AsyncMock()
        bot._on_resume_session = AsyncMock()
        channel = _FakeDiscordChannel()
        bot._fetch_channel = AsyncMock(return_value=channel)

        session = _codex_attach_session()
        await bot.open_resume_session_modal(
            trigger_id=None,
            sessions=[session],
            channel_id="C1",
            thread_id="TH1",
            host_message_ts="MSG1",
        )

        _, view = channel.sent[0]
        selection_value = build_resume_picker_entries([session])[0].selection_value
        view.session_select._values = [selection_value]
        select_interaction = _FakeDiscordInteraction()
        await view.session_select.callback(select_interaction)

        labels = [getattr(child, "label", None) for child in view.children]
        self.assertIn("Resume", labels)
        self.assertIn("Fork", labels)
        select_interaction.response.edit_message.assert_awaited()
        edit_call = select_interaction.response.edit_message.await_args_list[0]
        self.assertIn(_TRANSLATIONS["modal.resume.codexSummaryAllowedActionsLabel"], edit_call.kwargs["content"])
        self.assertIn("Resume, Fork", edit_call.kwargs["content"])

        fork_button = next(child for child in view.children if getattr(child, "label", None) == "Fork")
        await fork_button.callback(_FakeDiscordInteraction())
        bot._on_resume_session.assert_awaited_once()
        resume_call = bot._on_resume_session.await_args_list[0]
        self.assertEqual(resume_call.kwargs["action_intent"], "fork")
        self.assertEqual(resume_call.kwargs["codex_thread_id"], "thread_attach_123")

    async def test_telegram_attach_flow_requires_explicit_action_before_submit(self):
        bot = _TelegramHarness.__new__(_TelegramHarness)
        bot._resume_states = {}
        bot._interaction_scope_key = lambda context: f"{context.channel_id}:{context.user_id}"
        bot.edit_message = AsyncMock()
        bot._delete_interaction_message = AsyncMock()
        bot.send_message = AsyncMock()
        session_handler = SimpleNamespace(handle_resume_session_submission=AsyncMock())
        bot._controller = SimpleNamespace(session_handler=session_handler)

        entry = build_resume_picker_entries([_codex_attach_session()])[0]
        context = MessageContext(
            user_id="U1",
            channel_id="TG1",
            thread_id="TOPIC1",
            message_id="M1",
            platform="telegram",
            platform_specific={"is_dm": False},
        )
        state = _TelegramResumeSessionState(message_id="M1", entries=[entry], is_dm=False)
        bot._resume_states[bot._interaction_scope_key(context)] = state

        await bot._handle_resume_callback(context, "tg_resume:select:0")
        session_handler.handle_resume_session_submission.assert_not_awaited()
        self.assertEqual(state.selected_index, 0)
        edit_call = bot.edit_message.await_args_list[0]
        self.assertIn(_TRANSLATIONS["modal.resume.codexSummaryAllowedActionsLabel"], edit_call.kwargs["text"])
        self.assertIn("Resume, Fork", edit_call.kwargs["text"])

        await bot._handle_resume_callback(context, "tg_resume:action:fork")
        session_handler.handle_resume_session_submission.assert_awaited_once()
        submit_call = session_handler.handle_resume_session_submission.await_args_list[0]
        self.assertEqual(submit_call.kwargs["action_intent"], "fork")
        self.assertEqual(submit_call.kwargs["codex_thread_id"], "thread_attach_123")

    async def test_telegram_preview_only_codex_attach_stays_safe(self):
        bot = _TelegramHarness.__new__(_TelegramHarness)
        entry = build_resume_picker_entries(
            [
                _codex_attach_session(
                    allowed_actions=["inspect_only"],
                    submission_payloads={},
                )
            ]
        )[0]

        text, keyboard = bot._render_resume_state(
            _TelegramResumeSessionState(message_id="M1", entries=[entry], is_dm=False, selected_index=0)
        )

        self.assertIn(_TRANSLATIONS["modal.resume.codexUnsupportedHint"], text)
        self.assertEqual([[button.text for button in row] for row in keyboard.buttons], [["Back"], ["✖️ Cancel"]])

    async def test_feishu_attach_flow_sends_preview_then_submits_explicit_action(self):
        bot = _FeishuHarness.__new__(_FeishuHarness)
        bot._resume_picker_cache = {}
        bot._resume_attach_cache = {}
        bot.send_message = AsyncMock()
        bot.send_message_with_buttons = AsyncMock(return_value="CARD1")
        bot._on_resume_session = AsyncMock()

        entry = build_resume_picker_entries([_codex_attach_session()])[0]
        cache_key = "LK1:U1"
        bot._resume_picker_cache[cache_key] = {entry.selection_value: entry}
        context = MessageContext(
            user_id="U1",
            channel_id="LK1",
            thread_id="OT1",
            message_id="CARD0",
            platform="lark",
            platform_specific={"is_dm": False},
        )

        await bot._handle_resume_form_submit(
            context,
            {"session_select": entry.selection_value, "manual_session_id": "", "agent_select": "codex"},
            "resume_submit:OT1:HOST1",
        )

        bot._on_resume_session.assert_not_awaited()
        bot.send_message_with_buttons.assert_awaited_once()
        send_call = bot.send_message_with_buttons.await_args_list[0]
        sent_text = send_call.args[1]
        sent_keyboard = send_call.args[2]
        self.assertIn(_TRANSLATIONS["modal.resume.codexSummaryAllowedActionsLabel"], sent_text)
        self.assertIn("Resume, Fork", sent_text)
        callback_data = sent_keyboard.buttons[0][1].callback_data

        await bot._handle_resume_attach_callback(context, callback_data)
        bot._on_resume_session.assert_awaited_once()
        resume_call = bot._on_resume_session.await_args_list[0]
        self.assertEqual(resume_call.kwargs["action_intent"], "fork")
        self.assertEqual(resume_call.kwargs["codex_thread_id"], "thread_attach_123")

    async def test_feishu_resume_submit_name_stays_compact_without_working_path(self):
        bot = _FeishuHarness.__new__(_FeishuHarness)
        bot._send_card_to_channel = AsyncMock()
        sessions = [_codex_attach_session()]

        await bot.open_resume_session_modal(
            trigger_id=None,
            sessions=sessions,
            channel_id="LK1",
            thread_id="OT1",
            host_message_ts="HOST1",
            working_path="/home/raystorm/projects/some/very/long/path/that/should/not/be/encoded/into/the/form/submit/name",
        )

        send_call = bot._send_card_to_channel.await_args_list[0]
        card = send_call.args[1]
        submit_button = card["body"]["elements"][0]["elements"][-1]
        self.assertEqual(submit_button["name"], "resume_submit:OT1:HOST1")

    async def test_feishu_attach_callback_rejects_wrong_user_without_consuming_token(self):
        bot = _FeishuHarness.__new__(_FeishuHarness)
        bot._resume_attach_cache = {
            "tok123": {
                "user_id": "OWNER",
                "channel_id": "LK1",
                "thread_id": "OT1",
                "host_message_ts": "HOST1",
                "is_dm": False,
                "agent": "codex",
                "session_id": "thread_attach_123",
                "submission_payloads": {
                    "resume": {
                        "agent": "codex",
                        "session_id": "thread_attach_123",
                        "codex_thread_id": "thread_attach_123",
                        "action_intent": "resume",
                    }
                },
            }
        }
        bot.send_message = AsyncMock()
        bot._on_resume_session = AsyncMock()
        context = MessageContext(user_id="OTHER", channel_id="LK1", thread_id="OT1", platform="lark")

        await bot._handle_resume_attach_callback(context, "resume_attach:tok123:resume")

        bot._on_resume_session.assert_not_awaited()
        self.assertIn("tok123", bot._resume_attach_cache)
        bot.send_message.assert_awaited_once()

    async def test_discord_resume_view_rejects_other_user_interaction(self):
        bot = _DiscordHarness.__new__(_DiscordHarness)
        bot._controller = SimpleNamespace(agent_service=SimpleNamespace(agents={"codex": object(), "claude": object(), "opencode": object()}))
        channel = _FakeDiscordChannel()
        bot._fetch_channel = AsyncMock(return_value=channel)

        await bot.open_resume_session_modal(
            trigger_id=None,
            sessions=[_codex_attach_session()],
            channel_id="C1",
            thread_id="TH1",
            host_message_ts="MSG1",
        )

        _, view = channel.sent[0]
        view.owner_id = "OWNER"

        self.assertTrue(await view.interaction_check(_FakeDiscordInteraction(user_id="OWNER")))
        self.assertFalse(await view.interaction_check(_FakeDiscordInteraction(user_id="OTHER")))

    async def test_discord_resume_view_uses_followup_when_interaction_already_deferred(self):
        bot = _DiscordHarness.__new__(_DiscordHarness)
        bot._controller = SimpleNamespace(agent_service=SimpleNamespace(agents={"codex": object(), "claude": object(), "opencode": object()}))
        interaction = _FakeDiscordInteraction(response_done=True)

        original_interaction_cls = discord_module.discord.Interaction
        discord_module.discord.Interaction = _FakeDiscordInteraction
        try:
            await bot.open_resume_session_modal(
                trigger_id=interaction,
                sessions=[_codex_attach_session()],
                channel_id="C1",
                thread_id="TH1",
                host_message_ts="MSG1",
            )
        finally:
            discord_module.discord.Interaction = original_interaction_cls

        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()

    async def test_feishu_preview_only_codex_attach_stays_preview_only(self):
        bot = _FeishuHarness.__new__(_FeishuHarness)
        bot._resume_attach_cache = {}
        bot.send_message_with_buttons = AsyncMock(return_value="CARD1")

        entry = build_resume_picker_entries(
            [
                _codex_attach_session(
                    allowed_actions=["inspect_only"],
                    submission_payloads={},
                )
            ]
        )[0]
        context = MessageContext(user_id="U1", channel_id="LK1", thread_id="OT1", platform="lark")

        await bot._send_resume_attach_card(
            context,
            entry=entry,
            host_message_ts="HOST1",
            thread_id="OT1",
            is_dm=False,
        )

        send_call = bot.send_message_with_buttons.await_args_list[0]
        sent_text = send_call.args[1]
        sent_keyboard = send_call.args[2]
        self.assertIn(_TRANSLATIONS["modal.resume.codexUnsupportedHint"], sent_text)
        self.assertEqual([[button.text for button in row] for row in sent_keyboard.buttons], [["Cancel"]])


if __name__ == "__main__":
    unittest.main()
