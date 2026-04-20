"""Codex attach helpers over the existing app-server transport."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from modules.agents.codex.transport import CodexTransport


TransportProvider = Callable[[str], Awaitable[CodexTransport]]


@dataclass(frozen=True)
class CodexWorkspaceIdentity:
    cwd: str
    realpath: str
    repo_root: Optional[str]
    workspace_fingerprint: str


@dataclass(frozen=True)
class CodexWorkspaceValidationResult:
    requested_workspace: CodexWorkspaceIdentity
    candidate_workspace: Optional[CodexWorkspaceIdentity]
    status: str
    is_valid: bool
    message: str


@dataclass(frozen=True)
class CodexAttachMetadata:
    requested_workspace: CodexWorkspaceIdentity
    candidate_workspace: Optional[CodexWorkspaceIdentity]
    workspace_validation: CodexWorkspaceValidationResult
    attach_mode: str
    requested_thread_id: str
    resolved_thread_id: str
    forked_from_thread_id: Optional[str]


@dataclass(frozen=True)
class CodexThreadSummary:
    thread_id: str
    thread: dict[str, Any]
    workspace: Optional[CodexWorkspaceIdentity]
    workspace_validation: CodexWorkspaceValidationResult


@dataclass(frozen=True)
class CodexThreadListResult:
    threads: list[CodexThreadSummary]
    next_cursor: Optional[str]
    raw_result: dict[str, Any]


@dataclass(frozen=True)
class CodexThreadReadResult:
    thread_id: str
    thread: dict[str, Any]
    turns: list[dict[str, Any]]
    workspace: Optional[CodexWorkspaceIdentity]
    workspace_validation: CodexWorkspaceValidationResult
    attach_metadata: CodexAttachMetadata
    raw_result: dict[str, Any]


@dataclass(frozen=True)
class CodexAttachOperationResult:
    thread_id: str
    thread: dict[str, Any]
    workspace: Optional[CodexWorkspaceIdentity]
    workspace_validation: CodexWorkspaceValidationResult
    attach_metadata: CodexAttachMetadata
    raw_result: dict[str, Any]


class CodexAttachService:
    """Codex app-server thread attach primitives bound to a cwd-scoped transport."""

    def __init__(self, transport_provider: TransportProvider) -> None:
        self._transport_provider = transport_provider

    async def list_threads(
        self,
        cwd: str,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> CodexThreadListResult:
        requested_workspace = self._normalize_workspace(cwd)
        request_params = dict(params or {})
        request_params.setdefault("cwd", requested_workspace.realpath)

        transport = await self._transport_provider(cwd)
        raw_result = await transport.send_request("thread/list", request_params, timeout_seconds=timeout_seconds)

        items = raw_result.get("data")
        if not isinstance(items, list):
            items = raw_result.get("threads")
        if not isinstance(items, list):
            items = []

        summaries: list[CodexThreadSummary] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            thread_id = self._extract_thread_id(item)
            workspace = self._workspace_from_thread(item)
            validation = self.validate_workspace(
                cwd,
                candidate_cwd=workspace.cwd if workspace else None,
            )
            summaries.append(
                CodexThreadSummary(
                    thread_id=thread_id,
                    thread=item,
                    workspace=workspace,
                    workspace_validation=validation,
                )
            )

        next_cursor = raw_result.get("nextCursor")
        return CodexThreadListResult(
            threads=summaries,
            next_cursor=next_cursor if isinstance(next_cursor, str) else None,
            raw_result=raw_result,
        )

    async def read_thread(self, cwd: str, thread_id: str, *, include_turns: bool = False) -> CodexThreadReadResult:
        transport = await self._transport_provider(cwd)
        raw_result = await transport.send_request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
        )
        thread = self._extract_thread_payload(raw_result, method_name="thread/read")
        resolved_thread_id = self._extract_thread_id(thread, fallback=thread_id)
        workspace = self._workspace_from_thread(thread)
        validation = self.validate_workspace(
            cwd,
            candidate_cwd=workspace.cwd if workspace else None,
        )
        attach_metadata = self._build_attach_metadata(
            attach_mode="read",
            requested_thread_id=thread_id,
            resolved_thread_id=resolved_thread_id,
            cwd=cwd,
            workspace=workspace,
            workspace_validation=validation,
            forked_from_thread_id=None,
        )
        turns = raw_result.get("turns")
        if not isinstance(turns, list):
            turns = thread.get("turns")
        normalized_turns = [turn for turn in turns if isinstance(turn, dict)] if isinstance(turns, list) else []

        return CodexThreadReadResult(
            thread_id=resolved_thread_id,
            thread=thread,
            turns=normalized_turns,
            workspace=workspace,
            workspace_validation=validation,
            attach_metadata=attach_metadata,
            raw_result=raw_result,
        )

    async def resume_thread(
        self,
        cwd: str,
        thread_id: str,
        params: Optional[dict[str, Any]] = None,
    ) -> CodexAttachOperationResult:
        request_params = {"threadId": thread_id, **dict(params or {})}
        transport = await self._transport_provider(cwd)
        raw_result = await transport.send_request("thread/resume", request_params)
        return self._build_operation_result(
            cwd,
            raw_result,
            attach_mode="resume",
            requested_thread_id=thread_id,
            fallback_thread_id=thread_id,
            forked_from_thread_id=None,
        )

    async def fork_thread(
        self,
        cwd: str,
        thread_id: str,
        params: Optional[dict[str, Any]] = None,
    ) -> CodexAttachOperationResult:
        request_params = {"threadId": thread_id, **dict(params or {})}
        transport = await self._transport_provider(cwd)
        raw_result = await transport.send_request("thread/fork", request_params)
        return self._build_operation_result(
            cwd,
            raw_result,
            attach_mode="fork",
            requested_thread_id=thread_id,
            fallback_thread_id=thread_id,
            forked_from_thread_id=thread_id,
        )

    def validate_workspace(
        self,
        cwd: str,
        *,
        candidate_cwd: Optional[str] = None,
        expected_realpath: Optional[str] = None,
        expected_repo_root: Optional[str] = None,
        expected_fingerprint: Optional[str] = None,
    ) -> CodexWorkspaceValidationResult:
        requested_workspace = self._normalize_workspace(cwd)
        candidate_workspace = self._build_candidate_workspace(
            candidate_cwd=candidate_cwd,
            expected_realpath=expected_realpath,
            expected_repo_root=expected_repo_root,
            expected_fingerprint=expected_fingerprint,
        )

        if candidate_workspace is None:
            return CodexWorkspaceValidationResult(
                requested_workspace=requested_workspace,
                candidate_workspace=None,
                status="unverifiable",
                is_valid=False,
                message="Target workspace metadata is missing, so attach safety cannot be verified.",
            )

        if candidate_workspace.realpath and requested_workspace.realpath != candidate_workspace.realpath:
            status = (
                "cwd_mismatch_same_repo"
                if requested_workspace.workspace_fingerprint == candidate_workspace.workspace_fingerprint
                else "realpath_mismatch"
            )
            return CodexWorkspaceValidationResult(
                requested_workspace=requested_workspace,
                candidate_workspace=candidate_workspace,
                status=status,
                is_valid=False,
                message="Normalized working directory does not match the target thread workspace.",
            )

        if (requested_workspace.repo_root or candidate_workspace.repo_root) and (
            requested_workspace.repo_root != candidate_workspace.repo_root
        ):
            return CodexWorkspaceValidationResult(
                requested_workspace=requested_workspace,
                candidate_workspace=candidate_workspace,
                status="repo_root_mismatch",
                is_valid=False,
                message="Repository root does not match the target thread workspace.",
            )

        if candidate_workspace.workspace_fingerprint != requested_workspace.workspace_fingerprint:
            return CodexWorkspaceValidationResult(
                requested_workspace=requested_workspace,
                candidate_workspace=candidate_workspace,
                status="fingerprint_mismatch",
                is_valid=False,
                message="Workspace fingerprint does not match the target thread workspace.",
            )

        return CodexWorkspaceValidationResult(
            requested_workspace=requested_workspace,
            candidate_workspace=candidate_workspace,
            status="valid",
            is_valid=True,
            message="Workspace validation succeeded.",
        )

    def _build_operation_result(
        self,
        cwd: str,
        raw_result: dict[str, Any],
        *,
        attach_mode: str,
        requested_thread_id: str,
        fallback_thread_id: str,
        forked_from_thread_id: Optional[str],
    ) -> CodexAttachOperationResult:
        thread = self._extract_thread_payload(raw_result, method_name=f"thread/{attach_mode}")
        resolved_thread_id = self._extract_thread_id(thread, fallback=fallback_thread_id)
        workspace = self._workspace_from_thread(thread)
        validation = self.validate_workspace(
            cwd,
            candidate_cwd=workspace.cwd if workspace else None,
        )
        attach_metadata = self._build_attach_metadata(
            attach_mode=attach_mode,
            requested_thread_id=requested_thread_id,
            resolved_thread_id=resolved_thread_id,
            cwd=cwd,
            workspace=workspace,
            workspace_validation=validation,
            forked_from_thread_id=forked_from_thread_id,
        )
        return CodexAttachOperationResult(
            thread_id=resolved_thread_id,
            thread=thread,
            workspace=workspace,
            workspace_validation=validation,
            attach_metadata=attach_metadata,
            raw_result=raw_result,
        )

    def _build_attach_metadata(
        self,
        *,
        attach_mode: str,
        requested_thread_id: str,
        resolved_thread_id: str,
        cwd: str,
        workspace: Optional[CodexWorkspaceIdentity],
        workspace_validation: CodexWorkspaceValidationResult,
        forked_from_thread_id: Optional[str],
    ) -> CodexAttachMetadata:
        return CodexAttachMetadata(
            requested_workspace=self._normalize_workspace(cwd),
            candidate_workspace=workspace,
            workspace_validation=workspace_validation,
            attach_mode=attach_mode,
            requested_thread_id=requested_thread_id,
            resolved_thread_id=resolved_thread_id,
            forked_from_thread_id=forked_from_thread_id,
        )

    def _normalize_workspace(self, cwd: str) -> CodexWorkspaceIdentity:
        realpath = os.path.realpath(cwd)
        repo_root = self._find_repo_root(Path(realpath))
        return CodexWorkspaceIdentity(
            cwd=cwd,
            realpath=realpath,
            repo_root=repo_root,
            workspace_fingerprint=self._build_workspace_fingerprint(realpath, repo_root),
        )

    def _build_candidate_workspace(
        self,
        *,
        candidate_cwd: Optional[str],
        expected_realpath: Optional[str],
        expected_repo_root: Optional[str],
        expected_fingerprint: Optional[str],
    ) -> Optional[CodexWorkspaceIdentity]:
        if candidate_cwd:
            return self._normalize_workspace(candidate_cwd)

        if expected_realpath is None and expected_repo_root is None and expected_fingerprint is None:
            return None

        normalized_realpath = os.path.realpath(expected_realpath) if expected_realpath else ""
        normalized_repo_root = os.path.realpath(expected_repo_root) if expected_repo_root else None
        fingerprint = expected_fingerprint
        if fingerprint is None and (normalized_realpath or normalized_repo_root):
            fingerprint = self._build_workspace_fingerprint(
                normalized_realpath,
                normalized_repo_root,
            )

        if not normalized_realpath and not normalized_repo_root and not fingerprint:
            return None

        return CodexWorkspaceIdentity(
            cwd=expected_realpath or normalized_realpath,
            realpath=normalized_realpath,
            repo_root=normalized_repo_root,
            workspace_fingerprint=fingerprint or "",
        )

    def _workspace_from_thread(self, thread: dict[str, Any]) -> Optional[CodexWorkspaceIdentity]:
        workspace_cwd = thread.get("cwd")
        if not isinstance(workspace_cwd, str) or not workspace_cwd.strip():
            workspace = thread.get("workspace")
            if isinstance(workspace, dict):
                workspace_cwd = workspace.get("cwd")
        if isinstance(workspace_cwd, str) and workspace_cwd.strip():
            return self._normalize_workspace(workspace_cwd)
        return None

    @staticmethod
    def _extract_thread_payload(raw_result: dict[str, Any], *, method_name: str) -> dict[str, Any]:
        thread = raw_result.get("thread")
        if isinstance(thread, dict):
            return thread
        if raw_result.get("id"):
            return raw_result
        raise RuntimeError(f"Codex {method_name} returned no thread payload")

    @staticmethod
    def _extract_thread_id(thread: dict[str, Any], fallback: str = "") -> str:
        thread_id = thread.get("id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
        nested_thread = thread.get("thread")
        if isinstance(nested_thread, dict):
            nested_thread_id = nested_thread.get("id")
            if isinstance(nested_thread_id, str) and nested_thread_id:
                return nested_thread_id
        if fallback:
            return fallback
        raise RuntimeError("Codex thread payload returned no thread id")

    @staticmethod
    def _build_workspace_fingerprint(realpath: str, repo_root: Optional[str]) -> str:
        if repo_root:
            return f"repo:{repo_root}"
        return f"cwd:{realpath}"

    @staticmethod
    def _find_repo_root(path: Path) -> Optional[str]:
        search_path = path if path.is_dir() else path.parent
        for candidate in (search_path, *search_path.parents):
            if (candidate / ".git").exists():
                return os.path.realpath(str(candidate))
        return None


__all__ = [
    "CodexAttachMetadata",
    "CodexAttachOperationResult",
    "CodexAttachService",
    "CodexThreadListResult",
    "CodexThreadReadResult",
    "CodexThreadSummary",
    "CodexWorkspaceIdentity",
    "CodexWorkspaceValidationResult",
]
