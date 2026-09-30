"""Phase 11 closure: a question put to the person is answered by the person.

Found by running the real system - the first two only with the real local
model (``provider=local brain=llm``) - and all the same shape: something that
was not the person's answer was taken as one.

* The trigger. A plain question ("In two sentences, why does a millstone need
  dressing?") was judged "ambiguous" by the model, with candidates it never
  stated ("a millstone" / "a mill", once the schema's own placeholder text),
  and a question naming alternatives ("... Sydney or Canberra?") was put back
  to the person as "which is correct?". A remark that asks is answered; a
  candidate must be said in the remark, as words of its own.
* The objective loop. Full Brain pursued the turn after a step had asked the
  person something, and its own next step was taken as the answer: "Got it -
  I'll go with a millstone from now on", remembered as a fact - and the same
  gap confirmed a pending send nobody approved. The loop now stops there.
* The confirmation gate. yes/no were matched as bare substrings: "are you
  sure?" sent the message ("sure"), "yes, send it now" cancelled it ("no" in
  "now"), "don't send it" with a curly apostrophe sent it.
* The correction question. "Which should I change?" took any remark that
  mentioned an option as the choice and wrote the stale replacement over it -
  a recall question rewrote a favourite colour; a restated note became
  "green". A choice now only names.

Every test drives the real production door (``build()`` + ``exchange()``); a
scripted provider stands in for the model, deliberately misbehaving the way
the real local model did. The collision matrix at the end keeps the five kinds
of remark - substantive questions, memory creation, correction, forgetting and
ordinary conversation - apart in both Brain modes.
"""

from __future__ import annotations

import json
import re

import pytest

import scripts.control_center as control_center_module
from scripts.control_center import build
from scripts.exchange import exchange
from src.agents.tools.communication import CommunicationSendTool
from src.agents.tools.toolset import ToolSet
from src.config.settings import Settings
from src.manager.activity_session import (
    ActivityBrainOps,
    ActivitySessions,
    CorrectionOption,
    GuidedActivityState,
    PendingClarification,
    PendingConfirmation,
    PendingCorrection,
)
from src.manager.guided_interaction import _candidates_were_stated
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.providers.communication import (
    CommunicationProvider,
    CommunicationResult,
    CommunicationStatus,
    utc_now,
)

ANSWER = "Dressing recuts the grooves that grind the grain."


@pytest.fixture(autouse=True)
def _shipped_ecosystem_only(tmp_path, monkeypatch):
    """The owner's own specialists and connections are not inputs to this
    file (see test_interaction_clarification_and_learning.py for the day one
    was)."""
    for name, leaf in (
        ("DEFAULT_CONFIG_PATH", "config.json"), ("DEFAULT_LEDGER_PATH", "ledger.json"),
        ("DEFAULT_ACCOUNTS_PATH", "accounts.json"), ("DEFAULT_SERVICES_PATH", "services.json"),
        ("DEFAULT_SITES_PATH", "sites.json"),
    ):
        monkeypatch.setattr(control_center_module, name, tmp_path / leaf)


class _Model(LLMProvider):
    """Judges every remark ambiguous with the given candidates, and always
    wants the objective continued with ``next_step`` - the two ways the real
    local model misbehaved. Everything else answers plainly."""

    def __init__(self, *, candidates=("a millstone", "a mill"), question="What is the subject of the sentence?",
                 next_step="Describe a millstone"):
        self.candidates = candidates
        self.question = question
        self.next_step = next_step
        self.ambiguity_asked: list[str] = []

    @property
    def name(self):
        return "anthropic"

    def complete(self, prompt, *, system=None):
        system = system or ""
        if "candidate values" in system:
            self.ambiguity_asked.append(re.search(r"THE REMARK:\n(.*?)\n", prompt).group(1))
            return self._json({"is_ambiguous": True, "candidates": list(self.candidates), "question": self.question})
        if "judging the progress of one objective" in system:
            return self._json({"status": "continue", "next_step": self.next_step, "question": None, "final_answer": None})
        if "begin an interactive learning" in system:
            return self._json({"should_start": False, "topic": None})
        if "should_retain" in system or "narrow question about a remark" in system:
            return self._json({"should_retain": False, "content": None})
        if "Reply with a single JSON object" in system:
            return self._json({"action": "unknown", "reason": "n/a"})
        return Completion(text=ANSWER, provider="anthropic")

    @staticmethod
    def _json(data):
        return Completion(text=json.dumps(data), provider="anthropic")


