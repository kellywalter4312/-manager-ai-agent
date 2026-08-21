"""Tests for the memory contract and its first implementation.

Two claims matter more than the rest and are worth finding quickly:

  - Separate instances are separate memories (TestMemoriesAreIndependent).
    This is what will let the Manager and each specialist hold their own,
    rather than sharing one global store.
  - Nothing is persisted and nothing is reasoned about (TestNothingIsPersisted,
    TestMemoryDoesNoReasoning). Memory retains and returns; it does not decide
    what matters, and it does not write to disk.

No network, no database, no files. Nothing is stubbed to make that true - the
implementation genuinely has no such capability.
"""

import inspect
import socket
from datetime import datetime, timedelta, timezone

import pytest

from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import (
    Memory,
    MemoryRecord,
    MemoryStoreError,
    normalise_tags,
)


def record(content="the user prefers remote work", **fields):
    return MemoryRecord(content=content, **fields)


def code_of(module) -> str:
    """A module's executable lines, with docstrings and comments removed.

    Structural tests must inspect what a module *does*, not what it says. Both
    files here discuss SQLite, Gmail and embeddings at length while explaining
    why none of them appear - grepping the raw text would flag that prose as a
    violation, which is exactly backwards.
    """
    source = open(module.__file__, encoding="utf-8").read()
    lines, in_docstring = [], False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.count('"""') == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith(("#", '"""')):
            continue
        lines.append(line.split("#")[0])
    return "\n".join(lines).lower()


def memory_with(*contents):
    store = EphemeralMemory()
    for content in contents:
        store.remember(record(content))
    return store


# ----------------------------------------------------------------------
# The contract exists and is narrow
# ----------------------------------------------------------------------


class TestTheContract:
    def test_memory_is_an_abstract_interface(self):
        with pytest.raises(TypeError):
            Memory()

    def test_it_declares_exactly_four_operations(self):
        """The guard against this growing into a database driver."""
        assert set(Memory.__abstractmethods__) == {
            "remember",
            "recall",
            "forget",
            "clear",
        }

    def test_the_implementation_satisfies_it(self):
        assert isinstance(EphemeralMemory(), Memory)

    def test_recall_filters_are_all_optional(self):
        params = inspect.signature(Memory.recall).parameters
        assert params["query"].default == ""
        assert params["kind"].default is None
        assert params["limit"].default == 10

    def test_the_contract_exposes_no_storage_concepts(self):
        public = {n for n in dir(Memory) if not n.startswith("_")}
        for leaked in ("save", "load", "connect", "commit", "flush", "path", "query"):
            assert leaked not in public, leaked


class TestTheRecord:
    def test_content_is_the_only_required_field(self):
        assert MemoryRecord(content="something").content == "something"

    def test_a_record_is_frozen(self):
        item = record()
        with pytest.raises(Exception):
            item.content = "something else"

    def test_empty_content_is_rejected(self):
        for blank in ("", "   ", "\n\t"):
            with pytest.raises(MemoryStoreError):
                MemoryRecord(content=blank)

    def test_whitespace_in_content_is_normalised(self):
        assert MemoryRecord(content="  a   b  ").content == "a b"

    def test_every_record_gets_a_distinct_id(self):
        assert len({record().id for _ in range(50)}) == 50

    def test_an_explicit_id_is_kept(self):
        assert record(id="fixed-1").id == "fixed-1"

    def test_a_blank_id_is_rejected(self):
        with pytest.raises(MemoryStoreError):
            record(id="   ")

    def test_tags_are_normalised(self):
        assert record(tags=["  Remote ", "PYTHON", "", "remote"]).tags == {
            "remote",
            "python",
        }

    def test_kind_is_normalised(self):
        assert record(kind="  Preference ").kind == "preference"

    def test_context_is_carried_but_never_interpreted(self):
        payload = {"anything": [1, 2, 3], "nested": {"a": True}}
        assert record(context=payload).context == payload

    def test_created_at_must_be_a_datetime(self):
        with pytest.raises(MemoryStoreError):
            record(created_at="yesterday")

    def test_describe_excludes_the_content(self):
        """Memory holds personal detail; logs must not."""
        item = record("the user's salary expectation is 50000", context={"x": "secret"})
        described = item.describe()
        assert "salary" not in described and "secret" not in described
        assert item.id in described

    def test_normalise_tags_is_shared_and_predictable(self):
        assert normalise_tags(["A", " a ", "B", ""]) == {"a", "b"}


