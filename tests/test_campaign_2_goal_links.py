"""Whole-ecosystem closure campaign 2 - how goals and what serves them connect,
reasoned without a model.

Each link carries its evidence: what the person told me (kept as a governed
personal-context fact, superseded by a later statement), shared words, or - only
with a Brain - a suggestion validated against the person's real goals,
commitments and tasks and offered for confirmation, never stored. With no
model, a link the words do not show is asked about, never guessed. The served
run is recorded in REQUIREMENT_LEDGER.md.
"""

from __future__ import annotations

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.brain import BrainAction, BrainDecision
from src.manager.capabilities import MANAGER_CAPABILITIES, select_capability
from src.services.memory.guidance import (
    GoalLink,
    best_match,
    goal_connections,
    guidance_kind,
    parse_goal_link,
    validated_suggestions,
)

GOALS = ["to learn to sail", "to publish a short story collection"]
ITEMS = ["swim 2 time(s) a week", "writing mornings 4 time(s) a week", "buy a wetsuit", "email three literary magazines"]


class TestTaughtLinks:
    @pytest.mark.parametrize("text, thing, goal, negated", [
        ("My swimming is for my sailing goal.", "swimming", "sailing", False),
        ("Writing mornings count towards my goal of publishing a short story collection", "Writing mornings", "publishing a short story collection", False),
        ("buying a wetsuit isn't for my story collection goal", "buying a wetsuit", "story collection", True),
        ("Note that emailing magazines supports my aim to publish", "emailing magazines", "publish", False),
    ])
    def test_a_link_statement_is_read_with_its_polarity(self, text, thing, goal, negated):
        assert parse_goal_link(text) == (thing, goal, negated)

    @pytest.mark.parametrize("text", ["This gift is for my mum.", "The meeting is for the marketing team.", "What is my goal?"])
    def test_statements_without_a_goal_are_not_links(self, text):
        assert parse_goal_link(text) is None

    def test_best_match_refuses_ties_and_strangers(self):
        assert best_match("sailing lessons", GOALS) == "to learn to sail"  # "sailing" and "sail" share a stem
        assert best_match("knitting", GOALS) is None
        assert best_match("story", GOALS) == "to publish a short story collection"
        assert best_match("write", ["write a novel", "write a song"]) is None


class TestConnectionsCarryEvidence:
    def test_told_links_come_first_and_a_told_no_removes_a_word_match(self):
        taught = [("swimming", "sail", False), ("buy a wetsuit", "to learn to sail", False),
                  ("writing mornings", "short story collection", True)]
        items = ITEMS + ["story drafts"]
        links = goal_connections(GOALS, items, taught)
        assert [(l.item, l.evidence) for l in links["to learn to sail"]] == [("swim 2 time(s) a week", "told"), ("buy a wetsuit", "told")]
        story = links["to publish a short story collection"]
        assert [(l.item, l.evidence) for l in story] == [("story drafts", "shared words: story")]

    def test_brain_suggestions_are_kept_only_when_both_ends_are_real(self):
        known = goal_connections(GOALS, ITEMS)
        kept = validated_suggestions(
            "swim 2 time(s) a week => to learn to sail ; buy a yacht => to learn to sail ; "
            "email three literary magazines => to publish a short story collection ; writing mornings => become famous",
            GOALS, ITEMS, known,
        )
        assert kept == [GoalLink("swim 2 time(s) a week", "to learn to sail", "suggested"),
                        GoalLink("email three literary magazines", "to publish a short story collection", "suggested")]


class TestRouting:
    def test_a_link_statement_reaches_guidance_not_generic_personal_facts(self):
        task = Task(description="My duolingo lessons are for my Portuguese goal.")
        assert guidance_kind(task.description) == "teach_goal_link"
        assert select_capability(task, MANAGER_CAPABILITIES).name == "personal_guidance"


@pytest.fixture
def system(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _say(system, text):
    return exchange(system.channel, system.conversation, text)


def _teach(system):
    _say(system, "My goal this year is to learn to sail.")
    _say(system, "My other goal this year is to publish a short story collection.")
    _say(system, "My weekly non-negotiables are: swim 2 times, writing mornings 4 times.")
    _say(system, "Add a task: buy a wetsuit.")


class TestInConversation:
    def test_half_brain_links_by_words_asks_about_the_rest_then_uses_what_it_is_told(self, system):
        _teach(system)
        first = _say(system, "How do my commitments relate to my goals?").outcome
        assert "Not linked to any goal:" in first and "swim 2 time(s) a week" in first and "is for my goal" in first
        told = _say(system, "My swimming is for my goal to learn to sail.").outcome
        assert told == "Noted: swim 2 time(s) a week serves your goal: to learn to sail."
        after = _say(system, "How do my commitments relate to my goals?").outcome
        assert "swim 2 time(s) a week (you told me)" in after
        assert "Which goal" in _say(system, "Knitting is for my astronomy goal.").outcome

    def test_a_brain_suggests_links_that_are_validated_and_never_stored(self, system):
        _teach(system)
        seen = []

        def brain(task, notes):
            seen.append(notes)
            if any("item => goal" in str(n) for n in notes):
                return BrainDecision(BrainAction.HANDLE_SELF,
                                     "swim 2 time(s) a week => to learn to sail ; buy a wetsuit => to learn to sail ; "
                                     "run a marathon => to learn to sail", source="scripted_brain")
            return BrainDecision(BrainAction.HANDLE_SELF, "Block two writing mornings before work.", source="scripted_brain")

        system.manager.own_skills._converse = brain
        turn = _say(system, "How do my commitments relate to my goals?")
        assert "Possible links (suggested, not confirmed" in turn.outcome
        assert "swim 2 time(s) a week for to learn to sail" in turn.outcome and "run a marathon" not in turn.outcome
        assert turn.outcome.endswith("Block two writing mornings before work.")
        system.manager.own_skills._converse = None
        again = _say(system, "How do my commitments relate to my goals?").outcome
        assert "(you told me)" not in again and "Possible links" not in again
