"""Environment picker (final acceptance sweep, 2026-09-08).

The gap this sweep found: EnvironmentProvider/EnvironmentProfile already
had a real, governed write path (set_environment_preference, reached
through both the CLI's own "environment" command and a real POST
/environment route - see scripts/control_center.py's own "Whole-System
Reality Audit... the identical governed-preference pattern the 'avatar'
command above already uses, extended to the environment axis" comment),
but two things were missing:

1. AgentView/snapshot() never carried an agent's current environment at
   all - not even read-only - so no UI surface could show it, let alone
   change it (unlike "avatar", which the snapshot has always carried).
2. ui/index.html had no picker control for it - "avatar" got one
   (.avatar-picker, wireAvatarPicker()) the day Stage 17.2 added the
   route; "environment" got the route but never the picker.

This file proves both pieces, at the same depth this project's own
existing avatar-picker tests already do (see
test_stage17_2_digital_human.py's TestAppearancePicker): the snapshot
carries the real catalog and each agent's real current choice, and the
shell defines a genuinely distinct picker function that POSTs to
"environment" with {agent, environment_id} - never a second
implementation of set_environment_preference, never touching the avatar
picker's own selectedAgent/WHO concerns.
"""

from __future__ import annotations

from pathlib import Path

from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.snapshot import snapshot
from src.services.email.in_memory_mailbox import InMemoryMailbox

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"


def settings(**overrides):
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)


def system(**overrides):
    runtime = build_runtime(settings(**overrides))
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    channel = build_channel(runtime, manager)
    conversation = build_conversation(manager)
    centre = ControlCenter(runtime, manager)
    return runtime, manager, channel, conversation, centre


class TestSnapshotCarriesTheRealEnvironmentAxis:
    def test_the_snapshot_carries_the_real_environment_catalog(self):
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)
        assert data["environment_catalog"]
        for profile in data["environment_catalog"]:
            assert profile["environment_id"]
            assert profile["label"]

    def test_every_agent_carries_a_real_current_environment(self):
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)
        for agent in data["agents"]:
            assert agent["environment"] is not None
            assert agent["environment"]["environment_id"]

    def test_choosing_an_environment_is_reflected_in_the_next_snapshot(self):
        runtime, manager, channel, conversation, centre = system()
        from scripts.control_center import run_command

        manager_name = manager.own_skills.name
        before = snapshot(centre, conversation=conversation, channel=channel)
        before_view = next(a for a in before["agents"] if a["is_manager"])
        catalog = before["environment_catalog"]
        other = next(p for p in catalog if p["environment_id"] != before_view["environment"]["environment_id"])

        result = run_command(centre, f"environment {manager_name} {other['environment_id']}", 80, channel, conversation)
        assert "is now" in result

        after = snapshot(centre, conversation=conversation, channel=channel)
        after_view = next(a for a in after["agents"] if a["is_manager"])
        assert after_view["environment"]["environment_id"] == other["environment_id"]


class TestTheUIDefinesAGenuinelyDistinctEnvironmentPicker:
    def test_the_shell_defines_an_environment_picker_control(self):
        shell = UI.read_text(encoding="utf-8")
        assert "env-picker" in shell
        assert "data-env-agent=" in shell
        assert "data-env-id=" in shell
        assert "function wireEnvironmentPicker(){" in shell

    def test_the_environment_picker_posts_to_environment_with_the_right_shape(self):
        shell = UI.read_text(encoding="utf-8")
        fn = shell.split("function wireEnvironmentPicker(){", 1)[1].split("\n  }", 1)[0]
        assert '"environment"' in fn
        assert "environment_id" in fn
        assert "agent" in fn

    def test_the_environment_picker_is_wired_alongside_the_avatar_picker(self):
        shell = UI.read_text(encoding="utf-8")
        assert "wireEnvironmentPicker();" in shell

    def test_the_environment_picker_never_touches_selectedagent(self):
        """The same WHO-vs-preference confusion the avatar picker's own
        test already guards against - proven here too."""
        shell = UI.read_text(encoding="utf-8")
        fn = shell.split("function wireEnvironmentPicker(){", 1)[1].split("\n  }", 1)[0]
        assert "selectedAgent" not in fn

    def test_the_environment_picker_is_a_distinct_function_from_the_avatar_picker(self):
        shell = UI.read_text(encoding="utf-8")
        avatar_fn = shell.split("function wireAvatarPicker(){", 1)[1].split("\n  }", 1)[0]
        assert '"environment"' not in avatar_fn and "'environment'" not in avatar_fn
