"""Pre-Stage-9 Campaign 1 - one canonical personal context, learned
continuously, recalled without a model, and read by every agent.

Everything here runs through the real production composition
(scripts.control_center.build -> scripts.exchange.exchange) with no language
model configured, so each assertion is about the deterministic floor Half
Brain actually has. The subjects used below are examples of the general
behaviour, never a list the code knows about - the code under test contains
no subject at all.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from scripts.control_center import build
from scripts.exchange import exchange
from src.config.settings import Settings
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import MemoryRecord
from src.services.memory.personal_context import (
    all_current_personal_context,
    build_personal_context_record,
    describe_for_user,
    extract_learning_activity,
    extract_personal_context_candidate,
    personal_context_history,
)
from src.services.profile.profile import SkillTier, UserProfile
from src.services.profile.user_knowledge import UserKnowledge, effective_profile

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


def topics(manager):
    return {item.topic: item for item in all_current_personal_context(recall=manager.memory.recall)}


class TestLearningIsRecognisedGenerally:
    @pytest.mark.parametrize(
        "said,topic,category",
        [
            ("I'm also learning Java.", "Java", "learning"),
            ("I'm learning C++ too.", "C++", "learning"),
            ("I've started learning Python.", "Python", "learning"),
            ("I am still studying organic chemistry", "organic chemistry", "learning"),
            ("I've learned Rust", "Rust", "skill"),
            ("My goal this year is to run a marathon", "goal this year", "context"),
            ("My favourite colour is green", "favourite colour", "context"),
            ("I'm starting a project called Beacon", "a project called Beacon", "context"),
        ],
    )
    def test_a_personal_statement_is_learned_immediately(self, said, topic, category):
        system = real_system()
        turn = say(system, said)
        assert turn.succeeded, turn.outcome
        assert turn.capability == "personal_context"
        assert REFUSAL not in turn.outcome
        learned = topics(system[1])
        assert topic in learned, learned
        assert learned[topic].category == category

    @pytest.mark.parametrize(
        "ordinary",
        [
            "I have been learning to cook lately, it is fun.",
            "My birthday is coming up and I have no plans yet.",
            "can you check if my code is correct",
            "what do you think?",
            "thanks, that works",
        ],
    )
    def test_ordinary_conversation_is_not_learned(self, ordinary):
        assert extract_personal_context_candidate(ordinary) is None

    def test_nothing_is_written_to_a_separate_learning_store(self):
        system = real_system()
        say(system, "I'm also learning Java.")
        say(system, "I'm learning C++ too.")
        memory = system[1].memory
        assert memory.recall(kind="learning", limit=10) == ()
        assert len(memory.recall(kind="personal_context", limit=10)) == 2


class TestOneCanonicalStore:
    def test_having_learned_supersedes_learning_the_same_subject(self):
        system = real_system()
        say(system, "I'm learning Go.")
        say(system, "I've learned Go.")
        learned = topics(system[1])
        assert len(learned) == 1
        assert learned["Go"].category == "skill"
        assert len(personal_context_history("Go", recall=system[1].memory.recall)) == 2

    def test_remember_that_a_personal_fact_is_one_canonical_record(self):
        system = real_system()
        turn = say(system, "remember that my favourite colour is blue")
        assert turn.succeeded and REFUSAL not in turn.outcome
        memory = system[1].memory
        assert len(memory.recall(kind="personal_context", limit=10)) == 1
        assert memory.recall(kind="user_context", limit=10) == ()

    def test_an_older_learning_record_is_still_read_and_superseded(self):
        store = EphemeralMemory()
        old = datetime.now(timezone.utc) - timedelta(days=30)
        store.remember(MemoryRecord(
            content="Currently studying: statistics", kind="learning",
            context={"subject": "statistics"}, created_at=old,
        ))
        current = all_current_personal_context(recall=store.recall)
        assert [(c.topic, c.category) for c in current] == [("statistics", "learning")]

        store.remember(build_personal_context_record(
            extract_personal_context_candidate("I've learned statistics"), agent_name="Alfred",
        ))
        current = all_current_personal_context(recall=store.recall)
        assert len(current) == 1 and current[0].category == "skill"


class TestRecallWithoutAModel:
    def test_questions_about_the_user_are_answered_from_the_store(self):
        system = real_system()
        for said in (
            "I'm also learning Java.", "I'm learning C++ too.",
            "This is my schedule: Mon-Fri 9-3", "This is my updated schedule: Tue-Thu 10-4",
            "My goal this year is to run a marathon",
        ):
            assert say(system, said).succeeded

        learning = say(system, "What am I learning?")
        assert learning.succeeded and REFUSAL not in learning.outcome
        assert "Java" in learning.outcome and "C++" in learning.outcome
        assert "marathon" not in learning.outcome

        schedule = say(system, "what's my schedule?")
        assert schedule.succeeded and "Tue-Thu 10-4" in schedule.outcome
        assert "Mon-Fri" not in schedule.outcome

        remembered = say(system, "what do you remember about my schedule")
        assert remembered.succeeded and "Tue-Thu 10-4" in remembered.outcome
        assert "Mon-Fri" not in remembered.outcome

        everything = say(system, "what do you know about me")
        assert everything.succeeded
        for fragment in ("Java", "C++", "Tue-Thu 10-4", "marathon"):
            assert fragment in everything.outcome

    def test_an_unknown_topic_is_honestly_unknown(self):
        system = real_system()
        say(system, "I'm learning Java.")
        turn = say(system, "what's my favourite film?")
        assert turn.succeeded
        assert "haven't told me" in turn.outcome

    def test_it_is_said_back_in_the_second_person(self):
        candidate = extract_learning_activity("I'm revising for my finals")
        assert describe_for_user(candidate) == "You're revising for your finals."


class TestRestartUpdateAndHistory:
    def test_teach_restart_recall_change_restart(self):
        memory_dir = tempfile.mkdtemp()
        first = real_system(memory_dir)
        for said in ("I'm learning Java.", "My schedule is Mon-Fri 9-3", "I've started a project called Beacon"):
            assert say(first, said).succeeded

        second = real_system(memory_dir)
        assert {"Java", "schedule", "a project called Beacon"} <= set(topics(second[1]))
        assert "Mon-Fri 9-3" in say(second, "what's my schedule?").outcome

        assert say(second, "My schedule has changed to Tue-Thu 10-4").succeeded

        third = real_system(memory_dir)
        assert "Tue-Thu 10-4" in say(third, "what's my schedule?").outcome
        history = personal_context_history("schedule", recall=third[1].memory.recall)
        assert [("Tue-Thu" in h.content, "Mon-Fri" in h.content) for h in history] == [(True, False), (False, True)]


class TestEveryAgentReadsTheSameCanonicalSource:
    def test_arthur_ranks_against_what_the_user_taught_alfred(self):
        system = real_system()
        manager = system[1]
        arthur = manager.registry.get("Arthur")
        analyzer = arthur.analyzers[0]
        assert analyzer.current_profile() is None

        say(system, "I've learned SQL")
        say(system, "I'm learning Power BI")

        seen_by_arthur = analyzer.current_profile()
        seen_by_alfred = manager.own_skills.user
        assert seen_by_arthur.tier_of("SQL") is SkillTier.WORKING
        assert seen_by_arthur.tier_of("Power BI") is SkillTier.LEARNING
        assert seen_by_alfred.skills == seen_by_arthur.skills
        # One source, not a copy: both read the same UserKnowledge object.
        assert analyzer._profile_source.__self__ is manager.own_skills.user_knowledge

    def test_the_owner_document_is_never_demoted_by_conversation(self):
        base = UserProfile(skills={"sql": SkillTier.STRONG})
        store = EphemeralMemory()
        store.remember(build_personal_context_record(
            extract_personal_context_candidate("I'm learning SQL"), agent_name="Alfred",
        ))
        knowledge = UserKnowledge(base_profile=base, recall=store.recall)
        assert knowledge.profile().tier_of("SQL") is SkillTier.STRONG
        assert knowledge.base_profile is base

    def test_a_project_is_context_and_never_a_skill(self):
        store = EphemeralMemory()
        store.remember(build_personal_context_record(
            extract_personal_context_candidate("My project is called Beacon"), agent_name="Alfred",
        ))
        assert effective_profile(None, all_current_personal_context(recall=store.recall)) is None


class TestTheBroadShapeIsConfirmedWhenABrainExists:
    """"My X is Y" is general enough to also describe a problem ("my code is
    broken"). With a Brain available it is asked first; without one the
    deterministic reading stands, so Half Brain still learns."""

    def _skills(self, answer):
        from src.core.contracts import Task  # noqa: F401 - imported for callers
        from src.manager.brain import BrainAction, BrainDecision
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity
        from src.services.memory.policy import PolicyVerdict

        store = EphemeralMemory()
        written = []

        def retain(record):
            written.append(record)
            store.remember(record)
            return PolicyVerdict(allowed=True, reason="ok")

        converse = None
        if answer is not None:
            def converse(task, notes):
                return BrainDecision(action=BrainAction.HANDLE_SELF, reason=answer, source="scripted")
        skills = ManagerSkills(
            load_identity("IDENTITY.md"), converse=converse,
            recall=store.recall, forget=store.forget, retain=retain,
        )
        return skills, written

    def _personal(self, written):
        return [record for record in written if record.kind == "personal_context"]

    def test_a_brain_that_says_it_is_not_personal_keeps_it_out_of_memory(self):
        from src.core.contracts import Task

        skills, written = self._skills("TOPIC: none")
        skills.execute(Task(description="My code is broken"))
        assert self._personal(written) == []

    def test_a_brain_that_confirms_it_lets_it_be_learned(self):
        from src.core.contracts import Task

        skills, written = self._skills("TOPIC: favourite colour")
        result = skills.execute(Task(description="My favourite colour is green"))
        assert result.succeeded
        assert len(self._personal(written)) == 1

    def test_half_brain_keeps_the_deterministic_reading(self):
        from src.core.contracts import Task

        skills, written = self._skills(None)
        result = skills.execute(Task(description="My favourite colour is green"))
        assert result.succeeded
        assert len(self._personal(written)) == 1

    def test_context_that_is_not_a_skill_never_becomes_one(self):
        store = EphemeralMemory()
        store.remember(build_personal_context_record(
            extract_personal_context_candidate("My goal this year is to run a marathon"), agent_name="Alfred",
        ))
        assert effective_profile(None, all_current_personal_context(recall=store.recall)) is None
