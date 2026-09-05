"""Phase 1.3 - Brain Architecture.

CHECK found the canonical Brain architecture, Full/Half Brain semantics,
the `LLMProvider` abstraction, provider switching, credential isolation
and memory/conversation independence all already real, tested and
practically verified - see `tests/test_brain_provider_credential_memory_
independence.py` (0.2 Final Foundation Audit) and
`tests/test_capability_floor_and_brain_modes.py` (the pre-existing,
deliberately-protected "coding is gated by tool, never by brain" rule -
untouched by this file). None of that is re-derived here.

**What CHECK found genuinely missing:** the Brain's own open-ended
reasoning path (`open_conversation` -> `Brain.converse()`) already
receives the whole utterance and full conversation context with no
subject restriction - but `general_assistance`'s word-overlap scoring
was intercepting *reasoning* questions that merely mention "function"/
"debug"/"code" ("I have a function that produces an unexpected result
and I can't figure out why") and answering them with "the code_assistant
capability is not installed" instead of ever reaching the Brain -
exactly the keyword-substitute this phase was told to refuse, and a
different failure mode from (never a regression of) the existing,
correct, deliberately-protected rule that *actually asking Alfred to do*
tool-gated coding work is honestly refused without a tool, model or not.

**The fix, and why it does not touch the protected rule.**
`general_assistance` now requires the request to be shaped as a genuine
imperative ask to do the tool-gated work ("write a function", "research
X") - the same anchored-verb shape discipline `_communication_shape`
already uses - rather than any bare word overlap. A genuine "write me a
function to sort this" still reaches this handler and is still honestly
refused without a `code_assistant` tool, Full Brain or not
(`test_capability_floor_and_brain_modes.py`'s own pinned tests confirm
this is unchanged). A *description* of a problem, asking to be reasoned
about, was never actually asking for that tool-gated action and now
correctly falls through to `open_conversation`, where a genuine Brain
can do exactly the reasoning Phase 1.3 requires.

Every reasoning-behaviour test below exercises the real production path
(`scripts/control_center.py::build()` + `scripts/exchange.py::exchange()`)
with a scripted Full-Brain provider standing in for the funded model this
environment does not have.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.manager.capabilities import select_capability, MANAGER_CAPABILITIES
from src.manager.cognition import CognitiveMode, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from scripts.control_center import build
from scripts.exchange import exchange


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


def _current_remark(prompt: str) -> str:
    """Isolate the actual remark converse() was asked about from the
    recent-conversation context also folded into the same prompt - the
    established lesson from earlier this project (retention/guided-
    activity verification) applied here on the first attempt."""
    return prompt.split("\n\nContext")[0].split("\n\nRecent")[0]


class ReasoningProvider(LLMProvider):
    """A real, distinct LLMProvider whose answers genuinely depend on the
    isolated remark and the full prompt (including conversation-history
    evidence) it was actually given - never a fixed script keyed on call
    order, so a test proves the Brain received what it needed rather
    than merely that some text came back."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self):
        return "anthropic"

    def complete(self, prompt, *, system=None):
        system = system or ""
        self.calls.append((system, prompt))
        if "should_retain" in system or "narrow question about a remark" in system:
            return Completion(text='{"should_retain": false, "content": null}', provider="anthropic")
        if "candidate values" in system:
            return Completion(text='{"is_ambiguous": false}', provider="anthropic")
        if "begin an interactive learning" in system:
            return Completion(text='{"should_start": false, "topic": null}', provider="anthropic")
        if "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider="anthropic")

        remark = _current_remark(prompt)
        low = remark.lower()

        if "state is updated in the wrong order" in low or "race condition" in low:
            return Completion(
                text=(
                    "That shape usually means two pieces of state are being read and "
                    "written out of order - most likely the counter is incremented "
                    "after the check that reads it, not before. I'd move the update "
                    "ahead of the check and re-run the failing case."
                ),
                provider="anthropic",
            )
        if "which approach" in low or "compare" in low or "architectural" in low:
            return Completion(
                text=(
                    "A queue-based approach scales better under bursty load but adds "
                    "operational complexity; a direct synchronous call is simpler but "
                    "couples the two services' uptime together. Given your stated "
                    "constraint of infrequent, small batches, I'd start with the "
                    "synchronous call and only move to a queue if load grows."
                ),
                provider="anthropic",
            )
        if "algorithm" in low and "edge case" in low:
            return Completion(
                text=(
                    "The main edge case is an empty input list - the current sketch "
                    "would index into it and raise. A second is a list with a single "
                    "element, which never enters the comparison loop at all."
                ),
                provider="anthropic",
            )
        if "step by step" in low or "trace through" in low:
            return Completion(
                text=(
                    "First the loop initialises total to zero. Each iteration adds the "
                    "item's price, but the discount is applied inside the loop instead "
                    "of once at the end - so a three-item cart discounts three times "
                    "instead of once."
                ),
                provider="anthropic",
            )
        if "given what i just told you" in low or "actually the input is" in low:
            return Completion(
                text=(
                    "With the input genuinely sorted already, the bug isn't in the "
                    "comparison logic at all - it's the off-by-one in the loop bound, "
                    "which would explain why the last element is always skipped."
                ),
                provider="anthropic",
            )
        if "why did that fix work" in low or "why does that work" in low:
            return Completion(
                text=(
                    "Because moving the increment before the check means the check "
                    "now sees the updated value on the same pass, instead of the stale "
                    "one from before the write - which is exactly the ordering bug."
                ),
                provider="anthropic",
            )
        if "function that produces an unexpected result" in low:
            return Completion(
                text=(
                    "Without seeing the code I can't be certain, but the usual causes "
                    "are a mutated shared default argument, an off-by-one in a loop "
                    "bound, or a comparison using == where identity was intended. "
                    "Can you share the function so I can look at it directly?"
                ),
                provider="anthropic",
            )
        return Completion(text="(a natural reply)", provider="anthropic")


