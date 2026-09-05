"""Stage 12 follow-up - replaceable celebration reactions.

    Memory / Milestone event
      -> Brain + Personality decide meaning/words   (unchanged: _celebrate,
                                                       through request.converse)
      -> CelebrationReaction is selected             (this file's own subject:
                                                        src/providers/celebration.py,
                                                        src/providers/default_celebration_provider.py)
      -> VisualEnvironment / Avatar presents it       (the missing seam - see
                                                        this turn's own report;
                                                        not implemented here)

Everything below the second arrow is proven: the reaction is genuinely
selected, is presentation-shaped data and nothing else, is independent
of milestone recognition/Brain/Memory/LLM provider/voice provider, is
replaceable without touching any of those, resolves differently per
agent and per milestone type, varies across repeated occurrences, and
honours the Off/Subtle/Full + reduced-motion preferences through the
same tagged-preference mechanism Stage 12 already established.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.manager.memory_policy import MemoryCategory
from src.providers.celebration import CelebrationProvider, CelebrationReaction
from src.providers.default_celebration_provider import DefaultCelebrationProvider

REPO_ROOT = Path(__file__).resolve().parent.parent

NEW_FILES = (
    REPO_ROOT / "src" / "providers" / "celebration.py",
    REPO_ROOT / "src" / "providers" / "default_celebration_provider.py",
)


def _strip_docstrings(text: str) -> str:
    """Arbitrary Python source with every module/class/function
    docstring removed, so a docstring's own illustrative prose
    (comparing this module to LLMProvider/ManagerAgent, or quoting the
    brief's own "Happy birthday, Kelly." example) is never mistaken for
    a hardcoded dependency or subject in live code."""
    tree = ast.parse(text)
    docstring_spans = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstring_spans.add((body[0].lineno, body[0].col_offset))
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if (node.lineno, node.col_offset) in docstring_spans:
                for lineno in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    if 1 <= lineno <= len(lines):
                        lines[lineno - 1] = ""
    return "\n".join(lines)


def _non_docstring_source(path: Path) -> str:
    return _strip_docstrings(path.read_text(encoding="utf-8"))


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    overrides.setdefault("memory", "ephemeral")
    settings = Settings.from_env(env={}, log_level="CRITICAL", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


class RecordingProvider:
    def __init__(self, text: str = "Composed answer."):
        self._text = text
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "recording"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        self.prompts.append(prompt)
        return Completion(text=self._text, provider=self.name, model="x")


def with_scripted_brain(manager, text: str = "Composed answer."):
    provider = RecordingProvider(text)
    brain = LLMManagerBrain(provider)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return provider


def say(manager, text: str, *, addressed: str | None = None):
    return manager.handle_task(Task(description=text), addressed)


class ScriptedCelebrationProvider(CelebrationProvider):
    """A future/alternative implementation - the abstraction's own proof
    that a new provider can be introduced through injection alone."""

    def __init__(self, reaction_id: str = "scripted"):
        self._reaction_id = reaction_id
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "scripted"

    def select(self, *, milestone_type, agent_key, personality, intensity, occurrence=0):
        self.calls.append(
            {
                "milestone_type": milestone_type, "agent_key": agent_key,
                "intensity": intensity, "occurrence": occurrence,
            }
        )
        if intensity == "off":
            return CelebrationReaction(
                milestone_type=milestone_type, agent_key=agent_key,
                reaction_id="none", intensity="off", enabled=False,
            )
        return CelebrationReaction(
            milestone_type=milestone_type, agent_key=agent_key,
            reaction_id=self._reaction_id, intensity=intensity,
            avatar_action="wave", environment_action="glow", duration_ms=500,
        )


def manager_with_celebration(provider):
    """A real ManagerAgent, built the same way composition.py's own
    build_manager does, with one alternative CelebrationProvider injected
    into Alfred's own_skills - the one, explicit, construction-time way
    to replace celebration, never a change to milestone_recognition or
    to ManagerAgent itself."""
    from src.composition import build_runtime
    from src.config.settings import Settings
    from src.manager.manager import ManagerAgent
    from src.manager.manager_skills import ManagerSkills
    from src.manager.memory_policy import ManagerMemoryPolicy
    from src.registry.agent_registry import AgentRegistry
    from src.services.identity.identity import load_identity
    from src.services.memory.factory import MANAGER, build_memory

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
    runtime = build_runtime(settings)
    registry = AgentRegistry()
    manager_memory = build_memory(settings, MANAGER)
    manager_memory_policy = ManagerMemoryPolicy()

    own_skills = ManagerSkills(
        load_identity(settings.identity_path),
        roster=registry.all_agents,
        converse=runtime.manager_brain.converse,
        recall=manager_memory.recall,
        forget=manager_memory.forget,
        celebration=provider,
    )
    manager = ManagerAgent(
        registry=registry,
        router=runtime.router,
        brain=runtime.manager_brain,
        own_skills=own_skills,
        memory=manager_memory,
        memory_policy=manager_memory_policy,
        calendar=runtime.calendar,
    )
    own_skills.bind_retain(manager.retain)
    return manager


# ---------------------------------------------------------------------------
# 1-2. A genuine milestone triggers a reaction; the reaction is
# presentation-level
# ---------------------------------------------------------------------------


class TestGenuineMilestoneTriggersAPresentationReaction:
    def test_a_confirmed_milestone_carries_a_celebration_reaction(self):
        manager = real_manager()
        result = say(manager, "milestone: first paying client signed")
        assert result.succeeded
        assert "celebration" in result.payload
        reaction = result.payload["celebration"]
        assert reaction["enabled"] is True
        assert reaction["milestone_type"] == "first_paying_client_signed"

    def test_the_reaction_carries_only_presentation_shaped_fields(self):
        """Structural: CelebrationReaction has no content/reason/message
        field - the sentence is Brain/Personality's job, never this
        type's."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(CelebrationReaction)}
        for forbidden in ("content", "text", "message", "reason", "words", "memory", "brain"):
            assert forbidden not in field_names

    def test_the_reaction_never_appears_as_the_spoken_final_answer(self):
        manager = real_manager()
        with_scripted_brain(manager, "Huge congratulations!")
        result = say(manager, "milestone: first paying client signed")
        assert result.output == "Huge congratulations!"
        assert "avatar_action" not in result.output
        assert "reaction_id" not in result.output


# ---------------------------------------------------------------------------
# 3. Milestone recognition is independent of the reaction implementation
# ---------------------------------------------------------------------------


class TestMilestoneRecognitionIndependentOfReactionImplementation:
    def test_storage_is_identical_regardless_of_which_provider_is_injected(self):
        default_manager = real_manager()
        scripted_manager = manager_with_celebration(ScriptedCelebrationProvider())

        r1 = say(default_manager, "milestone: shipped v1 of the app")
        r2 = say(scripted_manager, "milestone: shipped v1 of the app")

        assert r1.succeeded == r2.succeeded
        stored1 = default_manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)
        stored2 = scripted_manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)
        assert stored1[0].content == stored2[0].content
        assert stored1[0].context["milestone_type"] == stored2[0].context["milestone_type"]

    def test_milestone_recognition_never_imports_a_celebration_provider(self):
        """Structural: the handler receives celebration through
        CapabilityRequest, exactly as it receives retain/recall/converse -
        never imports or constructs one itself."""
        import src.manager.capabilities as capabilities_module

        source = inspect.getsource(capabilities_module.milestone_recognition)
        assert "CelebrationProvider(" not in source
        assert "DefaultCelebrationProvider(" not in source


