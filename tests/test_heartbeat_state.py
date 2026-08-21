"""Tests for cross-run memory: fingerprinting, persistence, recovery, pruning."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.heartbeat.state import (
    SCHEMA_VERSION,
    ProcessedState,
    StateStore,
    fingerprint,
    select_unseen,
)

NOW = datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)


def opportunity(role="Python Developer", company="Acme", source="Wellfound",
                message_id="m1", subject="Role at Acme"):
    return SimpleNamespace(
        role=role, company=company, source=source,
        message_id=message_id, subject=subject,
        headline=lambda: f"{role} at {company}",
    )


class TestFingerprinting:
    def test_same_posting_gives_the_same_fingerprint(self):
        assert fingerprint(opportunity()) == fingerprint(opportunity())

    def test_ignores_message_id_when_role_and_company_are_known(self):
        """The same role re-advertised in a different email is still the same role."""
        assert fingerprint(opportunity(message_id="a")) == fingerprint(opportunity(message_id="b"))

    def test_different_roles_differ(self):
        assert fingerprint(opportunity(role="Data Analyst")) != fingerprint(opportunity())

    def test_different_companies_differ(self):
        assert fingerprint(opportunity(company="Globex")) != fingerprint(opportunity())

    def test_insensitive_to_case_and_punctuation(self):
        a = opportunity(role="Python Developer", company="Acme Ltd.")
        b = opportunity(role="python  developer", company="acme ltd")
        assert fingerprint(a) == fingerprint(b)

    def test_falls_back_to_message_and_subject_when_fields_are_missing(self):
        vague = opportunity(role=None, company=None, message_id="m9", subject="Great chance")
        assert fingerprint(vague) == fingerprint(
            opportunity(role=None, company=None, message_id="m9", subject="Great chance")
        )

    def test_fallback_keeps_distinct_messages_distinct(self):
        a = opportunity(role=None, company=None, message_id="m1", subject="One")
        b = opportunity(role=None, company=None, message_id="m2", subject="Two")
        assert fingerprint(a) != fingerprint(b)

    def test_fingerprints_are_short_and_stable(self):
        value = fingerprint(opportunity())
        assert len(value) == 16 and value == fingerprint(opportunity())


class TestSelectUnseen:
    def test_splits_new_from_already_reported(self):
        state = ProcessedState()
        state.remember_opportunity(fingerprint(opportunity()), "seen", NOW)
        fresh, seen = select_unseen([opportunity(), opportunity(role="New Role")], state)
        assert len(fresh) == 1 and len(seen) == 1

    def test_everything_is_new_on_a_first_run(self):
        fresh, seen = select_unseen([opportunity()], ProcessedState())
        assert len(fresh) == 1 and seen == []


class TestPersistence:
    def test_absent_file_yields_empty_state(self, tmp_path):
        assert StateStore(tmp_path / "s.json").load().messages == {}

    def test_round_trips(self, tmp_path):
        store = StateStore(tmp_path / "s.json")
        state = store.load()
        state.remember_message("m1", NOW)
        state.remember_opportunity("fp1", "Role at Acme", NOW)
        store.save(state, NOW)

        reloaded = store.load()
        assert reloaded.has_message("m1")
        assert reloaded.has_opportunity("fp1")

    def test_records_the_run_time(self, tmp_path):
        store = StateStore(tmp_path / "s.json")
        store.save(ProcessedState(), NOW)
        assert store.load().last_run_at == NOW.isoformat()

    def test_creates_the_directory(self, tmp_path):
        store = StateStore(tmp_path / "nested" / "deep" / "s.json")
        store.save(ProcessedState(), NOW)
        assert store.path.exists()

    def test_writes_are_atomic(self, tmp_path):
        """No .tmp file is left behind, so a reader never sees a half-write."""
        store = StateStore(tmp_path / "s.json")
        store.save(ProcessedState(), NOW)
        assert list(tmp_path.glob("*.tmp")) == []
        assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == SCHEMA_VERSION

    def test_first_seen_is_preserved_on_re_report(self, tmp_path):
        state = ProcessedState()
        state.remember_opportunity("fp", "head", NOW)
        later = NOW + timedelta(days=1)
        state.remember_opportunity("fp", "head", later)
        assert state.opportunities["fp"]["first_seen"] == NOW.isoformat()
        assert state.opportunities["fp"]["last_reported"] == later.isoformat()


class TestRecovery:
    def test_corrupt_file_does_not_crash(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert StateStore(path).load().messages == {}

    def test_corrupt_file_is_preserved_for_inspection(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("{not valid json", encoding="utf-8")
        StateStore(path).load()
        assert list(tmp_path.glob("s.corrupt-*"))

    def test_non_object_json_is_rejected_safely(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert StateStore(path).load().messages == {}

    def test_unknown_schema_version_starts_fresh(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"version": 999, "messages": {"m": "x"}}), encoding="utf-8")
        assert StateStore(path).load().messages == {}


class TestRetention:
    def test_prunes_entries_older_than_retention(self, tmp_path):
        store = StateStore(tmp_path / "s.json", retention_days=30)
        state = ProcessedState()
        state.remember_message("old", NOW - timedelta(days=90))
        state.remember_message("recent", NOW)
        store.save(state, NOW)

        reloaded = store.load()
        assert not reloaded.has_message("old")
        assert reloaded.has_message("recent")

    def test_prunes_stale_opportunities(self, tmp_path):
        store = StateStore(tmp_path / "s.json", retention_days=30)
        state = ProcessedState()
        state.remember_opportunity("old", "h", NOW - timedelta(days=90))
        state.remember_opportunity("new", "h", NOW)
        store.save(state, NOW)
        assert set(store.load().opportunities) == {"new"}

    def test_prune_reports_how_many_were_removed(self):
        state = ProcessedState()
        state.remember_message("old", NOW - timedelta(days=90))
        assert state.prune(NOW - timedelta(days=30)) == 1


class TestExclusionList:
    def test_returns_known_ids_newest_first(self):
        state = ProcessedState()
        state.remember_message("older", NOW - timedelta(hours=2))
        state.remember_message("newer", NOW)
        assert state.known_message_ids()[0] == "newer"

    def test_is_bounded(self):
        """The exclusion list cannot grow without limit as history accumulates."""
        state = ProcessedState()
        for index in range(50):
            state.remember_message(f"m{index}", NOW + timedelta(seconds=index))
        assert len(state.known_message_ids(limit=10)) == 10