class _Recorder(CommunicationProvider):
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    @property
    def name(self):
        return "recorder"

    def read_messages(self, *, limit=10):
        return ()

    def send_message(self, *, to, content):
        self.sent.append((to, content))
        return CommunicationResult(operation="send", party=to, status=CommunicationStatus.SUCCEEDED,
                                   source=self.name, attempted_at=utc_now())

    def place_call(self, *, to):
        return CommunicationResult(operation="call", party=to, status=CommunicationStatus.SUCCEEDED,
                                   source=self.name, attempted_at=utc_now())


def half_brain():
    _runtime, manager, channel, conversation = build(Settings.from_env(env={}, memory="ephemeral"))
    return manager, channel, conversation


def full_brain(model: "_Model | None" = None):
    runtime, manager, channel, conversation = build(Settings.from_env(env={}, memory="ephemeral"))
    brain = LLMManagerBrain(model or _Model(), personality=runtime.identity.personality)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    manager.own_skills._activity_brain = ActivityBrainOps.from_brain(brain)
    return manager, channel, conversation


def memories(manager) -> list[str]:
    return [record.content for record in manager.memory.recall(limit=50)]


def sessions(manager) -> ActivitySessions:
    return manager.own_skills._activity_sessions


def with_recorder(manager) -> _Recorder:
    recorder = _Recorder()
    manager.own_skills._tools = ToolSet([CommunicationSendTool(recorder)])
    return recorder


# ---------------------------------------------------------------------------
# The trigger: questions are answered; candidates must have been said
# ---------------------------------------------------------------------------


class TestAQuestionIsAnsweredNeverAskedBack:
    def test_the_millstone_question_is_answered_and_nothing_is_remembered(self):
        model = _Model()
        manager, channel, conversation = full_brain(model)
        turn = exchange(channel, conversation, "In two sentences, why does a millstone need dressing?")
        assert ANSWER in turn.outcome
        assert "from now on" not in turn.outcome
        assert memories(manager) == []
        assert not sessions(manager).awaits_reply("Alfred")
        # The loop's own statement-form steps may be judged (and their invented
        # candidates refused); the person's question never is.
        assert "In two sentences, why does a millstone need dressing?" not in model.ambiguity_asked

    def test_a_question_naming_alternatives_is_not_put_back_to_the_person(self):
        model = _Model(candidates=("Sydney", "Canberra"), question="What is the capital of Australia?")
        manager, channel, conversation = full_brain(model)
        turn = exchange(channel, conversation, "What's the capital of Australia, Sydney or Canberra?")
        assert ANSWER in turn.outcome
        reply = exchange(channel, conversation, "Canberra")
        assert "from now on" not in reply.outcome
        assert not any("unsure between" in m for m in memories(manager))

    def test_half_brain_says_it_cannot_answer_and_writes_nothing(self):
        manager, channel, conversation = half_brain()
        turn = exchange(channel, conversation, "In two sentences, why does a millstone need dressing?")
        assert not turn.succeeded
        assert "rather say so than guess" in turn.outcome
        assert memories(manager) == []


class TestOnlyStatedCandidatesAreAskedAbout:
    @pytest.mark.parametrize("candidates, remark", [
        (("a millstone", "a mill"), "I think a millstone needs dressing."),
        (("the first candidate value", "the second"), "I think a millstone needs dressing."),
        (("Tuesday", "next Tuesday"), "It's maybe next Tuesday."),
        (("Tuesday", "Tuesday"), "It's Tuesday or so."),
        (("Tuesday", ""), "It's Tuesday or Wednesday."),
    ])
    def test_a_candidate_not_said_on_its_own_is_refused(self, candidates, remark):
        assert not _candidates_were_stated(candidates, remark)

    @pytest.mark.parametrize("candidates, remark", [
        (("Tuesday", "Wednesday"), "It's either Tuesday or Wednesday, I can't remember which."),
        (("the 5th", "the 12th."), "The deadline is the 5th or maybe the 12th, I'm not sure."),
        (("a mill", "a millstone"), "It was either a mill or a millstone, not sure."),
    ])
    def test_candidates_said_in_the_remark_are_accepted(self, candidates, remark):
        assert _candidates_were_stated(candidates, remark)

    def test_an_invented_candidate_raises_no_clarification_at_the_door(self):
        manager, channel, conversation = full_brain(_Model(next_step=""))
        turn = exchange(channel, conversation, "I think a millstone needs dressing every autumn.")
        assert "subject of the sentence" not in turn.outcome
        assert sessions(manager).pending_clarification("Alfred") is None


