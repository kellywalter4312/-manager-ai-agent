"""Phase 1.1 - Core Alfred: Identity.

Proves, end to end through a real assembled Manager (not just parsing),
the five requirements this phase names:

    1. canonical identity        - Alfred, loaded from IDENTITY.md
    2. self-description          - accurate, deterministic, never fabricated
    3. role and responsibilities - both actually reach a person, not just
                                    a loaded field nothing renders
    4. user recognition/context  - real when a profile is declared, honest
                                    ("no profile configured") when it is not
    5. provider independence     - identity is byte-identical whether the
                                    Brain behind it is Null or a genuinely
                                    usable, verified model

And, running through every one of them: Half Brain never claims to need a
model for something that needs none, and nothing here can invent a fact
about the user or about Alfred that neither document actually states.

This file exercises the real, already-existing mechanism
(`src/services/identity/identity.py`, `introduction.py`, `recognition.py`,
`capabilities.py`'s `about_self`/`roster_report`, `render.py`'s
`render_agent`) - nothing here is a parallel identity system. Parsing-level
coverage of `AgentIdentity` itself already exists in `test_identity.py` and
is not repeated here.
"""

from __future__ import annotations

import dataclasses

import pytest

from src.control_center.observer import ControlCenter
from src.control_center.render import render_agent
from src.core.contracts import Task
from src.manager.cognition import CognitiveMode, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.providers.base import Completion, LLMProvider
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.identity.identity import AgentIdentity, load_identity
from src.services.profile.loader import load_profile
from src.services.profile.profile import UserProfile

IDENTITY = load_identity("IDENTITY.md")

IDENTITY_QUESTIONS = (
    "who are you?",
    "what is your name?",
    "what is your role?",
    "introduce yourself",
    "tell me about yourself",
)


def alfred(brain=None, user=None):
    """A Manager with Alfred's real, loaded identity - the same
    construction pattern every other test in this suite already uses."""
    manager = ManagerAgent(brain=brain)
    manager.own_skills = ManagerSkills(
        IDENTITY,
        roster=manager.registry.all_agents,
        user=user,
        converse=manager.brain.converse,
    )
    manager.registry.register(manager.own_skills)
    return manager


class WorkingProvider(LLMProvider):
    """Configured, keyed, and genuinely usable - proof that a real,
    verified Full Brain changes nothing about who Alfred says he is."""

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        if system and "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider="anthropic")
        return Completion(text="a model's own answer", provider="anthropic")


def full_brain() -> LLMManagerBrain:
    brain = LLMManagerBrain(WorkingProvider())
    brain.deliberate(Task(description="warm up"))  # the call that proves usability
    return brain


# ---------------------------------------------------------------------------
# 1. Canonical identity
# ---------------------------------------------------------------------------


class TestCanonicalIdentity:
    def test_the_committed_identity_document_names_alfred(self):
        assert IDENTITY.name == "Alfred"

    def test_the_manager_registers_and_answers_as_alfred(self):
        manager = alfred()
        assert manager.own_skills.name == "Alfred"
        result = manager.handle_task(Task(description="status"))
        assert result.agent_name == "Alfred"

    def test_the_name_is_consistent_across_every_identity_surface(self):
        manager = alfred()
        who = manager.handle_task(Task(description="who are you?")).output
        greeting = manager.handle_task(Task(description="hello")).output
        assert "Alfred" in who
        assert "Alfred" in greeting


# ---------------------------------------------------------------------------
# 2. Self-description
# ---------------------------------------------------------------------------


class TestSelfDescription:
    @pytest.mark.parametrize("question", IDENTITY_QUESTIONS)
    def test_answers_accurately_and_deterministically(self, question):
        manager = alfred()
        result = manager.handle_task(Task(description=question))
        assert result.succeeded
        assert "Alfred" in result.output
        assert "orchestrator" in result.output.lower()

    def test_never_fabricates_beyond_the_document(self):
        """Nothing in IDENTITY.md names a vendor, a model or a specific
        tool - a genuine self-description must not invent one."""
        manager = alfred()
        answer = manager.handle_task(Task(description="who are you?")).output.lower()
        for invented in ("gpt", "claude", "anthropic", "openai", "gemini"):
            assert invented not in answer

    def test_identical_answer_every_time(self):
        """Deterministic: the same question asked twice gets the same
        answer, because nothing here is composed by a model."""
        manager = alfred()
        first = manager.handle_task(Task(description="who are you?")).output
        second = manager.handle_task(Task(description="who are you?")).output
        assert first == second


# ---------------------------------------------------------------------------
# 3. Role and responsibilities
# ---------------------------------------------------------------------------


