"""Tests for the governed-evolution vocabulary: Migration, MigrationChain,
MigrationUnavailable, and SYSTEM_VERSION.

Every test here is pure - no file, no Path, no clock, no network - matching
the module's own claim about itself. If any test here needed tmp_path, that
would mean the module had stopped being what its docstring says it is.
"""

from __future__ import annotations

import pytest

from src.core.versioning import (
    SYSTEM_VERSION,
    Migration,
    MigrationChain,
    MigrationUnavailable,
)


class TestSystemVersion:
    def test_system_version_is_a_string(self):
        """Deliberately not an int, unlike every SCHEMA_VERSION/
        SNAPSHOT_VERSION in this codebase - a different shape is one more
        way this cannot be confused for one of them by accident."""
        assert isinstance(SYSTEM_VERSION, str)
        assert SYSTEM_VERSION.strip() != ""


class TestMigrationIsGaplessAndSingleStep:
    def test_a_single_step_migration_is_accepted(self):
        Migration(from_version=1, to_version=2, apply=lambda d: d)

    def test_a_skipped_version_is_refused(self):
        with pytest.raises(ValueError):
            Migration(from_version=1, to_version=3, apply=lambda d: d)

    def test_a_backward_step_is_refused(self):
        with pytest.raises(ValueError):
            Migration(from_version=2, to_version=1, apply=lambda d: d)

    def test_a_migration_to_itself_is_refused(self):
        with pytest.raises(ValueError):
            Migration(from_version=1, to_version=1, apply=lambda d: d)


class TestMigrationChainWithNothingRegistered:
    """The state every real store in this codebase is wired with today:
    schema 1 has always been schema 1, so no Migration exists yet. This is
    the behavior-preserving case - wiring the seam in must not change what
    happens for any version that exists in the wild right now."""

    def test_a_version_can_always_migrate_to_itself(self):
        chain = MigrationChain()
        assert chain.can_migrate(1, 1) is True

    def test_no_other_migration_is_possible(self):
        chain = MigrationChain()
        assert chain.can_migrate(0, 1) is False
        assert chain.can_migrate(1, 2) is False

    def test_migrating_to_a_different_version_raises(self):
        chain = MigrationChain()
        with pytest.raises(MigrationUnavailable):
            chain.migrate({"version": 0}, from_version=0, to_version=1)


class TestMigrationChainWithARegisteredPath:
    def _chain(self):
        return MigrationChain([
            Migration(from_version=0, to_version=1, apply=lambda d: {**d, "version": 1, "step": "0-1"}),
            Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2, "step": "1-2"}),
        ])

    def test_a_single_registered_step_is_reported_as_possible(self):
        assert self._chain().can_migrate(0, 1) is True

    def test_a_multi_step_path_is_walked_in_order(self):
        result = self._chain().migrate({"version": 0}, from_version=0, to_version=2)
        assert result["version"] == 2
        assert result["step"] == "1-2"  # the last step applied, proving both ran in order

    def test_a_gap_with_no_step_for_an_intermediate_version_is_unavailable(self):
        """Registering 0->1 and 1->2 does not imply 0->2 exists on its own -
        it exists only because both steps are present. Removing the middle
        step must make the whole path unavailable, not silently skip it."""
        chain = MigrationChain([
            Migration(from_version=0, to_version=1, apply=lambda d: {**d, "version": 1}),
            # no 1->2 registered
        ])
        assert chain.can_migrate(0, 2) is False
        with pytest.raises(MigrationUnavailable):
            chain.migrate({"version": 0}, from_version=0, to_version=2)

    def test_a_version_ahead_of_the_target_cannot_migrate_backward(self):
        chain = self._chain()
        assert chain.can_migrate(2, 1) is False
        with pytest.raises(MigrationUnavailable):
            chain.migrate({"version": 2}, from_version=2, to_version=1)

    def test_duplicate_migrations_from_the_same_version_are_rejected_at_construction(self):
        with pytest.raises(ValueError):
            MigrationChain([
                Migration(from_version=0, to_version=1, apply=lambda d: d),
                Migration(from_version=0, to_version=1, apply=lambda d: d),
            ])


class TestMigrationIsPureAndDoesNotMutateTheCaller:
    def test_the_callers_original_dict_is_unchanged(self):
        chain = MigrationChain([
            Migration(from_version=0, to_version=1, apply=lambda d: {**d, "version": 1, "added": True}),
        ])
        original = {"version": 0, "records": []}
        chain.migrate(original, from_version=0, to_version=1)
        assert original == {"version": 0, "records": []}, "migrate() mutated the caller's own dict"

    def test_the_same_input_always_produces_the_same_output(self):
        """Deterministic, not just pure - no clock, no randomness, no
        environment read anywhere in the path a Migration.apply can take."""
        chain = MigrationChain([
            Migration(from_version=0, to_version=1, apply=lambda d: {**d, "version": 1}),
        ])
        first = chain.migrate({"version": 0, "records": ["a"]}, from_version=0, to_version=1)
        second = chain.migrate({"version": 0, "records": ["a"]}, from_version=0, to_version=1)
        assert first == second

    def test_an_untouched_version_returns_an_equal_but_independent_copy(self):
        chain = MigrationChain()
        original = {"version": 1, "records": []}
        result = chain.migrate(original, from_version=1, to_version=1)
        assert result == original
        assert result is not original


class TestAMigrationThatMisbehavesNeverEscapesAsSomethingElse:
    """The contract every store's own load path relies on: whatever goes
    wrong inside a Migration function, the caller only ever has to catch
    one exception type."""

    def test_a_migration_that_raises_is_reported_as_migrationunavailable(self):
        def broken(_raw):
            raise ValueError("simulated bug in a future migration")

        chain = MigrationChain([Migration(from_version=0, to_version=1, apply=broken)])
        with pytest.raises(MigrationUnavailable):
            chain.migrate({"version": 0}, from_version=0, to_version=1)

    def test_a_migration_that_returns_a_non_dict_is_reported_as_migrationunavailable(self):
        chain = MigrationChain([Migration(from_version=0, to_version=1, apply=lambda d: ["not", "a", "dict"])])
        with pytest.raises(MigrationUnavailable):
            chain.migrate({"version": 0}, from_version=0, to_version=1)

    def test_a_later_step_that_returns_a_non_dict_also_stops_the_chain(self):
        chain = MigrationChain([
            Migration(from_version=0, to_version=1, apply=lambda d: {**d, "version": 1}),
            Migration(from_version=1, to_version=2, apply=lambda d: None),
        ])
        with pytest.raises(MigrationUnavailable):
            chain.migrate({"version": 0}, from_version=0, to_version=2)
