"""Half Brain must be genuinely capable, not merely honest.

The owner's standing requirement is that the system stays fully useful with no
model attached - the model is an extra capability, never a dependency. Four
places where that was not true, found by talking to the real system
(2026-09-15):

* an ordinary sentence about the person's own life ("I go to the gym on
  Mondays") matched no grammar at all, so nothing was learned from it;
* a fact that *was* learned could not be asked back unless the question began
  with "what" ("who is my dentist" reached no capability);
* a question whose answer was written in the person's own documents was
  refused rather than quoted, although quoting needs no model;
* correcting one part of a fact ("it is Thursdays") replaced the whole of it,
  losing the subject that was right.

Nothing here asks Half Brain to produce prose about a subject: it quotes,
retrieves and swaps like for like. That line is what the refusals still
protect, and the tests below hold it from both sides.
"""

from __future__ import annotations

import pytest

from src.agents.tools.knowledge import KnowledgeLookupTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.manager.brain import NullBrain
from src.services.memory.personal_context import (
    extract_personal_context_candidate,
    extract_personal_context_query,
)

from tests.test_stage18_knowledge_and_learning import library, skills


class TestAnOrdinaryLifeStatementIsLearned:
    @pytest.mark.parametrize(
        "said,topic,reads",
        [
            ("I go to the gym on Mondays", "gym", "The user goes to the gym on Mondays."),
            ("I work at Barclays", "Barclays", "The user works at Barclays."),
            ("I live in Leeds", "Leeds", "The user lives in Leeds."),
            ("I play five-a-side on Saturdays", "five-a-side", "The user plays five-a-side on Saturdays."),
        ],
    )
    def test_it_is_recognised_and_reads_like_a_sentence(self, said, topic, reads):
        candidate = extract_personal_context_candidate(said)
        assert candidate is not None, said
        assert candidate.topic == topic and candidate.content == reads

    @pytest.mark.parametrize(
        "said",
        [
            "I need help with my code",
            "I want you to send an email",
            "I think that is wrong",
            "can you check my code",
            "remind me to call the bank",
            "I would like a coffee",
            # Found by the suite, not by inspection: each of these was being
            # learned as a fact about the person's life.
            "yeah, I still need to finish my Java work.",
            "I can't believe my boss just yelled at me in front of everyone, this is so unfair.",
            "I don't like that. Choose another.",
            "I have to leave early",
            "I am going to start running",
            "I never go to the gym",
        ],
    )
    def test_a_request_or_an_opinion_is_never_learned_as_a_fact(self, said):
        assert extract_personal_context_candidate(said) is None, said

    def test_an_adverb_is_not_mistaken_for_the_verb(self):
        candidate = extract_personal_context_candidate("I usually go to the gym on Mondays")
        assert candidate is not None and candidate.content == "The user goes to the gym on Mondays."


class TestAFactCanBeAskedBackHoweverItIsAsked:
    @pytest.mark.parametrize(
        "question,focus",
        [
            ("who is my dentist", "dentist"),
            ("where is my flat", "flat"),
            ("when is my flight", "flight"),
            ("how much is my rent", "rent"),
            ("when do I go to the gym", "go to the gym"),
            ("where do I work", "work"),
            ("what is my favourite colour", "favourite colour"),
        ],
    )
    def test_the_question_word_never_decides_whether_it_is_understood(self, question, focus):
        assert extract_personal_context_query(question) == focus

    @pytest.mark.parametrize("statement", ["my dentist is Dr Ade", "I go to the gym on Mondays"])
    def test_a_statement_is_still_never_read_as_a_question(self, statement):
        assert extract_personal_context_query(statement) is None


class TestALookupIsAnsweredFromTheMaterialItself:
    def test_a_question_whose_answer_is_written_down_is_quoted_back(self, tmp_path):
        tools = ToolSet([KnowledgeLookupTool(library(
            tmp_path, trip="The train to Leeds leaves at 07:40 from platform 4."
        ))])
        agent, _ = skills(brain=NullBrain(), tools=tools)
        result = agent.execute(Task(description="What time does the train to Leeds leave?"))
        assert result.succeeded, result.error
        assert "07:40" in result.output
        assert "word for word" in result.output and "did not interpret" in result.output
        assert result.payload["reasoning"] == "none - quoted verbatim"

    def test_being_told_about_a_subject_is_still_refused(self, tmp_path):
        tools = ToolSet([KnowledgeLookupTool(library(tmp_path, b="A passage about a thing."))])
        agent, _ = skills(brain=NullBrain(), tools=tools)
        result = agent.execute(Task(description="Tell me about a passage about a thing"))
        assert not result.succeeded and "can't reason" in result.error

    @pytest.mark.parametrize("question", [
        "Explain what this document means",
        "Summarise the trip document",
        "Why does the train leave then?",
    ])
    def test_an_explanation_is_still_refused(self, question, tmp_path):
        tools = ToolSet([KnowledgeLookupTool(library(
            tmp_path, trip="The train to Leeds leaves at 07:40 from platform 4."
        ))])
        agent, _ = skills(brain=NullBrain(), tools=tools)
        result = agent.execute(Task(description=question))
        assert not result.succeeded, result.output


class TestACorrectionKeepsWhatWasRight:
    @pytest.mark.parametrize(
        "content,replacement,expected",
        [
            ("The user is learning Spanish on Tuesdays.", "Thursdays",
             "The user is learning Spanish on Thursdays."),
            ("The user goes to the gym at 7am.", "8am", "The user goes to the gym at 8am."),
        ],
    )
    def test_a_like_for_like_value_replaces_only_itself(self, content, replacement, expected):
        from src.manager.capabilities import _like_for_like_correction

        assert _like_for_like_correction(content, replacement) == expected

    def test_a_different_kind_of_value_is_left_to_the_ordinary_path(self):
        from src.manager.capabilities import _like_for_like_correction

        assert _like_for_like_correction("The user is learning Spanish on Tuesdays.", "French") is None


class TestItIsSaidBackInPlainEnglish:
    """A habitual statement is stored in the third person and said back in
    the second - "you goes to the gym" was the first thing the runtime check
    printed."""

    @pytest.mark.parametrize(
        "stored,spoken",
        [
            ("The user goes to the gym on Mondays.", "You go to the gym on Mondays."),
            ("The user works at Barclays.", "You work at Barclays."),
            ("The user watches football on Sundays.", "You watch football on Sundays."),
            ("The user flies to Lagos in December.", "You fly to Lagos in December."),
            ("The user is learning Spanish on Tuesdays.", "You're learning Spanish on Tuesdays."),
            ("The user's dentist is Dr Ade.", "Your dentist is Dr Ade."),
        ],
    )
    def test_the_verb_agrees_with_you(self, stored, spoken):
        import datetime

        from src.services.memory.personal_context import PersonalContextRecord, describe_for_user

        record = PersonalContextRecord(
            topic="t", content=stored, category="routine", status="current", noted_by="Alfred",
            created_at=datetime.datetime.now(datetime.timezone.utc), record_id="x",
        )
        assert describe_for_user(record) == spoken
