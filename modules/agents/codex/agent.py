"""Codex agent — persistent app-server mode with JSON-RPC 2.0 transport."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from modules.agents.base import AgentRequest, BaseAgent
from modules.agents.codex.attach_service import CodexAttachService
from modules.agents.subagent_router import SubagentDefinition, load_codex_subagent
from modules.agents.codex.event_handler import CodexEventHandler
from modules.agents.codex.session import CodexPendingApproval, CodexSessionManager
from modules.agents.codex.transport import CodexTransport
from modules.agents.codex.turn_state import CodexTurnRegistry
from modules.agents.question_ui import PendingQuestion, Question, QuestionOption, QuestionUIHandler

logger = logging.getLogger(__name__)


class CodexAgent(BaseAgent):
    """Codex CLI integration via persistent ``codex app-server`` subprocess.

    One transport (subprocess) is maintained per unique working directory.
    Multiple Slack threads in the same channel share a transport but each
    gets its own Codex thread.
    """

    name = "codex"

    def __init__(self, controller: Any, codex_config: Any) -> None:
        super().__init__(controller)
        self.codex_config = codex_config

        # cwd → CodexTransport (one persistent process per working dir)
        self._transports: Dict[str, CodexTransport] = {}
        self._transport_locks: Dict[str, asyncio.Lock] = {}
        self._transport_last_activity: Dict[str, float] = {}

        self._session_mgr = CodexSessionManager()
        self._turn_registry = CodexTurnRegistry()
        self._event_handler = CodexEventHandler(self)
        self._attach_service = CodexAttachService(self._get_or_create_transport)
        self._approval_ui = QuestionUIHandler(
            controller=controller,
            im_client=self.im_client,
            settings_manager=self.settings_manager,
            callback_prefix="codex_approval",
        )

        # base_session_id → asyncio.Lock (serialize turn lifecycle per session)
        self._session_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def handle_message(self, request: AgentRequest) -> None:
        """Process a user message by routing it through app-server.

        Flow:
        1. Get or create transport for the working directory
        2. Get or create a Codex thread for this Slack thread
        3. If a turn is active → interrupt it first
        4. Start a new turn with the user's message
        """
        if str(getattr(request, "message", "") or "").startswith("codex_approval:"):
            await self._handle_approval_response(request)
            return

        try:
            transport = await self._get_or_create_transport(request.working_path)
        except FileNotFoundError:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "❌ Codex CLI not found. Please install it or set CODEX_CLI_PATH.",
            )
            await self._remove_ack_reaction(request)
            return
        except Exception as e:
            logger.error("Failed to start Codex transport: %s", e, exc_info=True)
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                f"❌ Failed to start Codex CLI: {e}",
            )
            await self._remove_ack_reaction(request)
            return

        # Track session_key and cwd for scoped invalidation
        self._session_mgr.set_session_key(request.base_session_id, request.session_key)
        self._session_mgr.set_cwd(request.base_session_id, request.working_path)
        self._touch_transport_activity(request.working_path)

        await self._delete_ack(request)

        # Serialize turn lifecycle per session
        if request.base_session_id not in self._session_locks:
            self._session_locks[request.base_session_id] = asyncio.Lock()

        async with self._session_locks[request.base_session_id]:
            self._turn_registry.remember_request(request)
            try:
                # Get or create thread (with resume support)
                thread_id = self._session_mgr.get_thread_id(request.base_session_id)

                if not thread_id:
                    thread_id = await self._start_or_resume_thread(request)

                # If a turn is active, interrupt it first
                active_turn = self._turn_registry.get_active_turn(request.base_session_id)
                if active_turn:
                    try:
                        await transport.send_request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": active_turn},
                        )
                    except Exception as e:
                        logger.warning("Failed to interrupt turn %s: %s", active_turn, e)
                        await self.controller.emit_agent_message(
                            request.context,
                            "notify",
                            f"❌ Failed to interrupt previous Codex turn: {e}",
                        )
                        await self._remove_ack_reaction(request)
                        return
                    interrupted_request = self._event_handler.clear_pending(active_turn)
                    if interrupted_request:
                        await self._remove_ack_reaction(interrupted_request)

                thread_id = await self._start_turn(transport, request, thread_id)

            except Exception as e:
                # Safety net: if the thread is stale (e.g. Codex server-side
                # expiry, or the proactive invalidation in _get_or_create_transport
                # was bypassed by a race), invalidate and retry once.
                if "thread not found" in str(e).lower():
                    logger.warning(
                        "Stale Codex thread for session %s, clearing and retrying: %s",
                        request.base_session_id,
                        e,
                    )
                    self._session_mgr.invalidate_thread(request.base_session_id)
                    self._turn_registry.clear_session(request.base_session_id)
                    self.sessions.clear_agent_session_mapping(
                        request.session_key,
                        self.name,
                        request.base_session_id,
                    )
                    try:
                        thread_id = await self._start_or_resume_thread(request)
                        await self._start_turn(transport, request, thread_id)
                        return  # retry succeeded
                    except Exception as retry_err:
                        e = retry_err  # fall through to normal error handling

                self._turn_registry.clear_pending_turn_start(request.base_session_id, request)
                logger.error("Error in Codex handle_message: %s", e, exc_info=True)
                error_text = f"❌ Codex error: {e}"
                handled = await self.controller.agent_auth_service.maybe_emit_auth_recovery_message(
                    request.context,
                    "codex",
                    error_text,
                )
                if not handled:
                    await self.controller.emit_agent_message(
                        request.context,
                        "notify",
                        error_text,
                    )
                await self._remove_ack_reaction(request)

    async def handle_stop(self, request: AgentRequest) -> bool:
        """Gracefully interrupt the active turn."""
        thread_id = self._session_mgr.get_thread_id(request.base_session_id)
        turn_id = self._turn_registry.get_active_turn(request.base_session_id)

        if not thread_id or not turn_id:
            return False

        transport = self._transports.get(request.working_path)
        if not transport or not transport.is_alive:
            return False

        try:
            await transport.send_request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            )
            interrupted_request = self._event_handler.clear_pending(turn_id)
            if interrupted_request:
                await self._remove_ack_reaction(interrupted_request)
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "🛑 Terminated Codex execution.",
            )
            logger.info("Codex turn %s interrupted via /stop", turn_id)
            return True
        except Exception as e:
            logger.error("Failed to interrupt Codex turn: %s", e)
            return False

    async def clear_sessions(self, session_key: str) -> int:
        """Clear sessions scoped to a specific session_key."""
        self.sessions.clear_agent_sessions(session_key, self.name)

        # Use session_key index (not _threads) so sessions with
        # invalidated threads are still cleaned up properly.
        to_clear = self._session_mgr.get_sessions_by_session_key(session_key)

        count = self._session_mgr.clear_by_session_key(session_key)

        # Clean up in-memory turn state and session locks for cleared sessions
        for bid in to_clear:
            self._turn_registry.clear_session(bid)
            self._session_locks.pop(bid, None)
            approval_ui = getattr(self, "_approval_ui", None)
            if approval_ui is not None:
                await approval_ui.clear(bid)

        return count

    async def refresh_auth_state(self) -> None:
        """Drop app-server runtime state so future turns pick up fresh auth."""
        if not hasattr(self, "_transport_last_activity"):
            self._transport_last_activity = {}
        transports = list(self._transports.values())
        self._transports.clear()
        self._transport_last_activity.clear()

        for transport in transports:
            try:
                await transport.stop()
            except Exception as exc:
                logger.warning("Failed to stop Codex transport during auth refresh: %s", exc)

        for base_session_id in self._session_mgr.all_base_sessions():
            self._session_mgr.invalidate_thread(base_session_id)
            self._turn_registry.clear_session(base_session_id)
            approval_ui = getattr(self, "_approval_ui", None)
            if approval_ui is not None:
                await approval_ui.clear(base_session_id)

        logger.info("Refreshed Codex auth state across %d transport(s)", len(transports))

    async def prepare_resume_binding(
        self,
        *,
        base_session_id: str,
        session_key: str,
        working_path: str,
    ) -> None:
        """Restart a Codex transport only when the resumed session owns that cwd."""
        transport = self._transports.get(working_path)
        if transport is None:
            return

        affected_sessions = self._session_mgr.sessions_for_cwd(working_path)
        other_sessions = [session_id for session_id in affected_sessions if session_id != base_session_id]
        if other_sessions:
            logger.info(
                "Skipping Codex resume preparation for %s; cwd=%s is shared by %d other session(s)",
                base_session_id,
                working_path,
                len(other_sessions),
            )
            return

        try:
            await transport.stop()
        except Exception as exc:
            logger.warning("Failed to stop Codex transport during resume preparation: %s", exc)
            return

        self._transports.pop(working_path, None)
        self._transport_last_activity.pop(working_path, None)
        self._session_mgr.invalidate_thread(base_session_id)
        self._turn_registry.clear_session(base_session_id)
        logger.info("Prepared Codex runtime for resumed session %s", base_session_id)

    async def shutdown_runtime(self) -> None:
        """Stop all app-server transports during vibe-remote shutdown."""
        if not hasattr(self, "_transport_last_activity"):
            self._transport_last_activity = {}
        if not hasattr(self, "_transport_locks"):
            self._transport_locks = {}
        if not hasattr(self, "_session_locks"):
            self._session_locks = {}
        transports = list(self._transports.values())
        self._transports.clear()
        self._transport_last_activity.clear()
        self._transport_locks.clear()

        for transport in transports:
            try:
                await transport.stop()
            except Exception as exc:
                logger.warning("Failed to stop Codex transport during shutdown: %s", exc)

        for base_session_id in list(self._session_mgr.all_base_sessions()):
            session_key = self._session_mgr.get_session_key(base_session_id)
            if session_key:
                self.sessions.clear_agent_session_mapping(session_key, self.name, base_session_id)
            self._session_mgr.clear(base_session_id)
            self._turn_registry.clear_session(base_session_id)
            approval_ui = getattr(self, "_approval_ui", None)
            if approval_ui is not None:
                await approval_ui.clear(base_session_id)

        self._session_locks.clear()
        logger.info("Stopped Codex runtime across %d transport(s)", len(transports))

    async def evict_idle_transports(self, idle_timeout: float) -> int:
        """Stop idle Codex transports and invalidate stale thread mappings."""
        if idle_timeout <= 0:
            return 0
        if not hasattr(self, "_transport_last_activity"):
            self._transport_last_activity = {}
        if not hasattr(self, "_transport_locks"):
            self._transport_locks = {}
        if not hasattr(self, "_session_locks"):
            self._session_locks = {}

        now = time.monotonic()
        evicted = 0

        for cwd, last_activity in list(self._transport_last_activity.items()):
            transport = self._transports.get(cwd)
            if transport is None:
                self._transport_last_activity.pop(cwd, None)
                continue
            if self._has_active_turns_for_cwd(cwd):
                continue
            idle_for = now - last_activity
            if idle_for < idle_timeout:
                continue

            lock = self._transport_locks.setdefault(cwd, asyncio.Lock())
            async with lock:
                current_transport = self._transports.get(cwd)
                current_last_activity = self._transport_last_activity.get(cwd)
                if current_transport is None or current_transport is not transport:
                    continue
                if self._has_active_turns_for_cwd(cwd):
                    continue
                if current_last_activity is None:
                    continue
                idle_for = time.monotonic() - current_last_activity
                if idle_for < idle_timeout:
                    continue

                logger.info("Evicting idle Codex transport for cwd=%s after %.1fs idle", cwd, idle_for)
                try:
                    await transport.stop()
                except Exception as exc:
                    logger.warning("Failed to stop idle Codex transport for cwd=%s: %s", cwd, exc)
                    continue

                self._transports.pop(cwd, None)
                self._transport_last_activity.pop(cwd, None)

                for base_session_id in list(self._session_mgr.sessions_for_cwd(cwd)):
                    # Keep the persisted thread mapping so a later transport restart
                    # can resume the same Codex conversation for this Slack thread.
                    self._session_mgr.invalidate_thread(base_session_id)
                    self._turn_registry.clear_session(base_session_id)
                    self._session_locks.pop(base_session_id, None)

                evicted += 1

        return evicted

    # ------------------------------------------------------------------
    # Transport management
    # ------------------------------------------------------------------

    async def _get_or_create_transport(self, cwd: str) -> CodexTransport:
        """Return an initialized transport for the given working directory."""
        # Serialize creation per cwd
        if cwd not in self._transport_locks:
            self._transport_locks[cwd] = asyncio.Lock()

        async with self._transport_locks[cwd]:
            # Double-check after acquiring lock
            existing = self._transports.get(cwd)
            if existing and existing.is_initialized:
                self._touch_transport_activity(cwd)
                return existing

            # Stop stale transport if any
            if existing:
                await existing.stop()
                # The new app-server process won't know about threads/turns
                # from the old process.  Invalidate only sessions bound to
                # this cwd so healthy sessions on other cwds are unaffected.
                affected = self._session_mgr.sessions_for_cwd(cwd)
                for bid in affected:
                    self._session_mgr.invalidate_thread(bid)
                    self._turn_registry.clear_session(bid)
                if affected:
                    logger.info(
                        "Invalidated %d stale Codex session(s) after transport restart for cwd=%s",
                        len(affected),
                        cwd,
                    )

            transport = CodexTransport(
                binary=self.codex_config.binary,
                cwd=cwd,
                extra_args=list(self.codex_config.extra_args),
            )

            # Wire up callbacks
            transport.on_notification(self._on_notification)
            transport.on_server_request(self._on_server_request)

            await transport.start()
            self._transports[cwd] = transport
            self._touch_transport_activity(cwd)
            return transport

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    async def _start_thread(
        self,
        transport: CodexTransport,
        request: AgentRequest,
    ) -> str:
        """Create a new Codex thread and return its threadId."""
        _, _, _, agent_instructions = self._resolve_codex_agent_settings(request)

        params: Dict[str, Any] = {
            "cwd": request.working_path,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }
        platform = (
            request.context.platform
            or (request.context.platform_specific or {}).get("platform")
            or self.controller.config.platform
        )

        instruction_parts: list[str] = []
        if agent_instructions:
            instruction_parts.append(agent_instructions)

        if getattr(self.controller.config, "reply_enhancements", True):
            from core.reply_enhancer import build_reply_enhancements_prompt

            instruction_parts.append(
                build_reply_enhancements_prompt(
                    include_quick_replies=platform != "wechat",
                    context=request.context,
                    fallback_platform=platform,
                )
            )

        if instruction_parts:
            params["developerInstructions"] = "\n\n".join(part for part in instruction_parts if part)

        resp = await transport.send_request("thread/start", params)
        # thread/start returns Thread directly OR may nest under "thread"
        thread_id = resp.get("id", "")
        if not thread_id:
            thread_obj = resp.get("thread")
            if isinstance(thread_obj, dict):
                thread_id = thread_obj.get("id", "")
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no thread id")

        if hasattr(self._session_mgr, "set_lineage"):
            self._session_mgr.set_lineage(request.base_session_id, binding_origin="vibe_created", attach_mode="start")
        self._session_mgr.set_thread_id(request.base_session_id, thread_id)
        # Also persist for resume support
        self.sessions.set_agent_session_mapping(
            request.session_key,
            self.name,
            request.base_session_id,
            thread_id,
        )
        return thread_id

    def _resolve_codex_agent_settings(
        self,
        request: AgentRequest,
    ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        request_context = getattr(request, "context", None)
        controller = getattr(self, "controller", None)
        routing = None
        settings_key = request.session_key
        used_context_settings_manager = False

        if request_context is not None and controller is not None and hasattr(controller, "_get_settings_key"):
            settings_key = controller._get_settings_key(request_context)
            manager_getter = getattr(controller, "get_settings_manager_for_context", None)
            if callable(manager_getter):
                try:
                    context_settings_manager = manager_getter(request_context)
                except Exception:
                    context_settings_manager = None
                if context_settings_manager is not None:
                    used_context_settings_manager = True
                    get_channel_settings = getattr(context_settings_manager, "get_channel_settings", None)
                    channel_settings = get_channel_settings(settings_key) if callable(get_channel_settings) else None
                    routing = getattr(channel_settings, "routing", None) if channel_settings else None

        if routing is None and not used_context_settings_manager:
            channel_settings = self.settings_manager.get_channel_settings(settings_key)
            routing = getattr(channel_settings, "routing", None) if channel_settings else None

        request_subagent = getattr(request, "subagent_name", None)
        request_model = getattr(request, "subagent_model", None)
        request_effort = getattr(request, "subagent_reasoning_effort", None)

        effective_agent = request_subagent or (getattr(routing, "codex_agent", None) if routing else None)
        explicit_model = request_model or (getattr(routing, "codex_model", None) if routing else None)
        explicit_effort = request_effort or (
            getattr(routing, "codex_reasoning_effort", None) if routing else None
        )

        agent_definition: Optional[SubagentDefinition] = None
        if effective_agent:
            try:
                working_path = getattr(request, "working_path", None)
                project_root = Path(working_path) if working_path else None
                agent_definition = load_codex_subagent(effective_agent, project_root=project_root)
            except Exception as exc:
                logger.warning("Failed to load Codex subagent %s: %s", effective_agent, exc)

        effective_model = explicit_model or (agent_definition.model if agent_definition else None) or self.codex_config.default_model
        effective_effort = explicit_effort or (agent_definition.reasoning_effort if agent_definition else None)
        developer_instructions = agent_definition.developer_instructions if agent_definition else None

        return effective_agent, effective_model, effective_effort, developer_instructions

    async def _start_or_resume_thread(
        self,
        request: AgentRequest,
    ) -> str:
        """Try to resume a persisted thread, fall back to creating a new one."""
        attachment = self.sessions.get_codex_external_attachment(
            request.session_key,
            request.base_session_id,
        )
        persisted = self.sessions.get_agent_session_id(
            request.session_key,
            request.base_session_id,
            self.name,
        )

        if attachment and getattr(attachment, "binding_origin", "") == "external_attached":
            return await self._resume_external_thread(request, attachment, persisted)

        if persisted:
            try:
                resume_result = await self._attach_service.resume_thread(
                    request.working_path,
                    persisted,
                )
                thread_id = resume_result.thread_id
                if thread_id:
                    if hasattr(self._session_mgr, "set_lineage"):
                        self._session_mgr.set_lineage(
                            request.base_session_id,
                            binding_origin="vibe_created",
                            attach_mode="resume",
                        )
                    self._session_mgr.set_thread_id(request.base_session_id, thread_id)
                    logger.info("Resumed Codex thread %s for session %s", thread_id, request.base_session_id)
                    return thread_id
            except Exception as e:
                logger.warning("Failed to resume Codex thread %s: %s, starting new", persisted, e)

        transport = await self._get_or_create_transport(request.working_path)
        return await self._start_thread(transport, request)

    async def _resume_external_thread(
        self,
        request: AgentRequest,
        attachment: Any,
        persisted_thread_id: Optional[str],
    ) -> str:
        target_thread_id = str(persisted_thread_id or getattr(attachment, "codex_thread_id", "") or "")
        if not target_thread_id:
            raise RuntimeError("Codex external attachment is missing a resumable thread id")

        validation = self._attach_service.validate_workspace(
            request.working_path,
            expected_realpath=getattr(attachment, "workspace_realpath", None),
            expected_repo_root=getattr(attachment, "workspace_repo_root", None),
            expected_fingerprint=getattr(attachment, "workspace_fingerprint", None),
        )
        if not validation.is_valid:
            raise RuntimeError(f"Codex external attachment validation failed: {validation.message}")

        duplicate_binding = self._find_live_duplicate_external_attachment(
            request,
            target_thread_id,
        )

        attach_mode = str(getattr(attachment, "attach_mode", "") or "resume")
        source_thread_id = str(
            getattr(attachment, "forked_from_thread_id", None)
            or getattr(attachment, "codex_thread_id", None)
            or target_thread_id
        )

        self._session_mgr.begin_external_attach(
            request.base_session_id,
            target_thread_id,
            attach_mode=attach_mode,
            external_thread_id=source_thread_id,
            forked_from_thread_id=getattr(attachment, "forked_from_thread_id", None),
        )

        try:
            if duplicate_binding is not None:
                if attach_mode != "resume" or getattr(attachment, "forked_from_thread_id", None):
                    raise RuntimeError(
                        "Codex external thread is already attached to another live session; refusing to share writers."
                    )

                fork_result = await self._attach_service.fork_thread(request.working_path, target_thread_id)
                await self._bind_external_thread_result(
                    request,
                    result=fork_result,
                    prior_attachment=attachment,
                    attach_mode="fork",
                    source_thread_id=source_thread_id,
                    forked_from_thread_id=target_thread_id,
                )
                logger.info(
                    "Forked duplicate Codex external attach %s -> %s for session %s",
                    target_thread_id,
                    fork_result.thread_id,
                    request.base_session_id,
                )
                return fork_result.thread_id

            resume_result = await self._attach_service.resume_thread(request.working_path, target_thread_id)
            await self._bind_external_thread_result(
                request,
                result=resume_result,
                prior_attachment=attachment,
                attach_mode=attach_mode,
                source_thread_id=source_thread_id,
                forked_from_thread_id=getattr(attachment, "forked_from_thread_id", None),
            )
            logger.info(
                "Resumed Codex external thread %s for session %s",
                resume_result.thread_id,
                request.base_session_id,
            )
            return resume_result.thread_id
        except Exception:
            self._session_mgr.clear_pending_attach(request.base_session_id)
            raise

    async def _bind_external_thread_result(
        self,
        request: AgentRequest,
        *,
        result: Any,
        prior_attachment: Any,
        attach_mode: str,
        source_thread_id: str,
        forked_from_thread_id: Optional[str],
    ) -> None:
        if not result.workspace_validation.is_valid:
            raise RuntimeError(f"Codex external attachment validation failed: {result.workspace_validation.message}")

        requested_workspace = result.attach_metadata.requested_workspace
        resolved_thread_id = result.thread_id
        self._session_mgr.set_lineage(
            request.base_session_id,
            binding_origin="external_attached",
            attach_mode=attach_mode,
            external_thread_id=source_thread_id,
            forked_from_thread_id=forked_from_thread_id,
        )
        self._session_mgr.complete_external_attach(request.base_session_id, resolved_thread_id)

        self.sessions.set_agent_session_mapping(
            request.session_key,
            self.name,
            request.base_session_id,
            resolved_thread_id,
        )
        timestamp = self._utc_timestamp()
        self.sessions.upsert_codex_external_attachment(
            request.session_key,
            request.base_session_id,
            binding_origin="external_attached",
            codex_thread_id=resolved_thread_id,
            attach_mode=attach_mode,
            workspace_realpath=requested_workspace.realpath,
            workspace_repo_root=requested_workspace.repo_root,
            workspace_fingerprint=requested_workspace.workspace_fingerprint,
            forked_from_thread_id=forked_from_thread_id,
            attached_at=getattr(prior_attachment, "attached_at", None) or timestamp,
            last_validated_at=timestamp,
            validation_status=result.workspace_validation.status,
        )

    def _find_live_duplicate_external_attachment(
        self,
        request: AgentRequest,
        codex_thread_id: str,
    ) -> Optional[Any]:
        for binding in self.sessions.find_codex_external_attachments_by_thread_id(codex_thread_id):
            if binding.session_key == request.session_key and binding.base_session_id == request.base_session_id:
                continue
            if self._session_mgr.is_session_live(binding.base_session_id):
                return binding

        live_owner = self._session_mgr.find_base_session_id_for_thread(codex_thread_id)
        if live_owner and live_owner != request.base_session_id:
            return live_owner
        return None

    async def _start_turn(
        self,
        transport: CodexTransport,
        request: AgentRequest,
        thread_id: str,
    ) -> str:
        """Build input, configure overrides, and send turn/start to Codex."""
        input_items = self._build_input(request)
        _, effective_model, effective_effort, _ = self._resolve_codex_agent_settings(request)

        turn_params: Dict[str, Any] = {
            "threadId": thread_id,
            "input": input_items,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
        }
        if effective_model:
            turn_params["model"] = effective_model
        if effective_effort:
            turn_params["effort"] = effective_effort

        self._turn_registry.begin_turn_start(request, thread_id)
        resp = await transport.send_request("turn/start", turn_params)

        turn_id = resp.get("id", "")
        if not turn_id:
            turn_obj = resp.get("turn")
            if isinstance(turn_obj, dict):
                turn_id = turn_obj.get("id", "")
        if not turn_id:
            turn_id = self._turn_registry.get_bootstrapped_turn_id(request.base_session_id, request) or ""
        if not turn_id:
            raise RuntimeError("Codex turn/start returned no turn id")

        turn_state = self._turn_registry.finalize_turn_start_response(turn_id, request)
        logger.info(
            "Codex turn started: thread=%s turn=%s session=%s state=%s",
            thread_id,
            turn_id,
            request.composite_session_id,
            "registered" if turn_state else "already-finished",
        )
        return thread_id

    # ------------------------------------------------------------------
    # Input building
    # ------------------------------------------------------------------

    def _build_input(self, request: AgentRequest) -> list[Dict[str, Any]]:
        """Convert AgentRequest into Codex UserInput items."""
        items: list[Dict[str, Any]] = []

        # Text input
        message = request.message
        if request.files:
            # Append file info like Claude agent does
            file_lines = ["", "[User Attachments]"]
            for attachment in request.files:
                if not attachment.local_path:
                    continue
                is_image = (attachment.mimetype or "").startswith("image/")
                if is_image:
                    # Send as localImage input
                    items.append(
                        {
                            "type": "localImage",
                            "path": attachment.local_path,
                        }
                    )
                else:
                    size_str = f", {attachment.size} bytes" if attachment.size else ""
                    file_lines.append(f"- File: {attachment.local_path} ({attachment.mimetype}{size_str})")
            if len(file_lines) > 2:
                message = f"{message}\n" + "\n".join(file_lines)

        if message:
            items.insert(0, {"type": "text", "text": message})

        return items

    # ------------------------------------------------------------------
    # Callback handlers (wired to transport)
    # ------------------------------------------------------------------

    async def _on_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Route a server notification to the event handler."""
        request = self._find_request_for_notification(method, params)
        if not request:
            thread_id = self._extract_thread_id(params)
            turn_id = self._extract_turn_id(params)
            logger.debug(
                "No active request for Codex notification %s (thread=%s turn=%s)",
                method,
                thread_id,
                turn_id,
            )
            return

        self._touch_transport_activity(request.working_path)
        await self._event_handler.handle_notification(method, params, request)

    async def _on_server_request(
        self,
        req_id: int | str,
        method: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle server requests, surfacing resumed external approvals via IM."""
        if method in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        ):
            request = self._find_request_for_server_request(params)
            if request is None:
                logger.warning("Rejecting Codex approval with no request context: %s", params)
                return {"approved": False}

            lineage = self._session_mgr.get_lineage(request.base_session_id)
            if not lineage or lineage.binding_origin != "external_attached":
                logger.info("Auto-approving Codex %s (item=%s)", method, params.get("itemId"))
                return {"approved": True}

            return await self._handle_external_approval_request(req_id, method, params, request)

        logger.warning("Unknown Codex server request: %s", method)
        return {"approved": False}

    async def _handle_external_approval_request(
        self,
        req_id: int | str,
        method: str,
        params: Dict[str, Any],
        request: AgentRequest,
    ) -> Dict[str, Any]:
        base_session_id = request.base_session_id
        pending_approval = self._session_mgr.get_pending_approval(base_session_id)
        if pending_approval is not None:
            await self._fail_pending_approval(
                base_session_id,
                request,
                reason="Conflicting Codex approval request detected on the resumed external thread.",
            )
            return {"approved": False}

        thread_id = self._extract_thread_id(params)
        turn_id = self._extract_turn_id(params)
        item_id = str(params.get("itemId", "") or "")

        approval = CodexPendingApproval(
            request_id=req_id,
            method=method,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
        )
        pending_question = self._build_approval_question(request, method, params)
        self._session_mgr.set_pending_approval(base_session_id, approval)
        self._approval_ui.set_pending(base_session_id, pending_question)

        try:
            prompt_message_id = await self._approval_ui.render_question_ui(request, pending_question)
            pending_question.prompt_message_id = prompt_message_id
            self._session_mgr.update_pending_approval_prompt_message(base_session_id, prompt_message_id)
            if not prompt_message_id:
                await self._fail_pending_approval(
                    base_session_id,
                    request,
                    reason="Codex approval UI could not be rendered on this platform.",
                    notify=False,
                )
                return {"approved": False}

            answered = await self._approval_ui.wait_for_answer(request, pending_question)
            final_state = self._session_mgr.get_pending_approval(base_session_id)
            if not answered or final_state is None:
                return {"approved": False}
            if final_state.status not in {"approved", "denied"}:
                return {"approved": False}
            return {"approved": bool(final_state.decision)}
        finally:
            self._session_mgr.pop_pending_approval(base_session_id)
            await self._approval_ui.clear(base_session_id)

    def _build_approval_question(
        self,
        request: AgentRequest,
        method: str,
        params: Dict[str, Any],
    ) -> PendingQuestion:
        item_kind = "command" if "commandExecution" in method else "file change"
        command = str(params.get("command", "") or "").strip()
        changes = params.get("changes")
        change_count = len(changes) if isinstance(changes, list) else 0

        lines = [f"Resumed Codex thread requires approval for a pending {item_kind} request."]
        if command:
            lines.append(f"Command: `{command}`")
        if change_count:
            lines.append(f"Changed files: {change_count}")
        item_id = str(params.get("itemId", "") or "")
        if item_id:
            lines.append(f"Request ID: `{item_id}`")

        prompt_text = "\n".join(lines)
        questions = [
            Question(
                header="Codex approval required",
                question=prompt_text,
                options=[
                    QuestionOption(label="Approve", description="Allow Codex to continue this resumed thread."),
                    QuestionOption(label="Deny", description="Reject the pending approval request."),
                ],
            )
        ]
        return PendingQuestion(
            questions=questions,
            prompt_text=self._approval_ui.build_prompt_text(questions),
            option_labels=["Approve", "Deny"],
            base_session_id=request.base_session_id,
            thread_id=request.context.thread_id,
            agent_data={"kind": "codex_approval"},
        )

    async def _handle_approval_response(self, request: AgentRequest) -> None:
        pending = self._approval_ui.get_pending(request.base_session_id)
        approval = self._session_mgr.get_pending_approval(request.base_session_id)
        if pending is None or approval is None or approval.status != "pending":
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "❌ This Codex approval prompt is stale and was rejected.",
            )
            return

        if request.message == "codex_approval:open_modal":
            await self._approval_ui.open_modal(request, pending)
            return

        pending_message_id = pending.prompt_message_id or approval.prompt_message_id
        callback_message_id = getattr(request.context, "message_id", None)
        if pending_message_id and callback_message_id and pending_message_id != callback_message_id:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "❌ This Codex approval prompt is stale and was rejected.",
            )
            return

        selected_label = self._parse_approval_answer(request.message)
        if selected_label is None:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "❌ Please choose Approve or Deny for the Codex request.",
            )
            return

        approval.decision = selected_label == "Approve"
        approval.status = "approved" if approval.decision else "denied"
        await self._approval_ui.update_prompt_after_answer(request, pending)
        await self._approval_ui.send_answer_receipt(request, [[selected_label]])
        self._approval_ui.signal_answer_received(request.base_session_id)

    def _parse_approval_answer(self, message: str) -> Optional[str]:
        if message.startswith("codex_approval:choose:"):
            try:
                option_index = int(message.rsplit(":", 1)[-1])
            except Exception:
                return None
            if option_index == 1:
                return "Approve"
            if option_index == 2:
                return "Deny"
            return None

        if message.startswith("codex_approval:modal:"):
            try:
                payload = json.loads(message.split(":", 2)[-1])
            except Exception:
                return None
            answers = payload.get("answers") if isinstance(payload, dict) else payload
            if isinstance(answers, list) and answers:
                first = answers[0]
                if isinstance(first, list) and first:
                    choice = str(first[0]).strip()
                else:
                    choice = str(first).strip()
                if choice in {"Approve", "Deny"}:
                    return choice
        return None

    async def _fail_pending_approval(
        self,
        base_session_id: str,
        request: AgentRequest,
        *,
        reason: str,
        notify: bool = True,
    ) -> None:
        approval = self._session_mgr.get_pending_approval(base_session_id)
        if approval is None:
            return

        approval.decision = False
        approval.status = "conflict"
        pending = self._approval_ui.get_pending(base_session_id)
        im_client: Any = self._get_im_client(request.context)
        if pending and pending.prompt_message_id and hasattr(im_client, "remove_inline_keyboard"):
            try:
                await im_client.remove_inline_keyboard(
                    request.context,
                    pending.prompt_message_id,
                    text=pending.prompt_text,
                    parse_mode="markdown",
                )
            except Exception:
                logger.debug("Failed to remove stale Codex approval UI", exc_info=True)

        if notify:
            await self.controller.emit_agent_message(request.context, "notify", f"❌ {reason}")
        self._approval_ui.signal_answer_received(base_session_id)

    async def _expire_pending_approval_for_turn(self, turn_id: str, request: AgentRequest, *, reason: str) -> None:
        base_session_id = self._session_mgr.find_base_session_id_for_pending_approval_turn(turn_id)
        if not base_session_id:
            return
        await self._fail_pending_approval(base_session_id, request, reason=reason, notify=False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_request_for_thread(self, thread_id: str) -> Optional[AgentRequest]:
        """Look up the active AgentRequest for a given Codex threadId."""
        base_session_id = self._session_mgr.find_base_session_id_for_thread(thread_id)
        if not base_session_id:
            return None
        return self._turn_registry.get_latest_request(base_session_id)

    def _find_request_for_server_request(self, params: Dict[str, Any]) -> Optional[AgentRequest]:
        turn_id = self._extract_turn_id(params)
        if turn_id:
            request = self._turn_registry.get_request_for_turn(turn_id)
            if request:
                return request

        thread_id = self._extract_thread_id(params)
        if thread_id:
            return self._find_request_for_thread(thread_id)
        return None

    def _find_request_for_notification(self, method: str, params: Dict[str, Any]) -> Optional[AgentRequest]:
        turn_id = self._extract_turn_id(params)
        if turn_id:
            request = self._turn_registry.get_request_for_turn(turn_id)
            if request:
                return request

            thread_id = self._extract_thread_id(params)
            if not thread_id:
                return None
            if method != "turn/started":
                return None
            base_session_id = self._session_mgr.find_base_session_id_for_thread(thread_id)
            if not base_session_id:
                return None

            bootstrap_state = self._turn_registry.bootstrap_turn(turn_id, base_session_id, thread_id)
            if bootstrap_state:
                logger.info(
                    "Bootstrapped Codex turn %s for notification %s on session %s",
                    turn_id,
                    method,
                    base_session_id,
                )
                return bootstrap_state.request
            return None

        thread_id = self._extract_thread_id(params)
        if thread_id:
            return self._find_request_for_thread(thread_id)
        return None

    def _extract_thread_id(self, params: Dict[str, Any]) -> str:
        thread_id = params.get("threadId", "")
        if not thread_id:
            thread_obj = params.get("thread")
            if isinstance(thread_obj, dict):
                thread_id = thread_obj.get("id", "")
        return thread_id

    def _extract_turn_id(self, params: Dict[str, Any]) -> str:
        turn_id = params.get("turnId", "")
        if not turn_id:
            turn_obj = params.get("turn")
            if isinstance(turn_obj, dict):
                turn_id = turn_obj.get("id", "")
        return turn_id

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _delete_ack(self, request: AgentRequest) -> None:
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(request.context.channel_id, ack_id)
            except Exception as err:
                logger.debug("Could not delete ack message: %s", err)
            finally:
                request.ack_message_id = None

    def _touch_transport_activity(self, cwd: str) -> None:
        if not hasattr(self, "_transport_last_activity"):
            self._transport_last_activity = {}
        if cwd:
            self._transport_last_activity[cwd] = time.monotonic()

    def _has_active_turns_for_cwd(self, cwd: str) -> bool:
        for base_session_id in self._session_mgr.sessions_for_cwd(cwd):
            if self._turn_registry.get_active_turn(base_session_id):
                return True
            has_pending_turn_start = getattr(self._turn_registry, "has_pending_turn_start", None)
            if callable(has_pending_turn_start) and has_pending_turn_start(base_session_id):
                return True
        return False
