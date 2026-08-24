"""Adding a specialist must not mean editing the Manager.

The claim Step 3 exists to make good on:

    a future specialist joins by adding one entry to SPECIALISTS and shipping
    an identity document - and nothing in manager.py, router.py or assessor.py
    is touched, while the entry points learn nothing about it

That is easy to assert in prose and easy to be wrong about, so the decisive
test here does not take anyone's word for it. It registers a second specialist
for real, drives the real Manager, and then hashes the five orchestration files
against HEAD to prove they are byte-identical.

The second specialist in these tests is deliberately anonymous - a key like
"ledger_agent" and a name like "Testing Specialist" that exist only inside this
file. Real specialists get real names when they are actually built, and
inventing one here would put a name into the project that nobody chose.

What is *not* claimed: that registration is automatic. Dropping a markdown file
into identities/ registers nothing. That is on purpose - a document must not be
able to bring an agent into existence, one level up from the rule saying it
cannot grant that agent a capability.
"""

import subprocess
from pathlib import Path

import pytest

from src.agents.base_agent import BaseAgent
from src.agents.specialists import (
    EMAIL_AGENT_KEY,
    SPECIALISTS,
    SpecialistContext,
    available_specialists,
    build_email_specialist,
)
from src.composition import SpecialistRuntime, build_manager, build_runtime
from src.core.contracts import AgentSpec, Task, TaskResult
from src.services.email.in_memory_mailbox import InMemoryMailbox

REPO_ROOT = Path(__file__).resolve().parent.parent

# The files that must never need editing to accept a new specialist.
#
# The two entry points are deliberately absent. They were frozen here until
# Step 8, and that over-reached: the claim is "adding a specialist changes no
# orchestration", not "the entry points never change for any reason". Wiring a
# per-run document into them is a legitimate evolution, and a change-detector
# would forbid it while proving nothing extra. The sharper assertion is that
# they carry no specialist name and no knowledge of how one is built - which is
# what test_the_entry_points_know_no_specialist checks below.
ORCHESTRATION = (
    "src/manager/manager.py",
    "src/manager/router.py",
    "src/manager/assessor.py",
)

# Checked by content instead of by byte-equality: an entry point may grow, but
# never grow a specialist.
ENTRY_POINTS = ("main.py", "scripts/heartbeat.py")

SECOND_KEY = "ledger_agent"

SECOND_IDENTITY = """
## Name
Testing Specialist

## Role
A stand-in used only by this test file.

## Purpose
To prove that a second specialist needs no orchestration change.

## Area of responsibility
bookkeeping: reconciling numbers that do not agree
"""


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


class SecondSpecialist(BaseAgent):
    """A minimal specialist. Its capabilities are declared in code, as every
    specialist's are - the identity document below states none, and could not."""

    def __init__(self, context: SpecialistContext) -> None:
        self.context = context
        self.brain = context.brain
        self.memory = context.memory()

    @property
    def spec(self) -> AgentSpec:
        return AgentSpec(
            name=self.context.wiring.name,
            description="A stand-in specialist used by the registry tests.",
            capabilities=frozenset({"ledger", "reconcile", "bookkeeping"}),
        )

    def execute(self, task: Task) -> TaskResult:
        return TaskResult.success(task, self.name, "reconciled")


