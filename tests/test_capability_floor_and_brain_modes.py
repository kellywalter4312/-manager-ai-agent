"""Two axes that must never merge, and a Manager who is not a router.

Step 9 closed two defects the audit measured rather than suspected.

**Alfred had no capability floor.** Every ordinary request - look something up,
explain something, write a small function - returned ``no_agent_found``. That
answer conflated "nobody is registered for this" with "the system cannot do
this", and it made the Manager refuse anything no specialist had claimed. A
specialist is a depth advantage, not the reason a Manager is incapable.

**FULL BRAIN was claimed from configuration alone.** An API key with an
exhausted credit balance reported full model reasoning, confidently, until
somebody asked a question and got nothing.

The two axes those defects sit on are independent, and this file exists mostly
to keep them that way:

    CAPABILITY   what can I do?           ToolSet membership
    BRAIN        how deeply can I think?  FULL or HALF

Neither implies the other. A Half Brain Alfred with a working tool does the
work. A Full Brain Alfred without one still cannot, and must say so rather than
reason his way to an answer he has no means to fetch.

Every subject in these tests is arbitrary on purpose. Liverpool, a film, a
company, a war, a sport: the point is that the *same* capability handles all of
them and that none of them is a capability. A test that passed only for the
subjects someone thought of would be proving the opposite of what is claimed.
"""

import pytest

from src.agents.tools.tool import Tool, ToolSpec
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task, TaskStatus
from src.manager.capabilities import (
    GENERAL_CAPABILITIES,
    CapabilityRequest,
    handled_words,
)
from src.manager.cognition import CognitiveMode, assess_cognition, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.providers.availability import Availability, AvailabilityState
from src.providers.base import Completion, LLMProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.identity import load_identity
from src.services.profile.loader import parse_profile
from src.services.schedule.loader import parse_schedule

# Arbitrary subjects. Deliberately varied, deliberately not a list anything in
# src/ knows about, and deliberately including one nobody would think to
# special-case.
ARBITRARY_SUBJECTS = (
    "Liverpool",
    "the Titanic",
    "Reacher",
    "Elon Musk",
    "World War II",
    "Nigerian history",
    "a company called Northwind",
    "the mating habits of cuttlefish",
)


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


IDENTITY = load_identity("IDENTITY.md")


def alfred(tools=None, brain=None, **kwargs):
    """A Manager with Alfred's real identity and an explicit ToolSet."""
    manager = ManagerAgent(brain=brain)
    manager.own_skills = ManagerSkills(
        IDENTITY, roster=manager.registry.all_agents, tools=tools, **kwargs
    )
    manager.registry.register(manager.own_skills)
    return manager


def assembled(tools=None, **overrides):
    from src.composition import build_manager, build_runtime

    runtime = build_runtime(settings(log_level="CRITICAL", **overrides))
    return runtime, build_manager(
        runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools
    )


class FakeSearch(Tool):
    """A stand-in for a real search tool, existing only in this file.

    It answers with the query it was given rather than inventing facts about a
    subject - the tests here are about the wiring, and a fixture that returned
    plausible-looking research would be exactly the fabrication Step 9 forbids.
    """

    def __init__(self, name="web_search", fails=False):
        self._name = name
        self._fails = fails

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description="A stand-in used by tests. Fetches nothing.",
            schema={"query": str},
        )

    def run(self, **arguments) -> str:
        if self._fails:
            raise RuntimeError("upstream refused the connection")
        return f"[{self._name}] handled: {arguments['query']}"


class Exhausted(LLMProvider):
    """Configured, keyed, reachable - and out of credit."""

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt, *, system=None):
        raise RuntimeError(
            "Your credit balance is too low to access the Anthropic API"
        )


class Working(LLMProvider):
    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt, *, system=None):
        return Completion(text="{}", provider="anthropic")


# ---------------------------------------------------------------------------
# Alfred has a capability floor
# ---------------------------------------------------------------------------


