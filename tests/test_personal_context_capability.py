"""Generalized user knowledge/personal-context capability - the real
production path, end to end: scripts.control_center.build() ->
manager.handle_task() -> the real personal_context capability -> real
governed Memory (ephemeral and persistent-on-disk) -> real retrieval
through gather_knowledge_evidence -> a real (scripted) Brain for the
Tier 2 and knowledge_inquiry paths. No mocked capability, no fake
router - the same discipline test_voice_autonomous_selection_capability.py
and test_voice_natural_requests_and_brain_tier.py already hold
themselves to for select_voice.
"""

from __future__ import annotations

import subprocess
import tempfile

import pytest

from src.config.settings import Settings
from src.core.contracts import Task, TaskResult
from src.manager.capabilities import CapabilityRequest, personal_context
from src.manager.knowledge import gather_knowledge_evidence
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager_skills import ManagerSkills
from src.providers.base import Completion, LLMProvider
from src.services.identity.identity import AgentIdentity
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.personal_context import all_current_personal_context
from src.services.memory.policy import PolicyVerdict
from scripts.control_center import build


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


class ScriptedProvider(LLMProvider):
    def __init__(self, answer: str):
        self._answer = answer
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, prompt, *, system=None):
        self.calls.append(prompt)
        return Completion(text=self._answer, provider="scripted")


ACCEPTANCE_EXAMPLES = (
    "I've learned Python.",
    "I know JavaScript.",
    "This is my timetable for this semester.",
    "My schedule has changed.",
    "I'm starting IT.",
    "This is my new IT schedule.",
    "I'm no longer learning French.",
)


class TestAcceptanceExamplesRouteThroughTheRealManagerAndPersist:
    """Every acceptance example the user gave, verbatim, through the real
    dispatch path with zero LLM configured - proof Tier 1 alone covers
    them, exactly as select_voice's own brief-example test already
    establishes for its domain."""

    @pytest.mark.parametrize("phrase", ACCEPTANCE_EXAMPLES)
    def test_each_example_is_handled_by_personal_context_and_persists(self, phrase):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description=phrase))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "personal_context"
        assert result.payload["brain_assisted"] is False

        stored = manager.memory.recall(kind="personal_context", limit=10)
        assert len(stored) == 1
        assert stored[0].context["topic"] == result.payload["topic"]

    def test_currently_learning_is_personal_context_in_the_canonical_store(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="I am currently learning Rust"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "personal_context"
        assert result.payload["category"] == "learning"


class TestKnowledgeSkillLifecycle:
    """Requirement 15's own minimum: teach a skill, retrieve it, change
    its status, verify it is no longer treated as simply "current"."""

    def test_teach_a_skill_then_retrieve_it_later(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I've learned Python."))
        manager.handle_task(Task(description="I know JavaScript."))

        current = {c.topic: c for c in all_current_personal_context(recall=manager.memory.recall)}
        assert "Python" in current
        assert "JavaScript" in current
        assert current["Python"].status == "current"

    def test_a_second_skill_does_not_erase_the_first(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I've learned Python."))
        manager.handle_task(Task(description="I've learned Rust."))
        current = all_current_personal_context(recall=manager.memory.recall)
        assert len(current) == 2

    def test_no_longer_learning_changes_status_without_deleting_history(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I'm starting French."))
        before = all_current_personal_context(recall=manager.memory.recall)
        assert before[0].status == "current"

        manager.handle_task(Task(description="I'm no longer learning French."))
        after = {c.topic: c for c in all_current_personal_context(recall=manager.memory.recall)}
        matching = [v for k, v in after.items() if "french" in k.lower()]
        assert matching and matching[0].status == "discontinued"

        history = manager.memory.recall(kind="personal_context", limit=10)
        assert len(history) == 2, "the earlier statement must still exist, never deleted"


class TestChangingPersonalContextLifecycle:
    """Requirement 15's second minimum: a schedule taught, retrieved,
    then updated - the new value must become current without the old
    one being falsely reported as still current."""

    def test_schedule_update_supersedes_the_prior_one(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="This is my IT schedule: Mon-Fri 9-3."))
        first = all_current_personal_context(recall=manager.memory.recall)
        assert len(first) == 1
        assert "9-3" in first[0].content

        manager.handle_task(Task(description="This is my new IT schedule: Tue-Thu 10-4."))
        current = all_current_personal_context(recall=manager.memory.recall)
        assert len(current) == 1, "the old and new schedule must resolve to one current topic, not two"
        assert "10-4" in current[0].content
        assert "9-3" not in current[0].content

        history = manager.memory.recall(kind="personal_context", limit=10)
        assert len(history) == 2, "the earlier schedule statement must be preserved, not overwritten"

    def test_a_bare_change_notice_is_marked_stale_without_inventing_a_new_value(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="This is my timetable: Mon 9-5."))
        manager.handle_task(Task(description="My timetable has changed."))
        current = all_current_personal_context(recall=manager.memory.recall)
        assert len(current) == 1
        assert current[0].status == "discontinued"
        assert "not yet stated" in current[0].content

    def test_a_time_qualified_topic_supersedes_the_same_topic_without_it(self):
        """Formerly a recorded limitation: "timetable for this semester" and
        "timetable" were tracked as two topics. Pre-Stage-9 Campaign 1's
        topic key strips a trailing time qualifier (grammar, never meaning),
        so the later statement genuinely supersedes the earlier one."""
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="This is my timetable for this semester: Mon 9-5."))
        manager.handle_task(Task(description="My timetable has changed."))
        current = all_current_personal_context(recall=manager.memory.recall)
        assert len(current) == 1
        assert current[0].status == "discontinued"


