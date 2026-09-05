"""Stage 5 — stable identity, stable memory ownership.

An implementation change, or a display-name change, must never accidentally
create a different agent or a different memory store. For the Manager:

    stable technical key = "manager"      selects the file, always
    display name         = whatever IDENTITY.md currently says ("Alfred")
    memory owner          = the stable key, never the display name

This file tests two layers:

    adopt_legacy_store()            agent-agnostic file mechanics -
                                     src/services/memory/persistent.py
    adopt_legacy_manager_memory()   the Manager-specific policy wrapper -
                                     src/services/memory/factory.py

test_specialist_identity.py::TestAlfredAndArthurArePeers proves the same
rule end to end through the real composition root; this file proves the
mechanics that make it safe: checkpointed, atomic, idempotent, and never
destructive to data this project cannot explain.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.services.memory.factory import MANAGER, adopt_legacy_manager_memory
from src.services.memory.memory import MemoryRecord
from src.services.memory.persistent import (
    SCHEMA_VERSION,
    PersistentMemory,
    adopt_legacy_store,
)

WHEN = datetime(2026, 8, 21, 11, 30, 45, 123456, tzinfo=timezone.utc)


def record(content="the user prefers remote work", **kwargs):
    kwargs.setdefault("kind", "user_context")
    kwargs.setdefault("created_at", WHEN)
    return MemoryRecord(content=content, **kwargs)


def settings(**overrides):
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


# ---------------------------------------------------------------------------
# adopt_legacy_store() - agent-agnostic file mechanics
# ---------------------------------------------------------------------------


class TestLegacyStoreIsAdopted:
    def test_a_legacy_file_is_adopted_under_the_stable_owner(self, tmp_path):
        legacy = PersistentMemory(tmp_path / "alfred.json")
        legacy.remember(record("kept across the identity fix"))

        adopted = adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        assert adopted is True
        assert (tmp_path / "manager.json").exists()
        restored = PersistentMemory(tmp_path / "manager.json")
        assert len(restored) == 1
        assert restored.recall("kept")[0].content == "kept across the identity fix"

    def test_all_records_survive_the_adoption(self, tmp_path):
        """Not just one - every record, in its original relative order."""
        legacy = PersistentMemory(tmp_path / "alfred.json")
        for i in range(5):
            legacy.remember(record(f"record number {i}", tags={f"n{i}"}))

        adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        restored = PersistentMemory(tmp_path / "manager.json")
        assert len(restored) == 5
        newest_first = restored.recall(limit=10)
        assert [r.content for r in newest_first] == [
            "record number 4", "record number 3", "record number 2",
            "record number 1", "record number 0",
        ], "original insertion order was not preserved"

    def test_record_ids_are_preserved_not_regenerated(self, tmp_path):
        legacy = PersistentMemory(tmp_path / "alfred.json")
        original = legacy.remember(record())

        adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        restored = PersistentMemory(tmp_path / "manager.json")
        assert restored.recall(limit=1)[0].id == original.id


class TestAdoptionIsCheckpointedAndAtomic:
    def test_the_pre_adoption_file_is_preserved_as_a_checkpoint(self, tmp_path):
        legacy_path = tmp_path / "alfred.json"
        legacy = PersistentMemory(legacy_path)
        legacy.remember(record("must not be lost"))
        original_bytes = legacy_path.read_text(encoding="utf-8")

        adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        checkpoints = list(tmp_path.glob("alfred.pre-stable-owner-checkpoint"))
        assert checkpoints, "no checkpoint of the pre-adoption file was kept"
        assert checkpoints[0].read_text(encoding="utf-8") == original_bytes

    def test_the_original_named_file_no_longer_exists_after_adoption(self, tmp_path):
        """Renamed aside, not copied-and-left: a person listing the memory
        directory afterward sees one authoritative file, not two."""
        PersistentMemory(tmp_path / "alfred.json").remember(record())

        adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        assert not (tmp_path / "alfred.json").exists()

    def test_the_adopted_file_is_written_at_the_current_schema(self, tmp_path):
        PersistentMemory(tmp_path / "alfred.json").remember(record())

        adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        on_disk = json.loads((tmp_path / "manager.json").read_text(encoding="utf-8"))
        assert on_disk["version"] == SCHEMA_VERSION

    def test_a_write_failure_leaves_the_checkpoint_recoverable(self, tmp_path, monkeypatch):
        """Simulates the write step failing after the checkpoint succeeded:
        the checkpoint must still hold the complete original data, and
        nothing partial or corrupt is left at the stable path.

        Selective on purpose: Path.replace() (the checkpoint rename) and
        this test's own target, os.replace() (the final atomic write), both
        ultimately call the same os-level function - a blanket patch would
        break the checkpoint step too and never reach the write this test
        means to fail. Only the call writing to the stable path is broken;
        the real os.replace still runs for everything else.
        """
        import os as os_module

        legacy_path = tmp_path / "alfred.json"
        PersistentMemory(legacy_path).remember(record("must survive a failed write"))

        real_replace = os_module.replace

        def selective_replace(src, dst):
            if str(dst).endswith("manager.json"):
                raise OSError("simulated disk failure")
            return real_replace(src, dst)

        monkeypatch.setattr("src.services.memory.persistent.os.replace", selective_replace)

        adopted = adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        assert adopted is False
        assert not (tmp_path / "manager.json").exists(), "a failed write must not leave a file behind"
        checkpoint = tmp_path / "alfred.pre-stable-owner-checkpoint"
        assert checkpoint.exists()
        recovered = PersistentMemory(checkpoint)
        assert len(recovered) == 1
        assert recovered.recall("survive")[0].content == "must survive a failed write"


class TestAdoptionIsIdempotentAndConservative:
    def test_calling_it_twice_does_not_duplicate_records(self, tmp_path):
        PersistentMemory(tmp_path / "alfred.json").remember(record())

        first = adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")
        second = adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        assert first is True
        assert second is False
        assert len(PersistentMemory(tmp_path / "manager.json")) == 1

    def test_repeated_loading_of_the_adopted_store_stays_stable(self, tmp_path):
        """The scenario that actually matters: composition rebuilds the
        Manager's memory on every tick, so the object constructed after
        adoption must see exactly what adoption wrote, every time."""
        PersistentMemory(tmp_path / "alfred.json").remember(record("stable across reloads"))
        adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        for _ in range(3):
            reloaded = PersistentMemory(tmp_path / "manager.json")
            assert len(reloaded) == 1
            assert reloaded.recall("stable")[0].content == "stable across reloads"

    def test_an_existing_stable_file_is_never_touched(self, tmp_path):
        """The safe default when the situation is ambiguous: a real
        manager.json, however it got there, is authoritative. The legacy
        file is left exactly where it is - not merged, not deleted."""
        PersistentMemory(tmp_path / "manager.json").remember(record("the real, current manager memory"))
        PersistentMemory(tmp_path / "alfred.json").remember(record("an unrelated old file"))

        adopted = adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        assert adopted is False
        manager_store = PersistentMemory(tmp_path / "manager.json")
        assert len(manager_store) == 1
        assert manager_store.recall("current")[0].content == "the real, current manager memory"
        assert (tmp_path / "alfred.json").exists(), "an unrelated file must never be deleted"

    def test_no_legacy_file_means_nothing_happens(self, tmp_path):
        adopted = adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")
        assert adopted is False
        assert not (tmp_path / "manager.json").exists()

    def test_the_same_owner_on_both_sides_is_a_no_op(self, tmp_path):
        PersistentMemory(tmp_path / "manager.json").remember(record())
        assert adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="manager") is False

    def test_a_different_agents_store_is_never_touched(self, tmp_path):
        """Not "unrelated old file" in the abstract - a real second agent's
        store, adjacent in the same directory."""
        PersistentMemory(tmp_path / "email_agent.json").remember(record("Arthur's own memory"))

        adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        arthur_store = PersistentMemory(tmp_path / "email_agent.json")
        assert len(arthur_store) == 1
        assert arthur_store.recall("Arthur")[0].content == "Arthur's own memory"


class TestUnsupportedOrDamagedLegacyDataStaysQuarantined:
    """The refusal this whole seam must never weaken: adoption only ever
    carries forward data PersistentMemory's own load path already trusts."""

    def test_a_damaged_legacy_file_is_quarantined_not_adopted(self, tmp_path):
        (tmp_path / "alfred.json").write_text("{not valid json", encoding="utf-8")

        adopted = adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        assert adopted is False
        assert not (tmp_path / "manager.json").exists()
        assert list(tmp_path.glob("alfred.corrupt-*")), "the damaged file must still be preserved for inspection"

    def test_an_unknown_schema_version_in_the_legacy_file_is_quarantined(self, tmp_path):
        (tmp_path / "alfred.json").write_text(
            json.dumps({"version": 999, "records": []}), encoding="utf-8"
        )

        adopted = adopt_legacy_store(tmp_path, stable_owner="manager", legacy_owner="alfred")

        assert adopted is False
        assert not (tmp_path / "manager.json").exists()
        assert list(tmp_path.glob("alfred.corrupt-*"))