class TestAlfredIsNotAGlorifiedRouter:
    def test_an_ordinary_request_reaches_alfred_rather_than_nobody(self):
        """The defect this step closed. Before it, this was no_agent_found."""
        _, manager = assembled()
        result = manager.handle_task(Task(description="research the Titanic"))
        assert result.status is not TaskStatus.NO_AGENT_FOUND
        assert result.agent_name == "Alfred"

    def test_every_arbitrary_subject_reaches_the_same_capability(self):
        """The generality claim, stated over subjects nothing in src/ knows.

        If any of these needed its own capability, this fails - which is the
        point. The subject is data.
        """
        _, manager = assembled()
        for subject in ARBITRARY_SUBJECTS:
            result = manager.handle_task(Task(description=f"tell me about {subject}"))
            assert result.status is not TaskStatus.NO_AGENT_FOUND, subject
            assert result.agent_name == "Alfred", subject

    def test_no_capability_is_named_after_a_subject(self):
        """Capabilities are kinds of work, never subjects.

        Checked against the shipped vocabulary rather than a hand-written list,
        so a subject smuggled in later fails this without anyone remembering to
        update it.
        """
        vocabulary = handled_words()
        for subject in ARBITRARY_SUBJECTS:
            for word in subject.lower().split():
                if len(word) > 3:
                    assert word not in vocabulary, word

    def test_the_general_capabilities_are_kinds_of_work(self):
        for capability in GENERAL_CAPABILITIES:
            assert capability.kind in ("research", "coding"), capability.kind
            assert capability.tool

    def test_alfred_never_says_he_is_only_the_manager(self):
        """The sentence this whole step exists to make impossible."""
        _, manager = assembled()
        answer = (manager.handle_task(Task(description="research Liverpool")).error or "")
        for excuse in ("only the manager", "just the manager", "i am the manager"):
            assert excuse not in answer.lower()

    def test_specialist_work_still_goes_to_the_specialist(self):
        """A capability floor must not steal a specialist's domain."""
        _, manager = assembled()
        assert manager.handle_task(
            Task(description="read my inbox")
        ).agent_name == "Arthur"
        assert manager.handle_task(
            Task(description="scan my inbox for job opportunities")
        ).agent_name == "Arthur"


# ---------------------------------------------------------------------------
# Honest about what is missing - and never fabricating
# ---------------------------------------------------------------------------


class TestHonestyAboutMissingCapability:
    def test_a_missing_tool_is_named_rather_than_worked_around(self):
        _, manager = assembled()
        result = manager.handle_task(Task(description="research Liverpool"))
        assert not result.succeeded
        assert "web_search" in result.error
        assert "not installed" in result.error

    def test_nothing_about_the_subject_is_invented(self):
        """No search tool exists, so there is no result to give. Answering from
        whatever a model happens to remember would be worse than saying
        nothing."""
        _, manager = assembled()
        result = manager.handle_task(Task(description="tell me about Liverpool"))
        answer = (result.output or "") + (result.error or "")
        for fabricated in ("football", "england", "merseyside", "anfield", "1892"):
            assert fabricated not in answer.lower()

    def test_the_project_ships_no_search_or_coding_tool(self):
        """The honest position, asserted so it cannot drift quietly.

        Step 9 built the room for these, not the things themselves.
        """
        _, manager = assembled()
        assert manager.own_skills.tools.is_empty

    def test_a_present_tool_is_actually_used(self):
        _, manager = assembled(tools=ToolSet([FakeSearch()]))
        result = manager.handle_task(Task(description="research Liverpool"))
        assert result.succeeded
        assert "handled:" in result.output
        assert result.payload["tool"] == "web_search"

    def test_a_failing_tool_reports_its_own_failure(self):
        """Three different outcomes, never collapsed into one: no tool, a
        broken tool, and a working tool are distinguishable answers."""
        _, manager = assembled(tools=ToolSet([FakeSearch(fails=True)]))
        result = manager.handle_task(Task(description="research Liverpool"))
        assert not result.succeeded
        assert "failed" in result.error
        assert "not installed" not in result.error

    def test_coding_and_research_are_separate_capabilities(self):
        _, manager = assembled()
        assert "code_assistant" in manager.handle_task(
            Task(description="write a small function")
        ).error
        assert "web_search" in manager.handle_task(
            Task(description="research anything")
        ).error