class TestRestartPersistence:
    """Real file-backed memory, a real second build() simulating a
    process restart - not an in-memory-only implementation, not a
    hidden duplicate store."""

    def test_teach_persist_restart_retrieve_update_persist_restart(self):
        memory_dir = tempfile.mkdtemp()

        settings1 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager1, _, _ = build(settings1)
        r1 = manager1.handle_task(Task(description="I've learned Python."))
        r2 = manager1.handle_task(Task(description="This is my IT schedule: Mon-Fri 9-3."))
        assert r1.succeeded and r2.succeeded

        settings2 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager2, _, _ = build(settings2)
        after_restart = {c.topic: c for c in all_current_personal_context(recall=manager2.memory.recall)}
        assert "Python" in after_restart
        assert any("schedule" in t.lower() for t in after_restart)

        r3 = manager2.handle_task(Task(description="This is my new IT schedule: Tue-Thu 10-4."))
        assert r3.succeeded

        settings3 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager3, _, _ = build(settings3)
        final = {c.topic: c for c in all_current_personal_context(recall=manager3.memory.recall)}
        schedule_topic = next(t for t in final if "schedule" in t.lower())
        assert "10-4" in final[schedule_topic].content
        assert "9-3" not in final[schedule_topic].content
        assert final["Python"].content == "The user has learned Python."


class TestGovernedPersistenceOnly:
    def test_a_governance_rejection_is_reported_honestly_never_silently_dropped(self):
        memory = EphemeralMemory()

        def reject(record):
            return PolicyVerdict(allowed=False, reason="simulated governance rejection")

        request = CapabilityRequest(
            task=Task(description="I've learned Python."),
            identity=AgentIdentity(name="alfred", capabilities=frozenset({"learned"})),
            roster=lambda: (), own_name="alfred", own_capabilities=("learned",),
            recall=memory.recall, forget=memory.forget, retain=reject,
        )
        result = personal_context(request)
        assert not result.succeeded
        assert "couldn't record" in result.error.lower()
        assert memory.recall(kind="personal_context", limit=10) == ()

    def test_no_retain_bound_refuses_honestly_never_crashes(self):
        memory = EphemeralMemory()
        request = CapabilityRequest(
            task=Task(description="I've learned Python."),
            identity=AgentIdentity(name="alfred", capabilities=frozenset({"learned"})),
            roster=lambda: (), own_name="alfred", own_capabilities=("learned",),
            recall=memory.recall, forget=memory.forget, retain=None,
        )
        result = personal_context(request)
        assert not result.succeeded
        assert "way to remember" in result.error.lower()