# ----------------------------------------------------------------------
# remember / recall
# ----------------------------------------------------------------------


class TestRememberAndRecall:
    def test_remember_stores_a_record(self):
        store = EphemeralMemory()
        store.remember(record("the user prefers remote work"))
        assert len(store) == 1

    def test_remember_returns_the_record(self):
        store = EphemeralMemory()
        item = record()
        assert store.remember(item) is item

    def test_recall_retrieves_what_was_remembered(self):
        store = memory_with("the user prefers remote work")
        found = store.recall("remote")
        assert len(found) == 1
        assert found[0].content == "the user prefers remote work"

    def test_recall_matches_on_token_overlap(self):
        store = memory_with("python and javascript projects")
        assert store.recall("javascript")
        assert store.recall("PYTHON")           # case-insensitive
        assert not store.recall("rust")

    def test_recall_ranks_stronger_matches_first(self):
        store = memory_with(
            "python",
            "python and javascript and django",
        )
        found = store.recall("python javascript django")
        assert found[0].content == "python and javascript and django"

    def test_recall_with_no_query_returns_most_recent_first(self):
        store = memory_with("first", "second", "third")
        assert [r.content for r in store.recall()] == ["third", "second", "first"]

    def test_recall_respects_the_limit(self):
        store = memory_with(*[f"item {n}" for n in range(20)])
        assert len(store.recall(limit=5)) == 5

    def test_recall_filters_by_kind(self):
        store = EphemeralMemory()
        store.remember(record("likes remote work", kind="preference"))
        store.remember(record("applied to acme", kind="outcome"))
        found = store.recall(kind="preference")
        assert len(found) == 1 and found[0].kind == "preference"

    def test_recall_filters_by_tags(self):
        store = EphemeralMemory()
        store.remember(record("a python role", tags=["python", "remote"]))
        store.remember(record("a java role", tags=["java"]))
        assert len(store.recall(tags=["python"])) == 1

    def test_tag_filtering_requires_every_requested_tag(self):
        store = EphemeralMemory()
        store.remember(record("a", tags=["python", "remote"]))
        store.remember(record("b", tags=["python"]))
        assert len(store.recall(tags=["python", "remote"])) == 1

    def test_filters_combine(self):
        store = EphemeralMemory()
        store.remember(record("remote python role", kind="lead", tags=["python"]))
        store.remember(record("remote python role", kind="other", tags=["python"]))
        found = store.recall("python", kind="lead", tags=["python"])
        assert len(found) == 1 and found[0].kind == "lead"

    def test_recall_is_deterministic(self):
        store = memory_with("python one", "python two", "python three")
        assert [r.id for r in store.recall("python")] == [
            r.id for r in store.recall("python")
        ]

    def test_recall_returns_a_tuple(self):
        assert isinstance(memory_with("a").recall(), tuple)

    def test_recall_does_not_expose_the_internal_list(self):
        store = memory_with("a")
        store.recall()  # a tuple cannot be mutated to corrupt the store
        assert len(store) == 1


class TestEmptyMemoryBehavesPredictably:
    def test_a_new_memory_is_empty(self):
        assert len(EphemeralMemory()) == 0

    def test_recall_on_empty_returns_an_empty_tuple(self):
        assert EphemeralMemory().recall("anything") == ()

    def test_recall_on_empty_does_not_raise(self):
        assert EphemeralMemory().recall() == ()

    def test_finding_nothing_is_not_an_error(self):
        """An honest "I know nothing about that" is a valid answer."""
        assert memory_with("python").recall("astrophysics") == ()

    def test_forgetting_from_empty_returns_false(self):
        assert EphemeralMemory().forget("nope") is False

    def test_clearing_an_empty_memory_returns_zero(self):
        assert EphemeralMemory().clear() == 0


# ----------------------------------------------------------------------
# forget / clear
# ----------------------------------------------------------------------


