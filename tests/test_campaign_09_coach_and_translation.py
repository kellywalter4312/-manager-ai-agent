"""Pre-Stage-9 Campaign 9 - recovered capabilities: a ruthless personal coach
(a capability of Alfred himself) and translation.

Real isolated systems through the production composition and real governed
memory. The coach answers only from what Alfred keeps - goals taught in
conversation, open tasks, reminders whose moment has passed - so it is exact in
Half Brain. Time moves forward only through ``Calendar.now``. Translation is
routed to its own capability and, with no language model, refuses honestly;
with a Brain that decides, its answer is what is returned (a scripted in-process
Brain double - never a vendor).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import scripts.control_center as cc
from scripts.exchange import exchange
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.capabilities import coach_intent, translate, translation_request
from src.services.memory.day import Calendar
from src.services.memory.personal_context import all_current_personal_context


def _build(tmp_path):
    return cc.build(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _say(channel, conversation, text):
    return exchange(channel, conversation, text)


class TestTheCoachIsRecognised:
    @pytest.mark.parametrize("text", [
        "coach me", "hold me accountable", "keep me accountable", "how am I doing on my goals?",
        "am I on track?", "be my ruthless coach", "be harder on me", "go easy on me", "what have I been putting off",
    ])
    def test_coaching_requests(self, text):
        assert coach_intent(Task(description=text))

    @pytest.mark.parametrize("text", ["I need a coach for my football team", "the coach was late", "my track shoes are worn"])
    def test_ordinary_sentences_are_not(self, text):
        assert not coach_intent(Task(description=text))


class TestCoachingFromRealRecords:
    def test_with_nothing_kept_it_asks_for_a_goal(self, tmp_path):
        _runtime, _manager, channel, conversation = _build(tmp_path)
        turn = _say(channel, conversation, "coach me")
        assert turn.capability == "coach" and turn.succeeded
        assert "my goal this year is" in turn.outcome

    def test_goals_open_tasks_and_missed_reminders_are_named(self, tmp_path, monkeypatch):
        _runtime, manager, channel, conversation = _build(tmp_path)
        for text in ("My goal this year is to run a marathon", "add a task to book a 10k race", "remind me in 10 minutes to stretch"):
            assert _say(channel, conversation, text).succeeded
        later = datetime.now(timezone.utc) + timedelta(days=3)
        monkeypatch.setattr(Calendar, "now", lambda self: later)
        monkeypatch.setattr(Calendar, "today", lambda self: later.date())
        turn = _say(channel, conversation, "how am I doing on my goals?")
        assert turn.capability == "coach" and turn.succeeded, turn.outcome
        assert "run a marathon" in turn.outcome
        assert "'stretch'" in turn.outcome and "book a 10k race" in turn.outcome
        assert "3 day(s)" in turn.outcome and ".." not in turn.outcome

    def test_the_style_is_the_persons_own_setting_and_persists_as_personal_context(self, tmp_path):
        _runtime, manager, channel, conversation = _build(tmp_path)
        _say(channel, conversation, "add a task to write the report")
        ruthless = _say(channel, conversation, "be my ruthless coach")
        assert ruthless.outcome.startswith("Done - I'll coach you ruthlessly") and "finish it before anything else" in ruthless.outcome
        stored = [item for item in all_current_personal_context(recall=manager.memory.recall) if item.topic_key == "coaching style"]
        assert stored and "ruthless" in (stored[0].value + stored[0].content).lower()
        again = _say(channel, conversation, "hold me accountable")
        assert "finish it before anything else" in again.outcome
        gentle = _say(channel, conversation, "go easy on me")
        assert gentle.outcome.startswith("Done - I'll go easier on you") and "Which one moves a goal forward most?" in gentle.outcome

    def test_a_goal_without_a_task_is_called_out(self, tmp_path):
        _runtime, _manager, channel, conversation = _build(tmp_path)
        _say(channel, conversation, "My goal is to learn Spanish")
        _say(channel, conversation, "be my ruthless coach")
        turn = _say(channel, conversation, "coach me")
        assert "A goal without a next step is a wish" in turn.outcome


class TestTranslation:
    @pytest.mark.parametrize("text, expected", [
        ("translate good morning into French", ("good morning", "French")),
        ("how do you say thank you in Spanish?", ("thank you", "Spanish")),
        ("what's 'where is the station' in Italian", ("where is the station", "Italian")),
        ("say hello in German", ("hello", "German")),
    ])
    def test_translation_requests_name_phrase_and_language(self, text, expected):
        assert translation_request(text) == expected

    @pytest.mark.parametrize("text", ["what's the weather in Paris", "what is love in the end", "I say that in class"])
    def test_other_questions_are_not_translation(self, text):
        assert translation_request(text) is None

    def test_with_no_language_model_it_refuses_honestly(self, tmp_path):
        _runtime, _manager, channel, conversation = _build(tmp_path)
        turn = _say(channel, conversation, "translate good morning into French")
        assert turn.capability == "translate" and not turn.succeeded
        assert "won't guess" in turn.outcome and "only English is installed" in turn.outcome

    def test_a_deciding_brain_answer_is_returned_as_the_translation(self, tmp_path):
        from src.manager.brain import BrainAction, BrainDecision
        from src.manager.capabilities import CapabilityRequest

        runtime, _manager, _channel, _conversation = _build(tmp_path)
        asked = []

        def converse(task, notes):
            asked.append(task.description)
            return BrainDecision(action=BrainAction.HANDLE_SELF, reason="Bonjour", confidence=0.9)

        request = CapabilityRequest(
            task=Task(description="translate good morning into French"), identity=runtime.identity,
            roster=lambda: (), own_name="Alfred", own_capabilities=(), converse=converse,
        )
        result = translate(request)
        assert result.output == "Bonjour" and "into French" in asked[0]