# ---------------------------------------------------------------------------
# The two axes are independent
# ---------------------------------------------------------------------------


class TestCapabilityAndBrainAreSeparateAxes:
    def test_half_brain_with_a_working_tool_still_succeeds(self):
        """The decisive one. No model anywhere, and the work still gets done."""
        manager = alfred(tools=ToolSet([FakeSearch()]))
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN
        assert manager.handle_task(Task(description="research Liverpool")).succeeded

    def test_full_brain_without_a_tool_still_cannot(self):
        """Reasoning is not a substitute for a means to act."""
        brain = LLMManagerBrain(Working())
        brain.deliberate(Task(description="warm up"))
        manager = alfred(tools=ToolSet(), brain=brain)

        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN
        result = manager.handle_task(Task(description="research Liverpool"))
        assert not result.succeeded
        assert "not installed" in result.error

    def test_half_brain_without_a_tool_gives_the_same_honest_answer(self):
        """The message depends on the capability, never on the brain."""
        half = alfred(tools=ToolSet())
        brain = LLMManagerBrain(Working())
        brain.deliberate(Task(description="warm up"))
        full = alfred(tools=ToolSet(), brain=brain)

        task = Task(description="research Liverpool")
        assert half.handle_task(task).error == full.handle_task(task).error

    def test_changing_brain_mode_invents_no_capability(self):
        brain = LLMManagerBrain(Working())
        brain.deliberate(Task(description="warm up"))
        assert alfred(tools=ToolSet(), brain=brain).own_skills.tools.is_empty

    def test_changing_the_toolset_changes_no_brain_mode(self):
        before = assess_manager(alfred(tools=ToolSet())).mode
        after = assess_manager(alfred(tools=ToolSet([FakeSearch()]))).mode
        assert before is after is CognitiveMode.HALF_BRAIN

    def test_a_toolset_cannot_grow(self):
        """Capability is membership, and membership is fixed at construction.

        A tool that ran cannot widen what runs next.
        """
        tools = ToolSet([FakeSearch()])
        for mutator in ("add", "register", "update", "extend"):
            assert not hasattr(tools, mutator), mutator


# ---------------------------------------------------------------------------
# Full Brain means the model is actually usable
# ---------------------------------------------------------------------------


