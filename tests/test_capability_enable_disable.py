"""Control Center repair item #1 (2026-09-07): capability enable/disable.

The gap yesterday's report measured: no backend write operation existed
for this at all - MANAGER_CAPABILITIES had no enabled/disabled concept,
and even if it had, nothing consulted it. This closes that gap by reusing
GovernedConfig (the same real, persisted, non-secret store
CapabilityConfigHandler/ToolConfigHandler already write through) and
filtering MANAGER_CAPABILITIES at the one real composition site
(scripts/control_center.py::build()) before it ever reaches ManagerSkills.

Every test here proves *behaviour*, not construction: a disabled
capability must be genuinely unreachable by a real request routed through
a real, freshly-built Manager - not merely absent from a rendered list.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.core.contracts import Task
import scripts.control_center as control_center_module
from scripts.control_center import (
    MANAGER_CAPABILITIES,
    available_capabilities,
    build,
    disabled_capability_names,
    filtered_manager_capabilities,
    run_capability_registry_command,
    run_command,
)
from src.services.evolution.governed_config import load_governed_config


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


@pytest.fixture(autouse=True)
def _isolated_config_file(tmp_path, monkeypatch):
    """Every test in this file gets its own, empty governed-config file -
    never the real one, and never shared between tests."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(control_center_module, "DEFAULT_CONFIG_PATH", path)
    return path


class TestTheRegistryViewIsHonest:
    def test_list_shows_every_registered_capability(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        listing = run_capability_registry_command(centre, "list")
        for name in available_capabilities():
            assert name in listing

    def test_a_freshly_installed_system_has_nothing_disabled(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        listing = run_capability_registry_command(centre, "list")
        assert "[disabled" not in listing

    def test_bare_capabilities_command_is_completely_unchanged(self):
        """The pre-existing feature-matrix view (render_capabilities) must
        still be reached by the bare verb, with no argument - this repair
        is additive, not a replacement."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_command(centre, "capabilities", 80, channel, conversation)
        assert "CAPABILITIES - the real, current state" in result


class TestDisablingIsPersistedAndIndependentlyReRead:
    def test_disable_persists_to_the_real_config_file(self, _isolated_config_file):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        result = run_capability_registry_command(centre, "disable small_talk")
        assert "now disabled" in result
        assert "independently re-read" in result

        reloaded = load_governed_config(_isolated_config_file)
        assert "small_talk" in disabled_capability_names(reloaded)

    def test_enable_removes_the_persisted_disabled_marker(self, _isolated_config_file):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_capability_registry_command(centre, "disable small_talk")

        result = run_capability_registry_command(centre, "enable small_talk")
        assert "now enabled" in result

        reloaded = load_governed_config(_isolated_config_file)
        assert "small_talk" not in disabled_capability_names(reloaded)

    def test_an_unknown_capability_name_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_capability_registry_command(centre, "disable not_a_real_capability")
        assert "no such capability" in result

    def test_missing_name_argument_reports_usage(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        assert "usage:" in run_capability_registry_command(centre, "disable")
        assert "usage:" in run_capability_registry_command(centre, "enable")


class TestStructuralFallbacksCannotBeDisabled:
    """select_capability() reaches OPEN_CONVERSATION/ROSTER_REPORT as
    literal fallback objects, never through the registry dict - so
    "disabling" either could never actually take effect. Refused
    honestly rather than silently accepted and silently ineffective."""

    @pytest.mark.parametrize("name", ["open_conversation", "roster_report"])
    def test_disabling_a_structural_fallback_is_refused(self, name, _isolated_config_file):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        result = run_capability_registry_command(centre, f"disable {name}")
        assert "cannot be disabled" in result

        reloaded = load_governed_config(_isolated_config_file)
        assert name not in disabled_capability_names(reloaded)


class TestFilteredCapabilitiesIsTheRealEnforcementPoint:
    def test_a_disabled_capability_is_absent_from_the_filtered_registry(self, _isolated_config_file):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_capability_registry_command(centre, "disable current_datetime")

        config = load_governed_config(_isolated_config_file)
        filtered = filtered_manager_capabilities(config)
        assert "current_datetime" not in filtered
        # Nothing else moved - every other real handler is still present.
        assert len(filtered) == len(MANAGER_CAPABILITIES) - 1

    def test_no_disabled_capability_returns_the_full_registry_unchanged(self, _isolated_config_file):
        config = load_governed_config(_isolated_config_file)
        filtered = filtered_manager_capabilities(config)
        assert filtered == MANAGER_CAPABILITIES


class TestDisablingGenuinelyPreventsRouting:
    """The decisive behavioural proof: a real request that would have been
    answered by a disabled capability must genuinely stop reaching it, in
    a real, freshly-built Manager - not merely absent from a listing."""

    def test_a_disabled_capability_is_never_reached_by_a_matching_request(self, _isolated_config_file):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_capability_registry_command(centre, "disable current_datetime")

        reloaded = control_center_module._reload_session(centre, "reload")
        assert reloaded is not None
        new_centre, new_channel, new_conversation = reloaded

        result = run_command(new_centre, "what time is it", 80, new_channel, new_conversation)
        assert "current_datetime" not in result
        assert "It's" not in result  # the deterministic answer it would have given

    def test_re_enabling_restores_reachability_after_reload(self, _isolated_config_file):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_capability_registry_command(centre, "disable current_datetime")
        reloaded = control_center_module._reload_session(centre, "reload")
        centre, channel, conversation = reloaded

        run_capability_registry_command(centre, "enable current_datetime")
        reloaded_again = control_center_module._reload_session(centre, "reload")
        assert reloaded_again is not None
        new_centre, new_channel, new_conversation = reloaded_again

        result = run_command(new_centre, "what time is it", 80, new_channel, new_conversation)
        assert "current_datetime" in result

    def test_disabling_does_not_take_effect_on_the_still_running_process(self, _isolated_config_file):
        """The identical, already-established rule every other persisted
        Control Center change lives under: persisted only, until reload
        or a restart - proven here so this repair does not silently
        break that invariant."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        before = run_command(centre, "what time is it", 80, channel, conversation)
        run_capability_registry_command(centre, "disable current_datetime")
        after = run_command(centre, "what time is it", 80, channel, conversation)
        assert before == after
        assert "current_datetime" in after

    def test_disabling_an_unrelated_capability_leaves_everything_else_reachable(self, _isolated_config_file):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_capability_registry_command(centre, "disable small_talk")
        reloaded = control_center_module._reload_session(centre, "reload")
        new_centre, new_channel, new_conversation = reloaded

        result = run_command(new_centre, "what time is it", 80, new_channel, new_conversation)
        assert "current_datetime" in result
