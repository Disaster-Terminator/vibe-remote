from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from core.handlers.session_handler import SessionHandler
from modules.agents.native_sessions.types import NativeResumeSession
from modules.im import MessageContext
from tests.scenario_harness.core import BaseScenarioHarness, ScenarioControllerBase


class _ScenarioSettingsManager:
    def __init__(self):
        self.set_calls = []
        self.mark_calls = []
        self.routing_calls = []
        self.codex_attachment_upserts = []
        self.codex_attachments = {}
        self.agent_session_mappings = {}

    def set_agent_session_mapping(self, settings_key, agent_name, thread_id, session_id):
        self.set_calls.append((settings_key, agent_name, thread_id, session_id))
        self.agent_session_mappings[(str(settings_key), str(thread_id), str(agent_name))] = session_id

    def get_agent_session_id(self, settings_key, thread_id, agent_name):
        return self.agent_session_mappings.get((str(settings_key), str(thread_id), str(agent_name)))

    def mark_thread_active(self, user_id, channel_id, thread_ts):
        self.mark_calls.append((user_id, channel_id, thread_ts))

    def get_channel_routing(self, settings_key):
        del settings_key
        return None

    def set_channel_routing(self, settings_key, routing):
        self.routing_calls.append((settings_key, routing))

    def upsert_codex_external_attachment(self, session_key, base_session_id, **kwargs):
        record = SimpleNamespace(**kwargs)
        key = (str(session_key), str(base_session_id))
        self.codex_attachments[key] = record
        self.codex_attachment_upserts.append((str(session_key), str(base_session_id), record))

    def get_codex_external_attachment(self, session_key, base_session_id):
        return self.codex_attachments.get((str(session_key), str(base_session_id)))

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


class _ScenarioIMClient:
    def __init__(self, probe):
        self.probe = probe

    async def send_message(self, context, text, parse_mode=None):
        del context, parse_mode
        self.probe.record("message", text)
        return f"msg-{len(self.probe.events)}"

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        del context, parse_mode
        self.probe.record("buttons", text, keyboard)
        return f"btn-{len(self.probe.events)}"

    def should_use_thread_for_reply(self):
        return True

    def should_use_message_id_for_channel_session(self, context=None):
        del context
        return True

    def should_use_thread_for_dm_session(self):
        return False

    async def prepare_resume_context(self, context, host_message_ts=None, is_dm=False):
        del host_message_ts, is_dm
        return context


class _ScenarioNativeSessionService:
    def __init__(self, sessions=None):
        self.sessions = sessions or []
        self.list_calls = []
        self.get_calls = []

    def list_recent_sessions(self, working_path: str, limit: int = 100):
        self.list_calls.append((working_path, limit))
        return list(self.sessions)

    def get_session(self, working_path: str, agent: str, native_session_id: str):
        self.get_calls.append((working_path, agent, native_session_id))
        for item in self.sessions:
            if item.agent == agent and item.native_session_id == native_session_id:
                return item
        return None


class _ScenarioController(ScenarioControllerBase):
    def __init__(self, *, native_session, attach_service, settings_manager=None):
        super().__init__(default_backend="codex", platform="slack")
        self.settings_manager = settings_manager or _ScenarioSettingsManager()
        self.sessions = self.settings_manager
        self.im_client = _ScenarioIMClient(self.im_probe)
        self.session_manager = None
        self.claude_sessions = {}
        self.receiver_tasks = {}
        self.stored_session_mappings = {}
        self.native_session_service = _ScenarioNativeSessionService([native_session])
        self.codex_agent = SimpleNamespace(
            prepare_resume_binding=AsyncMock(),
            _attach_service=attach_service,
        )
        self.agent_service = SimpleNamespace(agents={"codex": self.codex_agent})
        self.session_handler = SessionHandler(self)

    def _get_session_key(self, context: MessageContext) -> str:
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.config.platform
        return f"{platform}::{self._get_settings_key(context)}"

    def get_cwd(self, context: MessageContext) -> str:
        del context
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


def _codex_attach_session(*, thread_id: str, allowed_actions: list[str]):
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
            "validation_status": "valid",
            "workspace_match": True,
            "workspace_realpath": "/Users/cyh/vibe-remote",
            "workspace_repo_root": "/Users/cyh/vibe-remote",
            "workspace_fingerprint": "repo:/Users/cyh/vibe-remote",
            "materialized_history": True,
            "allowed_actions": list(allowed_actions),
        },
    )


