"""Pre-Stage-9 Campaign 6 - timed reminders and alarms, delivered proactively.

Real isolated systems through the production composition (``build`` /
``build_live_system``) and real governed memory. Time is moved forward by
giving the proactive check a later ``now`` (or, on the served path, by
advancing ``Calendar.now``) - never by sleeping.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.manager.proactive_experience import evaluate_and_persist_experiences
from src.manager.proactive_milestones import due_proactive_events
from src.services.memory.day import Calendar
from src.services.memory.tasks import extract_reminder, reminder_intent

EVENING = datetime(2026, 9, 14, 18, 30, tzinfo=timezone(timedelta(hours=1)))


def _settings(tmp_path, **overrides):
    return Settings.from_env(
        env={}, memory=overrides.pop("memory", "ephemeral"), profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **overrides,
    )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, leaf in (("DEFAULT_ACCOUNTS_PATH", "accounts.json"), ("DEFAULT_CONFIG_PATH", "config.json"),
                       ("DEFAULT_LEDGER_PATH", "state.json"), ("DEFAULT_STAGING_DIR", "staging"),
                       ("DEFAULT_BACKUPS_DIR", "backups")):
        monkeypatch.setattr(cc, name, tmp_path / leaf)
    return tmp_path


def _reminders(manager):
    return [r for r in manager.memory.recall(kind="user_task", limit=500) if "reminder" in (r.tags or set())]


class TestTheMomentIsReadFromOrdinaryWords:
    @pytest.mark.parametrize("text, title, due", [
        ("remind me to call Mum at 5pm", "call Mum", "2026-09-15T17:00"),
        ("remind me tonight at 9pm to take the bins out", "take the bins out", "2026-09-14T21:00"),
        ("remind me to buy 2 apples at 5pm tomorrow", "buy 2 apples", "2026-09-15T17:00"),
        ("remind me in 20 minutes to check the oven", "check the oven", "2026-09-14T18:50"),
        ("remind me at noon on Friday to submit the form", "submit the form", "2026-09-18T12:00"),
        ("remind me on 3rd October at 17:30 to pay rent", "pay rent", "2026-10-03T17:30"),
        ("set a reminder for 2026-09-20 at 09:15 dentist", "dentist", "2026-09-20T09:15"),
        ("wake me up at 7", "Alarm", "2026-09-15T07:00"),
        ("set an alarm for 6:45am", "Alarm", "2026-09-15T06:45"),
    ])
    def test_title_and_due_moment(self, text, title, due):
        found = extract_reminder(text, now=EVENING)
        assert found is not None, text
        assert found[0] == title
        assert found[1].isoformat().startswith(due)

    @pytest.mark.parametrize("text", ["remind me what I said about Tuesday", "remind me who called", "remind me of the plan"])
    def test_a_question_about_what_is_known_is_never_a_reminder(self, text):
        assert not reminder_intent(text)

    def test_no_time_named_means_no_timed_reminder(self):
        assert extract_reminder("remind me to renew my passport", now=EVENING) is None


class TestSettingAReminderInConversation:
    def test_a_timed_reminder_is_stored_with_its_moment_and_said_back(self, tmp_path):
        runtime, manager, channel, conversation = cc.build(_settings(tmp_path))
        turn = exchange(channel, conversation, "remind me in 30 minutes to stretch")
        assert turn.succeeded and turn.capability == "create_task", turn.outcome
        assert turn.outcome.startswith("I'll remind you: stretch - at ")
        [reminder] = _reminders(manager)
        assert reminder.content == "stretch" and reminder.context["due_at"]

    def test_an_untimed_remind_me_to_is_still_an_ordinary_task(self, tmp_path):
        runtime, manager, channel, conversation = cc.build(_settings(tmp_path))
        turn = exchange(channel, conversation, "remind me to renew my passport")
        assert turn.succeeded and turn.outcome.startswith("Added: "), turn.outcome
        assert _reminders(manager) == []

    def test_an_alarm_with_no_time_asks_when(self, tmp_path):
        runtime, manager, channel, conversation = cc.build(_settings(tmp_path))
        turn = exchange(channel, conversation, "set an alarm")
        assert "When should I remind you?" in turn.outcome
        assert _reminders(manager) == []


class TestADueReminderIsDelivered:
    def test_due_only_once_its_moment_arrives(self, tmp_path):
        runtime, manager, channel, conversation = cc.build(_settings(tmp_path))
        exchange(channel, conversation, "remind me in 10 minutes to call the plumber")
        [reminder] = _reminders(manager)
        due_at = datetime.fromisoformat(reminder.context["due_at"])

        assert not [e for e in due_proactive_events(manager, now=due_at - timedelta(minutes=1)) if e.source == "reminder"]
        [event] = [e for e in due_proactive_events(manager, now=due_at + timedelta(seconds=1)) if e.source == "reminder"]
        assert event.label == "call the plumber"
        # A day-only check never guesses which of today's reminders are due.
        assert not [e for e in due_proactive_events(manager) if e.source == "reminder"]

    def test_the_reminder_is_delivered_without_any_celebration_provider(self, tmp_path):
        runtime, manager, channel, conversation = cc.build(_settings(tmp_path))
        manager.own_skills._celebration_provider = None
        exchange(channel, conversation, "remind me in 5 minutes to take my tablets")
        [reminder] = _reminders(manager)
        later = datetime.fromisoformat(reminder.context["due_at"]) + timedelta(minutes=1)

        records = evaluate_and_persist_experiences(manager, now=later)
        [delivered] = [r for r in records if r.event_type == "reminder"]
        assert delivered.message == "take my tablets" and not delivered.delivered
        again = evaluate_and_persist_experiences(manager, now=later + timedelta(minutes=5))
        assert len([r for r in again if r.event_type == "reminder"]) == 1

    def test_a_reminder_survives_a_restart(self, tmp_path):
        settings = _settings(tmp_path, memory="persistent", memory_dir=str(tmp_path / "memory"))
        runtime, manager, channel, conversation = cc.build(settings)
        exchange(channel, conversation, "remind me in 15 minutes to leave for the station")
        [reminder] = _reminders(manager)
        later = datetime.fromisoformat(reminder.context["due_at"]) + timedelta(minutes=1)

        _runtime, restarted, _channel, _conversation = cc.build(settings)
        [event] = [e for e in due_proactive_events(restarted, now=later) if e.source == "reminder"]
        assert event.label == "leave for the station"


class TestTheUnattendedHeartbeatReportsIt:
    def test_the_scheduled_check_reports_a_due_reminder_as_a_reminder(self, tmp_path):
        import re
        from pathlib import Path

        import scripts.heartbeat as hb

        settings = _settings(
            tmp_path, memory="persistent", memory_dir=str(tmp_path / "memory"),
            reports_dir=str(tmp_path / "reports"), state_path=str(tmp_path / "state" / "processed.json"),
        )
        runtime, manager, channel, conversation = cc.build(settings)
        exchange(channel, conversation, "remind me in 5 minutes to water the plants")
        [reminder] = _reminders(manager)
        later = datetime.fromisoformat(reminder.context["due_at"]) + timedelta(minutes=1)

        summary = hb.check_proactive_events(settings, False, clock=lambda: later)
        body = Path(re.search(r"-> (.+\.md)\s*$", summary).group(1)).read_text(encoding="utf-8")
        assert "- **reminder**: water the plants" in body
        assert "  - reminder: water the plants" in body and "celebration:" not in body


class TestTheServedPageShowsIt:
    def test_the_snapshot_carries_the_message_and_delivery_clears_it(self, isolated, monkeypatch):
        system = su.build_live_system(_settings(isolated))
        exchange(system.channel, system.conversation, "remind me in 2 minutes to join the call")
        [reminder] = _reminders(system.manager)
        later = datetime.fromisoformat(reminder.context["due_at"]) + timedelta(minutes=1)
        monkeypatch.setattr(Calendar, "now", lambda self: later.astimezone(timezone.utc))

        snapshot = json.loads(su.route("/snapshot.json", system)[1])
        [pending] = [p for p in snapshot["experience"]["pending"] if p["event_type"] == "reminder"]
        assert pending["message"] == "join the call"

        status, _body, _ = su.route_experience(pending["record_id"], system)
        assert status == 200
        snapshot = json.loads(su.route("/snapshot.json", system)[1])
        assert not [p for p in snapshot["experience"]["pending"] if p["event_type"] == "reminder"]