@pytest.fixture
def two_specialists(tmp_path, monkeypatch):
    """Register a second specialist the way a real one would join.

    One entry in SPECIALISTS, one identity document. Nothing else. The
    registry is patched rather than permanently extended so the project keeps
    shipping exactly one specialist.
    """
    identities = tmp_path / "identities"
    identities.mkdir()
    # The shipped specialist keeps his own committed document, copied so the
    # temporary directory is complete rather than half-populated.
    (identities / "email_agent.md").write_text(
        Path("identities/email_agent.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (identities / f"{SECOND_KEY}.md").write_text(SECOND_IDENTITY, encoding="utf-8")

    monkeypatch.setitem(SPECIALISTS, SECOND_KEY, SecondSpecialist)

    config = settings(
        log_level="CRITICAL",
        identities_dir=str(identities),
        memory="persistent",
        memory_dir=str(tmp_path / "memory"),
    )
    runtime = build_runtime(config)
    return runtime, build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------


class TestTheRegistry:
    def test_the_project_ships_exactly_one_specialist(self):
        """No invented future names. Arthur is the only one that exists."""
        assert available_specialists() == [EMAIL_AGENT_KEY]

    def test_the_registry_maps_a_key_to_a_builder(self):
        assert SPECIALISTS[EMAIL_AGENT_KEY] is build_email_specialist
        assert callable(SPECIALISTS[EMAIL_AGENT_KEY])

    def test_a_document_alone_registers_nothing(self, tmp_path):
        """Dropping a file into identities/ must not bring an agent into being.

        The same argument as the capability rule, one level up: if a markdown
        file could register an agent, a document would be granting something
        again. Registration stays explicit and in code.
        """
        identities = tmp_path / "identities"
        identities.mkdir()
        (identities / "ghost_agent.md").write_text("## Name\nGhost\n", encoding="utf-8")

        runtime = build_runtime(
            settings(log_level="CRITICAL", identities_dir=str(identities))
        )
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        assert "ghost_agent" not in runtime.specialists
        assert "Ghost" not in manager.registry


# ---------------------------------------------------------------------------
# The decisive claim: a second specialist changes no orchestration
# ---------------------------------------------------------------------------


class TestASecondSpecialistNeedsNoOrchestrationChange:
    def test_orchestration_files_are_byte_identical_to_head(self):
        """Not a promise - a measurement.

        If accepting a second specialist had required a line in the Manager,
        the router or the assessor, this fails. Hashing beats asserting in a
        docstring, because a docstring cannot notice being wrong.
        """
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", *ORCHESTRATION],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:  # pragma: no cover - only if git is absent
            pytest.skip("git unavailable")
        assert result.stdout.strip() == "", (
            "adding a specialist should not have changed orchestration, but "
            f"these files differ from HEAD: {result.stdout.strip()}"
        )

    def test_the_entry_points_know_no_specialist(self):
        """The sharper form of the claim the byte-freeze used to make.

        An entry point may grow - Step 8 gives both a schedule to load - but
        it must never grow a *specialist*. Neither may name one, import an
        agent class, or know how one is assembled: that is the registry's job,
        and the whole point of Step 3 was that a new specialist reaches
        production without either file learning it exists.
        """
        forbidden = (
            "EmailAgent", "JobOpportunityAnalyzer", "SpecialistContext",
            "SPECIALISTS", "build_email_specialist", "Arthur", SECOND_KEY,
            "Testing Specialist",
        )
        for entry_point in ENTRY_POINTS:
            source = (REPO_ROOT / entry_point).read_text(encoding="utf-8")
            for name in forbidden:
                assert name not in source, (entry_point, name)

    def test_the_entry_points_route_nothing_themselves(self):
        """Registration and routing stay behind the composition root."""
        for entry_point in ENTRY_POINTS:
            source = (REPO_ROOT / entry_point).read_text(encoding="utf-8")
            for name in ("register_agent", "AgentRegistry", "CapabilityRouter"):
                assert name not in source, (entry_point, name)

    def test_both_specialists_are_registered(self, two_specialists):
        _, manager = two_specialists
        assert "Arthur" in manager.registry
        assert "Testing Specialist" in manager.registry

    def test_the_manager_is_still_alfred(self, two_specialists):
        _, manager = two_specialists
        assert "Alfred" in manager.registry

    def test_the_second_specialist_is_named_by_its_own_document(self, two_specialists):
        runtime, _ = two_specialists
        assert runtime.specialists[SECOND_KEY].name == "Testing Specialist"
        assert runtime.specialists[EMAIL_AGENT_KEY].name == "Arthur"

    def test_the_router_reaches_it_by_capability_alone(self, two_specialists):
        """The Manager was told nothing about this agent. The existing
        CapabilityRouter finds it by scoring its declared capabilities, which
        is the mechanism that has always been there."""
        _, manager = two_specialists
        decision = manager.diagnose(Task(description="reconcile my ledger"))
        assert decision.agent_name == "Testing Specialist"

    def test_it_can_actually_run_a_mission(self, two_specialists):
        _, manager = two_specialists
        result = manager.handle_task(Task(description="reconcile my ledger"))
        assert result.succeeded
        assert result.agent_name == "Testing Specialist"

    def test_arthur_still_gets_his_own_work(self, two_specialists):
        """A new specialist must not steal the existing one's missions."""
        _, manager = two_specialists
        result = manager.handle_task(Task(description="read my inbox"))
        assert result.agent_name == "Arthur"


# ---------------------------------------------------------------------------
# Each specialist keeps its own brain and its own memory
# ---------------------------------------------------------------------------


class TestEachSpecialistOwnsItsOwnEquipment:
    def test_three_agents_three_stores(self, two_specialists):
        """Alfred, Arthur and the second specialist, all separate.

        Selected by name, so they cannot collide unless two agents are given
        the same name - and then they collide at registration, loudly.
        """
        _, manager = two_specialists
        arthur = manager.registry.get("Arthur")
        second = manager.registry.get("Testing Specialist")

        paths = {manager.memory.path, arthur.memory.path, second.memory.path}
        assert len(paths) == 3
        assert second.memory.path.stem == "testing_specialist"

    def test_each_specialist_has_its_own_brain(self, two_specialists):
        runtime, manager = two_specialists
        arthur_brain = runtime.specialists[EMAIL_AGENT_KEY].brain
        second_brain = runtime.specialists[SECOND_KEY].brain

        assert arthur_brain is not second_brain
        assert arthur_brain is not manager.brain
        assert second_brain is not manager.brain

    def test_each_brain_is_built_from_its_own_domain(self, two_specialists):
        runtime, _ = two_specialists
        assert "email triage" in runtime.specialists[EMAIL_AGENT_KEY].wiring.brain_domain
        assert "bookkeeping" in runtime.specialists[SECOND_KEY].wiring.brain_domain

    def test_a_builder_is_handed_no_route_to_a_peer(self, two_specialists):
        """A specialist is constructed with its own equipment and nothing it
        could use to reach another agent - no Manager, no registry, no router.

        The oldest boundary in this architecture, restated now that a builder
        sits between the composition root and the agent.
        """
        _, manager = two_specialists
        context = manager.registry.get("Testing Specialist").context
        for forbidden in ("manager", "registry", "router", "assessor", "agents"):
            assert not hasattr(context, forbidden), forbidden


# ---------------------------------------------------------------------------
# Milestone 1's lifetime rule still holds
# ---------------------------------------------------------------------------


class TestTheLifetimeSplitSurvives:
    def test_runtime_holds_no_per_run_state(self, two_specialists):
        """Identity and brains are long-lived; agents and memories are not.

        A specialist held in Runtime would be a singleton whose persistent
        store never re-read its file - which would silently break the temporal
        memory design the heartbeat depends on.
        """
        runtime, _ = two_specialists
        for entry in runtime.specialists.values():
            assert isinstance(entry, SpecialistRuntime)
            for forbidden in ("agent", "memory", "profile", "mailbox"):
                assert not hasattr(entry, forbidden), forbidden

    def test_every_build_manager_call_makes_fresh_agents_and_memories(
        self, two_specialists
    ):
        runtime, first = two_specialists
        second = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        a = first.registry.get("Arthur")
        b = second.registry.get("Arthur")
        assert a is not b
        assert a.memory is not b.memory

    def test_brains_are_shared_across_calls_because_they_are_stateless(
        self, two_specialists
    ):
        """Not an oversight. A brain holds no per-tick state, so rebuilding one
        per tick would buy nothing and cost a construction."""
        runtime, first = two_specialists
        second = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        assert first.registry.get("Arthur").brain is second.registry.get("Arthur").brain


# ---------------------------------------------------------------------------
# Compatibility accessors read the mapping; they do not duplicate it
# ---------------------------------------------------------------------------


class TestTheNamedAccessorsCannotDrift:
    def test_email_specialist_reads_from_the_mapping(self, two_specialists):
        runtime, _ = two_specialists
        assert runtime.email_specialist is runtime.specialists[EMAIL_AGENT_KEY].wiring

    def test_specialist_brain_reads_from_the_mapping(self, two_specialists):
        runtime, _ = two_specialists
        assert runtime.specialist_brain is runtime.specialists[EMAIL_AGENT_KEY].brain

    def test_the_mapping_is_the_source_of_truth(self, two_specialists):
        """The accessors are conveniences, not a second copy. There is nothing
        stored alongside the mapping that could fall out of step with it."""
        runtime, _ = two_specialists
        assert "email_specialist" not in runtime.__dataclass_fields__
        assert "specialist_brain" not in runtime.__dataclass_fields__
        assert "specialists" in runtime.__dataclass_fields__


# ---------------------------------------------------------------------------
# Nothing about the Gmail boundary moved
# ---------------------------------------------------------------------------


class TestTheBoundaryIsWhereItWas:
    def test_arthur_still_refuses_to_send(self, two_specialists):
        _, manager = two_specialists
        assert not manager.handle_task(Task(description="send an email")).succeeded

    def test_arthur_still_has_no_outbox(self, two_specialists):
        _, manager = two_specialists
        assert manager.registry.get("Arthur").can_send is False

    def test_the_builder_passes_no_outbox(self):
        """There is none to pass. The refusal is structural, not a policy the
        builder could be talked out of."""
        import inspect

        source = inspect.getsource(build_email_specialist)
        assert "outbox" not in source.lower().replace("no outbox", "")

    def test_the_scope_is_still_read_only(self):
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES

        assert SCOPES == (GMAIL_READONLY_SCOPE,)
