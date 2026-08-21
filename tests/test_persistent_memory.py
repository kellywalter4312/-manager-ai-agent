"""Tests for memory that outlives the process that wrote it.

The audit measured the problem: the heartbeat builds a fresh Manager on every
tick, so an ephemeral store gave the orchestrator a memory lasting exactly one
wake. Tick 1 recorded a delegation outcome; tick 2 held zero records.

The load-bearing test in this file is TestRestart - construct a store, write to
it, throw the object away, construct a *different* object over the same path,
and get the record back. Everything else follows from that.

What this milestone is deliberately NOT: daily memory. created_at is preserved
exactly, in full precision with its timezone, because the temporal stage comes
next - but nothing here interprets it, and recall has no date filter. Durable
foundation, not New Dawn.

No Gmail, no model, no network. Every store is under tmp_path.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.email_agent import EmailAgent
from src.agents.email.analyzers import JobOpportunityAnalyzer
from src.config.settings import Settings
from src.heartbeat.runner import HeartbeatRunner
from src.heartbeat.state import StateStore
from src.manager.manager import ManagerAgent
from src.services.email.sample_inbox import sample_inbox
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.factory import (
    EMAIL_AGENT,
    MANAGER,
    available_memories,
    build_memory,
)
from src.services.memory.memory import Memory, MemoryRecord, MemoryStoreError
from src.services.memory.persistent import SCHEMA_VERSION, PersistentMemory
from src.services.profile.loader import load_profile

WHEN = datetime(2026, 8, 21, 11, 30, 45, 123456, tzinfo=timezone.utc)


def record(content="the user prefers remote work", **kwargs):
    kwargs.setdefault("kind", "user_context")
    kwargs.setdefault("tags", {"preference", "remote"})
    kwargs.setdefault("created_at", WHEN)
    return MemoryRecord(content=content, **kwargs)


def store(tmp_path, name="manager.json"):
    return PersistentMemory(tmp_path / name)


def settings(**overrides):
    return Settings.from_env(env={}, **overrides)


# ----------------------------------------------------------------------
# THE core requirement
# ----------------------------------------------------------------------


class TestRestart:
    def test_a_second_instance_recalls_what_the_first_wrote(self, tmp_path):
        """Instance A writes, A is discarded, B reads. The whole milestone."""
        first = store(tmp_path)
        first.remember(record("the user prefers remote work"))
        del first

        second = store(tmp_path)
        recalled = second.recall("remote")
        assert len(recalled) == 1
        assert recalled[0].content == "the user prefers remote work"

    def test_many_records_survive(self, tmp_path):
        first = store(tmp_path)
        for i in range(25):
            first.remember(record(f"fact number {i}", tags={f"t{i}"}))

        assert len(store(tmp_path)) == 25

    def test_a_third_instance_still_sees_them(self, tmp_path):
        store(tmp_path).remember(record())
        store(tmp_path).remember(record("a second thing", tags={"second"}))
        assert len(store(tmp_path)) == 2

    def test_the_file_is_where_it_was_told(self, tmp_path):
        target = tmp_path / "nested" / "deep" / "manager.json"
        PersistentMemory(target).remember(record())
        assert target.exists()


class TestFieldsSurvive:
    def test_content_survives_exactly(self, tmp_path):
        store(tmp_path).remember(record("exact content here"))
        assert store(tmp_path).recall(limit=5)[0].content == "exact content here"

    def test_kind_survives(self, tmp_path):
        store(tmp_path).remember(record(kind="delegation_outcome"))
        assert store(tmp_path).recall(limit=5)[0].kind == "delegation_outcome"

    def test_tags_survive(self, tmp_path):
        store(tmp_path).remember(record(tags={"alpha", "beta"}))
        assert store(tmp_path).recall(limit=5)[0].tags == frozenset({"alpha", "beta"})

    def test_identity_survives(self, tmp_path):
        original = record()
        store(tmp_path).remember(original)
        assert store(tmp_path).recall(limit=5)[0].id == original.id

    def test_context_survives(self, tmp_path):
        store(tmp_path).remember(record(context={"agent": "email_agent", "findings": 3}))
        restored = store(tmp_path).recall(limit=5)[0]
        assert restored.context == {"agent": "email_agent", "findings": 3}

    def test_created_at_survives_to_the_microsecond(self, tmp_path):
        """Preserved for the temporal stage that comes next - not rounded to a
        date, not reinterpreted, not dropped."""
        store(tmp_path).remember(record())
        restored = store(tmp_path).recall(limit=5)[0]
        assert restored.created_at == WHEN
        assert restored.created_at.microsecond == 123456

    def test_created_at_keeps_its_timezone(self, tmp_path):
        store(tmp_path).remember(record())
        assert store(tmp_path).recall(limit=5)[0].created_at.tzinfo is not None

    def test_a_record_with_no_context_round_trips(self, tmp_path):
        store(tmp_path).remember(record(context=None))
        assert store(tmp_path).recall(limit=5)[0].context is None


# ----------------------------------------------------------------------
# Recall semantics are not reimplemented
# ----------------------------------------------------------------------


class TestRecallIsUnchanged:
    def _both(self, tmp_path, records):
        ephemeral = EphemeralMemory()
        persistent = store(tmp_path)
        for item in records:
            ephemeral.remember(item)
            persistent.remember(item)
        return ephemeral, PersistentMemory(tmp_path / "manager.json")

    def test_query_matching_is_identical(self, tmp_path):
        items = [record("python backend role", tags={"a"}, kind="k"),
                 record("calendar sync failed", tags={"b"}, kind="k"),
                 record("python data analyst", tags={"c"}, kind="k")]
        ephemeral, persistent = self._both(tmp_path, items)
        assert [r.id for r in ephemeral.recall("python")] == [
            r.id for r in persistent.recall("python")
        ]

    def test_ordering_is_identical(self, tmp_path):
        items = [record(f"shared token number {i}", tags={f"t{i}"}) for i in range(6)]
        ephemeral, persistent = self._both(tmp_path, items)
        assert [r.id for r in ephemeral.recall("shared")] == [
            r.id for r in persistent.recall("shared")
        ]

    def test_kind_filtering_is_identical(self, tmp_path):
        items = [record("one", kind="lesson", tags={"a"}),
                 record("two", kind="decision", tags={"b"})]
        ephemeral, persistent = self._both(tmp_path, items)
        assert [r.id for r in ephemeral.recall(kind="lesson")] == [
            r.id for r in persistent.recall(kind="lesson")
        ]

    def test_tag_filtering_is_identical(self, tmp_path):
        items = [record("one", tags={"x", "y"}), record("two", tags={"x"})]
        ephemeral, persistent = self._both(tmp_path, items)
        assert [r.id for r in ephemeral.recall(tags={"x", "y"})] == [
            r.id for r in persistent.recall(tags={"x", "y"})
        ]

    def test_limit_is_respected(self, tmp_path):
        for i in range(10):
            store(tmp_path).remember(record(f"item {i}", tags={f"t{i}"}))
        assert len(store(tmp_path).recall(limit=3)) == 3

    def test_a_non_positive_limit_still_raises(self, tmp_path):
        with pytest.raises(MemoryStoreError):
            store(tmp_path).recall(limit=0)

    def test_insertion_order_survives_a_reload(self, tmp_path):
        """The bug this caught: saving in recall order reversed the store on
        every write, so a reloaded store tie-broke differently from the one
        that wrote it."""
        first = store(tmp_path)
        for i in range(5):
            first.remember(record(f"shared token number {i}", tags={f"t{i}"}))
        before = [r.id for r in first.recall("shared")]

        assert [r.id for r in store(tmp_path).recall("shared")] == before

    def test_order_is_stable_across_repeated_saves(self, tmp_path):
        """A reversal bug would only show after an odd number of rewrites."""
        first = store(tmp_path)
        for i in range(4):
            first.remember(record(f"shared token number {i}", tags={f"t{i}"}))
        expected = [r.id for r in first.recall("shared")]

        for _ in range(3):
            reloaded = store(tmp_path)
            reloaded.remember(record(f"filler {_}", tags={f"f{_}"}))
            reloaded.forget(reloaded.recall(f"filler")[0].id)

        assert [r.id for r in store(tmp_path).recall("shared")] == expected

    def test_there_is_no_date_filter_yet(self, tmp_path):
        """This milestone is the foundation, not New Dawn."""
        import inspect

        signature = inspect.signature(PersistentMemory.recall)
        assert list(signature.parameters) == ["self", "query", "kind", "tags", "limit"]


# ----------------------------------------------------------------------
# The rest of the contract
# ----------------------------------------------------------------------


class TestTheContract:
    def test_it_is_a_memory(self, tmp_path):
        assert isinstance(store(tmp_path), Memory)

    def test_remember_returns_the_stored_record(self, tmp_path):
        original = record()
        assert store(tmp_path).remember(original).id == original.id

    def test_forget_removes_from_disk_too(self, tmp_path):
        first = store(tmp_path)
        stored = first.remember(record())
        assert first.forget(stored.id) is True
        assert len(store(tmp_path)) == 0

    def test_forget_returns_false_for_an_unknown_id(self, tmp_path):
        assert store(tmp_path).forget("nope") is False

    def test_clear_empties_the_store_on_disk(self, tmp_path):
        first = store(tmp_path)
        first.remember(record())
        assert first.clear() == 1
        assert len(store(tmp_path)) == 0

    def test_a_cleared_store_is_a_real_empty_file(self, tmp_path):
        """"Cleared" and "never existed" must not look the same on disk."""
        first = store(tmp_path)
        first.remember(record())
        first.clear()
        assert (tmp_path / "manager.json").exists()

    def test_duplicate_ids_are_still_refused(self, tmp_path):
        first = store(tmp_path)
        stored = first.remember(record())
        with pytest.raises(MemoryStoreError):
            first.remember(record("different content", id=stored.id))

    def test_a_refused_duplicate_is_not_written(self, tmp_path):
        first = store(tmp_path)
        stored = first.remember(record())
        with pytest.raises(MemoryStoreError):
            first.remember(record("different", id=stored.id))
        assert len(store(tmp_path)) == 1

    def test_len_reflects_what_is_stored(self, tmp_path):
        first = store(tmp_path)
        first.remember(record())
        assert len(first) == 1


# ----------------------------------------------------------------------
# Damaged and missing stores
# ----------------------------------------------------------------------


class TestRecovery:
    def test_a_missing_file_loads_as_empty(self, tmp_path):
        assert len(store(tmp_path)) == 0

    def test_a_missing_directory_is_created_on_write(self, tmp_path):
        PersistentMemory(tmp_path / "a" / "b" / "m.json").remember(record())
        assert (tmp_path / "a" / "b" / "m.json").exists()

    def test_malformed_json_does_not_crash(self, tmp_path):
        (tmp_path / "manager.json").write_text("{not json", encoding="utf-8")
        assert len(store(tmp_path)) == 0

    def test_a_damaged_file_is_kept_for_inspection(self, tmp_path):
        (tmp_path / "manager.json").write_text("{not json", encoding="utf-8")
        store(tmp_path)
        assert list(tmp_path.glob("manager.corrupt-*")), "the damaged file was destroyed"

    def test_a_damaged_store_still_accepts_new_records(self, tmp_path):
        (tmp_path / "manager.json").write_text("{not json", encoding="utf-8")
        recovered = store(tmp_path)
        recovered.remember(record())
        assert len(store(tmp_path)) == 1

    def test_a_json_array_instead_of_an_object_is_quarantined(self, tmp_path):
        (tmp_path / "manager.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert len(store(tmp_path)) == 0
        assert list(tmp_path.glob("manager.corrupt-*"))

    def test_an_unknown_schema_version_is_quarantined(self, tmp_path):
        (tmp_path / "manager.json").write_text(
            json.dumps({"version": 999, "records": []}), encoding="utf-8"
        )
        store(tmp_path)
        assert list(tmp_path.glob("manager.corrupt-*"))

    def test_one_unexpected_record_does_not_cost_the_others(self, tmp_path):
        """Skipping the whole file over a single bad entry would be worse."""
        good = PersistentMemory._serialise(record("a good one", tags={"good"}))
        payload = {"version": SCHEMA_VERSION, "records": [good, {"nonsense": True}, "not a dict"]}
        (tmp_path / "manager.json").write_text(json.dumps(payload), encoding="utf-8")

        recovered = store(tmp_path)
        assert len(recovered) == 1
        assert recovered.recall("good")[0].content == "a good one"

    def test_skipped_records_are_reported(self, tmp_path, caplog):
        import logging

        payload = {"version": SCHEMA_VERSION, "records": [{"nonsense": True}]}
        (tmp_path / "manager.json").write_text(json.dumps(payload), encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="src"):
            store(tmp_path)
        assert "unreadable record" in caplog.text

    def test_an_unwritable_store_raises_rather_than_pretending(self, tmp_path, monkeypatch):
        first = store(tmp_path)

        import os as os_module

        def refuse(src, dst, *args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(os_module, "replace", refuse)
        with pytest.raises(MemoryStoreError):
            first.remember(record())

    def test_a_failed_write_leaves_the_index_consistent(self, tmp_path, monkeypatch):
        """The in-memory view must not hold a record the disk never got."""
        first = store(tmp_path)

        import os as os_module

        def refuse(src, dst, *args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(os_module, "replace", refuse)
        with pytest.raises(MemoryStoreError):
            first.remember(record())

        assert len(first) == 0, "the index kept a record that was never persisted"

    def test_an_unserialisable_context_is_refused(self, tmp_path):
        first = store(tmp_path)
        with pytest.raises(MemoryStoreError):
            first.remember(record(context={"bad": {1, 2, 3}}))


# ----------------------------------------------------------------------
# Ephemeral is untouched
# ----------------------------------------------------------------------


class TestEphemeralIsPreserved:
    def test_it_still_exists(self):
        assert EphemeralMemory().recall() == ()

    def test_it_still_does_not_persist(self, tmp_path):
        first = EphemeralMemory()
        first.remember(record())
        assert len(EphemeralMemory()) == 0

    def test_it_writes_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        EphemeralMemory().remember(record())
        assert list(tmp_path.iterdir()) == []

    def test_the_two_are_different_types(self, tmp_path):
        assert type(EphemeralMemory()) is not type(store(tmp_path))

    def test_both_satisfy_the_same_contract(self, tmp_path):
        for memory in (EphemeralMemory(), store(tmp_path)):
            assert isinstance(memory, Memory)


# ----------------------------------------------------------------------
# The factory
# ----------------------------------------------------------------------


class TestFactory:
    def test_the_default_is_ephemeral(self):
        assert settings().memory == "ephemeral"
        assert isinstance(build_memory(settings(), MANAGER), EphemeralMemory)

    def test_persistent_is_selectable(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        assert isinstance(build_memory(config, MANAGER), PersistentMemory)

    def test_the_owner_selects_the_file(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        assert build_memory(config, MANAGER).path.name == "manager.json"
        assert build_memory(config, EMAIL_AGENT).path.name == "email_agent.json"

    def test_different_owners_get_different_stores(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        manager = build_memory(config, MANAGER)
        specialist = build_memory(config, EMAIL_AGENT)

        manager.remember(record("only the manager knows this", tags={"m"}))
        assert len(specialist.recall("manager")) == 0

    def test_an_empty_owner_is_refused(self):
        with pytest.raises(MemoryStoreError):
            build_memory(settings(), "   ")

    def test_an_unknown_memory_name_fails_fast(self):
        with pytest.raises(ValueError) as exc:
            build_memory(settings(memory="telepathy"), MANAGER)
        assert "telepathy" in str(exc.value)

    def test_the_registry_lists_what_exists(self):
        assert available_memories() == ["ephemeral", "persistent"]

    def test_the_setting_is_read_from_the_environment(self):
        assert Settings.from_env(env={"MANAGER_MEMORY": "PERSISTENT"}).memory == "persistent"

    def test_an_empty_setting_is_rejected(self):
        with pytest.raises(ValueError):
            Settings(memory="  ")
        with pytest.raises(ValueError):
            Settings(memory_dir="  ")

    def test_describe_reports_it_without_a_secret(self):
        described = settings(memory="persistent", api_key="sk-ant-SECRET").describe()
        assert "memory=persistent" in described
        assert "SECRET" not in described


# ----------------------------------------------------------------------
# Manager recreation, and the heartbeat lifecycle
# ----------------------------------------------------------------------


class TestManagerRecreation:
    def test_a_new_manager_recalls_the_previous_one_s_memory(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))

        first = ManagerAgent(memory=build_memory(config, MANAGER))
        first.memory.remember(record("delegated inbox work to email_agent", tags={"m"}))
        del first

        second = ManagerAgent(memory=build_memory(config, MANAGER))
        assert len(second.memory.recall("delegated")) == 1

    def test_recall_context_sees_the_earlier_run(self, tmp_path):
        from src.core.contracts import Task

        config = settings(memory="persistent", memory_dir=str(tmp_path))
        first = ManagerAgent(memory=build_memory(config, MANAGER))
        first.memory.remember(record("inbox scanning went well", kind="delegation_outcome",
                                     tags={"email_agent"}))

        second = ManagerAgent(memory=build_memory(config, MANAGER))
        assert second.recall_context(Task(description="scan my inbox")) != ()

    def test_an_ephemeral_manager_still_forgets(self, tmp_path):
        config = settings(memory_dir=str(tmp_path))
        first = ManagerAgent(memory=build_memory(config, MANAGER))
        first.memory.remember(record())
        second = ManagerAgent(memory=build_memory(config, MANAGER))
        assert len(second.memory.recall(limit=50)) == 0


class TestHeartbeatLifecycle:
    def _runner(self, tmp_path, config, seen):
        def build_manager(user_profile):
            manager = ManagerAgent(memory=build_memory(config, MANAGER))
            agent = EmailAgent(
                sample_inbox(),
                analyzers=[JobOpportunityAnalyzer(profile=user_profile)],
                memory=build_memory(config, EMAIL_AGENT),
            )
            manager.register_agent(agent)
            seen.append((manager, agent))
            return manager

        return HeartbeatRunner(
            build_manager=build_manager,
            profile_loader=lambda: load_profile("USER.example.md"),
            state_store=StateStore(tmp_path / "state" / "processed.json"),
            enabled=True,
            reports_dir=tmp_path / "reports",
        )

    def test_tick_two_recalls_what_tick_one_learned(self, tmp_path):
        """The exact failure the audit measured, now fixed."""
        config = settings(memory="persistent", memory_dir=str(tmp_path / "memory"))
        seen = []
        runner = self._runner(tmp_path, config, seen)

        runner.tick()
        after_first = len(seen[0][0].memory.recall(limit=100))
        assert after_first > 0, "tick 1 recorded nothing to carry forward"

        runner.tick()
        assert len(seen[1][0].memory.recall(limit=100)) >= after_first

    def test_the_managers_are_still_different_objects(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path / "memory"))
        seen = []
        runner = self._runner(tmp_path, config, seen)
        runner.tick()
        runner.tick()
        assert seen[0][0] is not seen[1][0], "the Manager unexpectedly survived"

    def test_the_ephemeral_default_still_forgets_between_ticks(self, tmp_path):
        """The previous behaviour, preserved and pinned."""
        config = settings(memory_dir=str(tmp_path / "memory"))
        seen = []
        runner = self._runner(tmp_path, config, seen)
        runner.tick()
        runner.tick()
        assert len(seen[1][0].memory.recall(limit=100)) == 0


# ----------------------------------------------------------------------
# Manager and Specialist stay separate
# ----------------------------------------------------------------------


class TestSeparation:
    def test_they_use_different_files(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        assert build_memory(config, MANAGER).path != build_memory(config, EMAIL_AGENT).path

    def test_manager_records_never_appear_in_the_specialist_store(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        ManagerAgent(memory=build_memory(config, MANAGER)).memory.remember(
            record("a manager-only decision", tags={"manager"})
        )
        agent = EmailAgent(sample_inbox(), memory=build_memory(config, EMAIL_AGENT))
        assert agent.memory.recall(limit=100) == ()

    def test_specialist_records_never_appear_in_the_manager_store(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        EmailAgent(sample_inbox(), memory=build_memory(config, EMAIL_AGENT)).memory.remember(
            record("a specialist-only lesson", tags={"email"})
        )
        manager = ManagerAgent(memory=build_memory(config, MANAGER))
        assert manager.memory.recall(limit=100) == ()

    def test_they_are_different_objects(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        assert build_memory(config, MANAGER) is not build_memory(config, EMAIL_AGENT)

    def test_separation_survives_a_restart(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        build_memory(config, MANAGER).remember(record("manager thing", tags={"m"}))
        build_memory(config, EMAIL_AGENT).remember(record("specialist thing", tags={"s"}))

        assert len(build_memory(config, MANAGER).recall(limit=100)) == 1
        assert len(build_memory(config, EMAIL_AGENT).recall(limit=100)) == 1
        assert build_memory(config, MANAGER).recall("specialist") == ()


# ----------------------------------------------------------------------
# Nothing reaches production or the network
# ----------------------------------------------------------------------


class TestIsolation:
    def test_no_store_is_written_outside_tmp(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        build_memory(config, MANAGER).remember(record())
        assert (tmp_path / "manager.json").exists()

    def test_the_production_guard_would_catch_a_default_path(self):
        """MANAGER_MEMORY_DIR defaults under data/, which the conftest guard
        blocks - so a test that forgets tmp_path fails loudly."""
        from conftest import _is_production_data

        assert _is_production_data(settings().memory_dir + "/manager.json")

    def test_the_module_imports_no_vendor_or_network_package(self):
        import inspect

        import src.services.memory.persistent as module

        source = inspect.getsource(module)
        for forbidden in ("anthropic", "requests", "httpx", "googleapiclient", "socket"):
            assert forbidden not in source, forbidden

    def test_it_does_not_touch_heartbeat_state(self):
        """processed.json is operational state, not cognitive memory.

        Checked on the imports rather than the text: the module docstring
        legitimately cites StateStore as the atomic-write pattern it follows.
        """
        import ast
        import inspect

        import src.services.memory.persistent as module

        imported = []
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)

        assert not any("heartbeat" in name.lower() for name in imported), imported
        assert not any("statestore" in name.lower() for name in imported), imported