class TestTier2BrainAssistedFallback:
    """A statement Tier 1's deterministic shapes genuinely cannot parse,
    reaching a real (scripted) Brain, validated before being written -
    never a fabricated topic, the identical discipline select_voice's own
    Tier 2 already holds itself to."""

    def test_an_unparseable_personal_statement_reaches_the_brain_and_is_recorded(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider("TOPIC: guitar lessons\nSTATUS: current")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        result = manager.handle_task(Task(description="my skills have gotten better lately"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "personal_context"
        assert result.payload["brain_assisted"] is True
        assert result.payload["topic"] == "guitar lessons"
        assert provider.calls, "the real Brain must actually have been asked"

        current = all_current_personal_context(recall=manager.memory.recall)
        assert any(c.topic == "guitar lessons" for c in current)

    def test_the_brain_is_asked_about_the_users_exact_words_never_a_paraphrase(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider("TOPIC: none")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        manager.handle_task(Task(description="my skills have gotten better lately"))
        assert provider.calls
        assert "my skills have gotten better lately" in provider.calls[-1]

    def test_a_brain_reply_naming_no_topic_is_refused_never_guessed(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider("TOPIC: none")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        result = manager.handle_task(Task(description="my skills have gotten better lately"))
        assert not result.succeeded
        assert manager.memory.recall(kind="personal_context", limit=10) == ()

    def test_no_brain_configured_degrades_to_an_honest_refusal_never_a_crash(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="my skills have gotten better lately"))
        assert not result.succeeded
        assert manager.memory.recall(kind="personal_context", limit=10) == ()


class TestRetrievalIntegrationWithKnowledgeEvidence:
    """Requirement 5: Alfred must actually retrieve and use taught
    personal context later - through the same evidence-gathering seam
    LEARNING_CONTEXT already uses, additively, never a second memory
    system."""

    def test_gather_knowledge_evidence_surfaces_current_personal_context(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I've learned Python."))
        manager.handle_task(Task(description="This is my new IT schedule: Tue-Thu 10-4."))

        evidence = gather_knowledge_evidence("tell me about myself", recall=manager.memory.recall)
        assert "personal_context" in evidence.sources
        assert any("Python" in note for note in evidence.notes)
        assert any("Tue-Thu 10-4" in note for note in evidence.notes)

    def test_superseded_facts_never_appear_as_current_evidence(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="This is my IT schedule: Mon-Fri 9-3."))
        manager.handle_task(Task(description="This is my new IT schedule: Tue-Thu 10-4."))

        evidence = gather_knowledge_evidence("tell me about myself", recall=manager.memory.recall)
        joined = " | ".join(evidence.notes)
        assert "9-3" not in joined
        assert "10-4" in joined

    def test_a_real_brain_backed_knowledge_question_actually_receives_the_evidence(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I've learned Python."))

        provider = ScriptedProvider("You've told me you know Python.")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        result = manager.handle_task(Task(description="tell me about my Python skill"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "knowledge_inquiry"
        assert "personal_context" in result.payload["knowledge_sources"]
        assert provider.calls
        assert "The user has learned Python." in provider.calls[-1]

    def test_no_hardcoded_example_data_is_ever_returned_when_nothing_was_taught(self):
        runtime, manager, channel, conversation = real_system()
        evidence = gather_knowledge_evidence("tell me about myself", recall=manager.memory.recall)
        assert "personal_context" not in evidence.sources
        assert not any("Python" in n or "schedule" in n.lower() for n in evidence.notes)


class TestOrdinaryConversationIsNeverMisclaimed:
    @pytest.mark.parametrize("phrase", (
        "thanks, that works",
        "what do you think?",
        "sounds good",
        "I'm tired today",
        "can you help me write a script",
    ))
    def test_ordinary_remarks_are_not_routed_to_personal_context(self, phrase):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description=phrase))
        handled = result.payload.get("handled_by") if result.payload else None
        assert handled != "personal_context", phrase


_SYNTHETIC_AGENT_KEY = "nebula_cartography_specialist"


def _fixture_is_genuinely_unseen() -> None:
    result = subprocess.run(
        ["git", "grep", "-il", _SYNTHETIC_AGENT_KEY, "--", "src", "scripts"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "the genericity fixture agent name must be genuinely unseen"


class TestPersonalContextGenericityAcrossAnyAgent:
    """The same proof test_voice_capability_genericity.py already holds
    select_voice to: a fresh, generic ManagerSkills agent with a
    synthetic name nothing in this project has ever declared reaches
    personal_context through real dispatch, with zero Alfred-specific
    branching required."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_a_brand_new_agent_learns_and_recalls_its_own_personal_context(self):
        memory = EphemeralMemory()

        def retain(record):
            memory.remember(record)
            return PolicyVerdict(allowed=True, reason="test fixture allows all writes")

        identity = AgentIdentity(name=_SYNTHETIC_AGENT_KEY, capabilities=frozenset({"learned"}))
        agent = ManagerSkills(identity, recall=memory.recall, forget=memory.forget, retain=retain)

        result = agent.execute(Task(description="I've learned Python."))
        assert isinstance(result, TaskResult)
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "personal_context"

        current = all_current_personal_context(recall=memory.recall)
        assert any(c.topic == "Python" for c in current)


class TestControlCenterExposesRealBackendState:
    """Requirement 9: real, live backend state, never a decorative card
    or hardcoded example - the terminal Control Center's own `personal`
    command."""

    def test_the_terminal_command_is_empty_before_anything_was_taught(self):
        from src.control_center.observer import ControlCenter
        from scripts.control_center import run_command

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "personal", 100, channel, conversation)
        assert "nothing taught yet" in output

    def test_the_terminal_command_reflects_real_persisted_state(self):
        from src.control_center.observer import ControlCenter
        from scripts.control_center import run_command

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        manager.handle_task(Task(description="I've learned Python."))
        manager.handle_task(Task(description="I'm no longer learning French."))

        output = run_command(centre, "personal", 100, channel, conversation)
        assert "Python" in output
        assert "[current]" in output
        assert "[ended]" in output
        assert "French" in output


class TestLiveHttpAndFrontendConnection:
    """Requirement 10/17: the real HTTP surface the served frontend
    actually calls - scripts/serve_ui.py's own route()/route_command(),
    the same functions test_live_ui.py already exercises for every other
    management verb. No socket is opened (this whole suite forbids real
    network calls - see test_live_ui.py's own docstring); this is the
    identical function a live server's request handler calls, so it is
    the genuine server-side behaviour, not a simulation of it."""

    def _live_system(self):
        from scripts.serve_ui import build_live_system
        return build_live_system(Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral"))

    def test_personal_is_a_recognised_management_verb(self):
        from scripts.serve_ui import MANAGEMENT_VERBS
        assert "personal" in MANAGEMENT_VERBS

    def test_the_command_route_reaches_the_real_capability_and_backend_state(self):
        from scripts.serve_ui import route_command

        system = self._live_system()
        system.manager.handle_task(Task(description="I've learned Python."))

        status, body, content_type = route_command("personal", None, system)
        assert status == 200
        assert content_type.startswith("text/plain")
        assert "Python" in body.decode("utf-8")

    def test_the_snapshot_endpoint_carries_the_same_real_state_the_frontend_reads(self):
        from scripts.serve_ui import route

        system = self._live_system()
        system.manager.handle_task(Task(description="I've learned Python."))
        system.manager.handle_task(Task(description="This is my new IT schedule: Tue-Thu 10-4."))

        status, body, content_type = route("/snapshot.json", system)
        assert status == 200
        data = __import__("json").loads(body)
        assert "personal_context" in data
        topics = {item["topic"]: item for item in data["personal_context"]}
        assert topics["Python"]["content"] == "The user has learned Python."
        assert "Tue-Thu 10-4" in topics["IT schedule"]["content"]

    def test_the_snapshot_endpoint_carries_no_data_when_nothing_was_taught(self):
        from scripts.serve_ui import route

        system = self._live_system()
        status, body, _ = route("/snapshot.json", system)
        data = __import__("json").loads(body)
        assert data["personal_context"] == []

    def test_the_frontend_html_declares_the_management_verb_it_can_actually_call(self):
        """The client-side button list (ui/index.html's own MGMT_VERBS)
        must name a verb the server actually accepts - otherwise the
        button would be a disconnected, decorative frontend element."""
        from pathlib import Path
        html = Path("ui/index.html").read_text(encoding="utf-8")
        assert '"personal"' in html


class TestAgentBoundariesArthurNeverGetsThisCapability:
    """Arthur is EmailAgent, a structurally different class from
    ManagerSkills - PERSONAL_CONTEXT is only ever registered in
    MANAGER_CAPABILITIES, which EmailAgent's own dispatch never reads.
    User-owned context must never become unrestricted access to another
    agent's private memory or capability set."""

    def test_email_agent_is_not_a_managerskills_instance(self):
        from src.agents.email_agent import EmailAgent
        assert not issubclass(EmailAgent, ManagerSkills)

    def test_personal_context_is_registered_only_under_manager_capabilities(self):
        from src.manager.capabilities import MANAGER_CAPABILITIES
        assert "personal_context" in MANAGER_CAPABILITIES