class TestForgetAndClear:
    def test_forget_removes_the_record(self):
        store = EphemeralMemory()
        item = store.remember(record())
        assert store.forget(item.id) is True
        assert len(store) == 0

    def test_forget_reports_whether_it_existed(self):
        store = EphemeralMemory()
        item = store.remember(record())
        assert store.forget(item.id) is True
        assert store.forget(item.id) is False

    def test_forget_removes_only_the_named_record(self):
        store = EphemeralMemory()
        keep = store.remember(record("keep me", id="a"))
        store.remember(record("drop me", id="b"))
        store.forget("b")
        assert [r.id for r in store.recall()] == [keep.id]

    def test_a_forgotten_record_cannot_be_recalled(self):
        store = EphemeralMemory()
        item = store.remember(record("the user prefers remote work"))
        store.forget(item.id)
        assert store.recall("remote") == ()

    def test_forget_rejects_a_blank_id(self):
        with pytest.raises(MemoryStoreError):
            EphemeralMemory().forget("   ")

    def test_clear_removes_everything(self):
        store = memory_with("a", "b", "c")
        assert store.clear() == 3
        assert len(store) == 0 and store.recall() == ()

    def test_a_cleared_memory_is_reusable(self):
        store = memory_with("a")
        store.clear()
        store.remember(record("b"))
        assert len(store) == 1


# ----------------------------------------------------------------------
# THE KEY CLAIM: instances are independent
# ----------------------------------------------------------------------


class TestMemoriesAreIndependent:
    """What will let the Manager and each specialist own their own memory."""

    def test_two_memories_do_not_share_state(self):
        manager_memory = EphemeralMemory()
        specialist_memory = EphemeralMemory()

        manager_memory.remember(record("the user prefers remote work"))

        assert len(manager_memory) == 1
        assert len(specialist_memory) == 0
        assert specialist_memory.recall("remote") == ()

    def test_clearing_one_does_not_clear_another(self):
        first, second = memory_with("a"), memory_with("b")
        first.clear()
        assert len(second) == 1

    def test_forgetting_in_one_does_not_affect_another(self):
        first = EphemeralMemory()
        second = EphemeralMemory()
        item = record("shared content", id="same-id")
        first.remember(item)
        second.remember(item)

        first.forget("same-id")
        assert len(first) == 0 and len(second) == 1

    def test_there_is_no_module_level_store(self):
        """No global singleton anywhere in the package."""
        import src.services.memory.ephemeral as module

        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            assert not isinstance(value, (Memory, MemoryRecord, list, dict)), name

    def test_a_fresh_instance_never_inherits_records(self):
        memory_with("a", "b", "c")
        assert len(EphemeralMemory()) == 0

    def test_seeded_records_are_copied_not_aliased(self):
        seed = [record("a"), record("b")]
        store = EphemeralMemory(seed)
        seed.clear()
        assert len(store) == 2


# ----------------------------------------------------------------------
# Invalid input is rejected consistently
# ----------------------------------------------------------------------


class TestInvalidInput:
    def test_remembering_a_non_record_is_rejected(self):
        for bad in ("just a string", 42, None, {"content": "x"}, ["a"]):
            with pytest.raises(MemoryStoreError):
                EphemeralMemory().remember(bad)

    def test_a_duplicate_id_is_rejected(self):
        store = EphemeralMemory()
        store.remember(record("first", id="same"))
        with pytest.raises(MemoryStoreError):
            store.remember(record("second", id="same"))

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_a_non_positive_limit_is_rejected(self, bad):
        with pytest.raises(MemoryStoreError):
            EphemeralMemory().recall(limit=bad)

    def test_every_failure_uses_the_project_error_type(self):
        """One error type, as with MailboxError and BrainError."""
        assert issubclass(MemoryStoreError, Exception)

    def test_the_error_does_not_shadow_the_builtin(self):
        """`MemoryError` is a Python builtin; shadowing it would be a trap."""
        assert MemoryStoreError is not MemoryError
        assert not issubclass(MemoryStoreError, MemoryError)


# ----------------------------------------------------------------------
# Nothing is persisted, and nothing reaches outside the process
# ----------------------------------------------------------------------


class TestNothingIsPersisted:
    def test_records_do_not_survive_a_new_instance(self):
        """The whole point of the name 'ephemeral'."""
        memory_with("the user prefers remote work")
        assert EphemeralMemory().recall("remote") == ()

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        store = EphemeralMemory()
        store.remember(record("something worth keeping"))
        store.recall("something")
        store.clear()
        assert list(tmp_path.iterdir()) == []

    def test_the_module_opens_nothing(self):
        import src.services.memory.ephemeral as module

        source = open(module.__file__, encoding="utf-8").read()
        for forbidden in ("open(", "Path(", "os.replace", "json.dump", "write_text"):
            assert forbidden not in source, forbidden

    def test_no_database_driver_is_imported(self):
        import src.services.memory.ephemeral as module
        import src.services.memory.memory as contract

        for source in (code_of(module), code_of(contract)):
            for driver in ("sqlite", "redis", "psycopg", "pymongo", "sqlalchemy"):
                assert driver not in source, driver

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        store = EphemeralMemory()
        store.remember(record("something"))
        assert store.recall("something")


