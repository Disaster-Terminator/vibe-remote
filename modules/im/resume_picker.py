from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from modules.agents.native_sessions.display import format_display_summary, format_display_time
from modules.agents.native_sessions.types import NativeResumeSession


_CODEX_ATTACH_ACTION_ORDER = ("resume", "fork")


def _normalize_codex_attach_actions(raw_actions: Any) -> tuple[str, ...]:
    if isinstance(raw_actions, str):
        candidates = [raw_actions]
    elif isinstance(raw_actions, (list, tuple, set)):
        candidates = list(raw_actions)
    else:
        return ()

    actions: list[str] = []
    for value in candidates:
        action = str(value or "").strip().lower()
        if action in {"resume", "fork", "inspect_only"} and action not in actions:
            actions.append(action)
    return tuple(actions)


def build_resume_selection_value(agent: str, native_session_id: str) -> str:
    return f"{agent}|{native_session_id}"


def parse_resume_selection_value(value: str) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(value, str) or "|" not in value:
        return None, None
    agent, session_id = value.split("|", 1)
    return agent or None, session_id or None


@dataclass(frozen=True)
class CodexAttachPresentation:
    codex_thread_id: str
    title: str
    preview: str
    validation_status: str
    workspace_match: Optional[bool]
    allowed_actions: tuple[str, ...]
    actionable_actions: tuple[str, ...]
    submission_payloads: dict[str, dict[str, str]]
    inspect_first: bool

    @property
    def is_actionable(self) -> bool:
        return bool(self.actionable_actions)


@dataclass(frozen=True)
class ResumePickerEntry:
    item: NativeResumeSession
    selection_value: str
    label: str
    description: str
    codex_attach: Optional[CodexAttachPresentation] = None


def build_codex_attach_presentation(item: NativeResumeSession) -> Optional[CodexAttachPresentation]:
    locator = item.locator if isinstance(item.locator, dict) else {}
    attach_payload = locator.get("codex_attach")
    if item.agent != "codex" or not isinstance(attach_payload, dict):
        return None

    payload = dict(attach_payload)
    codex_thread_id = str(
        payload.get("codex_thread_id")
        or payload.get("thread_id")
        or item.native_session_id
    ).strip()
    allowed_actions = _normalize_codex_attach_actions(payload.get("allowed_actions"))
    raw_submission_payloads = payload.get("submission_payloads")
    submission_payloads: dict[str, dict[str, str]] = {}
    if isinstance(raw_submission_payloads, dict):
        for action in _CODEX_ATTACH_ACTION_ORDER:
            raw_payload = raw_submission_payloads.get(action)
            if not isinstance(raw_payload, dict):
                continue
            normalized_payload = {str(key): str(value) for key, value in raw_payload.items() if value is not None}
            if normalized_payload:
                submission_payloads[action] = normalized_payload
    actionable_actions = tuple(action for action in _CODEX_ATTACH_ACTION_ORDER if action in submission_payloads)

    workspace_match = payload.get("workspace_match")
    if not isinstance(workspace_match, bool):
        workspace_match = None

    return CodexAttachPresentation(
        codex_thread_id=codex_thread_id,
        title=str(payload.get("title") or locator.get("title") or "Codex thread").strip(),
        preview=str(payload.get("preview") or item.last_agent_message or item.last_agent_tail or "").strip(),
        validation_status=str(payload.get("validation_status") or "unknown").strip() or "unknown",
        workspace_match=workspace_match,
        allowed_actions=allowed_actions,
        actionable_actions=actionable_actions,
        submission_payloads=submission_payloads,
        inspect_first=bool(payload.get("inspect_first", True)),
    )


def build_resume_picker_entries(sessions: list[NativeResumeSession]) -> list[ResumePickerEntry]:
    entries: list[ResumePickerEntry] = []
    for item in sessions:
        entries.append(
            ResumePickerEntry(
                item=item,
                selection_value=build_resume_selection_value(item.agent, item.native_session_id),
                label=format_display_summary(item),
                description=format_display_time(item),
                codex_attach=build_codex_attach_presentation(item),
            )
        )
    return entries


def index_resume_picker_entries(entries: list[ResumePickerEntry]) -> dict[str, ResumePickerEntry]:
    return {entry.selection_value: entry for entry in entries}


def build_codex_attach_summary_lines(attach: CodexAttachPresentation) -> list[str]:
    validation_bits = [attach.validation_status]
    if attach.workspace_match is True:
        validation_bits.append("workspace match")
    elif attach.workspace_match is False:
        validation_bits.append("workspace mismatch")

    if attach.allowed_actions:
        allowed_actions = ", ".join(
            "Inspect only" if action == "inspect_only" else action.capitalize()
            for action in attach.allowed_actions
        )
    else:
        allowed_actions = "None"

    lines = [
        f"Thread: {attach.title or attach.codex_thread_id}",
        f"Validation: {' / '.join(validation_bits)}",
        f"Allowed actions: {allowed_actions}",
    ]
    if attach.preview:
        lines.append(f"Preview: {attach.preview}")
    return lines