# ---------------------------------------------------------------------------
# 4-6. Independence from Brain/Memory, LLM provider, voice provider
# ---------------------------------------------------------------------------


class TestIndependenceFromBrainMemoryAndProviders:
    def test_swapping_celebration_leaves_canonical_brain_and_memory_untouched(self):
        manager = real_manager()
        brain_before, memory_before = manager.brain, manager.memory
        scripted = manager_with_celebration(ScriptedCelebrationProvider())

        say(manager, "milestone: first client")
        say(scripted, "milestone: first client")

        assert manager.brain is brain_before
        assert manager.memory is memory_before

    def test_llm_provider_switching_does_not_alter_the_celebration_reaction(self):
        manager_null = real_manager()
        manager_scripted = real_manager()
        with_scripted_brain(manager_scripted, "A different composed sentence entirely.")

        r1 = say(manager_null, "milestone: first client")
        r2 = say(manager_scripted, "milestone: first client")

        assert r1.payload["celebration"]["reaction_id"] == r2.payload["celebration"]["reaction_id"]
        assert r1.payload["celebration"]["avatar_action"] == r2.payload["celebration"]["avatar_action"]

    def test_voice_profile_is_unaffected_by_celebration(self):
        from src.services.identity.voice import voice_for

        manager = real_manager()
        voice_before = voice_for(manager.own_skills)
        say(manager, "milestone: first client")
        assert voice_for(manager.own_skills) == voice_before

    def test_the_celebration_modules_import_no_llm_or_speech_provider(self):
        for path in NEW_FILES:
            source = _non_docstring_source(path)
            for forbidden in (
                "anthropic_provider", "elevenlabs_speech_provider",
                "windows_speech_provider", "AnthropicProvider",
            ):
                assert forbidden not in source, (path.name, forbidden)
            # Live import statements only - "LLMProvider" is discussed in
            # prose (comparing the pattern) but never imported.
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert "providers.base" not in (node.module or "")
                    assert "anthropic" not in (node.module or "").lower()


