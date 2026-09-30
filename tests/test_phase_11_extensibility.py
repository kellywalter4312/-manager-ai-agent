"""Phase 11 - Open-Ended Evolution & Extensibility, verified end to end.

What already existed (and is only re-proven here where Phase 11 changed it):
the governed evolution lifecycle (``src/evolution.py`` + ``src/services/
evolution/``), its open handler registry, the ecosystem registry, the
Control Center ``ecosystem`` command and its served panel, and the store
migration pipeline (``src/upgrade_*.py``).

What Phase 11 repaired or added, each proven below:

* The Governed Executor verifies the component's *real* state through its
  own handler (``observe``) - a handler that reports success without
  changing anything is no longer activated; a handler exception is recorded,
  never escaped; a half-done apply is compensated and re-read; and a
  component changed outside governance is refused before anything is written.
* Rollback is refused while a later change or an active dependent holds the
  target, and is recorded only once the prior state is re-read - a rollback
  that did not land leaves the change ACTIVE and the ledger unchanged.
* The upgrade description (``EvolutionProposal``) carries compatibility
  requirements and provenance, persisted and backward compatible; the
  deterministic Upgrade Planner answers "can this run now, in what order,
  and what does it affect" and is reachable from the Control Center.
* Persisted state this installation cannot read (a damaged or newer ledger
  or governed config) is reported and never overwritten; writes are atomic.
* Current entities stopped being architecture: credential requirements are
  declared beside each backend's builder, connection adapter versions are
  declared by the connection, and live activation is declared by the handler.

The genericity proof uses ``lumenweave`` - a technology that exists nowhere
in the shipped tree (checked with ``git grep`` first) - registered only in
this file, taken through register -> discover -> propose -> plan -> refuse
-> execute -> verify -> operate -> rebuild -> reload -> rollback -> rebuild
-> remove, with no production source change and no production residue.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

import scripts.control_center as cc
from src.config.settings import Settings
from src.core.contracts import Task, TaskResult
from src.core.versioning import MigrationUnavailable
from src.evolution import (
    EvolutionLedger,
    EvolutionProposal,
    EvolutionStatus,
    RiskLevel,
    plan_proposal,
)
from src.manager.capabilities import CapabilityRequest, ManagerCapability
from src.services.evolution import ecosystem_evolution as ee
from src.services.evolution import handlers as H
from src.services.evolution.governed_config import (
    GovernedConfigUnreadable,
    load_governed_config,
    save_governed_config,
)
from src.services.evolution.inventory import ComponentSnapshot
from src.services.evolution.ledger_store import EvolutionState, load_evolution_state, save_evolution_state

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_WORD = "lumenweave"
FIXTURE_COMPONENT = f"{FIXTURE_WORD}.focus"
FIXTURE_CAPABILITY = f"{FIXTURE_WORD}_report"


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Every Control Center path the lifecycle touches, redirected."""
    for name, leaf in (("DEFAULT_ACCOUNTS_PATH", "accounts.json"), ("DEFAULT_CONFIG_PATH", "config.json"),
                       ("DEFAULT_LEDGER_PATH", "state.json"), ("DEFAULT_STAGING_DIR", "staging"),
                       ("DEFAULT_BACKUPS_DIR", "backups")):
        monkeypatch.setattr(cc, name, tmp_path / leaf)
    return tmp_path


def _settings(tmp_path, **overrides) -> Settings:
    return Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **overrides,
    )


class _System:
    """One genuinely built system - composition root, Control Center and
    command door - rebuilt from scratch on every construction."""

    def __init__(self, tmp_path, **overrides):
        self.runtime, self.manager, self.channel, self.conversation = cc.build(_settings(tmp_path, **overrides))
        self.centre = cc.ControlCenter(self.runtime, self.manager)

    def run(self, command: str) -> str:
        return cc.run_command(self.centre, command, 100, self.channel, self.conversation)


def _proposal_id(output: str) -> str:
    match = re.search(r"proposed '([^']+)'", output)
    assert match, output
    return match.group(1)


def _digest(path: Path) -> "str | None":
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# ---------------------------------------------------------------------------
# The temporary extension: a component category, its handler, and a
# capability that reads the governed value on every turn. None of it exists
# outside this file.
# ---------------------------------------------------------------------------

FOCUS_LEVELS = ("off", "low", "high")


class LumenweaveHandler(H.EvolutionHandler):
    """Governs the lumenweave focus level. Uses the *default* observation
    contract (the governed config), so the proof also shows a new category
    needs nothing beyond apply/rollback to be independently verified."""

    def applies_to(self, component_id):
        return component_id.startswith(f"{FIXTURE_WORD}.")

    def initial_version(self, component_id):
        return "off"

    def default_version(self, component_id):
        return "off"

    def governed_components(self, *, config):
        return (ComponentSnapshot(
            component_id=FIXTURE_COMPONENT, category=FIXTURE_WORD, label="lumenweave focus level",
            version=str(config.get(FIXTURE_COMPONENT) or "off"),
        ),)

    def apply(self, proposal, *, context):
        if proposal.proposed_version not in FOCUS_LEVELS:
            return H.HandlerResult(False, f"'{proposal.proposed_version}' is not a focus level")
        save_governed_config(
            load_governed_config(context.config_path).with_value(proposal.target, proposal.proposed_version),
            context.config_path,
        )
        return H.HandlerResult(True, f"lumenweave focus is now {proposal.proposed_version}")

    def rollback(self, proposal, *, context):
        save_governed_config(
            load_governed_config(context.config_path).with_value(proposal.target, proposal.current_version),
            context.config_path,
        )
        return H.HandlerResult(True, f"lumenweave focus restored to {proposal.current_version}")

    def activation(self, component_id):
        return H.ACTIVATION_PER_TURN


