from pathlib import Path


def test_codex_package_exports_agent_class() -> None:
    content = (Path(__file__).resolve().parents[1] / "modules/agents/codex/__init__.py").read_text(encoding="utf-8")
    assert "from .agent import CodexAgent" in content
    assert "CodexAgent = cast(Any, None)" not in content