class _ScenarioCodexAttachService:
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


class _NextTurnRuntime:
    def __init__(self, controller: _ScenarioController):
        self.controller = controller
        self.turn_thread_ids = []

    def run_turn(self, context: MessageContext) -> str:
        session_key = self.controller._get_session_key(context)
        base_session_id = self.controller.session_handler.get_base_session_id(context)
        mapped_thread_id = self.controller.sessions.get_agent_session_id(session_key, base_session_id, "codex")
        assert mapped_thread_id, "Expected Codex thread binding for the next turn"
        self.turn_thread_ids.append(mapped_thread_id)
        return mapped_thread_id


class CodexAttachScenarioHarness(BaseScenarioHarness):
    def __init__(
        self,
        *,
        candidate_thread_id: str,
        requested_action: str,
        resolved_thread_id: str,
        seed_duplicate_binding: bool = False,
    ):
        self.requested_action = requested_action
        self.candidate_thread_id = candidate_thread_id
        self.resolved_thread_id = resolved_thread_id
        settings = _ScenarioSettingsManager()
        if seed_duplicate_binding:
            settings.upsert_codex_external_attachment(
                "slack::OTHER",
                "slack_existing",
                binding_origin="external_attached",
                codex_thread_id=candidate_thread_id,
                attach_mode="resume",
                workspace_realpath="/Users/cyh/vibe-remote",
                workspace_repo_root="/Users/cyh/vibe-remote",
                workspace_fingerprint="repo:/Users/cyh/vibe-remote",
                forked_from_thread_id=None,
                attached_at="2026-04-19T12:00:00Z",
                last_validated_at="2026-04-19T12:00:00Z",
                validation_status="valid",
            )

        native_session = _codex_attach_session(thread_id=candidate_thread_id, allowed_actions=["resume", "fork"])
        attach_service = _ScenarioCodexAttachService(
            validation=_codex_validation(),
            resume_result=_codex_attach_result(thread_id=candidate_thread_id, attach_mode="resume"),
            fork_result=_codex_attach_result(
                thread_id=resolved_thread_id,
                attach_mode="fork",
                source_thread_id=candidate_thread_id,
            ),
        )
        controller: _ScenarioController = _ScenarioController(
            native_session=native_session,
            attach_service=attach_service,
            settings_manager=settings,
        )
        super().__init__(controller, user_id="U123", channel_id="C111")
        self.controller = cast(_ScenarioController, self.controller)
        self.context.platform = "slack"
        self.context.thread_id = "169999.123"
        self.service = self.controller.session_handler
        self.attach_service = attach_service
        self.runtime = _NextTurnRuntime(self.controller)
        self.discovered_sessions = []
        self.inspected_session = None
        self.next_turn_thread_id = None

    def discover_candidates(self):
        controller = cast(_ScenarioController, self.controller)
        self.discovered_sessions = controller.native_session_service.list_recent_sessions(
            self.controller.get_cwd(self.context),
            limit=100,
        )
        return self.discovered_sessions

    def inspect_candidate(self):
        controller = cast(_ScenarioController, self.controller)
        self.inspected_session = controller.native_session_service.get_session(
            self.controller.get_cwd(self.context),
            "codex",
            self.candidate_thread_id,
        )
        return self.inspected_session

    async def choose_attach_action(self):
        await self.service.handle_resume_session_submission(
            user_id=self.context.user_id,
            channel_id=self.context.channel_id,
            thread_id=self.context.thread_id,
            agent="codex",
            session_id=self.candidate_thread_id,
            action_intent=self.requested_action,
            codex_thread_id=self.candidate_thread_id,
        )

    def send_next_turn(self):
        self.next_turn_thread_id = self.runtime.run_turn(self.context)
        return self.next_turn_thread_id

    def attachment_record(self):
        controller = cast(_ScenarioController, self.controller)
        session_key = controller._get_session_key(self.context)
        base_session_id = controller.session_handler.get_base_session_id(self.context)
        return controller.sessions.get_codex_external_attachment(session_key, base_session_id)