def _handle_lumenweave(request: CapabilityRequest) -> TaskResult:
    # Read on every turn from the governed configuration - the running
    # behaviour a governed change must actually alter.
    level = load_governed_config(cc.DEFAULT_CONFIG_PATH).get(FIXTURE_COMPONENT) or "off"
    return TaskResult.success(request.task, request.own_name, f"lumenweave focus: {level}")


def _lumenweave_capability() -> ManagerCapability:
    return ManagerCapability(
        name=FIXTURE_CAPABILITY, capabilities=frozenset({FIXTURE_WORD}), handle=_handle_lumenweave,
        description="A capability that exists only inside this test file.",
    )


def _load_extension(manager) -> None:
    """What the extension's own module does when a session starts: offer its
    capability through the one open registration seam (the manager's own
    capability map, declared and handled), touching nothing else."""
    capability = _lumenweave_capability()
    manager.own_skills._capabilities[capability.name] = capability
    manager.own_skills._identity = dataclasses.replace(
        manager.own_skills._identity,
        capabilities=manager.own_skills._identity.capabilities | capability.capabilities,
    )


def _operate(system: _System) -> str:
    result = system.manager.handle_task(Task(description=f"run {FIXTURE_WORD} report"))
    assert result.succeeded, result
    return result.output


@pytest.fixture
def lumenweave():
    handler = LumenweaveHandler()
    H.register_handler(handler)
    yield handler
    H._HANDLERS[:] = [h for h in H._HANDLERS if h is not handler]