class TestRoleAndResponsibilities:
    def test_self_description_states_the_role(self):
        manager = alfred()
        answer = manager.handle_task(Task(description="what is your role?")).output
        assert "orchestrator and coordinator" in answer.lower()

    def test_the_agent_detail_view_shows_role_and_responsibility(self):
        """Regression pin for the Phase 1.1 fix: AgentView.role and
        AgentView.area were already computed (observer.py's
        manager_view()) but render_agent() never printed either - a
        real 'architecturally present but not functional' gap. Both
        must now reach the actual rendered text."""
        manager = alfred()
        centre = ControlCenter(runtime=_FakeRuntime(manager), manager=manager)
        view = centre.manager_view()
        rendered = render_agent(view, 80)

        assert "ROLE" in rendered
        assert "orchestrator and coordinator" in rendered.lower()
        assert "RESPONSIBILITY" in rendered
        assert "coordination" in rendered.lower()

    def test_role_and_area_are_read_from_the_identity_document_only(self):
        manager = alfred()
        centre = ControlCenter(runtime=_FakeRuntime(manager), manager=manager)
        view = centre.manager_view()
        assert view.role == IDENTITY.role
        assert view.area == IDENTITY.area_of_responsibility


class _FakeRuntime:
    """The one attribute ControlCenter.manager_view() actually reads from
    a runtime - identity - supplied directly rather than assembling a
    full Runtime, the same narrowing every test double in this suite
    already practises."""

    def __init__(self, manager: ManagerAgent) -> None:
        self.identity = IDENTITY
        self.avatar_provider = DefaultAvatarProvider()
        self.presence_registry = None


# ---------------------------------------------------------------------------
# 4. User recognition/context
# ---------------------------------------------------------------------------


class TestUserRecognitionAndContext:
    def test_a_declared_profile_is_genuinely_recognised(self):
        profile = load_profile("USER.md")
        manager = alfred(user=profile)
        result = manager.handle_task(Task(description="status"))
        assert result.payload["serving"] != "no profile configured"
        assert "skill(s)" in result.payload["serving"]

    def test_a_task_naming_a_declared_skill_is_recognised_for_that_task(self):
        profile = load_profile("USER.md")
        skill = next(iter(profile.skills))
        manager = alfred(user=profile)
        result = manager.handle_task(Task(description=f"status on my {skill} work"))
        assert not result.payload["recognised"].startswith("0 strong, 0 working, 0 learning")

    def test_no_profile_is_reported_honestly_never_invented(self):
        manager = alfred(user=None)
        result = manager.handle_task(Task(description="status"))
        assert result.payload["serving"] == "no profile configured"
        assert result.payload["recognised"] == "no profile configured"

    def test_an_undeclared_skill_is_never_recognised(self):
        """Nothing here may claim the user knows something they never
        declared - the same honesty rule identity itself lives under."""
        profile = UserProfile(skills={"python": "strong"})
        manager = alfred(user=profile)
        result = manager.handle_task(Task(description="status on my quantum computing work"))
        assert result.payload["recognised"].startswith("0 strong, 0 working, 0 learning")


# ---------------------------------------------------------------------------
# 5. Provider-independent identity
# ---------------------------------------------------------------------------


class TestProviderIndependentIdentity:
    def test_agent_identity_carries_no_provider_or_model_field(self):
        """Structural, not behavioural: identity has nowhere to put a
        vendor name even if something tried."""
        field_names = {f.name for f in dataclasses.fields(AgentIdentity)}
        for forbidden in ("provider", "model", "api_key", "brain"):
            assert forbidden not in field_names

    @pytest.mark.parametrize("question", IDENTITY_QUESTIONS)
    def test_identical_self_description_half_and_full_brain(self, question):
        half = alfred()
        full = alfred(brain=full_brain())

        assert assess_manager(half).mode is CognitiveMode.HALF_BRAIN
        assert assess_manager(full).mode is CognitiveMode.FULL_BRAIN

        answer_half = half.handle_task(Task(description=question)).output
        answer_full = full.handle_task(Task(description=question)).output
        assert answer_half == answer_full

    def test_a_verified_full_brain_is_never_consulted_for_identity(self):
        """The decisive one. A genuinely usable model is available and
        is never asked who Alfred is - identity is answered from the
        document, not composed by whichever model happens to be
        configured."""
        provider = WorkingProvider()
        brain = LLMManagerBrain(provider)
        brain.deliberate(Task(description="warm up"))
        manager = alfred(brain=brain)

        result = manager.handle_task(Task(description="who are you?"))
        assert result.payload is None or "llm" not in str(result.payload.get("brain", "")).lower()
        assert "a model's own answer" not in result.output


# ---------------------------------------------------------------------------
# 6. Honest Half Brain behaviour, woven through every requirement above
# ---------------------------------------------------------------------------


class TestHonestHalfBrainBehaviour:
    def test_identity_never_claims_to_need_a_model(self):
        manager = alfred()  # NullBrain, no provider at all
        for question in IDENTITY_QUESTIONS:
            result = manager.handle_task(Task(description=question))
            assert result.succeeded
            assert "no language model" not in (result.error or "").lower()
            assert "reason about that" not in (result.error or "").lower()

    def test_half_brain_mode_is_correctly_reported_throughout(self):
        manager = alfred()
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN
        assert not assess_manager(manager).verified