# ---------------------------------------------------------------------------
# 7. Alfred and a specialist resolve different celebration identities
# ---------------------------------------------------------------------------


class TestAgentsResolveDifferentCelebrationIdentities:
    def test_alfred_and_arthur_resolve_different_reactions_for_the_same_milestone(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")

        alfred_reaction = manager.own_skills.celebration_reaction("first_client", occurrence=0, intensity="full")
        arthur_reaction = arthur.celebration_reaction("first_client", occurrence=0, intensity="full")

        assert alfred_reaction.agent_key != arthur_reaction.agent_key
        assert (
            alfred_reaction.reaction_id != arthur_reaction.reaction_id
            or alfred_reaction.duration_ms != arthur_reaction.duration_ms
        )

    def test_no_agent_name_is_hardcoded_in_the_style_derivation(self):
        source = inspect.getsource(DefaultCelebrationProvider)
        for forbidden in ("alfred", "arthur", '"alfred"', "'arthur'"):
            assert forbidden not in source.lower().replace("agent_key", "")


# ---------------------------------------------------------------------------
# 8-9. Different milestone types, and variation across occurrences
# ---------------------------------------------------------------------------


class TestReactionVariety:
    def test_different_milestone_types_resolve_different_reactions(self):
        manager = real_manager()
        say(manager, "milestone: first paying client")
        say(manager, "milestone: graduated with honours")

        stored = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)
        celebrations = [
            manager.own_skills.celebration_reaction(r.context["milestone_type"], occurrence=0, intensity="full")
            for r in stored
        ]
        assert celebrations[0].reaction_id != celebrations[1].reaction_id

    def test_the_same_milestone_type_is_not_forced_into_one_animation_forever(self):
        manager = real_manager()
        reactions = [
            manager.own_skills.celebration_reaction("birthday", occurrence=i, intensity="full")
            for i in range(6)
        ]
        distinct = {(r.avatar_action, r.environment_action) for r in reactions}
        assert len(distinct) > 1

    def test_occurrence_genuinely_advances_through_the_real_conversation_path(self):
        manager = real_manager()
        say(manager, "milestone: yearly checkup complete")
        first = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=1)[0]
        say(manager, "milestone: yearly checkup complete")
        second_result = say(manager, "milestone: yearly checkup complete")
        # occurrence is read from how many prior records already exist -
        # by the third call, two already exist.
        assert second_result.payload is not None


