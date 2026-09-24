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

    def test_disabling_takes_effect_on_the_running_process_at_once(self, _isolated_config_file):
        """Post-Pre-Stage-9: this used to pin "persisted only, until reload or
        a restart" - but 'reload' exists only in the terminal loop, so a choice
        made from the served Control Center never went live until a restart.
        The governed registry is now applied to the running Manager at once;
        every start still reads the same persisted choice."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        before = run_command(centre, "what time is it", 80, channel, conversation)
        assert "current_datetime" in before
        run_capability_registry_command(centre, "disable current_datetime")
        after = run_command(centre, "what time is it", 80, channel, conversation)
        assert "current_datetime" not in after
        assert "It's" not in after

    def test_disabling_an_unrelated_capability_leaves_everything_else_reachable(self, _isolated_config_file):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_capability_registry_command(centre, "disable small_talk")
        reloaded = control_center_module._reload_session(centre, "reload")
        new_centre, new_channel, new_conversation = reloaded

        result = run_command(new_centre, "what time is it", 80, new_channel, new_conversation)
        assert "current_datetime" in result


class TestGovernanceHoldsAcrossEveryEntryPointNotJustControlCenter:
    """Completeness-review finding (2026-09-08): disabling a capability
    only actually stopped it if the request was routed through
    scripts/control_center.py::build(). scripts/heartbeat.py built its
    own Manager without ever consulting the same GovernedConfig - a real
    governance-bypass-through-another-route bug, not a hypothetical one:
    the identical class of gap this project's own accounts= wiring in
    that file already had a name for and had already fixed once, for
    provider accounts specifically."""

    def test_the_heartbeats_own_composition_root_also_filters(self, _isolated_config_file, monkeypatch, tmp_path):
        import scripts.heartbeat as heartbeat_module
        from src.services.profile.profile import UserProfile

        monkeypatch.setattr(heartbeat_module, "DEFAULT_CONFIG_PATH", _isolated_config_file)
        run_capability_registry_command(
            ControlCenter(*real_system()[:2]), "disable current_datetime"
        )

        build = heartbeat_module.build_manager_factory(
            Settings.from_env(env={}, memory="ephemeral"), dry_run=True
        )
        manager = build(UserProfile())

        result = manager.handle_task(Task(description="what time is it"))
        assert result.payload is None or result.payload.get("handled_by") != "current_datetime"

    def test_a_capability_left_enabled_still_reaches_the_heartbeats_own_manager(
        self, _isolated_config_file, monkeypatch
    ):
        import scripts.heartbeat as heartbeat_module
        from src.services.profile.profile import UserProfile

        monkeypatch.setattr(heartbeat_module, "DEFAULT_CONFIG_PATH", _isolated_config_file)
        run_capability_registry_command(
            ControlCenter(*real_system()[:2]), "disable small_talk"
        )

        build = heartbeat_module.build_manager_factory(
            Settings.from_env(env={}, memory="ephemeral"), dry_run=True
        )
        manager = build(UserProfile())

        result = manager.handle_task(Task(description="what time is it"))
        assert result.payload is not None and result.payload.get("handled_by") == "current_datetime"

    def test_the_heartbeat_equips_the_owners_specialists_it_registers(
        self, _isolated_config_file, monkeypatch, tmp_path
    ):
        """Phase 9 (2026-09-24). One layer further in than the finding above,
        and the same class of gap: the heartbeat *registered* the owner's own
        specialists and then left them unequipped. With no capability handler
        bound, ``authorised_capabilities()`` was empty, so every capability
        the owner had granted was both undeclared and unreachable here - while
        the identical definition, read from the identical governed config, had
        them in every other entry point.

        Measured before the repair: Scout ``authorised=()`` and
        ``capability_reply`` None on a tick, against ``('create_diagram',)``
        and a real result through ``scripts/serve_ui.build_live_system``.

        "No mission is given to them here" did not save it either: the tick's
        mission is routed by capability-token overlap, and an area an owner
        could plainly choose really does win that routing - so the mission was
        handed to a specialist stripped of its grants, silently, every tick.
        """
        import scripts.heartbeat as heartbeat_module
        from src.agents.specialists import AGENT_DEFINITION_PREFIX, SpecialistDefinition
        from src.control_center.capability_access import with_grants
        from src.heartbeat.runner import MISSION
        from src.services.evolution.governed_config import save_governed_config
        from src.services.profile.profile import UserProfile

        monkeypatch.setattr(heartbeat_module, "DEFAULT_CONFIG_PATH", _isolated_config_file)
        definition = SpecialistDefinition(
            key="scout", name="Scout",
            area="gmail inbox freelance job opportunities and new leads",
        )
        config = load_governed_config(_isolated_config_file)
        config = config.with_value(AGENT_DEFINITION_PREFIX + "scout", definition.to_value())
        grant, value = with_grants("Scout", ("create_diagram",))
        save_governed_config(config.with_value(grant, value), _isolated_config_file)

        build_tick = heartbeat_module.build_manager_factory(
            Settings.from_env(
                env={}, memory="ephemeral", media="local",
                artifacts_dir=str(tmp_path / "artifacts"),
                media_asset_dir=str(tmp_path / "assets"),
            ),
            dry_run=True,
        )
        manager = build_tick(UserProfile())
        scout = manager.registry.get("Scout")
        assert scout is not None, "the owner's specialist is registered on every tick"

        # The owner's grant is live here, exactly as in every other entry point.
        assert scout.authorised_capabilities() == ("create_diagram",)
        assert "create_diagram" in scout.spec.capabilities
        assert scout.capability_reply(Task(description="make a diagram of a water filter")) is not None

        # And the exposure is real, not hypothetical: this tick's own mission
        # is what reaches this agent.
        decision = manager.router.route(
            Task(description=MISSION), manager.registry.all_agents()
        )
        assert decision.agent_name == "Scout"

    def test_the_static_exports_own_composition_root_also_filters(self, _isolated_config_file, monkeypatch):
        """Completeness-review finding (2026-09-08): scripts/export_ui.py's
        take_snapshot() - the real function serve_ui.py's own docstring
        names alongside control_center.py/heartbeat.py as one of this
        project's four real entry points, and the one 'python
        scripts/export_ui.py' itself runs for a static page - built its own
        Manager without ever consulting GovernedConfig either. Proven the
        same way the heartbeat repair above was: a real task, routed
        through the real Manager take_snapshot() actually builds, must
        genuinely stop reaching a disabled capability - not merely absent
        from some rendered list (agent.spec.capabilities, the snapshot's
        own per-agent view, turned out to be identity-declared words, a
        different concept entirely from the routing registry this repair
        governs - the reason this is checked by behaviour, not by shape)."""
        import scripts.export_ui as export_ui_module
        from src.core.contracts import Task

        monkeypatch.setattr(export_ui_module, "DEFAULT_CONFIG_PATH", _isolated_config_file)
        run_capability_registry_command(
            ControlCenter(*real_system()[:2]), "disable current_datetime"
        )

        captured: dict = {}
        real_build_manager = export_ui_module.build_manager

        def spying_build_manager(*args, **kwargs):
            manager = real_build_manager(*args, **kwargs)
            captured["manager"] = manager
            return manager

        monkeypatch.setattr(export_ui_module, "build_manager", spying_build_manager)

        export_ui_module.take_snapshot(Settings.from_env(env={}, memory="ephemeral"))

        result = captured["manager"].handle_task(Task(description="what time is it"))
        assert result.payload is None or result.payload.get("handled_by") != "current_datetime"

    def test_an_enabled_capability_still_reaches_the_static_exports_own_manager(
        self, _isolated_config_file, monkeypatch
    ):
        import scripts.export_ui as export_ui_module
        from src.core.contracts import Task

        monkeypatch.setattr(export_ui_module, "DEFAULT_CONFIG_PATH", _isolated_config_file)
        run_capability_registry_command(
            ControlCenter(*real_system()[:2]), "disable small_talk"
        )

        captured: dict = {}
        real_build_manager = export_ui_module.build_manager

        def spying_build_manager(*args, **kwargs):
            manager = real_build_manager(*args, **kwargs)
            captured["manager"] = manager
            return manager

        monkeypatch.setattr(export_ui_module, "build_manager", spying_build_manager)

        export_ui_module.take_snapshot(Settings.from_env(env={}, memory="ephemeral"))

        result = captured["manager"].handle_task(Task(description="what time is it"))
        assert result.payload is not None and result.payload.get("handled_by") == "current_datetime"