# ---------------------------------------------------------------------------
# The objective loop never answers on the person's behalf
# ---------------------------------------------------------------------------


class TestTheObjectiveLoopWaitsForThePerson:
    def test_a_clarification_waits_and_the_persons_own_answer_still_resolves_it(self):
        model = _Model(candidates=("Tuesday", "Wednesday"), question="Which is it - Tuesday or Wednesday?",
                       next_step="Tuesday")
        manager, channel, conversation = full_brain(model)
        asked = exchange(channel, conversation, "It's either Tuesday or Wednesday, I can't remember which.")
        assert asked.outcome == "Which is it - Tuesday or Wednesday?"
        assert sessions(manager).pending_clarification("Alfred") is not None
        assert memories(manager) == [], "the loop's own step must not have answered it"

        model.next_step = ""  # the person's own answer is the whole of the next turn
        answered = exchange(channel, conversation, "Wednesday")
        assert answered.outcome == "Got it - I'll go with Wednesday from now on."
        assert any("Resolved: Wednesday" in m for m in memories(manager))

    def test_a_pending_send_is_never_confirmed_by_the_loop(self):
        manager, channel, conversation = full_brain(_Model(next_step="Yes, make sure the message is sent"))
        recorder = with_recorder(manager)
        asked = exchange(channel, conversation, "send Kai a message saying the quern is ready")
        assert "(yes/no)" in asked.outcome
        assert recorder.sent == []
        exchange(channel, conversation, "yes")
        assert recorder.sent == [("Kai", "saying the quern is ready")]

    @pytest.mark.parametrize("raise_it", [
        lambda s: s.raise_clarification("Alfred", PendingClarification("Which?", ("a", "b"))),
        lambda s: s.raise_confirmation("Alfred", PendingConfirmation(operation="send", party="Kai", content="hi", description="Send?")),
        lambda s: s.raise_correction("Alfred", PendingCorrection(
            "Which?", "replace", "green", (CorrectionOption("a", "1"), CorrectionOption("b", "2")))),
        lambda s: s.start_activity("Alfred", GuidedActivityState(topic="anything")),
    ])
    def test_every_question_to_the_person_is_awaited(self, raise_it):
        store = ActivitySessions()
        assert not store.awaits_reply("Alfred")
        raise_it(store)
        assert store.awaits_reply("Alfred")
        assert not store.awaits_reply("Someone else")


# ---------------------------------------------------------------------------
# The consequential-action gate reads words, not pieces of words
# ---------------------------------------------------------------------------


class TestTheConfirmationGateReadsWholeWords:
    def _asked(self, brain):
        manager, channel, conversation = brain()
        recorder = with_recorder(manager)
        exchange(channel, conversation, "send Kai a message saying the quern is ready")
        return manager, channel, conversation, recorder

    @pytest.mark.parametrize("brain", [half_brain, full_brain])
    @pytest.mark.parametrize("reply", [
        "are you sure?", "what is the pressure like there?", "yesterday was fun", "I'm not sure", "yes?",
    ])
    def test_a_reply_that_is_not_a_yes_sends_nothing(self, brain, reply):
        _manager, channel, conversation, recorder = self._asked(brain)
        exchange(channel, conversation, reply)
        assert recorder.sent == []

    @pytest.mark.parametrize("brain", [half_brain, full_brain])
    @pytest.mark.parametrize("reply", ["yes, send it now", "do it now please", "yes", "Sure.", "go ahead"])
    def test_a_clear_yes_sends(self, brain, reply):
        _manager, channel, conversation, recorder = self._asked(brain)
        exchange(channel, conversation, reply)
        assert recorder.sent == [("Kai", "saying the quern is ready")]

    @pytest.mark.parametrize("reply", ["don’t send it", "dont send it", "no, cancel that", "not now"])
    def test_a_clear_no_cancels(self, reply):
        manager, channel, conversation, recorder = self._asked(half_brain)
        turn = exchange(channel, conversation, reply)
        assert "won't" in turn.outcome
        assert recorder.sent == []
        assert sessions(manager).pending_confirmation("Alfred") is None


# ---------------------------------------------------------------------------
# The correction question takes a choice, never a remark of its own
# ---------------------------------------------------------------------------