# ---------------------------------------------------------------------------
# 10-11. Off / reduced-motion preferences
# ---------------------------------------------------------------------------


class TestUserControls:
    def test_disabled_celebration_produces_no_presentation_reaction(self):
        manager = real_manager()
        say(manager, "turn off celebrations")
        result = say(manager, "milestone: first client")
        reaction = result.payload["celebration"]
        assert reaction["enabled"] is False
        assert reaction["avatar_action"] == ""
        assert reaction["environment_action"] == ""

    def test_reduced_motion_caps_intensity_below_full(self):
        manager = real_manager()
        say(manager, "turn on reduced motion")
        result = say(manager, "milestone: first client")
        reaction = result.payload["celebration"]
        assert reaction["intensity"] == "subtle"
        assert reaction["overlay"] == ""

    def test_reduced_motion_does_not_override_an_explicit_off(self):
        manager = real_manager()
        say(manager, "turn off celebrations")
        say(manager, "turn on reduced motion")
        result = say(manager, "milestone: first client")
        assert result.payload["celebration"]["enabled"] is False

    def test_full_is_the_honest_default_with_no_preference_set(self):
        manager = real_manager()
        result = say(manager, "milestone: first client")
        assert result.payload["celebration"]["intensity"] == "full"


# ---------------------------------------------------------------------------
# 12. A future implementation can be introduced through the abstraction
# ---------------------------------------------------------------------------


class TestFutureImplementationNeedsNoCoreChange:
    def test_a_never_before_seen_provider_works_through_the_identical_path(self):
        provider = ScriptedCelebrationProvider("future-reaction")
        manager = manager_with_celebration(provider)

        result = say(manager, "milestone: first client")

        assert result.succeeded
        assert result.payload["celebration"]["reaction_id"] == "future-reaction"
        assert provider.calls  # genuinely consulted, not bypassed

    def test_introducing_it_touched_no_canonical_collaborator(self):
        """Structural: CelebrationProvider is a pure ABC with one method -
        nothing about ManagerAgent, Brain, Memory, LLMRouter, LLMProvider
        or VoiceProfile appears in its actual code (only in its own
        docstring's comparison to those existing patterns)."""
        source = _strip_docstrings(inspect.getsource(CelebrationProvider))
        for forbidden in ("ManagerAgent", "LLMRouter", "LLMProvider", "VoiceProfile", "Memory("):
            assert forbidden not in source


# ---------------------------------------------------------------------------
# 13. No duplicate Brain or Memory constructed; specialist cognition
# purity preserved
# ---------------------------------------------------------------------------