# ---------------------------------------------------------------------------
# adopt_legacy_manager_memory() - the Manager-specific policy wrapper
# ---------------------------------------------------------------------------


class TestAdoptLegacyManagerMemory:
    def test_adopts_from_the_declared_name_into_manager(self, tmp_path):
        PersistentMemory(tmp_path / "alfred.json").remember(record("Alfred's own history"))
        config = settings(memory="persistent", memory_dir=str(tmp_path))

        adopted = adopt_legacy_manager_memory(config, "Alfred")

        assert adopted is True
        store = PersistentMemory(tmp_path / "manager.json")
        assert store.recall("history")[0].content == "Alfred's own history"

    def test_a_declared_name_that_already_normalises_to_manager_is_a_no_op(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        assert adopt_legacy_manager_memory(config, "Manager") is False
        assert adopt_legacy_manager_memory(config, "manager") is False

    def test_ephemeral_memory_never_touches_the_filesystem(self, tmp_path, monkeypatch):
        """Nothing durable exists to adopt when persistence itself is off -
        this must not create data/memory just to check."""
        monkeypatch.chdir(tmp_path)
        config = settings(memory="ephemeral", memory_dir=str(tmp_path / "memory"))

        assert adopt_legacy_manager_memory(config, "Alfred") is False
        assert not (tmp_path / "memory").exists()

    def test_calling_it_on_every_composition_stays_safe(self, tmp_path):
        """The real usage pattern: build_manager() calls this unconditionally
        on every tick and every live-server request."""
        PersistentMemory(tmp_path / "alfred.json").remember(record())
        config = settings(memory="persistent", memory_dir=str(tmp_path))

        for _ in range(5):
            adopt_legacy_manager_memory(config, "Alfred")

        assert len(PersistentMemory(tmp_path / "manager.json")) == 1


# ---------------------------------------------------------------------------
# Provider and connection independence
# ---------------------------------------------------------------------------


class TestProviderAndConnectionBoundariesStayClean:
    def test_neither_function_names_a_provider(self):
        import inspect

        import src.services.memory.factory as factory_module
        import src.services.memory.persistent as persistent_module

        source = "".join([
            inspect.getsource(factory_module.adopt_legacy_manager_memory),
            inspect.getsource(persistent_module.adopt_legacy_store),
        ]).lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey"):
            assert forbidden not in source, forbidden

    def test_neither_function_names_a_connection_or_integration(self):
        import inspect

        import src.services.memory.factory as factory_module
        import src.services.memory.persistent as persistent_module

        source = "".join([
            inspect.getsource(factory_module.adopt_legacy_manager_memory),
            inspect.getsource(persistent_module.adopt_legacy_store),
        ]).lower()
        for forbidden in ("gmail", "connection", "integration", "oauth", "token"):
            assert forbidden not in source, forbidden
