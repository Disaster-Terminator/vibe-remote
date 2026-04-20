from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_settings import SettingsStore, UserSettings
from config.v2_config import UpdateConfig
from core import update_checker
from core.update_checker import UpdateChecker


class _StubSettingsManager:
    def __init__(self, store):
        self._store = store

    def get_store(self):
        return self._store


class _StubController:
    def __init__(self, store):
        self.settings_manager = _StubSettingsManager(store)
        self.config = type("Config", (), {"platform": "slack"})()
        self.im_client = object()


def test_get_admin_user_ids_includes_all_platforms(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    store = SettingsStore.get_instance()
    store.set_users_for_platform("slack", {"U1": UserSettings(display_name="Slack", is_admin=True)})
    store.set_users_for_platform("discord", {"D1": UserSettings(display_name="Discord", is_admin=True)})
    store.save()

    checker = UpdateChecker(_StubController(store), UpdateConfig())

    admin_ids = checker._get_admin_user_ids()

    assert set(admin_ids) == {"slack::U1", "discord::D1"}


def test_stop_returns_cancellable_task(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()

    async def run_test():
        checker = UpdateChecker(_StubController(SettingsStore.get_instance()), UpdateConfig(check_interval_minutes=1))
        checker.start()
        await asyncio.sleep(0)
        task = checker.stop()
        assert task is not None
        await checker.wait_stopped(task)
        assert task.done()

    asyncio.run(run_test())


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_pypi_version_sync_ignores_prerelease_for_stable_current(monkeypatch):
    payload = b"""
    {
      "info": {"version": "2.2.8rc1"},
      "releases": {
        "2.2.7": [{}],
        "2.2.8rc1": [{}]
      }
    }
    """

    with patch.object(update_checker.urllib.request, "urlopen", return_value=_FakeResponse(payload)):
        monkeypatch.setattr("vibe.__version__", "2.2.7", raising=False)
        info = update_checker._fetch_pypi_version_sync()

    assert info == {"current": "2.2.7", "latest": "2.2.7", "has_update": False, "error": None}


def test_is_dev_or_source_build_detects_dev_and_local_versions():
    assert update_checker._is_dev_or_source_build("0.1.dev944+g8abe5eecf") is True
    assert update_checker._is_dev_or_source_build("2.2.12+local") is True
    assert update_checker._is_dev_or_source_build("2.2.12") is False


def test_do_check_skips_auto_update_for_dev_build(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()

    async def run_test():
        checker = UpdateChecker(_StubController(SettingsStore.get_instance()), UpdateConfig(auto_update=True, notify_admins=False))
        checker.state.last_activity_at = 0
        monkeypatch.setattr(checker, "_is_idle", lambda: True)

        async def fail_perform_update(_latest: str):
            raise AssertionError("auto-update should be skipped for dev/source builds")

        monkeypatch.setattr(checker, "_perform_update", fail_perform_update)
        monkeypatch.setattr(
            update_checker,
            "_fetch_pypi_version_sync",
            lambda: {
                "current": "0.1.dev944+g8abe5eecf",
                "latest": "2.2.12",
                "has_update": True,
                "error": None,
            },
        )

        await checker._do_check()
        assert checker.state.last_check_at is not None
        assert checker.state.notified_version is None

    asyncio.run(run_test())


def test_do_check_still_auto_updates_stable_build(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()

    async def run_test():
        checker = UpdateChecker(_StubController(SettingsStore.get_instance()), UpdateConfig(auto_update=True, notify_admins=False))
        checker.state.last_activity_at = 0
        monkeypatch.setattr(checker, "_is_idle", lambda: True)

        seen = {}

        async def capture_perform_update(latest: str):
            seen["latest"] = latest

        monkeypatch.setattr(checker, "_perform_update", capture_perform_update)
        monkeypatch.setattr(
            update_checker,
            "_fetch_pypi_version_sync",
            lambda: {
                "current": "2.2.11",
                "latest": "2.2.12",
                "has_update": True,
                "error": None,
            },
        )

        await checker._do_check()
        assert seen["latest"] == "2.2.12"

    asyncio.run(run_test())