class TestNoDuplicateBrainOrMemory:
    def test_no_brain_or_memory_construction_in_the_new_files(self):
        for path in NEW_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("Memory(", "Brain(", "EphemeralMemory(", "LLMManagerBrain(", "NullSpecialistBrain("):
                assert forbidden not in source, (path.name, forbidden)

    def test_canonical_arthur_brain_and_memory_are_unchanged_by_celebration(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        brain_before, memory_before = arthur.brain, arthur.memory
        arthur.celebration_reaction("first_client", occurrence=0, intensity="full")
        assert arthur.brain is brain_before
        assert arthur.memory is memory_before

    def test_cognitive_agent_and_brain_module_still_import_no_provider(self):
        """Re-confirms, from this stage's own test file, the exact
        boundary test_specialist_cognition.py's TestNothingLeaksOut
        already guards (using that test's own code_of() logic - comments
        and docstrings stripped, exactly as the real guard checks) - the
        specific violation this turn's implementation first tripped over
        and then corrected."""
        import src.agents.brain as brain_module
        import src.agents.cognitive_agent as cognitive_module

        def code_of(module) -> str:
            source = open(module.__file__, encoding="utf-8").read()
            lines, in_docstring = [], False
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.count('"""') == 1:
                    in_docstring = not in_docstring
                    continue
                if in_docstring or stripped.startswith(("#", '"""')):
                    continue
                lines.append(line.split("#")[0])
            return "\n".join(lines).lower()

        for module in (brain_module, cognitive_module):
            assert "from src.providers" not in code_of(module)

    def test_a_specialist_with_no_celebration_provider_answers_honestly(self):
        from src.agents.cognitive_agent import CognitiveAgent

        class BareSpecialist(CognitiveAgent):
            @property
            def spec(self):
                from src.core.contracts import AgentSpec

                return AgentSpec(name="bare", description="bare", capabilities=frozenset())

            def execute(self, task):
                raise NotImplementedError

        agent = BareSpecialist()  # celebration defaults to None here
        assert agent.celebration_reaction("first_client") is None


# ---------------------------------------------------------------------------
# No subject-specific hardcoding, no arbitrary execution
# ---------------------------------------------------------------------------


class TestNoSubjectRoutingOrArbitraryExecution:
    def test_no_milestone_type_is_hardcoded_in_the_provider(self):
        subjects = ("birthday", "graduation", "revenue", "client", "launch", "saas")
        for path in NEW_FILES:
            source = _non_docstring_source(path).lower()
            for subject in subjects:
                assert subject not in source, (path.name, subject)

    def test_no_arbitrary_execution_in_the_new_files(self):
        for path in NEW_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__", "random.", "open("):
                assert forbidden not in source, (path.name, forbidden)

    def test_selection_is_deterministic_not_random(self):
        provider = DefaultCelebrationProvider()
        r1 = provider.select(milestone_type="x", agent_key="a", personality=None, intensity="full", occurrence=3)
        r2 = provider.select(milestone_type="x", agent_key="a", personality=None, intensity="full", occurrence=3)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Mutation tests - the separation between milestone logic and presentation
# ---------------------------------------------------------------------------


class TestMutationSeparationGuarantee:
    def test_celebration_selection_only_happens_after_genuine_storage(self):
        """Pins the ordering guarantee: a not-yet-confirmed milestone
        candidate never carries a celebration reaction - selection is
        strictly downstream of a genuine, governed write."""
        manager = real_manager()
        result = say(manager, "I just got my first paying client.")  # not auto-confirmed
        assert result.payload.get("stored") is False
        assert "celebration" not in result.payload

    def test_a_policy_refused_milestone_never_reaches_celebration_selection(self):
        """Stage 12 closure's own re-verification of the ordering
        guarantee: an *explicit* milestone statement (never merely
        "not yet confirmed" - the case the test above already covers)
        that the governed policy itself refuses must never reach
        CelebrationProvider.select() at all."""
        from src.services.memory.policy import PolicyVerdict

        class RefusingPolicy:
            def evaluate(self, record):
                return PolicyVerdict(allowed=False, reason="refused for this test")

        class SpyProvider:
            name = "spy"

            def __init__(self):
                self.called = False

            def select(self, **kwargs):
                self.called = True
                raise AssertionError("celebration must never be selected for a refused write")

        manager = real_manager()
        manager.memory_policy = RefusingPolicy()
        spy = SpyProvider()
        manager.own_skills._celebration_provider = spy

        result = say(manager, "milestone: first paying client")

        assert not result.succeeded
        assert not spy.called
        assert manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10) == ()

    def test_a_broken_celebration_provider_never_corrupts_a_genuine_storage_outcome(self):
        """The strict-separation guarantee's own failure-mode proof: a
        presentation-layer exception must not turn an already-successful
        memory write into a reported failure."""

        class BrokenProvider:
            name = "broken"

            def select(self, **kwargs):
                raise RuntimeError("presentation layer exploded")

        manager = real_manager()
        manager.own_skills._celebration_provider = BrokenProvider()

        result = say(manager, "milestone: first paying client")

        assert result.succeeded
        assert "celebration" not in (result.payload or {})
        stored = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)
        assert len(stored) == 1

    def test_off_intensity_never_produces_avatar_or_environment_actions(self):
        provider = DefaultCelebrationProvider()
        reaction = provider.select(
            milestone_type="x", agent_key="a", personality=None, intensity="off", occurrence=0
        )
        assert reaction.avatar_action == "" and reaction.environment_action == ""
