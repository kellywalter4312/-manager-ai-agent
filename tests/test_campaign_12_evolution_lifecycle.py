"""Pre-Stage-9 Campaign 12 - the evolution/governance lifecycle, verified end to
end through the real Control Center, including a category added at runtime
through ``register_handler`` with no engine change.

Real isolated systems; every Control Center path (ledger, config, staging,
backups, accounts) redirected to temp files.
"""

from __future__ import annotations

import re

import pytest

import scripts.control_center as cc
from src.config.settings import Settings
from src.services.evolution import handlers as evolution_handlers
from src.services.evolution.governed_config import load_governed_config, save_governed_config
from src.services.evolution.inventory import ComponentSnapshot


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, leaf in (("DEFAULT_ACCOUNTS_PATH", "accounts.json"), ("DEFAULT_CONFIG_PATH", "config.json"),
                       ("DEFAULT_LEDGER_PATH", "state.json"), ("DEFAULT_STAGING_DIR", "staging"),
                       ("DEFAULT_BACKUPS_DIR", "backups")):
        monkeypatch.setattr(cc, name, tmp_path / leaf)
    return tmp_path


def _system(tmp_path):
    runtime, manager, channel, conversation = cc.build(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))
    centre = cc.ControlCenter(runtime, manager)
    return lambda command: cc.run_command(centre, command, 100, channel, conversation)


def _proposal(output: str) -> str:
    match = re.search(r"proposed '([^']+)'", output)
    assert match, output
    return match.group(1)


class WidgetHandler(evolution_handlers.EvolutionHandler):
    """A category nobody wrote into the engine or the inventory."""

    def applies_to(self, component_id):
        return component_id.startswith("widget.")

    def initial_version(self, component_id):
        return "0"

    def governed_components(self, *, config):
        return (ComponentSnapshot(
            component_id="widget.dial", category="widget", label="a category added at runtime",
            version=str(config.get("widget.dial") or "0"),
        ),)

    def apply(self, proposal, *, context):
        save_governed_config(load_governed_config(context.config_path).with_value(proposal.target, proposal.proposed_version), context.config_path)
        return evolution_handlers.HandlerResult(True, f"{proposal.target} is now {proposal.proposed_version}")

    def rollback(self, proposal, *, context):
        save_governed_config(load_governed_config(context.config_path).with_value(proposal.target, proposal.current_version), context.config_path)
        return evolution_handlers.HandlerResult(True, f"{proposal.target} restored to {proposal.current_version}")


@pytest.fixture
def widget_handler():
    handler = WidgetHandler()
    evolution_handlers.register_handler(handler)
    yield handler
    evolution_handlers._HANDLERS[:] = [h for h in evolution_handlers._HANDLERS if h is not handler]


class TestTheFullLifecycle:
    def test_propose_refuse_unapproved_apply_history_rollback_and_refuse_a_second_rollback(self, isolated):
        run = _system(isolated)
        first = _proposal(run("ecosystem capability.general_conversation propose 8 low shorter context|lower latency"))
        assert run(f"ecosystem capability.general_conversation run {first} no").startswith("FAILED: not approved")
        second = _proposal(run("ecosystem capability.general_conversation propose 8 low shorter context|lower latency"))
        assert run(f"ecosystem capability.general_conversation run {second} yes").startswith("OK")
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get("capability.general_conversation") == "8"
        history = run("ecosystem capability.general_conversation")
        assert f"{second}: active" in history and f"{first}: evaluated" in history
        assert run(f"ecosystem capability.general_conversation rollback {second} yes").startswith("OK")
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get("capability.general_conversation") == "6"
        assert "must be ACTIVE" in run(f"ecosystem capability.general_conversation rollback {second} yes")

    def test_the_ledger_survives_a_restart(self, isolated):
        run = _system(isolated)
        proposal = _proposal(run("ecosystem capability.general_conversation propose 8 low r|b"))
        run(f"ecosystem capability.general_conversation run {proposal} yes")
        assert f"{proposal}: active" in _system(isolated)("ecosystem capability.general_conversation")

    def test_unknown_components_and_fabricated_versions_are_refused(self, isolated):
        run = _system(isolated)
        assert run("ecosystem nothing.here propose 2 low r|b").startswith("no such component")
        fake = _proposal(run("ecosystem brain.manager propose genius low r|b"))
        assert "not a real reasoning mode" in run(f"ecosystem brain.manager run {fake} yes")


class TestANewCategoryJoinsWithNoEngineChange:
    def test_without_a_handler_the_category_does_not_exist(self, isolated):
        assert _system(isolated)("ecosystem widget.dial propose 2 low r|b").startswith("no such component")

    def test_a_registered_handler_brings_its_component_into_the_control_center(self, isolated, widget_handler):
        run = _system(isolated)
        assert "widget.dial = 0" in run("ecosystem")
        proposal = _proposal(run("ecosystem widget.dial propose 2 low a new dial|proves open-ended growth"))
        assert run(f"ecosystem widget.dial run {proposal} yes").startswith("OK")
        assert "widget.dial = 2" in _system(isolated)("ecosystem")
        assert run(f"ecosystem widget.dial rollback {proposal} yes").startswith("OK")
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get("widget.dial") == "0"

    def test_built_in_handlers_declare_nothing_extra(self, isolated):
        config = load_governed_config(cc.DEFAULT_CONFIG_PATH)
        assert all(
            handler.governed_components(config=config) == ()
            for handler in evolution_handlers.registered_handlers()
            if not isinstance(handler, WidgetHandler)
        )