class TestTheCorrectionQuestionOnlyTakesAChoice:
    def _asked(self):
        manager, channel, conversation = half_brain()
        for remark in (
            "remember that my favourite colour is blue",
            "remember that the pump seal was replaced in March",
            "remember that I prefer short written answers",
            "actually my favourite colour is green",
        ):
            exchange(channel, conversation, remark)
        asked = exchange(channel, conversation, "actually it's green, not blue")
        assert "Which should I change" in asked.outcome
        return manager, channel, conversation

    def test_a_recall_question_is_answered_not_taken_as_the_choice(self):
        manager, channel, conversation = self._asked()
        turn = exchange(channel, conversation, "what do you remember about my favourite colour?")
        assert "green" in turn.outcome
        assert "corrected" not in turn.outcome
        assert "pump seal" not in exchange(channel, conversation, "what's my favourite colour?").outcome

    def test_a_restated_note_is_never_overwritten_with_the_pending_value(self):
        manager, channel, conversation = self._asked()
        exchange(channel, conversation, "actually the pump seal was replaced in April")
        assert "the pump seal was replaced in March" in memories(manager)
        assert not any(m.endswith("remember: green.") for m in memories(manager))
        assert "March" in exchange(channel, conversation, "what do you remember about the pump seal?").outcome

    @pytest.mark.parametrize("reply", [
        "the colour", "my favourite colour", "the first one", "the one about the colour", "colour, green",
    ])
    def test_naming_an_option_still_chooses_it(self, reply):
        options = (CorrectionOption("your favourite colour (blue)", "1", "favourite colour", "blue"),
                   CorrectionOption("your favourite food (rice)", "2", "favourite food", "rice"))
        pending = PendingCorrection("Which?", "replace", "green", options)
        assert pending.select_option(reply) is options[0]

    @pytest.mark.parametrize("reply", [
        "what do you remember about my favourite colour?", "my favourite colour is actually purple",
    ])
    def test_a_remark_of_its_own_chooses_nothing(self, reply):
        options = (CorrectionOption("your favourite colour (blue)", "1", "favourite colour", "blue"),
                   CorrectionOption("your favourite food (rice)", "2", "favourite food", "rice"))
        assert PendingCorrection("Which?", "replace", "green", options).select_option(reply) is None


# ---------------------------------------------------------------------------
# Memory is asked about a topic's own words, never "the"
# ---------------------------------------------------------------------------


class TestATopicIsAskedAboutByItsOwnWords:
    """memory/query.py: the caller decides what to ask about. Three callers
    passed the raw topic, so "the" matched every note."""

    def _kiln(self):
        manager, channel, conversation = half_brain()
        exchange(channel, conversation, "remember that the kiln shelf is cracked")
        return manager, channel, conversation

    def test_recall_of_an_unknown_topic_returns_nothing(self):
        _manager, channel, conversation = self._kiln()
        turn = exchange(channel, conversation, "what do you remember about the boiler?")
        assert turn.outcome == "I don't have anything remembered about that yet."

    def test_correcting_an_unknown_topic_changes_nothing(self):
        manager, channel, conversation = self._kiln()
        turn = exchange(channel, conversation, "update what you remembered about the boiler to serviced in May")
        assert "don't have anything remembered about the boiler" in turn.outcome
        assert memories(manager) == ["the kiln shelf is cracked"]

    def test_forgetting_an_unknown_topic_forgets_nothing(self):
        manager, channel, conversation = self._kiln()
        exchange(channel, conversation, "forget what I told you about the boiler")
        assert memories(manager) == ["the kiln shelf is cracked"]

    def test_the_real_topic_is_still_found_corrected_and_forgotten(self):
        manager, channel, conversation = self._kiln()
        assert "cracked" in exchange(channel, conversation, "what do you remember about the kiln?").outcome
        exchange(channel, conversation, "update what you remembered about the kiln shelf to repaired")
        assert memories(manager) == ["the kiln shelf is repaired"], "a note is corrected as a note"
        exchange(channel, conversation, "forget what I told you about the kiln")
        assert memories(manager) == []


# ---------------------------------------------------------------------------
# Words handed over as material are never acted on or remembered
# ---------------------------------------------------------------------------


