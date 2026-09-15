"""Pre-Stage-9 Campaign 5 - agents own open-ended, governed surfaces, and a
request reaches the agent it belongs to.

Real isolated systems through the production composition; the Control
Center's credential, config, ledger, staging and backup paths redirected to
temp files. Surface changes go through the real governed evolution lifecycle
(propose -> run -> rollback), never a direct config write.
"""

from __future__ import annotations

import json
import re

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.control_center.interfaces import (
    InterfaceDescriptor,
    InterfaceFeature,
    SurfaceKind,
    describe_interface,
    surface_component_id,
)
from src.services.evolution.handlers import handler_for
from src.services.evolution.inventory import MANAGER_COMPONENT_ID, discover_components, is_manager_component


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


def _cmd(system, command):
    return cc.run_command(system.centre, command, 100, system.channel, system.conversation)


def _proposal_id(output: str) -> str:
    match = re.search(r"proposed '([^']+)'", output)
    assert match, output
    return match.group(1)


def _arthur_surface(system, kind):
    arthur = system.manager.registry.get("Arthur")
    return describe_interface(arthur, governed=system.centre.governed_values())


class TestSurfaceKindsAreOpen:
    def test_any_plain_kind_is_a_surface_and_known_kinds_are_the_same_value(self):
        descriptor = InterfaceDescriptor(
            agent="Planner", display_name="Planner",
            features={"timeline", InterfaceFeature.CHAT}, implemented={"timeline"},
        )
        assert descriptor.can_render("timeline") and descriptor.offers(InterfaceFeature.CHAT)
        assert not descriptor.can_render("chat")
        assert SurfaceKind(InterfaceFeature.WORKSPACE) == "workspace"
        assert sorted(f.value for f in descriptor.implemented) == ["timeline"]

    def test_a_malformed_kind_is_refused(self):
        with pytest.raises(ValueError):
            SurfaceKind("not a kind!")

    def test_a_future_agent_brings_its_own_kind_with_no_change_here(self):
        class FutureAgent:
            name = "Cartographer"
            interface = InterfaceDescriptor(
                agent="Cartographer", display_name="Cartographer",
                features={"map", "chat"}, implemented={"map", "chat"},
            )

        assert describe_interface(FutureAgent()).can_render("map")


class TestSurfacesEvolveThroughGovernance:
    def test_an_agent_surface_is_proposed_applied_and_rolled_back(self, isolated):
        system = _system(isolated)
        component = surface_component_id("Arthur", "mailbox")
        assert component in _cmd(system, "ecosystem")
        assert not _arthur_surface(system, "mailbox").can_render("mailbox")

        proposed = _cmd(system, f"ecosystem {component} propose implemented low render the mailbox|browse messages directly")
        proposal = _proposal_id(proposed)
        ran = _cmd(system, f"ecosystem {component} run {proposal} yes")
        assert ran.startswith("OK"), ran
        assert "Live now" in ran
        assert _arthur_surface(system, "mailbox").can_render("mailbox")
        snapshot = json.loads(su.route("/snapshot.json", system)[1])
        arthur = next(a for a in snapshot["agents"] if a["name"] == "Arthur")
        assert "mailbox" in arthur["interface"]["implemented"]

        rolled = _cmd(system, f"ecosystem {component} rollback {proposal} yes")
        assert rolled.startswith("OK"), rolled
        assert not _arthur_surface(system, "mailbox").can_render("mailbox")

    def test_a_brand_new_kind_can_be_granted_and_opens_its_route(self, isolated):
        # Every agent now has a Workspace by default (post-Pre-Stage-9), so the
        # governed lifecycle is shown withdrawing it and granting it back.
        system = _system(isolated)
        component = surface_component_id("Arthur", "workspace")
        withdraw = _proposal_id(_cmd(system, f"ecosystem {component} propose withdrawn low withdraw|owner choice"))
        assert _cmd(system, f"ecosystem {component} run {withdraw} yes").startswith("OK")
        status, _body, _ = su.route_workspace_enter("Arthur", system)
        assert status == 400

        grant = _proposal_id(_cmd(system, f"ecosystem {component} propose implemented low a workspace|iterate on findings"))
        assert _cmd(system, f"ecosystem {component} run {grant} yes").startswith("OK")
        status, body, _ = su.route_workspace_enter("Arthur", system)
        assert status == 200, body

    def test_a_surface_for_an_agent_that_does_not_exist_is_refused(self, isolated):
        system = _system(isolated)
        output = _cmd(system, "ecosystem surface.nobody.map propose implemented low no such agent|none")
        assert output.startswith("no such component"), output

    def test_an_invalid_state_is_refused(self, isolated):
        system = _system(isolated)
        component = surface_component_id("Arthur", "mailbox")
        proposal = _proposal_id(_cmd(system, f"ecosystem {component} propose sparkling low nonsense|none"))
        ran = _cmd(system, f"ecosystem {component} run {proposal} yes")
        assert ran.startswith("FAILED")


class TestTheManagerComponentIsARole:
    def test_the_manager_component_is_keyed_by_role_with_the_old_id_accepted(self, isolated):
        system = _system(isolated)
        components = {c.component_id: c for c in discover_components(system.runtime, system.manager, accounts_path=cc.DEFAULT_ACCOUNTS_PATH, config_path=cc.DEFAULT_CONFIG_PATH)}
        assert MANAGER_COMPONENT_ID in components and "alfred" not in components
        assert components[MANAGER_COMPONENT_ID].label.endswith("(the Manager)")
        assert is_manager_component("alfred")
        assert type(handler_for("alfred")) is type(handler_for(MANAGER_COMPONENT_ID))


class TestARequestReachesTheRightAgent:
    def test_the_orchestrator_delegates_even_on_its_own_private_line(self, isolated):
        system = _system(isolated)
        alfred = system.manager.own_skills.name
        turn = exchange(system.channel, system.conversation, "scan my inbox for opportunities", addressed=alfred)
        assert turn.handled_by == "Arthur" and turn.delegated, turn.outcome
        own = exchange(system.channel, system.conversation, "what's the date today?", addressed=alfred)
        assert own.handled_by == alfred

    def test_a_specialist_addressed_directly_answers(self, isolated):
        system = _system(isolated)
        turn = exchange(system.channel, system.conversation, "what can you do?", addressed="Arthur")
        assert turn.handled_by == "Arthur"

    def test_in_the_room_whoever_is_named_answers(self, isolated):
        system = _system(isolated)
        exchange(system.channel, system.conversation, "bring Arthur in")
        arthur = exchange(system.channel, system.conversation, "Arthur, what can you do?")
        assert arthur.handled_by == "Arthur"
        alfred = exchange(system.channel, system.conversation, "Alfred, what can you do?")
        assert alfred.handled_by == system.manager.own_skills.name
