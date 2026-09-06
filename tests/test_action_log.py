"""Foundation Repair #4 (reconciliation audit, 2026-09-05): a durable,
cross-restart audit trail for consequential capability actions.

The gap this closes: the Control Center's own ``history`` command is real,
but is rebuilt fresh every process, so "what did Alfred do yesterday" had
no answer once that process exited. This is that missing durable record -
distinct from, and additive to, the already-real, already-persisted
``src.services.evolution.ledger_store`` (which records governed *evolution*
transitions, not ordinary conversation-driven actions).

Two layers are tested here: the store itself (this file), and its wiring
into the real production exchange path (see
TestConsequentialActionsAreDurablyRecorded in
tests/test_natural_retention_understanding.py's neighbouring file... in
practice, this file's own end-to-end class below, using the same
real_system() harness).
"""

from __future__ import annotations

import json

import pytest

from scripts.control_center import build
from scripts.exchange import exchange
from src.config.settings import Settings
from src.services.audit.action_log import (
    CONSEQUENTIAL_CAPABILITIES,
    SCHEMA_VERSION,
    ActionLogEntry,
    load_action_log,
    record_action,
)


def real_system(**env_overrides):
    settings = Settings.from_env(env=env_overrides, memory="ephemeral")
    return build(settings)


def entry(capability="communication", succeeded=True, actor="Alfred"):
    return ActionLogEntry(
        timestamp="2026-09-05T12:00:00+00:00",
        actor=actor,
        capability=capability,
        succeeded=succeeded,
    )


class TestEmptyStoreIsHonest:
    def test_a_missing_file_loads_as_empty(self, tmp_path):
        assert load_action_log(tmp_path / "action_log.json") == ()

    def test_loading_does_not_create_a_file(self, tmp_path):
        path = tmp_path / "action_log.json"
        load_action_log(path)
        assert not path.exists()


class TestRecordAndReload:
    def test_a_recorded_entry_round_trips(self, tmp_path):
        path = tmp_path / "action_log.json"
        record_action(entry(), path=path)

        reloaded = load_action_log(path)
        assert len(reloaded) == 1
        assert reloaded[0].capability == "communication"
        assert reloaded[0].succeeded is True
        assert reloaded[0].actor == "Alfred"

    def test_entries_accumulate_in_order(self, tmp_path):
        path = tmp_path / "action_log.json"
        record_action(entry(capability="communication"), path=path)
        record_action(entry(capability="connected_action"), path=path)

        reloaded = load_action_log(path)
        assert [e.capability for e in reloaded] == ["communication", "connected_action"]

    def test_a_fresh_object_over_the_same_path_sees_prior_entries(self, tmp_path):
        """The whole point, the same as PersistentMemory's own
        TestRestart: what one process wrote, the next process reads."""
        path = tmp_path / "action_log.json"
        record_action(entry(), path=path)
        record_action(entry(capability="media_production"), path=path)

        reloaded = load_action_log(path)
        assert len(reloaded) == 2

    def test_survives_a_failed_action_too(self, tmp_path):
        """A failed consequential action is exactly as worth a durable
        record as a successful one - "Alfred tried and could not" is not
        less auditable than "Alfred did"."""
        path = tmp_path / "action_log.json"
        record_action(entry(succeeded=False), path=path)
        assert load_action_log(path)[0].succeeded is False


class TestPrivacyDiscipline:
    def test_no_request_content_field_exists(self):
        fields = set(ActionLogEntry.__dataclass_fields__)
        for forbidden in ("content", "text", "request", "arguments", "output"):
            assert forbidden not in fields

    def test_the_stored_json_never_carries_more_than_the_declared_fields(self, tmp_path):
        path = tmp_path / "action_log.json"
        record_action(entry(), path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        stored_keys = set(raw["entries"][0])
        assert stored_keys == {"timestamp", "actor", "capability", "succeeded"}


class TestBounded:
    def test_entries_beyond_the_cap_are_dropped_oldest_first(self, tmp_path, monkeypatch):
        import src.services.audit.action_log as action_log_module

        monkeypatch.setattr(action_log_module, "MAX_ENTRIES", 3)
        path = tmp_path / "action_log.json"
        for i in range(5):
            record_action(entry(capability=f"communication-{i}"), path=path)

        reloaded = load_action_log(path)
        assert len(reloaded) == 3
        assert [e.capability for e in reloaded] == [
            "communication-2", "communication-3", "communication-4",
        ]


class TestSchemaVersioned:
    def test_a_fresh_file_is_written_at_the_current_schema(self, tmp_path):
        path = tmp_path / "action_log.json"
        record_action(entry(), path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == SCHEMA_VERSION

    def test_an_unrecognised_schema_with_no_migration_path_raises(self, tmp_path):
        path = tmp_path / "action_log.json"
        path.write_text(json.dumps({"schema_version": 99, "entries": []}), encoding="utf-8")
        with pytest.raises(Exception):
            load_action_log(path)


class TestConsequentialCapabilitiesIsScopedCorrectly:
    """The exact design correction this repair pass made mid-flight:
    ConversationTurn.tool_used measures something else entirely (see
    action_log.py's own docstring) - this pins the actual, narrow,
    deliberately-chosen set instead."""

    def test_the_four_write_shaped_capabilities_are_included(self):
        for name in ("connected_action", "communication", "media_production", "generate_document"):
            assert name in CONSEQUENTIAL_CAPABILITIES

    def test_read_only_lookups_are_excluded(self):
        for name in ("knowledge", "live_information", "web_search", "current_datetime", "recall_memory"):
            assert name not in CONSEQUENTIAL_CAPABILITIES


class TestWiredIntoTheRealProductionExchangePath:
    """Foundation Repair #4's other half: the store above is exercised for
    real, but it must actually be reached from exchange() - the same real
    build() + real exchange() harness
    test_natural_retention_understanding.py's own acceptance tests use."""

    def test_a_consequential_capability_is_durably_recorded(self, tmp_path):
        runtime, manager, channel, conversation = real_system()
        log_path = tmp_path / "action_log.json"

        turn = exchange(
            channel, conversation, "check my connected account", action_log_path=log_path
        )

        assert turn.capability == "connected_action"
        entries = load_action_log(log_path)
        assert len(entries) == 1
        assert entries[0].capability == "connected_action"
        assert entries[0].succeeded == turn.succeeded
        assert entries[0].actor == turn.handled_by

    def test_an_ordinary_conversation_records_nothing(self, tmp_path):
        runtime, manager, channel, conversation = real_system()
        log_path = tmp_path / "action_log.json"

        exchange(channel, conversation, "what day is it", action_log_path=log_path)

        assert load_action_log(log_path) == ()

    def test_with_no_action_log_path_nothing_is_ever_written(self, tmp_path, monkeypatch):
        """The default (None) must never touch a real file by surprise -
        the exact regression risk this parameter's own docstring names."""
        import src.services.audit.action_log as action_log_module

        monkeypatch.setattr(action_log_module, "DEFAULT_ACTION_LOG_PATH", tmp_path / "should-not-exist.json")
        runtime, manager, channel, conversation = real_system()

        exchange(channel, conversation, "check my connected account")

        assert not (tmp_path / "should-not-exist.json").exists()

    def test_a_second_consequential_action_appends_rather_than_overwrites(self, tmp_path):
        runtime, manager, channel, conversation = real_system()
        log_path = tmp_path / "action_log.json"

        exchange(channel, conversation, "check my connected account", action_log_path=log_path)
        exchange(channel, conversation, "check my connected account", action_log_path=log_path)

        assert len(load_action_log(log_path)) == 2