_PHRASES_THAT_ARE_ALSO_REQUESTS = (
    "what time is it", "add a task to buy milk", "I have a meeting tomorrow at 3", "I am learning to cook",
    "what day is it", "cancel the order", "remember that the quern is dressed", "my name is Sam",
    "good morning, how are you",
)


class TestMaterialIsNeverActedOn:
    @pytest.mark.parametrize("phrase", _PHRASES_THAT_ARE_ALSO_REQUESTS)
    @pytest.mark.parametrize("frame", ["translate {} into Spanish", "how do you say {} in French",
                                       "Nova, translate {} into Italian"])
    def test_a_translation_request_is_routed_to_translation(self, phrase, frame):
        from src.core.contracts import Task
        from src.manager.capabilities import select_capability

        assert select_capability(Task(description=frame.format(phrase))).name == "translate"

    @pytest.mark.parametrize("text, expected", [
        ("translate add a task to buy milk into Spanish", ("add a task to buy milk", "Spanish")),
        ("how do you say I live in Lagos in French", ("I live in Lagos", "French")),
        ("translate good morning, how are you into Portuguese", ("good morning, how are you", "Portuguese")),
        ("Nova, translate 'the harvest begins at dawn' into Italian", ("the harvest begins at dawn", "Italian")),
        ("translate hello into Brazilian Portuguese", ("hello", "Brazilian Portuguese")),
        ("what's the weather in Paris", None),
    ])
    def test_the_phrase_runs_to_the_last_separator(self, text, expected):
        from src.manager.capabilities import translation_request

        assert translation_request(text) == expected

    @pytest.mark.parametrize("text, is_language", [
        ("translate what time is it into Korean", True),
        ("say hello in French", True),
        ("say what the dog is doing in the footage", False),
        ("translate my notes into my notebook", False),
    ])
    def test_only_a_target_that_can_name_a_language_outranks_other_claims(self, text, is_language):
        from src.manager.capabilities import names_a_language_target

        assert names_a_language_target(text) is is_language

    def test_the_words_themselves_still_do_what_they_say(self):
        from src.core.contracts import Task
        from src.manager.capabilities import select_capability

        assert select_capability(Task(description="add a task to buy milk")).name == "create_task"
        assert select_capability(Task(description="what time is it?")).name == "current_datetime"

    @pytest.mark.parametrize("remark", [
        "translate add a task to buy milk into Spanish", "translate I have a meeting tomorrow at 3 into Spanish",
        "translate my name is Sam into Spanish", "translate remember that the quern is dressed into Spanish",
        "translate I am learning to cook into Spanish",
    ])
    def test_translating_writes_no_task_event_or_memory(self, remark):
        manager, channel, conversation = half_brain()
        exchange(channel, conversation, remark)
        assert memories(manager) == []
        assert exchange(channel, conversation, "list my tasks").outcome == "You have no pending tasks."
        assert "nothing on your calendar" in exchange(channel, conversation, "what is on my calendar tomorrow?").outcome

    def test_a_message_body_is_never_remembered_as_the_persons_own_words(self):
        manager, channel, conversation = half_brain()
        with_recorder(manager)
        exchange(channel, conversation, "send Kai a message saying my name is Sam")
        assert memories(manager) == [], "nothing is kept before, or without, the send"
        exchange(channel, conversation, "no")
        assert memories(manager) == []

    def test_what_the_person_says_to_alfred_is_still_kept(self):
        manager, channel, conversation = half_brain()
        exchange(channel, conversation, "remember that the kiln is fired on Fridays")
        exchange(channel, conversation, "my name is Ada")
        assert set(memories(manager)) == {"the kiln is fired on Fridays", "The user's name is Ada."}


# ---------------------------------------------------------------------------
# A collaboration nobody contributed to synthesises nothing
# ---------------------------------------------------------------------------


class TestAnEmptyCollaborationInventsNothing:
    """Found by the full suite against the owner's real roster: since P11Z-09
    an in-domain specialist is consulted, and when every contribution failed a
    Full Brain answered a research request from memory, reported as success."""

    def test_research_with_no_tool_fails_honestly_even_with_a_research_specialist(self, tmp_path):
        from scripts.control_center import _governed_control_center, run_command

        runtime, manager, channel, conversation = build(Settings.from_env(env={}, memory="ephemeral"))
        run_command(_governed_control_center(runtime, manager),
                    "agent create reef_lab Coral|marine biology and oceanographic research", 100,
                    channel, conversation, action_log_path=tmp_path / "action_log.json")
        manager, channel, conversation = full_brain(_Model(candidates=("x", "y"), next_step=""))
        assert "Coral" in [agent.name for agent in manager.registry.all_agents()]
        manager.own_skills._tools = ToolSet([])

        turn = exchange(channel, conversation, "research quantum computing and summarise it")
        assert not turn.succeeded
        assert "'web_search' capability is not installed" in turn.outcome
        assert ANSWER not in turn.outcome


