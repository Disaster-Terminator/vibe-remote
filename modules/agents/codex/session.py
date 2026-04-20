"""Maps vibe-remote session keys to Codex thread/turn state."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CodexSessionLineage:
    """Runtime lineage metadata for a Codex session binding."""

    binding_origin: str = "vibe_created"
    attach_mode: str = ""
    external_thread_id: Optional[str] = None
    forked_from_thread_id: Optional[str] = None


@dataclass
class CodexPendingApproval:
    """In-flight approval state for a Codex server request."""

    request_id: int | str
    method: str
    thread_id: str = ""
    turn_id: str = ""
    item_id: str = ""
    prompt_message_id: Optional[str] = None
    status: str = "pending"
    decision: Optional[bool] = None


class CodexSessionManager:
    """Track Codex thread and turn IDs per vibe-remote base session.

    A *base_session_id* corresponds to a Slack thread (channel + thread_ts).
    Each base session maps to exactly one Codex ``threadId``.
    """

    def __init__(self) -> None:
        # base_session_id → Codex threadId
        self._threads: dict[str, str] = {}
        # base_session_id → session_key (for scoped clear)
        self._session_keys: dict[str, str] = {}
        # base_session_id → working directory (for cwd-scoped invalidation)
        self._cwds: dict[str, str] = {}
        # base_session_id → attach target threadId while thread/resume or thread/fork is in flight
        self._pending_attach_threads: dict[str, str] = {}
        # base_session_id → runtime lineage metadata
        self._lineages: dict[str, CodexSessionLineage] = {}
        # base_session_id → pending approval request
        self._pending_approvals: dict[str, CodexPendingApproval] = {}

    # -- Thread mapping ---------------------------------------------------

    def get_thread_id(self, base_session_id: str) -> Optional[str]:
        return self._threads.get(base_session_id)

    def set_thread_id(self, base_session_id: str, thread_id: str) -> None:
        self._threads[base_session_id] = thread_id
        self._pending_attach_threads.pop(base_session_id, None)
        self._lineages.setdefault(base_session_id, CodexSessionLineage())
        logger.info("Session %s → Codex thread %s", base_session_id, thread_id)

    def invalidate_thread(self, base_session_id: str) -> None:
        """Remove only the thread_id, preserving session_key and cwd metadata."""
        self._threads.pop(base_session_id, None)
        self._pending_attach_threads.pop(base_session_id, None)
        self._pending_approvals.pop(base_session_id, None)

    def begin_external_attach(
        self,
        base_session_id: str,
        requested_thread_id: str,
        *,
        attach_mode: str,
        external_thread_id: Optional[str] = None,
        forked_from_thread_id: Optional[str] = None,
    ) -> None:
        self._pending_attach_threads[base_session_id] = requested_thread_id
        self._lineages[base_session_id] = CodexSessionLineage(
            binding_origin="external_attached",
            attach_mode=attach_mode,
            external_thread_id=external_thread_id or requested_thread_id,
            forked_from_thread_id=forked_from_thread_id,
        )

    def complete_external_attach(self, base_session_id: str, resolved_thread_id: str) -> None:
        self.set_thread_id(base_session_id, resolved_thread_id)

    def clear_pending_attach(self, base_session_id: str) -> None:
        self._pending_attach_threads.pop(base_session_id, None)

    def get_pending_attach_thread_id(self, base_session_id: str) -> Optional[str]:
        return self._pending_attach_threads.get(base_session_id)

    def set_lineage(
        self,
        base_session_id: str,
        *,
        binding_origin: str,
        attach_mode: str = "",
        external_thread_id: Optional[str] = None,
        forked_from_thread_id: Optional[str] = None,
    ) -> None:
        self._lineages[base_session_id] = CodexSessionLineage(
            binding_origin=binding_origin,
            attach_mode=attach_mode,
            external_thread_id=external_thread_id,
            forked_from_thread_id=forked_from_thread_id,
        )

    def get_lineage(self, base_session_id: str) -> Optional[CodexSessionLineage]:
        return self._lineages.get(base_session_id)

    def is_session_live(self, base_session_id: str) -> bool:
        return bool(self._threads.get(base_session_id) or self._pending_attach_threads.get(base_session_id))

    def set_pending_approval(self, base_session_id: str, approval: CodexPendingApproval) -> None:
        self._pending_approvals[base_session_id] = approval

    def get_pending_approval(self, base_session_id: str) -> Optional[CodexPendingApproval]:
        return self._pending_approvals.get(base_session_id)

    def pop_pending_approval(self, base_session_id: str) -> Optional[CodexPendingApproval]:
        return self._pending_approvals.pop(base_session_id, None)

    def update_pending_approval_prompt_message(self, base_session_id: str, prompt_message_id: Optional[str]) -> None:
        approval = self._pending_approvals.get(base_session_id)
        if approval is not None:
            approval.prompt_message_id = prompt_message_id

    def find_base_session_id_for_pending_approval_turn(self, turn_id: str) -> Optional[str]:
        for base_session_id, approval in self._pending_approvals.items():
            if approval.turn_id == turn_id:
                return base_session_id
        return None

    # -- Session-key tracking ---------------------------------------------

    def set_session_key(self, base_session_id: str, session_key: str) -> None:
        self._session_keys[base_session_id] = session_key

    def get_session_key(self, base_session_id: str) -> Optional[str]:
        return self._session_keys.get(base_session_id)

    # -- Cwd tracking -----------------------------------------------------

    def set_cwd(self, base_session_id: str, cwd: str) -> None:
        self._cwds[base_session_id] = cwd

    def sessions_for_cwd(self, cwd: str) -> list[str]:
        """Return base_session_ids associated with a given working directory."""
        return [bid for bid, stored_cwd in self._cwds.items() if stored_cwd == cwd]

    def get_sessions_by_session_key(self, session_key: str) -> list[str]:
        """Return base_session_ids associated with a given session_key."""
        return [bid for bid, sk in self._session_keys.items() if sk == session_key]

    def clear_by_session_key(self, session_key: str) -> int:
        """Remove all sessions associated with a given session_key. Returns count cleared."""
        to_remove = [bid for bid, sk in self._session_keys.items() if sk == session_key]
        for bid in to_remove:
            self._threads.pop(bid, None)
            self._session_keys.pop(bid, None)
            self._cwds.pop(bid, None)
            self._pending_attach_threads.pop(bid, None)
            self._lineages.pop(bid, None)
            self._pending_approvals.pop(bid, None)
        return len(to_remove)

    # -- Cleanup ----------------------------------------------------------

    def clear(self, base_session_id: str) -> None:
        """Remove all state for a session."""
        self._threads.pop(base_session_id, None)
        self._session_keys.pop(base_session_id, None)
        self._cwds.pop(base_session_id, None)
        self._pending_attach_threads.pop(base_session_id, None)
        self._lineages.pop(base_session_id, None)
        self._pending_approvals.pop(base_session_id, None)

    def clear_all(self) -> int:
        """Remove all tracked sessions. Returns count cleared."""
        count = len(
            set(self._threads)
            | set(self._session_keys)
            | set(self._cwds)
            | set(self._pending_attach_threads)
            | set(self._lineages)
            | set(self._pending_approvals)
        )
        self._threads.clear()
        self._session_keys.clear()
        self._cwds.clear()
        self._pending_attach_threads.clear()
        self._lineages.clear()
        self._pending_approvals.clear()
        return count

    def all_thread_ids(self) -> list[str]:
        """Return all known Codex thread IDs (for archiving on shutdown)."""
        return list(self._threads.values())

    def all_base_sessions(self) -> list[str]:
        """Return all base session IDs being tracked."""
        return list(
            set(self._threads)
            | set(self._session_keys)
            | set(self._cwds)
            | set(self._pending_attach_threads)
            | set(self._lineages)
            | set(self._pending_approvals)
        )

    def find_base_session_id_for_thread(self, thread_id: str) -> Optional[str]:
        for base_session_id, stored_thread_id in self._threads.items():
            if stored_thread_id == thread_id:
                return base_session_id
        for base_session_id, pending_thread_id in self._pending_attach_threads.items():
            if pending_thread_id == thread_id:
                return base_session_id
        return None
