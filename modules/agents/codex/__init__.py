"""Codex agent package — persistent app-server mode."""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .agent import CodexAgent as CodexAgent
else:
    CodexAgent = cast(Any, None)

__all__ = ["CodexAgent"]


def __getattr__(name: str):
    if name == "CodexAgent":
        from .agent import CodexAgent

        return CodexAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
