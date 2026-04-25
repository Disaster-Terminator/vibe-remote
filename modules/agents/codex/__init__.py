"""Codex agent package — persistent app-server mode."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import CodexAgent as CodexAgent

__all__ = ["CodexAgent"]


def __getattr__(name: str):
    if name == "CodexAgent":
        from .agent import CodexAgent

        return CodexAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