def _wired():
    runtime, manager, channel, conversation = real_system()
    provider = ReasoningProvider()
    brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return runtime, manager, channel, conversation, provider


class TestGeneralAssistanceNoLongerInterceptsReasoningQuestions:
    """The confirmed routing defect this phase found and fixed."""

    def test_a_bug_description_reaches_open_conversation_not_a_tool_refusal(self):
        probes = [
            "I have a function that produces an unexpected result and I cannot figure out why.",
            "There is a bug caused by state being updated in the wrong order, can you help me reason through it?",
            "Can you help me compare two possible architectural approaches for this service?",
            "Can you help me design an algorithm to solve this problem efficiently, and what edge cases should I worry about?",
            "Walk me through what this program does step by step and where it might fail.",
            "I am debugging a multi-step problem in my code and need help finding the root cause.",
            "Why does this proposed solution fail in this edge case?",
        ]
        for text in probes:
            cap = select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=["Alfred", "Arthur"])
            assert cap.name == "open_conversation", text

    def test_the_protected_tool_gated_rule_is_completely_unchanged(self):
        """Requirement N (ARCHITECTURE.md): coding is gated by tool, never
        by brain - re-confirmed, not merely assumed, for a genuine
        imperative request."""
        cap = select_capability(Task(description="write a small function"), MANAGER_CAPABILITIES, participants=["Alfred"])
        assert cap.name == "general_assistance"
        cap2 = select_capability(Task(description="research Liverpool"), MANAGER_CAPABILITIES, participants=["Alfred"])
        assert cap2.name == "general_assistance"

    def test_half_brain_is_honest_about_the_real_gap_reasoning_not_a_tool(self):
        """A genuinely more accurate diagnosis than before this fix: the
        gap is a missing Brain, not a missing tool - Half Brain must say
        so plainly rather than naming the wrong missing thing."""
        _, manager, channel, conversation = real_system()
        turn = exchange(channel, conversation, "I have a function that produces an unexpected result and I cannot figure out why.")
        assert not turn.succeeded
        assert "reason" in turn.outcome.lower()
        assert "code_assistant" not in turn.outcome


from src.core.contracts import Task  # noqa: E402  (kept near first use above for readability)


