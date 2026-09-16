"""The composition root's two-lifetime contract.

``src/composition.py``'s own docstring names the bug this file exists to
catch: ``build_runtime`` is built ONCE per process (a provider, a router,
brains, a calendar - collaborators safe to share because nothing in them
changes between heartbeat ticks), while ``build_manager`` is built PER RUN,
and for the heartbeat, per tick. Collapsing the two into one long-lived
Manager is "the obvious 'System' object", and would quietly break the
temporal memory design: a ``PersistentMemory`` re-reads its file at
construction, so it is a *fresh* ``build_manager()`` call, not a shared
Manager, that lets tick 2 see what tick 1 (or an edit made entirely outside
this process) wrote. Before this file, `Runtime` was never constructed in a
test and neither ``build_runtime`` nor ``build_manager`` was called by one -
so a regression here could pass every other suite. See
[[composition-refactor-in-progress]] / ARCHITECTURE.md's REQUIREMENT_LEDGER
N-04.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.composition import Runtime, build_manager, build_runtime
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.memory.memory import MemoryRecord
from src.services.memory.persistent import PersistentMemory

WHEN = datetime(2026, 9, 16, 9, 0, 0, tzinfo=timezone.utc)


def settings(**overrides):
    from src.config.settings import Settings

    overrides.setdefault("log_level", "CRITICAL")
    return Settings.from_env(env={}, **overrides)


def record(content="a fact only the next tick should be able to see", **kwargs):
    kwargs.setdefault("kind", "user_context")
    kwargs.setdefault("created_at", WHEN)
    return MemoryRecord(content=content, **kwargs)


def build(tmp_path, **overrides):
    overrides.setdefault("memory", "persistent")
    overrides.setdefault("memory_dir", str(tmp_path))
    return build_runtime(settings(**overrides))


class TestBuildRuntimeIsARealRuntime:
    def test_returns_a_runtime_instance(self, tmp_path):
        assert isinstance(build(tmp_path), Runtime)

    def test_two_calls_produce_genuinely_independent_runtimes(self, tmp_path):
        """build_runtime has no side effect worth fearing (no file written,
        no network touched) - proven here by calling it twice with nothing
        shared between the two calls, and getting two distinct objects back."""
        first = build(tmp_path)
        second = build(tmp_path)
        assert first is not second
        assert first.manager_brain is not second.manager_brain


class TestBuildManagerIsCalledOncePerRun:
    def test_two_managers_from_one_runtime_are_independent_objects(self, tmp_path):
        runtime = build(tmp_path)
        tick_one = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        tick_two = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        assert tick_one is not tick_two
        assert tick_one.memory is not tick_two.memory

    def test_the_runtime_collaborators_are_shared_not_rebuilt_per_tick(self, tmp_path):
        """The other half of the two-lifetime contract: a tick must NOT pay
        to rebuild the provider/router/brain - they are Runtime's, built
        once, and every Manager built from this Runtime shares the exact
        same instances."""
        runtime = build(tmp_path)
        tick_one = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        tick_two = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        assert tick_one.brain is runtime.manager_brain
        assert tick_two.brain is runtime.manager_brain
        assert tick_one.router is runtime.router
        assert tick_two.router is runtime.router


class TestAFreshBuildManagerCallSeesWhatAnEarlierOneWrote:
    def test_a_record_written_by_one_tick_is_recalled_by_the_next(self, tmp_path):
        """The exact property named in composition.py's own docstring: 'a
        fresh Manager is what lets tick 2 see what tick 1 wrote'."""
        runtime = build(tmp_path)

        tick_one = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        tick_one.memory.remember(record("the person mentioned a work trip in October"))

        tick_two = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        recalled = tick_two.memory.recall("work trip")

        assert recalled and "October" in recalled[0].content

    def test_an_edit_made_entirely_outside_this_process_is_only_visible_to_a_fresh_manager(
        self, tmp_path
    ):
        """This is the regression this file exists to prevent. If a future
        refactor collapsed the two lifetimes - one long-lived Manager reused
        across ticks instead of a fresh build_manager() call each time - this
        test starts failing, because the already-built Manager below never
        re-reads the file and therefore never sees a change made outside it,
        while a genuinely fresh call does. That gap is not a defect; it is
        the entire reason the two functions are kept separate. Losing the gap
        (both managers agreeing) would mean the design had quietly collapsed."""
        runtime = build(tmp_path)
        already_built = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        assert already_built.memory.recall("a secret written between ticks") == ()

        # Nothing in this process touches `already_built` - a separate write,
        # exactly as an operator editing USER.md or another process writing
        # to the same store between heartbeat ticks would.
        PersistentMemory(already_built.memory.path).remember(
            record("a secret written between ticks")
        )

        assert already_built.memory.recall("a secret written between ticks") == ()

        fresh = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        recalled = fresh.memory.recall("a secret written between ticks")
        assert recalled and "a secret written between ticks" in recalled[0].content