def _fixture_is_genuinely_unseen() -> None:
    result = subprocess.run(
        ["git", "grep", "-il", FIXTURE_WORD, "--", "src", "scripts", "ui", "main.py"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "the fixture technology must exist nowhere in the shipped tree"


def _source_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--", "src", "scripts", "ui", "main.py"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout


# ---------------------------------------------------------------------------
# 1. The genericity proof
# ---------------------------------------------------------------------------


class TestAnUnseenExtensionLivesItsWholeLifeWithNoSourceChange:
    def test_register_to_remove_through_the_real_control_center(self, isolated, lumenweave):
        _fixture_is_genuinely_unseen()
        source_before = _source_status()
        production_config = REPO_ROOT / "data" / "evolution" / "config.json"
        production_ledger = REPO_ROOT / "data" / "evolution" / "state.json"
        production_before = (_digest(production_config), _digest(production_ledger))

        # REGISTER + DISCOVER: the handler alone brings the component into the
        # Control Center's inventory; the capability is reachable by the Manager.
        system = _System(isolated)
        _load_extension(system.manager)
        assert f"{FIXTURE_COMPONENT} = off" in system.run("ecosystem")
        assert _operate(system) == "lumenweave focus: off"

        # REFUSE: an unapproved run changes nothing.
        refused = _proposal_id(system.run(f"ecosystem {FIXTURE_COMPONENT} propose high low r|b"))
        assert system.run(f"ecosystem {FIXTURE_COMPONENT} run {refused} no").startswith("FAILED: not approved")
        assert _operate(system) == "lumenweave focus: off"

        # PROPOSE with a real compatibility requirement (the Half Brain this
        # system actually runs) and PLAN it.
        proposal = _proposal_id(system.run(
            f"ecosystem {FIXTURE_COMPONENT} propose high medium brighter summaries|clearer answers"
            "|requires brain.manager=null"
        ))
        plan = system.run(f"ecosystem {FIXTURE_COMPONENT} plan {proposal}")
        assert "can proceed : yes" in plan
        assert "'brain.manager' at 'null' - yes" in plan
        assert f"governed rollback restores '{FIXTURE_COMPONENT}' to 'off'" in plan
        # The unapproved one is still open (EVALUATED) and starts from the same
        # version - the planner says running this one makes it stale.
        assert f"running this makes {refused} stale" in plan
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get(FIXTURE_COMPONENT) is None  # planning wrote nothing

        # EXECUTE + VERIFY + OPERATE (live, same session, no reload).
        ran = system.run(f"ecosystem {FIXTURE_COMPONENT} run {proposal} yes")
        assert ran.startswith("OK:"), ran
        assert "Live now: the running session reads this governed value on every turn." in ran
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get(FIXTURE_COMPONENT) == "high"
        assert _operate(system) == "lumenweave focus: high"

        # PERSIST -> genuine REBUILD -> RELOAD -> OPERATE.
        rebuilt = _System(isolated)
        _load_extension(rebuilt.manager)
        assert f"{FIXTURE_COMPONENT} = high" in rebuilt.run("ecosystem")
        assert f"{proposal}: active" in rebuilt.run(f"ecosystem {FIXTURE_COMPONENT}")
        assert "requires: brain.manager=null" in rebuilt.run(f"ecosystem {FIXTURE_COMPONENT}")
        assert _operate(rebuilt) == "lumenweave focus: high"
        reloaded = load_evolution_state(cc.DEFAULT_LEDGER_PATH)
        assert reloaded.proposal(proposal).requirements() == {"brain.manager": "null"}
        assert reloaded.proposal(proposal).provenance == "control-center"

        # ROLLBACK, re-read, reflected in the Control Center and in behaviour.
        rolled = rebuilt.run(f"ecosystem {FIXTURE_COMPONENT} rollback {proposal} yes")
        assert rolled.startswith("OK:"), rolled
        assert _operate(rebuilt) == "lumenweave focus: off"
        assert f"{proposal}: rolled_back" in rebuilt.run(f"ecosystem {FIXTURE_COMPONENT}")
        assert "active -> rolled_back" in rebuilt.run(f"ecosystem history {FIXTURE_COMPONENT}")

        # ... and the rollback survives another genuine rebuild.
        again = _System(isolated)
        _load_extension(again.manager)
        assert f"{FIXTURE_COMPONENT} = off" in again.run("ecosystem")
        assert _operate(again) == "lumenweave focus: off"

        # REMOVE: without its handler the category is gone - refused, never guessed.
        H._HANDLERS[:] = [h for h in H._HANDLERS if h is not lumenweave]
        after_removal = _System(isolated)
        listing = after_removal.run("ecosystem")
        # Gone from what is *installed* - but the ledger still holds a record of
        # it, and saying so is the point of the record-vs-reality section rather
        # than a leak: a component that was governed and is now absent is drift,
        # and drift is reported, never quietly dropped (Phase 11 closure).
        installed, _, drift = listing.partition("RECORD vs REALITY")
        assert FIXTURE_COMPONENT not in installed
        assert f"{FIXTURE_COMPONENT}: recorded 'off', but nothing by this id is installed now" in drift
        assert after_removal.run(f"ecosystem {FIXTURE_COMPONENT} propose low low r|b").startswith("no such component")

        # No production source change, no production residue.
        assert _source_status() == source_before
        assert (_digest(production_config), _digest(production_ledger)) == production_before
        _fixture_is_genuinely_unseen()


# ---------------------------------------------------------------------------
# 2. The Governed Upgrade Executor under failure
# ---------------------------------------------------------------------------


class _ScriptedHandler(H.EvolutionHandler):
    """A category whose behaviour each test scripts - it keeps its state in
    the governed config, like every built-in configuration handler."""

    def __init__(self, prefix, *, apply_writes=True, apply_ok=True, apply_raises=False,
                 rollback_writes=True, rollback_ok=True):
        self.prefix = prefix
        self.apply_writes, self.apply_ok, self.apply_raises = apply_writes, apply_ok, apply_raises
        self.rollback_writes, self.rollback_ok = rollback_writes, rollback_ok

    def applies_to(self, component_id):
        return component_id.startswith(self.prefix)

    def initial_version(self, component_id):
        return "0"

    def default_version(self, component_id):
        return "0"

    def _write(self, context, component_id, value):
        save_governed_config(load_governed_config(context.config_path).with_value(component_id, value), context.config_path)

    def apply(self, proposal, *, context):
        if self.apply_writes:
            self._write(context, proposal.target, proposal.proposed_version)
        if self.apply_raises:
            raise RuntimeError("the vendor fell over half way")
        return H.HandlerResult(self.apply_ok, "applied" if self.apply_ok else "apply failed")

    def rollback(self, proposal, *, context):
        if self.rollback_writes:
            self._write(context, proposal.target, proposal.current_version)
        return H.HandlerResult(self.rollback_ok, "restored" if self.rollback_ok else "could not restore")


@pytest.fixture
def engine(tmp_path):
    registered = []

    def register(handler):
        H.register_handler(handler)
        registered.append(handler)
        return handler

    paths = dict(
        ledger_path=tmp_path / "state.json", config_path=tmp_path / "config.json",
        accounts_path=tmp_path / "accounts.json", staging_dir=tmp_path / "staging", backups_dir=tmp_path / "backups",
    )
    yield register, paths
    H._HANDLERS[:] = [h for h in H._HANDLERS if h not in registered]


def _propose(state, component_id, version, paths, **extra):
    result = ee.propose(state, component_id=component_id, proposed_version=version, proposed_change="c",
                        reason="r", expected_benefit="b", ledger_path=paths["ledger_path"], **extra)
    assert result.success, result.reason
    return result


def _run(state, proposal_id, paths, approved=True):
    run_paths = {k: v for k, v in paths.items()}
    return ee.run(state, proposal_id=proposal_id, approved=approved, settings=None, **run_paths)


def _rollback(state, proposal_id, paths, approved=True):
    return ee.rollback(state, proposal_id=proposal_id, approved=approved, settings=None, **paths)


def _value(paths, component_id):
    return load_governed_config(paths["config_path"]).get(component_id)


class TestTheExecutorVerifiesTheRealState:
    def test_a_handler_that_only_reports_success_is_never_activated(self, engine):
        register, paths = engine
        register(_ScriptedHandler("probeliar.", apply_writes=False))
        proposed = _propose(EvolutionState(), "probeliar.x", "1", paths)
        result = _run(proposed.state, proposed.proposal.proposal_id, paths)
        assert not result.success
        assert result.proposal.status == EvolutionStatus.FAILED
        assert "reported success, but the real state" in result.reason
        assert result.state.ledger.version_of("probeliar.x") != "1"  # the ledger never claims it
        assert not load_evolution_state(paths["ledger_path"]).ledger.is_proposal_active(proposed.proposal.proposal_id)

    def test_a_handler_exception_is_recorded_and_its_half_done_write_undone(self, engine):
        register, paths = engine
        register(_ScriptedHandler("probecrash.", apply_raises=True))
        proposed = _propose(EvolutionState(), "probecrash.x", "1", paths)
        result = _run(proposed.state, proposed.proposal.proposal_id, paths)
        assert not result.success
        assert "RuntimeError: the vendor fell over half way" in result.reason
        assert "undone: the real state is back at '0'" in result.reason
        assert _value(paths, "probecrash.x") == "0"
        persisted = load_evolution_state(paths["ledger_path"])
        assert persisted.proposal(proposed.proposal.proposal_id).status == EvolutionStatus.FAILED
        assert any("vendor fell over" in entry.detail for entry in persisted.history)

    def test_a_refused_apply_writes_nothing_at_all(self, engine):
        register, paths = engine
        register(_ScriptedHandler("proberefuse.", apply_writes=False, apply_ok=False))
        proposed = _propose(EvolutionState(), "proberefuse.x", "1", paths)
        result = _run(proposed.state, proposed.proposal.proposal_id, paths)
        assert "Nothing was changed" in result.reason
        assert not paths["config_path"].exists()

    def test_a_component_changed_outside_governance_is_refused_before_any_write(self, engine):
        register, paths = engine
        register(_ScriptedHandler("probedrift."))
        proposed = _propose(EvolutionState(), "probedrift.x", "1", paths)
        save_governed_config(load_governed_config(paths["config_path"]).with_value("probedrift.x", "7"), paths["config_path"])
        result = _run(proposed.state, proposed.proposal.proposal_id, paths)
        assert not result.success
        assert "changed outside governance" in result.reason
        assert _value(paths, "probedrift.x") == "7"


class TestARecordThatDisagreesWithRealityIsFound:
    def test_a_change_recorded_active_whose_value_is_gone_is_detected_and_recoverable(self, isolated):
        """Found by runtime verification against a copy of real data: the
        ledger recorded the conversation turn-count ACTIVE at 12 while the
        governed config held nothing, so the running system used its default,
        6. Now the plan names it, a new change is refused before any write,
        and rolling the stale record back is the governed way out."""
        system = _System(isolated)
        component = "capability.general_conversation"
        stale = _proposal_id(system.run(f"ecosystem {component} propose 12 low r|b"))
        assert system.run(f"ecosystem {component} run {stale} yes").startswith("OK:")
        cc.DEFAULT_CONFIG_PATH.write_text('{"schema_version": 1, "data": {"values": {}}}', encoding="utf-8")

        fresh = _proposal_id(system.run(f"ecosystem {component} propose 9 low r|b"))
        plan = system.run(f"ecosystem {component} plan {fresh}")
        assert "can proceed : no" in plan and "the real state of 'capability.general_conversation' is '6'" in plan
        refused = system.run(f"ecosystem {component} run {fresh} yes")
        assert refused.startswith("FAILED:") and "the record and reality disagree" in refused
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get(component) is None

        assert system.run(f"ecosystem {component} rollback {stale} yes").startswith("OK:")
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get(component) == "6"
        again = _proposal_id(system.run(f"ecosystem {component} propose 9 low r|b"))
        assert system.run(f"ecosystem {component} run {again} yes").startswith("OK:")


class TestASurfaceGrantRollsBackToUndeclared:
    def test_granting_a_never_declared_surface_kind_and_rolling_it_back(self, isolated):
        """Rolling back to "undeclared" removes the governed value; the engine
        confirms that absence as the restored state rather than reporting a
        rollback that did not land. (Added after the mutation campaign.)"""
        from src.control_center.interfaces import surface_component_id

        system = _System(isolated)
        component = surface_component_id(system.manager.own_skills.name, "probeboard")
        proposal = _proposal_id(system.run(f"ecosystem {component} propose implemented low r|b"))
        assert system.run(f"ecosystem {component} run {proposal} yes").startswith("OK:")
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get(component) == "implemented"
        rolled = system.run(f"ecosystem {component} rollback {proposal} yes")
        assert rolled.startswith("OK:"), rolled
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get(component) is None


class TestTheExecutorEnforcesCompatibility:
    def test_an_unmet_requirement_is_refused_by_the_executor_itself(self, engine):
        """Not only the planner: running a change whose compatibility
        requirement does not hold is rejected before anything is applied.
        (Added after the mutation campaign showed only the planner was
        tested.)"""
        register, paths = engine
        register(_ScriptedHandler("probecompat."))
        state = EvolutionState().with_ledger(EvolutionLedger(current_versions={"probecompat.base": "1"}))
        proposed = _propose(state, "probecompat.x", "1", paths, requires={"probecompat.base": "2"})
        result = _run(proposed.state, proposed.proposal.proposal_id, paths)
        assert not result.success and result.proposal.status == EvolutionStatus.REJECTED
        assert "incompatible: requires 'probecompat.base' at '2'; it is at '1'" in result.reason
        assert not paths["config_path"].exists()


class TestRollbackIsGovernedAndVerified:
    def _two_changes(self, register, paths):
        register(_ScriptedHandler("probestack."))
        first = _propose(EvolutionState(), "probestack.x", "1", paths)
        state = _run(first.state, first.proposal.proposal_id, paths).state
        second = _propose(state, "probestack.x", "2", paths)
        state = _run(second.state, second.proposal.proposal_id, paths).state
        return first.proposal.proposal_id, second.proposal.proposal_id, state

    def test_rolling_back_a_superseded_change_is_refused(self, engine):
        register, paths = engine
        first, second, state = self._two_changes(register, paths)
        result = _rollback(state, first, paths)
        assert not result.success and "a later change holds it" in result.reason
        assert _value(paths, "probestack.x") == "2"
        assert result.state.proposal(second).status == EvolutionStatus.ACTIVE

    def test_newest_first_rolls_the_whole_stack_back(self, engine):
        register, paths = engine
        first, second, state = self._two_changes(register, paths)
        state = _rollback(state, second, paths).state
        assert _value(paths, "probestack.x") == "1"
        result = _rollback(state, first, paths)
        assert result.success and _value(paths, "probestack.x") == "0"
        assert result.state.ledger.version_of("probestack.x") == "0"

    def test_a_change_an_active_one_depends_on_cannot_be_rolled_back(self, engine):
        register, paths = engine
        register(_ScriptedHandler("probedep."))
        base = _propose(EvolutionState(), "probedep.base", "1", paths)
        state = _run(base.state, base.proposal.proposal_id, paths).state
        user = _propose(state, "probedep.user", "1", paths, dependencies=(base.proposal.proposal_id,))
        state = _run(user.state, user.proposal.proposal_id, paths).state
        result = _rollback(state, base.proposal.proposal_id, paths)
        assert not result.success and "roll them back first" in result.reason
        assert _value(paths, "probedep.base") == "1"

    def test_a_rollback_that_does_not_land_leaves_the_change_active_and_the_ledger_honest(self, engine):
        register, paths = engine
        register(_ScriptedHandler("probestuck.", rollback_writes=False))
        proposed = _propose(EvolutionState(), "probestuck.x", "1", paths)
        state = _run(proposed.state, proposed.proposal.proposal_id, paths).state
        result = _rollback(state, proposed.proposal.proposal_id, paths)
        assert not result.success and "rollback did not land" in result.reason
        assert result.proposal.status == EvolutionStatus.ACTIVE
        assert result.state.ledger.version_of("probestuck.x") == "1" == _value(paths, "probestuck.x")
        assert load_evolution_state(paths["ledger_path"]).ledger.is_proposal_active(proposed.proposal.proposal_id)


class TestAProvidersDefaultModelSurvivesARollback:
    def test_the_default_model_label_is_never_written_back_as_a_model(self, isolated, monkeypatch):
        """Found by this campaign: rolling back a model change on an account
        that pinned no model wrote the inventory's display label,
        '(default model)', into the account as its model name."""
        from src.services.credentials.account_store_file import load_credential_store

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback-for-specialists")
        system = _System(isolated, provider="anthropic")
        cc.run_provider_command(system.centre, "llm_provider add anthropic work sk-probe-1")
        assert "anthropic:work:(default model)" in system.run("ecosystem provider.llm_provider")
        proposal = _proposal_id(system.run("ecosystem provider.llm_provider propose anthropic:work:probe-model-2 low r|b"))
        assert system.run(f"ecosystem provider.llm_provider run {proposal} yes").startswith("OK:")
        assert load_credential_store(cc.DEFAULT_ACCOUNTS_PATH).active("anthropic").model == "probe-model-2"
        assert system.run(f"ecosystem provider.llm_provider rollback {proposal} yes").startswith("OK:")
        assert load_credential_store(cc.DEFAULT_ACCOUNTS_PATH).active("anthropic").model is None


# ---------------------------------------------------------------------------
# 3. The Upgrade Planner
# ---------------------------------------------------------------------------


def _p(pid, target, current="1", proposed="2", status=EvolutionStatus.PROPOSED, **extra):
    return EvolutionProposal(proposal_id=pid, target=target, current_version=current, proposed_version=proposed,
                             proposed_change="c", reason="r", expected_benefit="b", status=status, **extra)


def _plan(proposal, proposals, ledger, **extra):
    extra.setdefault("has_handler", True)
    extra.setdefault("can_roll_back", True)
    return plan_proposal(proposal, ledger=ledger, proposals={p.proposal_id: p for p in proposals}, **extra)


class TestThePlannerIsDeterministicAndHonest:
    def test_open_prerequisites_are_ordered_deepest_first(self):
        a = _p("a", "x.one")
        b = _p("b", "x.two", dependencies=("a",))
        c = _p("c", "x.three", dependencies=("b",))
        plan = _plan(c, (a, b, c), EvolutionLedger(current_versions={"x.one": "1", "x.two": "1", "x.three": "1"}))
        assert plan.order == ("a", "b", "c")
        assert not plan.can_proceed and "run a, b first (in that order)" in plan.blockers

    def test_a_circular_dependency_blocks(self):
        a = _p("a", "x.one", dependencies=("b",))
        b = _p("b", "x.two", dependencies=("a",))
        plan = _plan(a, (a, b), EvolutionLedger())
        assert any("circular dependency" in blocker for blocker in plan.blockers)

    def test_an_unmet_or_unknowable_compatibility_requirement_blocks(self):
        ledger = EvolutionLedger(current_versions={"x.one": "1", "y.dep": "3"})
        unmet = _plan(_p("a", "x.one", requires={"y.dep": "4"}), (), ledger)
        assert "requires 'y.dep' at '4'; it is at '3'" in unmet.blockers
        unknown = _plan(_p("a", "x.one", requires={"z.none": "1"}), (), ledger)
        assert any("cannot be established" in blocker for blocker in unknown.blockers)
        met = _plan(_p("a", "x.one", requires={"y.dep": "3"}), (), ledger)
        assert met.can_proceed

    def test_stale_affected_drift_and_rollback_are_reported(self):
        holder = _p("h", "x.base", current="0", proposed="1", status=EvolutionStatus.ACTIVE)
        dependent = _p("d", "x.user", status=EvolutionStatus.ACTIVE, dependencies=("h",))
        rival = _p("r", "x.base", current="1", proposed="3")
        mine = _p("m", "x.base", current="1", proposed="2")
        ledger = EvolutionLedger(current_versions={"x.base": "1", "x.user": "2"}, active_proposals=("h", "d"))
        plan = _plan(mine, (holder, dependent, rival, mine), ledger, observed_version="1")
        assert plan.can_proceed
        assert "running this makes r stale (they start from the same version)" in plan.warnings
        assert "x.user" in plan.affected
        drifted = _plan(mine, (mine,), ledger, observed_version="9")
        assert any("changed outside governance" in blocker for blocker in drifted.blockers)
        no_undo = _plan(mine, (mine,), ledger, can_roll_back=False)
        assert "no rollback is available for this change" in no_undo.warnings

    def test_a_target_nobody_handles_cannot_proceed(self):
        plan = _plan(_p("a", "nobody.handles"), (), EvolutionLedger(), has_handler=False)
        assert not plan.can_proceed

    def test_planning_through_the_engine_writes_nothing(self, engine):
        register, paths = engine
        register(_ScriptedHandler("probeplan."))
        proposed = _propose(EvolutionState(), "probeplan.x", "1", paths)
        ledger_bytes = paths["ledger_path"].read_bytes()
        plan = ee.plan(proposed.state, proposal_id=proposed.proposal.proposal_id, settings=None,
                       accounts_path=paths["accounts_path"], config_path=paths["config_path"])
        assert plan.can_proceed
        assert paths["ledger_path"].read_bytes() == ledger_bytes
        assert not paths["config_path"].exists()


# ---------------------------------------------------------------------------
# 4. Persistence, compatibility and unreadable state
# ---------------------------------------------------------------------------


class TestPersistedStateIsCompatibleAndNeverSilentlyRewritten:
    def test_a_pre_phase_11_ledger_still_loads(self, tmp_path):
        path = tmp_path / "state.json"
        legacy = {"schema_version": 1, "data": {
            "current_versions": {"a.b": "1"}, "active_proposals": [],
            "proposals": {"a.b-1": {
                "proposal_id": "a.b-1", "target": "a.b", "current_version": "1", "proposed_version": "2",
                "proposed_change": "c", "reason": "r", "expected_benefit": "b", "risk_level": "low",
                "dependencies": [], "status": "proposed"}},
            "history": []}}
        path.write_text(json.dumps(legacy), encoding="utf-8")
        loaded = load_evolution_state(path).proposal("a.b-1")
        assert loaded.requires == () and loaded.provenance == ""

    def test_requirements_and_provenance_round_trip(self, tmp_path):
        path = tmp_path / "state.json"
        state = EvolutionState().with_proposal(_p("a", "x.one", requires={"y.dep": "3"}, provenance="a person"))
        save_evolution_state(state, path)
        loaded = load_evolution_state(path).proposal("a")
        assert loaded.requirements() == {"y.dep": "3"} and loaded.provenance == "a person"
        assert not (tmp_path / "state.json.tmp").exists()

    @pytest.mark.parametrize("content", [
        '{"schema_version": 1, "data": {"proposals": {"x": {"status": "from_the_future"}}}}',
        "{ not json",
    ])
    def test_an_unreadable_ledger_is_reported_and_left_untouched(self, isolated, content):
        cc.DEFAULT_LEDGER_PATH.write_text(content, encoding="utf-8")
        before = cc.DEFAULT_LEDGER_PATH.read_bytes()
        with pytest.raises(MigrationUnavailable):
            load_evolution_state(cc.DEFAULT_LEDGER_PATH)
        output = _System(isolated).run("ecosystem")
        assert output.startswith("FAILED: the evolution ledger cannot be read")
        assert cc.DEFAULT_LEDGER_PATH.read_bytes() == before

    @pytest.mark.parametrize("content", [
        '{"schema_version": 99, "data": {"values": {"capability.general_conversation": "8"}}}',
        "{ damaged",
    ])
    def test_an_unreadable_governed_config_is_never_overwritten(self, isolated, content):
        cc.DEFAULT_CONFIG_PATH.write_text(content, encoding="utf-8")
        before = cc.DEFAULT_CONFIG_PATH.read_bytes()
        config = load_governed_config(cc.DEFAULT_CONFIG_PATH)
        assert config.values == {} and config.problem
        with pytest.raises(GovernedConfigUnreadable):
            save_governed_config(config.with_value("x", "y"), cc.DEFAULT_CONFIG_PATH)

        system = _System(isolated)
        assert "WARNING:" in system.run("ecosystem")
        proposal = _proposal_id(system.run("ecosystem capability.general_conversation propose 9 low r|b"))
        result = system.run(f"ecosystem capability.general_conversation run {proposal} yes")
        assert result.startswith("FAILED:") and "GovernedConfigUnreadable" in result
        assert cc.DEFAULT_CONFIG_PATH.read_bytes() == before

    def test_a_fresh_config_is_never_saved_over_an_unreadable_file(self, tmp_path):
        """A writer that never loaded the damaged file (a fresh config) still
        cannot replace it. (Added after the mutation campaign showed only the
        loaded-config guard was tested.)"""
        from src.services.evolution.governed_config import GovernedConfig

        path = tmp_path / "config.json"
        path.write_text('{"schema_version": 99, "data": {"values": {"kept": "yes"}}}', encoding="utf-8")
        before = path.read_bytes()
        with pytest.raises(GovernedConfigUnreadable):
            save_governed_config(GovernedConfig().with_value("x", "y"), path)
        assert path.read_bytes() == before

    def test_governed_config_writes_are_atomic(self, tmp_path):
        path = tmp_path / "config.json"
        save_governed_config(load_governed_config(path).with_value("a", "1"), path)
        assert load_governed_config(path).get("a") == "1"
        assert not (tmp_path / "config.json.tmp").exists()


# ---------------------------------------------------------------------------
# 5. Brain compatibility
# ---------------------------------------------------------------------------


class TestEvolutionIsIndependentOfTheBrain:
    @pytest.mark.parametrize("brain", ["null", "llm"])
    def test_governed_evolution_works_in_half_brain_and_with_an_unusable_full_brain(self, isolated, brain):
        """"llm" with no vendor behind it is a Full Brain whose every
        reasoning request fails - evolution is deterministic governance and
        must work, and leave the Brain exactly as it was, either way."""
        system = _System(isolated, brain=brain)
        brain_before = (system.runtime.settings.brain, type(system.manager.brain).__name__)
        proposal = _proposal_id(system.run("ecosystem capability.general_conversation propose 8 low r|b"))
        assert system.run(f"ecosystem capability.general_conversation plan {proposal}").count("can proceed : yes") == 1
        assert system.run(f"ecosystem capability.general_conversation run {proposal} yes").startswith("OK:")
        assert system.run(f"ecosystem capability.general_conversation rollback {proposal} yes").startswith("OK:")
        assert (system.runtime.settings.brain, type(system.manager.brain).__name__) == brain_before

    def test_no_brain_agent_or_capability_module_can_reach_the_executor(self):
        """The Brain can neither run nor roll back an evolution: nothing it
        can reach imports the engine or its handlers - approval stays the
        owner's, through the Control Center."""
        forbidden = ("src.services.evolution.ecosystem_evolution", "src.services.evolution.handlers")
        for package in ("manager", "agents", "conversation", "channels"):
            for path in (REPO_ROOT / "src" / package).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(forbidden):
                        pytest.fail(f"{path} imports {node.module}")
                    if isinstance(node, ast.Import) and any(a.name.startswith(forbidden) for a in node.names):
                        pytest.fail(f"{path} imports the evolution engine")

    def test_brain_prompts_carry_no_extension_metadata(self):
        for relative in ("src/manager/brain_prompts.py", "src/manager/prompts.py", "src/agents/brain_prompts.py"):
            text = (REPO_ROOT / relative).read_text(encoding="utf-8").lower()
            for word in ("evolution", "governed_config", "ledger", "proposal"):
                assert word not in text, f"{relative} mentions {word}"


# ---------------------------------------------------------------------------
# 6. The Control Center surface
# ---------------------------------------------------------------------------


class TestTheControlCenterReachesEveryUserManagedMechanism:
    def test_plan_is_read_only_and_needs_no_confirmation(self):
        assert cc.classify_administrative_write("ecosystem x.y plan x.y-1") == (False, "")

    def test_an_approved_system_upgrade_is_confirmed_and_audited(self):
        is_write, description = cc.classify_administrative_write("ecosystem system upgrade 0.2.0 yes")
        assert is_write and "store migration" in description
        assert cc.classify_administrative_write("ecosystem system upgrade 0.2.0 no") == (False, "")

    def test_the_served_door_gates_an_approved_run_behind_a_token(self, isolated):
        from scripts.serve_ui import LiveSystem, route_command
        import threading

        system = _System(isolated)
        live = LiveSystem(runtime=system.runtime, manager=system.manager, channel=system.channel,
                          conversation=system.conversation, centre=system.centre, lock=threading.Lock())
        proposal = _proposal_id(system.run("ecosystem capability.general_conversation propose 8 low r|b"))
        _, planned, _ = route_command(f"ecosystem capability.general_conversation plan {proposal}", None, live)
        assert b"can proceed" in planned
        command = f"ecosystem capability.general_conversation run {proposal} yes"
        _, pending, _ = route_command(command, None, live)
        token = json.loads(pending)["confirm_token"]
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get("capability.general_conversation") is None
        _, done, _ = route_command(command, token, live)
        assert b"OK:" in done
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get("capability.general_conversation") == "8"

    def test_the_served_panel_offers_plan_and_the_system_manifest(self):
        page = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        assert '<option value="plan">' in page
        assert 'command = "ecosystem " + component + " plan " + args' in page
        assert 'data-cc-verb="ecosystem system"' in page

    def test_live_activation_is_the_handlers_declaration_not_a_category_name(self, isolated, engine):
        register, _ = engine

        class _OnRead(_ScriptedHandler):
            def activation(self, component_id):
                return H.ACTIVATION_ON_READ

            def governed_components(self, *, config):
                return (ComponentSnapshot(component_id="probeshown.x", category="probeshown", label="l",
                                          version=str(config.get("probeshown.x") or "0")),)

        register(_OnRead("probeshown."))
        system = _System(isolated)
        proposal = _proposal_id(system.run("ecosystem probeshown.x propose 1 low r|b"))
        assert "described from the governed config on every read" in system.run(f"ecosystem probeshown.x run {proposal} yes")

    def test_propose_rejects_what_it_does_not_understand(self, isolated):
        system = _System(isolated)
        assert "is not understood" in system.run("ecosystem capability.general_conversation propose 8 low r|b|nonsense")
        assert "cannot require its own target" in system.run(
            "ecosystem capability.general_conversation propose 8 low r|b|requires capability.general_conversation=6")


# ---------------------------------------------------------------------------
# 7. Current entities are instances, not architecture
# ---------------------------------------------------------------------------


class TestBackendsDeclareTheirOwnFacts:
    def test_a_registered_keyless_reasoning_backend_is_reported_keyless(self, tmp_path):
        from src.providers import factory as llm_factory
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        llm_factory.register_provider("probelocal", lambda settings, env, account=None: None, requires_credential=False)
        llm_factory.register_provider("probecloud", lambda settings, env, account=None: None)
        try:
            adapter = build_ecosystem_registry(_settings(tmp_path)).adapter_for("llm_provider")
            keyed = {d.key: d.requires_credential for d in adapter.available()}
            assert keyed["probelocal"] is False and keyed["probecloud"] is True
        finally:
            for name in ("probelocal", "probecloud"):
                llm_factory._BUILDERS.pop(name, None)
                llm_factory._KEYLESS.discard(name)

    def test_a_registered_connection_with_adapter_versions_is_governed_with_no_handler_change(self, isolated):
        from src.providers import connection_factory
        from src.providers.null_connection import NullConnection

        connection_factory.register_connection(
            "probeline", lambda settings, adapter_version="1": NullConnection(), adapter_versions=("1", "2"),
        )
        try:
            assert isinstance(H.handler_for("connection.probeline"), H.ConnectionAdapterHandler)
            system = _System(isolated)
            assert "connection.probeline = 1" in system.run("ecosystem")
            proposal = _proposal_id(system.run("ecosystem connection.probeline propose 2 low r|b"))
            assert system.run(f"ecosystem connection.probeline run {proposal} yes").startswith("OK:")
            from src.services.evolution.inventory import governed_adapter_versions

            assert governed_adapter_versions(load_governed_config(cc.DEFAULT_CONFIG_PATH))["probeline"] == "2"
            assert "not a known probeline adapter version" in system.run(
                "ecosystem connection.probeline run " +
                _proposal_id(system.run("ecosystem connection.probeline propose 9 low r|b")) + " yes")
        finally:
            connection_factory._BUILDERS.pop("probeline", None)
            connection_factory._ADAPTER_VERSIONS.pop("probeline", None)

    def test_a_new_self_authenticating_connection_is_described_with_no_surface_change(self, isolated):
        """The Control Center's connections listing asks each connection how
        it authenticates - it knows none by name - so a new one with its own
        flow is listed, refused a typed credential, and validated correctly."""
        from src.providers import connection_factory
        from src.providers.connection import SelfAuthentication
        from src.providers.null_connection import NullConnection

        class _Paired(NullConnection):
            @property
            def name(self):
                return "probepair"

            def self_authentication(self):
                return SelfAuthentication(method="device pairing - paired on the device itself",
                                          connect="pair it from the device's own settings",
                                          disconnect="unpairing it on the device")

        connection_factory.register_connection("probepair", lambda settings, adapter_version="1": _Paired())
        try:
            system = _System(isolated)
            listing = cc.run_connections_command(system.centre, "")
            assert "probepair (device pairing - paired on the device itself):" in listing
            refused = cc.run_connections_command(system.centre, "probepair connect acct secret")
            assert "not a typed credential" in refused and "pair it from the device's own settings" in refused
            from src.services.credentials.account_store_file import load_credential_store

            assert load_credential_store(cc.DEFAULT_ACCOUNTS_PATH).get("probepair", "acct") is None
        finally:
            connection_factory._BUILDERS.pop("probepair", None)

    def test_a_new_visual_environment_is_versioned_by_the_manifest_with_no_code_change(self, tmp_path):
        from src.control_center.manifest import visual_environment_versions

        page = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        before = {v.surface: v for v in visual_environment_versions(REPO_ROOT / "ui" / "index.html")}
        # The five surfaces the manifest always reported keep their ids.
        assert {"agent:core", "agent:polygon", "brain", "memory", "room"} <= set(before)
        addition = (
            "\n  const LUMENWEAVE_VISUAL_ENVIRONMENTS = {\n"
            '    halo: { id: "lumenweave-halo", version: 4, build: buildHalo },\n'
            "  };\n"
            '  const LUMENWEAVE_FIELD_VISUAL_ENVIRONMENT = { id: "lumenweave-field", version: 2, build: buildField };\n'
        )
        ui = tmp_path / "index.html"
        ui.write_text(page + addition, encoding="utf-8")
        after = {v.surface: v for v in visual_environment_versions(ui)}
        assert after["lumenweave:halo"].id == "lumenweave-halo" and after["lumenweave:halo"].version == 4
        assert after["lumenweave_field"].version == 2
        assert {k: v for k, v in after.items() if not k.startswith("lumenweave")} == before

    def test_the_extension_layer_names_no_current_backend(self):
        """Every backend any factory offers today, read live - so this list is
        never itself a hardcoded catalogue - must not appear as a string
        constant in the Phase 11 extension layer. ``null``/``none`` are the
        architecture's own honest-absence sentinels, not backends."""
        from src.providers.avatar_factory import available_avatar_providers
        from src.providers.connection_factory import available_connection_providers
        from src.providers.factory import available_providers
        from src.providers.media.factory import available_video_generation_backends, available_video_understanding_backends
        from src.providers.speech_factory import available_speech_providers

        backends = set(available_providers()) | set(available_connection_providers()) | set(available_speech_providers()) \
            | set(available_video_generation_backends()) | set(available_video_understanding_backends()) \
            | set(available_avatar_providers())
        backends -= {"null", "none", "default"}
        modules = ("src/evolution.py", "src/services/evolution/handlers.py", "src/services/evolution/ecosystem_evolution.py",
                   "src/services/evolution/inventory.py", "src/services/evolution/governed_config.py",
                   "src/services/evolution/ledger_store.py", "src/services/ecosystem/registry_factory.py")
        for relative in modules:
            tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                          and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    assert node.value.strip().lower() not in backends, f"{relative} names the backend {node.value!r}"