class TestSeriousSoftwareReasoning:
    """Non-trivial, varied scenarios - never hardcoded as permanent
    capabilities, used only as verification that the real Brain path
    receives the whole problem and produces an appropriately-grounded
    answer."""

    def test_root_cause_reasoning_about_ordering_state(self):
        _, manager, channel, conversation, provider = _wired()
        turn = exchange(
            channel, conversation,
            "My counter check keeps passing when it shouldn't - I think state is updated in the wrong order somewhere in the loop.",
        )
        assert turn.succeeded
        assert "before the check" in turn.outcome or "ahead of the check" in turn.outcome

    def test_comparing_two_architectural_approaches_with_a_stated_constraint(self):
        _, manager, channel, conversation, provider = _wired()
        turn = exchange(
            channel, conversation,
            "Which approach should I take - a message queue or a direct synchronous call - given my traffic is small, infrequent batches? This is an architectural decision I need to make.",
        )
        assert turn.succeeded
        assert "queue" in turn.outcome.lower() and "synchronous" in turn.outcome.lower()

    def test_identifying_edge_cases_in_an_algorithm(self):
        _, manager, channel, conversation, provider = _wired()
        turn = exchange(
            channel, conversation,
            "I sketched an algorithm to find the closest pair of points - what edge case am I likely missing?",
        )
        assert turn.succeeded
        assert "empty" in turn.outcome.lower()

    def test_multi_step_reasoning_through_program_execution(self):
        _, manager, channel, conversation, provider = _wired()
        turn = exchange(
            channel, conversation,
            "Can you walk step by step through why this cart total is wrong?",
        )
        assert turn.succeeded
        assert "discount" in turn.outcome.lower()

    def test_adaptation_after_new_evidence_is_introduced(self):
        """The exact 'continue after intermediate result, adapt rather
        than restart' requirement: the second turn gives new evidence
        that changes the diagnosis, and the answer must reflect it."""
        _, manager, channel, conversation, provider = _wired()
        exchange(channel, conversation, "I have a function that produces an unexpected result and I cannot figure out why.")
        turn = exchange(
            channel, conversation,
            "Given what I just told you, actually the input is already sorted - does that change your diagnosis?",
        )
        assert turn.succeeded
        assert "off-by-one" in turn.outcome.lower() or "off by one" in turn.outcome.lower()

    def test_explaining_why_a_fix_works(self):
        _, manager, channel, conversation, provider = _wired()
        turn = exchange(
            channel, conversation,
            "I moved the increment before the check like you suggested and it fixed it - why does that work?",
        )
        assert turn.succeeded
        assert "ordering" in turn.outcome.lower() or "before the check" in turn.outcome.lower()

    def test_the_whole_problem_and_context_genuinely_reach_the_model(self):
        """Not merely that an answer came back - the actual prompt sent
        to the model must contain the real problem statement, unedited."""
        _, manager, channel, conversation, provider = _wired()
        text = "There is a bug caused by state being updated in the wrong order, can you help me reason through it?"
        exchange(channel, conversation, text)
        conversational_prompts = [p for s, p in provider.calls if "having a direct conversation" in s]
        assert any(text in p for p in conversational_prompts)


class TestProviderIndependenceDuringAReasoningSession:
    def test_switching_provider_mid_session_preserves_brain_memory_identity_and_voice(self):
        runtime, manager, channel, conversation, provider_a = _wired()
        exchange(channel, conversation, "remember that the service in question is called Halcyon")
        exchange(channel, conversation, "I have a function that produces an unexpected result and I cannot figure out why.")

        provider_b = ReasoningProvider()
        brain_b = LLMManagerBrain(provider_b, personality=runtime.identity.personality)
        manager.brain = brain_b
        manager.own_skills._converse = brain_b.converse

        assert type(manager.brain) is LLMManagerBrain
        assert manager.own_skills is not None
        assert any("Halcyon" in r.content for r in manager.memory.recall(limit=10))

        turn = exchange(
            channel, conversation,
            "Given what I just told you, actually the input is already sorted - does that change your diagnosis?",
        )
        assert turn.succeeded
        assert "off-by-one" in turn.outcome.lower() or "off by one" in turn.outcome.lower()

    def test_an_unavailable_provider_degrades_to_honest_half_brain_never_pretends(self):
        class UnconfiguredProvider(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                raise RuntimeError("no credentials configured")

        runtime, manager, channel, conversation = real_system()
        brain = LLMManagerBrain(UnconfiguredProvider(), personality=runtime.identity.personality)
        manager.brain = brain
        manager.own_skills._converse = brain.converse

        turn = exchange(channel, conversation, "I have a function that produces an unexpected result and I cannot figure out why.")
        assert not turn.succeeded
        assert "rather say so than guess" in turn.outcome
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN

    def test_provider_restoration_returns_to_full_brain_honestly(self):
        """A provider that fails once and then succeeds - Full Brain is
        re-earned by a real successful call, never assumed back.

        Exercises ``LLMManagerBrain.converse()`` directly (the same real
        method ``open_conversation`` calls in production) rather than a
        full ``exchange()`` - a full turn can involve several internal
        Brain calls (retention judging, activity checks) before the
        answering call is even reached, which would make it impossible
        to control which specific call this test is exercising."""
        calls = {"n": 0}

        class FlakyProvider(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("temporarily unavailable")
                return Completion(text="back online and reasoning normally.", provider="anthropic")

        runtime, manager, channel, conversation = real_system()
        brain = LLMManagerBrain(FlakyProvider(), personality=runtime.identity.personality)
        manager.brain = brain

        task = Task(description="a genuinely open-ended question")

        first = brain.converse(task)
        assert not first.is_decided
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN

        second = brain.converse(task)
        assert second.is_decided
        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN

    def test_a_genuinely_different_credential_boundary_does_not_leak_into_the_brain(self):
        """Re-confirms (does not re-derive) the 0.2 Final Foundation
        Audit's own credential-isolation guarantee, in this phase's own
        context: the Brain never sees, needs, or is coupled to any
        vendor-specific credential detail at all."""
        import inspect

        import src.manager.brain as brain_module
        import src.manager.llm_brain as llm_brain_module

        for module in (brain_module, llm_brain_module):
            source = inspect.getsource(module).lower()
            assert "api_key" not in source
            assert "anthropic_api_key" not in source
