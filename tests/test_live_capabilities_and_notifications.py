"""Post-Pre-Stage-9 D8a/D8b - capability choices go live in the running session;
due reminders reach the person as desktop notifications with no page open.
The Windows notification call is replaced at its one seam - never shown here."""

from __future__ import annotations

import subprocess

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
import src.providers.desktop_notifier as notifier_module
from scripts.exchange import exchange
from src.config.settings import Settings
from src.providers.desktop_notifier import NullDesktopNotifier, WindowsToastNotifier, build_desktop_notifier


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"),
        ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"),
        ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    return tmp_path


def _system(tmp_path):
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


class TestCapabilityChoicesAreLive:
    def test_disabling_a_capability_from_the_served_control_center_takes_effect_at_once(self, isolated):
        system = _system(isolated)
        assert exchange(system.channel, system.conversation, "what day is it today?").capability == "current_datetime"
        status, body, _ = su.route_command("capabilities disable current_datetime", None, system)
        token = __import__("json").loads(body)["confirm_token"]
        status, body, _ = su.route_command("capabilities disable current_datetime", token, system)
        assert "live in this session now" in body.decode("utf-8")
        assert exchange(system.channel, system.conversation, "what day is it today?").capability != "current_datetime"
        cc.run_command(system.centre, "capabilities enable current_datetime", 100, system.channel, system.conversation)
        assert exchange(system.channel, system.conversation, "what day is it today?").capability == "current_datetime"


class TestDesktopNotifications:
    def test_null_is_the_default_and_shows_nothing(self):
        notifier = build_desktop_notifier(Settings.from_env(env={}))
        assert isinstance(notifier, NullDesktopNotifier) and notifier.notify("Alfred", "x", tag="t") is False

    def test_unknown_backends_fail_loudly(self):
        with pytest.raises(ValueError, match="Unknown desktop notification backend"):
            build_desktop_notifier(Settings.from_env(env={}, desktop_notifications="pager"))

    def test_windows_delivery_is_observed_never_assumed(self, monkeypatch):
        seen = []

        def fake(script, *, timeout):
            seen.append(script)
            return subprocess.CompletedProcess([], 0, stdout="delivered=1\n", stderr="")

        monkeypatch.setattr(notifier_module, "_run_powershell", fake)
        monkeypatch.setattr(WindowsToastNotifier, "available", staticmethod(lambda: True))
        assert WindowsToastNotifier().notify("Alfred", "Join the <standup> & call Mum's line", tag="reminder:abc:2026")
        assert "Join the &lt;standup&gt; &amp; call Mum''s line" in seen[0]

        monkeypatch.setattr(notifier_module, "_run_powershell", lambda s, *, timeout: subprocess.CompletedProcess([], 0, stdout="delivered=0\n", stderr=""))
        assert WindowsToastNotifier().notify("Alfred", "x", tag="t") is False

    def test_the_control_center_category_lists_both_backends(self, isolated):
        system = _system(isolated)
        listing = cc.run_command(system.centre, "provider desktop_notification_provider", 100, system.channel, system.conversation)
        assert "windows" in listing and "null" in listing