class TestFullBrainRequiresRealUsability:
    def test_case_a_usable_model_is_full_brain(self):
        brain = LLMManagerBrain(Working())
        brain.deliberate(Task(description="x"))

        profile = assess_cognition(brain)
        assert profile.mode is CognitiveMode.FULL_BRAIN
        assert profile.verified is True

    def test_case_b_exhausted_credit_is_not_full_brain(self):
        """The defect. A key with no credit reported full model reasoning."""
        provider = (Exhausted())
        brain = LLMManagerBrain(provider)
        brain.deliberate(Task(description="x"))

        profile = assess_cognition(brain)
        assert profile.mode is CognitiveMode.HALF_BRAIN
        assert profile.availability.looks_like_billing
        assert "credit" in profile.reason.lower()

    def test_case_b_any_provider_failure_is_not_full_brain(self):
        """Not only billing. A bad key or an unreachable host is equally
        'configured but unusable'."""
        class BadKey(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                raise RuntimeError("invalid x-api-key")

        brain = LLMManagerBrain(BadKey())
        brain.deliberate(Task(description="x"))
        assert assess_cognition(brain).mode is CognitiveMode.HALF_BRAIN

    def test_case_c_no_configuration_is_half_brain(self):
        from src.providers.null_provider import NullProvider

        assert assess_cognition(
            LLMManagerBrain(NullProvider())
        ).mode is CognitiveMode.HALF_BRAIN

    def test_configuration_alone_is_half_brain(self):
        """The correction. Constructing a provider proves somebody intended
        model reasoning, never that any is obtainable - so the mode itself
        says Half Brain rather than annotating an over-claim."""
        profile = assess_cognition(LLMManagerBrain(Working()))
        assert profile.mode is CognitiveMode.HALF_BRAIN
        assert profile.verified is False
        assert "not yet verified" in profile.reason

    def test_a_verified_brain_says_full_brain_plainly(self):
        """There is no unverified Full Brain to annotate any more: the label
        itself is the claim."""
        brain = LLMManagerBrain(Working())
        brain.deliberate(Task(description="x"))
        assert "FULL BRAIN" in assess_cognition(brain).describe()
        assert "unverified" not in assess_cognition(brain).describe()

    def test_availability_is_unknown_until_a_call_is_made(self):
        brain = LLMManagerBrain(Working())
        assert brain.provider_availability.state is AvailabilityState.UNKNOWN
        assert brain.provider_availability.is_usable is False

    def test_a_known_failure_outranks_silence(self):
        """One component knowing the model is broken settles it, even if
        another has simply never tried."""
        broken = Availability(state=AvailabilityState.UNAVAILABLE, reason="no credit")
        assert broken.is_known_broken
        assert not broken.is_usable

    def test_recording_changes_no_behaviour(self):
        """Observing a call must not change who handles its failure.

        The boundary catch each consumer already had still degrades to "no
        view"; recording happens first and then gets out of the way.
        """
        brain = LLMManagerBrain(Exhausted())
        decision = brain.deliberate(Task(description="x"))
        assert decision.confidence == 0.0
        assert brain.provider_availability.is_known_broken


# ---------------------------------------------------------------------------
# Half Brain is operational, for Alfred and for Arthur
# ---------------------------------------------------------------------------


class TestHalfBrainIsOperationalNotDisabled:
    def test_alfred_in_half_brain_keeps_every_core_capability(self):
        _, manager = assembled()
        profile = assess_manager(manager)
        assert profile.mode is CognitiveMode.HALF_BRAIN
        for core in (
            "routing", "delegation", "self-handling", "assessment", "memory recall",
        ):
            assert core in profile.capabilities

    def test_alfred_in_half_brain_routes_delegates_and_self_handles(self):
        _, manager = assembled()
        assert manager.handle_task(Task(description="read my inbox")).agent_name == "Arthur"
        assert manager.handle_task(Task(description="status report")).succeeded

    def test_alfred_in_half_brain_still_recalls_memory(self):
        from src.services.memory.memory import MemoryRecord

        _, manager = assembled()
        manager.memory.remember(
            MemoryRecord(content="inbox scanning went well", tags=("inbox",))
        )
        assert len(manager.memory.recall("inbox", limit=5)) == 1

    def test_arthur_in_half_brain_still_does_his_domain_work(self):
        """The deterministic pipeline is the work; the brain adds judgement."""
        _, manager = assembled()
        arthur = manager.registry.get("Arthur")
        assert arthur.can_reason is False
        assert manager.handle_task(
            Task(description="scan my inbox for job opportunities")
        ).succeeded

    def test_half_brain_still_recognises_user_skills(self):
        """Recognition is deterministic, so it is unaffected by brain mode."""
        manager = alfred(user=parse_profile("## Skills\n### Strong\npython\n"))
        assert manager.own_skills.recognise(
            Task(description="a python task")
        ).strong == ("python",)


# ---------------------------------------------------------------------------
# Every agent keeps its own Brain and Memory
# ---------------------------------------------------------------------------


class TestEveryAgentKeepsItsOwnBrainAndMemory:
    def test_alfred_and_arthur_never_share_a_brain(self):
        _, manager = assembled()
        assert manager.brain is not manager.registry.get("Arthur").brain

    def test_alfred_and_arthur_never_share_a_memory(self, tmp_path):
        # tmp_path, never data/: a test must not be able to write into the
        # user's real memory directory even by accident.
        _, manager = assembled(memory="persistent", memory_dir=str(tmp_path))
        arthur = manager.registry.get("Arthur")
        assert manager.memory is not arthur.memory
        assert manager.memory.path != arthur.memory.path

    def test_both_brain_contracts_offer_both_modes(self):
        from src.agents.brain import NullSpecialistBrain, SpecialistBrain
        from src.agents.llm_brain import LLMSpecialistBrain
        from src.manager.brain import ManagerBrain, NullBrain

        assert issubclass(NullBrain, ManagerBrain)
        assert issubclass(LLMManagerBrain, ManagerBrain)
        assert issubclass(NullSpecialistBrain, SpecialistBrain)
        assert issubclass(LLMSpecialistBrain, SpecialistBrain)

    def test_a_specialist_brain_reports_availability_too(self):
        """Full Brain verification is not an Alfred-only facility. Any agent
        whose brain has a provider behind it can be asked the same question."""
        from src.agents.llm_brain import LLMSpecialistBrain

        brain = LLMSpecialistBrain(Working(), domain="anything")
        assert brain.provider_availability.state is AvailabilityState.UNKNOWN
        brain.consider(Task(description="warm up"))
        assert brain.provider_availability.is_usable

    def test_manager_skills_owns_neither_brain_nor_memory(self):
        skills = ManagerSkills(IDENTITY)
        assert not hasattr(skills, "brain")
        assert not hasattr(skills, "memory")


# ---------------------------------------------------------------------------
# Nothing earlier was regressed
# ---------------------------------------------------------------------------


class TestEarlierInvariantsSurvive:
    HOSTILE_USER = (
        "## Skills\n### Strong\npython\n"
        "## Capabilities\nsend_email\n## Permissions\ngmail.write\n"
        "## Tools\nweb_search, outbox\n## Specialists\nsome_agent\n## Brain\nfull\n"
    )
    HOSTILE_SCHEDULE = (
        "## Working hours\nalways\n"
        "## Tools\nweb_search\n## Permissions\ngmail.write\n## Brain\nfull\n"
    )

    def test_a_hostile_profile_cannot_grant_a_tool(self):
        """The newest thing a document might try to grab, and it still cannot.

        A ToolSet is granted at construction by the composition root. There is
        no path from a markdown file to it.
        """
        manager = alfred(user=parse_profile(self.HOSTILE_USER))
        assert manager.own_skills.tools.is_empty
        assert not manager.handle_task(Task(description="research anything")).succeeded

    def test_a_hostile_schedule_cannot_grant_a_tool(self):
        manager = alfred(schedule=parse_schedule(self.HOSTILE_SCHEDULE))
        assert manager.own_skills.tools.is_empty

    def test_neither_document_can_change_brain_mode(self):
        manager = alfred(
            user=parse_profile(self.HOSTILE_USER),
            schedule=parse_schedule(self.HOSTILE_SCHEDULE),
        )
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN

    def test_neither_document_can_widen_the_gmail_scope(self):
        from src.services.email.gmail_auth import SCOPES

        alfred(
            user=parse_profile(self.HOSTILE_USER),
            schedule=parse_schedule(self.HOSTILE_SCHEDULE),
        ).handle_task(Task(description="roster"))
        assert SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)

    def test_gmail_stays_read_only_and_sending_refused(self):
        from src.services.email.mailbox import Outbox

        _, manager = assembled()
        assert manager.registry.get("Arthur").can_send is False
        assert not manager.handle_task(Task(description="send an email")).succeeded
        with pytest.raises(TypeError):
            Outbox()

    def test_no_future_specialist_was_invented(self):
        from src.agents.specialists import available_specialists

        assert available_specialists() == ["email_agent"]

    def test_runtime_still_owns_no_per_run_context(self):
        runtime, _ = assembled()
        for leaked in ("user", "profile", "schedule", "recognition", "tools"):
            assert leaked not in runtime.__dataclass_fields__, leaked

    def test_alfred_and_arthur_remain_correctly_named(self):
        from src.services.identity.specialist import load_specialist_identity

        assert load_identity("IDENTITY.md").name == "Alfred"
        assert load_specialist_identity("identities", "email_agent").name == "Arthur"

    def test_every_declared_capability_is_still_backed(self):
        """The Step 4 rule, now covering the words Step 9 added."""
        assert load_identity("IDENTITY.md").capabilities - handled_words() == set()

    def test_no_control_center_or_voice_arrived(self):
        import src.manager.capabilities as caps

        source = (caps.__doc__ or "").lower()
        for absent in ("control center", "voice", "dashboard", "frontend"):
            assert absent not in source, absent
        assert not hasattr(CapabilityRequest, "control_center")