# ---------------------------------------------------------------------------
# A request only to remember is answered by what was kept
# ---------------------------------------------------------------------------


class _AsksWhatNext(_Model):
    """The real local model's answer to "remember that ...": the fallback
    converses, then the objective judge asks the person a generic question."""

    def complete(self, prompt, *, system=None):
        if "judging the progress of one objective" in (system or ""):
            return self._json({"status": "needs_input", "next_step": None,
                               "question": "What should be attempted next?", "final_answer": None})
        return super().complete(prompt, system=system)


class TestTheSchemaHandedBackIsNoVerdict:
    @pytest.mark.parametrize("response", [
        '{"status": "needs_input", "question": "What should be attempted next?"}',
        '{"status": "continue", "next_step": "what should be attempted next"}',
        '{"status": "failed", "question": "What should be attempted next?", "final_answer": "It failed."}',
        '{"status": "complete", "final_answer": "An honest summary of what was accomplished."}',
    ])
    def test_an_echo_of_the_schema_is_unavailable(self, response):
        from src.manager.brain_prompts import parse_objective_or_unavailable

        assert not parse_objective_or_unavailable(response).decided

    @pytest.mark.parametrize("response", [
        '{"status": "needs_input", "question": "which format should the report use?"}',
        '{"status": "failed", "final_answer": "there is no way to access that data source"}',
        '{"status": "continue", "next_step": "look up the opening hours"}',
    ])
    def test_a_real_verdict_still_counts(self, response):
        from src.manager.brain_prompts import parse_objective_or_unavailable

        assert parse_objective_or_unavailable(response).decided

    def test_the_answer_is_kept_when_the_judge_only_echoes_the_schema(self):
        manager, channel, conversation = full_brain(_AsksWhatNext(candidates=("x", "y")))
        turn = exchange(channel, conversation, "Honestly today has been a pretty good day overall.")
        assert turn.outcome == ANSWER


class TestARequestToRememberIsAcknowledged:
    def test_full_brain_says_what_it_kept_not_the_models_question(self):
        manager, channel, conversation = full_brain(_AsksWhatNext())
        turn = exchange(channel, conversation, "remember that the millstone was last dressed in June")
        assert turn.outcome == "Got it - I've remembered that: the millstone was last dressed in June"
        assert memories(manager) == ["the millstone was last dressed in June"]

    def test_a_capability_that_took_the_request_keeps_its_own_answer(self):
        manager, channel, conversation = full_brain(_AsksWhatNext())
        turn = exchange(channel, conversation, "remember that my favourite colour is blue")
        assert turn.outcome == "Got it - I'll remember that your favourite colour is blue."


# ---------------------------------------------------------------------------
# The five kinds of remark stay apart, in both Brain modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brain", [half_brain, full_brain], ids=["half_brain", "full_brain"])
def test_the_five_kinds_of_remark_stay_apart(brain):
    manager, channel, conversation = brain()

    created = exchange(channel, conversation, "remember that the quern is dressed every autumn")
    assert "remembered" in created.outcome
    assert memories(manager) == ["the quern is dressed every autumn"]

    asked = exchange(channel, conversation, "In two sentences, why does a millstone need dressing?")
    assert "from now on" not in asked.outcome and "remembered" not in asked.outcome
    assert memories(manager) == ["the quern is dressed every autumn"], "a question writes no memory"

    chatted = exchange(channel, conversation, "thanks!")
    assert chatted.outcome == "You're welcome!"
    assert memories(manager) == ["the quern is dressed every autumn"]

    corrected = exchange(channel, conversation, "that's wrong, the quern is dressed every spring")
    assert corrected.succeeded
    assert "spring" in exchange(channel, conversation, "what do you remember about the quern?").outcome

    forgotten = exchange(channel, conversation, "forget what I told you about the quern")
    assert "Forgotten" in forgotten.outcome
    assert "don't have anything" in exchange(channel, conversation, "what do you remember about the quern?").outcome
    assert not sessions(manager).awaits_reply("Alfred")
