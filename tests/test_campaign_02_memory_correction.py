"""Pre-Stage-9 Campaign 2 - correcting what Alfred remembers, in ordinary
words, and asking whenever more than one stored fact could be meant.

Everything runs through the real production composition with no language
model configured. The phrasings below are examples of the general behaviour;
the code under test contains no subject or value at all.
"""

from __future__ import annotations

import tempfile

import pytest

from scripts.control_center import build
from scripts.exchange import exchange
from src.config.settings import Settings
from src.services.memory.correction import extract_correction
from src.services.memory.personal_context import all_current_personal_context
from src.services.profile.profile import SkillTier

REFUSAL = "no reasoning capability is configured"


def real_system(tmp_dir: "str | None" = None):
    overrides = {"profile_path": "does-not-exist-USER.md"}
    if tmp_dir is None:
        overrides["memory"] = "ephemeral"
    else:
        overrides.update(memory="persistent", memory_dir=tmp_dir)
    return build(Settings.from_env(env={}, **overrides))


def say(system, text):
    _runtime, _manager, channel, conversation = system
    return exchange(channel, conversation, text)


def current(system):
    return {item.topic: item for item in all_current_personal_context(recall=system[1].memory.recall)}


class TestCorrectionInOrdinaryWords:
    @pytest.mark.parametrize(
        "said",
        [
            "You remembered that incorrectly. It's green.",
            "That's wrong, it's green.",
            "Actually it's green, not blue.",
            "No, it's green.",
            "Alfred, that's incorrect: it's green",
            "Update what you remembered about my favourite colour to green.",
        ],
    )
    def test_each_phrasing_replaces_the_one_stored_fact(self, said):
        system = real_system()
        say(system, "My favourite colour is blue")
        turn = say(system, said)
        assert turn.succeeded, turn.outcome
        assert turn.capability == "correct_memory"
        assert REFUSAL not in turn.outcome
        facts = current(system)
        assert "green" in facts["favourite colour"].content
        assert not any("blue" in fact.content for fact in facts.values())

    def test_a_changed_preference_replaces_the_preference(self):
        system = real_system()
        say(system, "My favourite colour is blue")
        turn = say(system, "Actually, I prefer green now")
        assert turn.succeeded and turn.capability == "correct_memory"
        assert "green" in current(system)["favourite colour"].content

    def test_a_preference_that_ended_is_removed(self):
        system = real_system()
        say(system, "My favourite colour is blue")
        turn = say(system, "I don't like blue anymore.")
        assert turn.succeeded and turn.capability == "correct_memory"
        assert "favourite colour" not in current(system)

    def test_a_changed_fact_supersedes_and_keeps_history(self):
        system = real_system()
        say(system, "My schedule is Mon-Fri 9-3")
        say(system, "My schedule has changed to Tue-Thu 10-4")
        assert "Tue-Thu 10-4" in current(system)["schedule"].content

    @pytest.mark.parametrize("ordinary", ["it's raining", "that's great", "not now, later", "I don't like it", "you're right"])
    def test_ordinary_remarks_are_not_corrections(self, ordinary):
        assert extract_correction(ordinary) is None


class TestAmbiguityIsAskedNeverGuessed:
    def _two_favourites(self):
        system = real_system()
        say(system, "My favourite colour is blue")
        say(system, "My favourite food is rice")
        return system

    def test_it_asks_and_changes_nothing_until_answered(self):
        system = self._two_favourites()
        before = {topic: fact.content for topic, fact in current(system).items()}

        turn = say(system, "That's wrong, it's green.")
        assert turn.succeeded
        assert "Which should I change to green" in turn.outcome
        assert "favourite colour" in turn.outcome and "favourite food" in turn.outcome
        assert {topic: fact.content for topic, fact in current(system).items()} == before

        answer = say(system, "the colour")
        assert answer.succeeded and answer.capability == "correct_memory"
        facts = current(system)
        assert "green" in facts["favourite colour"].content
        assert "rice" in facts["favourite food"].content

    def test_it_can_be_answered_by_position(self):
        system = self._two_favourites()
        question = say(system, "That's wrong, it's green.").outcome
        first_is_food = question.index("favourite food") < question.index("favourite colour")
        say(system, "the second one")
        facts = current(system)
        changed = "favourite colour" if first_is_food else "favourite food"
        assert "green" in facts[changed].content

    def test_declining_leaves_everything_as_it_was(self):
        system = self._two_favourites()
        before = {topic: fact.content for topic, fact in current(system).items()}
        say(system, "That's wrong, it's green.")
        turn = say(system, "never mind")
        assert turn.succeeded and "left everything" in turn.outcome
        assert {topic: fact.content for topic, fact in current(system).items()} == before

    def test_an_unrelated_reply_drops_the_question(self):
        system = self._two_favourites()
        say(system, "That's wrong, it's green.")
        other = say(system, "what's the date today?")
        assert other.capability == "current_datetime"
        assert system[1].own_skills._activity_sessions.pending_correction(system[1].own_skills.name) is None

    def test_a_new_statement_is_never_taken_as_the_answer(self):
        """Found at runtime: with "which one?" open, "That's wrong, I'm still
        learning SQL" named an offered topic and was consumed as the answer,
        applying the pending value to the wrong fact."""
        system = real_system()
        say(system, "My favourite colour is blue")
        say(system, "I've learned SQL")
        asked = say(system, "You remembered that incorrectly. It's green.")
        assert "Which should I change to green" in asked.outcome

        turn = say(system, "That's wrong, I'm still learning SQL")
        assert turn.succeeded and turn.capability == "correct_memory"
        facts = current(system)
        assert facts["SQL"].category == "learning"
        assert "blue" in facts["favourite colour"].content
        assert "green" not in facts
        manager = system[1]
        assert manager.own_skills._activity_sessions.pending_correction(manager.own_skills.name) is None

    def test_removal_is_asked_too(self):
        system = self._two_favourites()
        turn = say(system, "That's no longer my preference.")
        assert "Which should I remove" in turn.outcome
        assert len(current(system)) == 2


class TestPersistenceAndSpecialists:
    def test_a_correction_survives_restart(self):
        memory_dir = tempfile.mkdtemp()
        first = real_system(memory_dir)
        say(first, "My favourite colour is blue")
        say(first, "You remembered that incorrectly. It's green.")
        second = real_system(memory_dir)
        assert "green" in current(second)["favourite colour"].content
        assert "green" in say(second, "what's my favourite colour?").outcome

    def test_arthur_sees_the_corrected_skill(self):
        system = real_system()
        manager = system[1]
        say(system, "I've learned SQL")
        analyzer = manager.registry.get("Arthur").analyzers[0]
        assert analyzer.current_profile().tier_of("SQL") is SkillTier.WORKING
        turn = say(system, "That's wrong, I'm still learning SQL")
        assert turn.succeeded and turn.capability == "correct_memory"
        assert analyzer.current_profile().tier_of("SQL") is SkillTier.LEARNING


class TestAPureNoteIsAcknowledgedPlainly:
    def test_no_refusal_is_attached_when_nothing_else_was_asked(self):
        system = real_system()
        turn = say(system, "remember that the blue folder holds the tax papers")
        assert turn.succeeded
        assert REFUSAL not in turn.outcome and "reason about" not in turn.outcome
        assert "tax papers" in turn.outcome