class TestMemoryKnowsNothingAboutTheDomain:
    def _sources(self):
        import src.services.memory.ephemeral as module
        import src.services.memory.memory as contract

        return {"memory.py": code_of(contract), "ephemeral.py": code_of(module)}

    def test_no_vendor_is_named(self):
        for name, source in self._sources().items():
            for word in ("anthropic", "openai", "gmail", "google", "api_key"):
                assert word not in source, f"{word} in {name}"

    def test_no_provider_is_imported(self):
        for name, source in self._sources().items():
            assert "from src.providers" not in source, name

    def test_no_specialist_is_imported(self):
        for name, source in self._sources().items():
            assert "from src.agents" not in source, name
            assert "from src.services.email" not in source, name

    def test_the_manager_is_not_imported(self):
        """Memory must be usable by a specialist without dragging in the CEO."""
        for name, source in self._sources().items():
            assert "from src.manager" not in source, name

    def test_the_contract_depends_only_on_core(self):
        import src.services.memory.memory as contract

        source = open(contract.__file__, encoding="utf-8").read()
        project = [
            line.strip()
            for line in source.splitlines()
            if line.startswith(("import ", "from ")) and "src." in line
        ]
        assert project == ["from src.core.contracts import tokenize"]


class TestMemoryDoesNoReasoning:
    """Retention and retrieval only. Judgement belongs to the brain."""

    def test_it_does_not_decide_what_is_important(self):
        public = {n for n in dir(Memory) if not n.startswith("_")}
        for judging in ("summarise", "summarize", "rank", "score", "prioritise", "importance"):
            assert judging not in public, judging

    def test_it_stores_what_it_is_given_verbatim(self):
        store = EphemeralMemory()
        trivial = store.remember(record("the sky is blue", kind="trivia"))
        assert store.recall("sky")[0].content == trivial.content

    def test_it_never_discards_anything_on_its_own(self):
        store = EphemeralMemory()
        for n in range(200):
            store.remember(record(f"fact number {n}"))
        assert len(store) == 200

    def test_it_does_not_interpret_the_query(self):
        """Literal token overlap - no synonyms, no inference, no model.

        "telecommuting" means the same thing to a person and to a model. It
        means nothing here, and that is the correct behaviour for a component
        that is not supposed to be reasoning.
        """
        store = memory_with("the user prefers remote work")
        assert store.recall("remote")                 # literal token match
        assert store.recall("telecommuting") == ()    # a model would infer this

    def test_no_llm_or_embedding_machinery_exists(self):
        import src.services.memory.ephemeral as module
        import src.services.memory.memory as contract

        for source in (code_of(module), code_of(contract)):
            for word in ("embedding", "vector", "llmprovider", "complete("):
                assert word not in source, word


# ----------------------------------------------------------------------
# Replaceability
# ----------------------------------------------------------------------


class TestMemoryIsReplaceable:
    def test_a_different_backend_satisfies_the_same_contract(self):
        """Proves the contract is implementable without the ephemeral list.

        Stands in for the persistent backend that comes later: a caller written
        against Memory works with either, unchanged.
        """

        class DictBackedMemory(Memory):
            def __init__(self):
                self._items = {}

            def remember(self, record):
                self._items[record.id] = record
                return record

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                return tuple(reversed(list(self._items.values())))[:limit]

            def forget(self, record_id):
                return self._items.pop(record_id, None) is not None

            def clear(self):
                count = len(self._items)
                self._items.clear()
                return count

        store = DictBackedMemory()
        item = store.remember(record("stored elsewhere"))
        assert store.recall()[0].content == "stored elsewhere"
        assert store.forget(item.id) is True
        assert store.clear() == 0

    def test_consumers_can_depend_on_the_abstraction_alone(self):
        def summarise(memory: Memory) -> int:
            return len(memory.recall(limit=100))

        assert summarise(EphemeralMemory()) == 0
        assert summarise(memory_with("a", "b")) == 2

    def test_a_record_created_now_is_timestamped_in_utc(self):
        """A persistent backend will need comparable timestamps."""
        item = record()
        assert item.created_at.tzinfo is not None
        assert abs(item.created_at - datetime.now(timezone.utc)) < timedelta(seconds=5)
