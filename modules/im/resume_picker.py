from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from modules.agents.native_sessions.display import format_display_summary, format_display_time
from modules.agents.native_sessions.types import NativeResumeSession


_CODEX_ATTACH_ACTION_ORDER = ("resume", "fork")
_CODEX_ATTACH_LOCATOR_KEYS = (
    "allowed_actions",
    "validation_status",
    "workspace_match",
    "materialized_history_available",
    "materialized_history",
    "codex_thread_id",
    "thread_id",
    "title",
    "preview",
    "last_activity",
    "last_activity_ts",
)


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


def get_codex_attach_validation_reason_key(status: str) -> str:
    normalized = str(status or "").strip() or "unknown"
    known_statuses = {
        "valid",
        "unverifiable",
        "cwd_mismatch_same_repo",
        "realpath_mismatch",
        "repo_root_mismatch",
        "fingerprint_mismatch",
        "attach_unavailable",
        "unknown",
    }
    if normalized not in known_statuses:
        normalized = "unknown"
    return f"error.codexAttachValidationReason.{normalized}"


def format_codex_attach_validation_reason(status: str, t: Callable[[str], str]) -> str:
    return t(get_codex_attach_validation_reason_key(status))


def _format_codex_attach_action_label(action: str, t: Callable[[str], str]) -> str:
    if action == "resume":
        return t("common.resume")
    if action == "fork":
        return t("common.fork")
    if action == "inspect_only":
        return t("modal.resume.codexActionInspectOnly")
    return action


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
    payload = _resolve_codex_attach_payload(item)
    if payload is None:
        return None

    locator = item.locator if isinstance(item.locator, dict) else {}
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
        title=str(payload.get("title") or locator.get("title") or codex_thread_id).strip(),
        preview=str(payload.get("preview") or item.last_agent_message or item.last_agent_tail or "").strip(),
        validation_status=str(payload.get("validation_status") or "unknown").strip() or "unknown",
        workspace_match=workspace_match,
        allowed_actions=allowed_actions,
        actionable_actions=actionable_actions,
        submission_payloads=submission_payloads,
        inspect_first=bool(payload.get("inspect_first", True)),
    )


def _resolve_codex_attach_payload(item: NativeResumeSession) -> Optional[dict[str, Any]]:
    if item.agent != "codex":
        return None

    locator = item.locator if isinstance(item.locator, dict) else {}
    attach_payload = locator.get("codex_attach")
    payload = dict(attach_payload) if isinstance(attach_payload, dict) else {}
    for key in _CODEX_ATTACH_LOCATOR_KEYS:
        if key not in payload and key in locator:
            payload[key] = locator[key]

    raw_submission_payloads = payload.get("submission_payloads")
    allowed_actions = _normalize_codex_attach_actions(payload.get("allowed_actions"))
    if not allowed_actions and isinstance(raw_submission_payloads, dict):
        allowed_actions = tuple(
            action for action in _CODEX_ATTACH_ACTION_ORDER if isinstance(raw_submission_payloads.get(action), dict)
        )

    if not payload and not allowed_actions:
        return None

    codex_thread_id = str(
        payload.get("codex_thread_id")
        or payload.get("thread_id")
        or item.native_session_id
    ).strip()
    if not codex_thread_id:
        return None

    payload["codex_thread_id"] = codex_thread_id
    payload["allowed_actions"] = allowed_actions
    payload["inspect_first"] = bool(payload.get("inspect_first", True))

    submission_payloads: dict[str, dict[str, str]] = {}
    for action in _CODEX_ATTACH_ACTION_ORDER:
        normalized_payload: dict[str, str] = {}
        if isinstance(raw_submission_payloads, dict):
            raw_payload = raw_submission_payloads.get(action)
            if isinstance(raw_payload, dict):
                normalized_payload = {str(key): str(value) for key, value in raw_payload.items() if value is not None}

        if not normalized_payload and action in allowed_actions:
            normalized_payload = {
                "agent": item.agent,
                "session_id": item.native_session_id,
                "codex_thread_id": codex_thread_id,
                "action_intent": action,
            }
        elif normalized_payload:
            normalized_payload.setdefault("agent", item.agent)
            normalized_payload.setdefault("session_id", item.native_session_id)
            normalized_payload.setdefault("codex_thread_id", codex_thread_id)
            normalized_payload.setdefault("action_intent", action)

        if normalized_payload:
            submission_payloads[action] = normalized_payload

    payload["submission_payloads"] = submission_payloads
    return payload


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


def build_codex_attach_summary_lines(
    attach: CodexAttachPresentation,
    *,
    t: Callable[[str], str],
) -> list[str]:
    validation_bits = [format_codex_attach_validation_reason(attach.validation_status, t)]
    if attach.workspace_match is True:
        validation_bits.append(t("modal.resume.codexWorkspaceMatch"))
    elif attach.workspace_match is False:
        validation_bits.append(t("modal.resume.codexWorkspaceMismatch"))

    if attach.allowed_actions:
        allowed_actions = ", ".join(
            _format_codex_attach_action_label(action, t) for action in attach.allowed_actions
        )
    else:
        allowed_actions = t("common.none")

    lines = [
        f"{t('modal.resume.codexSummaryThreadLabel')} {attach.title or attach.codex_thread_id}",
        f"{t('modal.resume.codexSummaryValidationLabel')} {' / '.join(validation_bits)}",
        f"{t('modal.resume.codexSummaryAllowedActionsLabel')} {allowed_actions}",
    ]
    if attach.preview:
        lines.append(f"{t('modal.resume.codexSummaryPreviewLabel')} {attach.preview}")
    return lines
