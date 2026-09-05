"""Stage 8 — the Upgrade Executor: perform an already-approved UpgradePlan.

Not a planner (src/control_center/upgrade_planner.py, untouched by this
milestone) and not the migration engine (src/core/versioning.py, also
untouched) - this module governs *when* the store's own, already-tested
checkpoint -> migrate -> atomic-write machinery fires, and reports whether
it landed where the plan said it would.

Lives at src/upgrade_executor.py, not under src/control_center/, on
purpose: tests/test_control_center.py::TestItOwnsNoBusinessLogic forbids
everything under control_center/ from importing src.services.memory.factory
(or any other constructor/engine module), because that package is a
read-only observation layer. This module constructs real stores and writes
real files - the same kind of "actor" src/composition.py already is, which
is why it lives beside composition.py instead of inside control_center/.

Every real MEMORY_MIGRATIONS/STATE_MIGRATIONS chain in this codebase is
still empty (schema 1 has never had a predecessor), so most tests here
register a synthetic chain via monkeypatch - the same technique
test_persistent_memory.py::TestGovernedMigration,
test_heartbeat_state.py::TestGovernedMigration and
test_upgrade_planner.py already use for exactly the same reason.
"""

from __future__ import annotations

import ast
import inspect
import json

import pytest

import src.heartbeat.state as state_module
import src.services.memory.persistent as persistent_module
import src.upgrade_executor as executor_module
from src.control_center.upgrade_planner import RequiredMigration, UpgradePlan, UpgradeStatus
from src.core.versioning import Migration, MigrationChain
from src.heartbeat.state import SCHEMA_VERSION as STATE_SCHEMA_VERSION
from src.services.memory.factory import MANAGER
from src.services.memory.persistent import SCHEMA_VERSION as MEMORY_SCHEMA_VERSION
from src.services.memory.persistent import PersistentMemory
from src.upgrade_executor import UpgradeResult, execute_upgrade


def _register_memory_migration(monkeypatch, *migrations):
    monkeypatch.setattr(persistent_module, "MEMORY_MIGRATIONS", MigrationChain(migrations))


def _register_state_migration(monkeypatch, *migrations):
    monkeypatch.setattr(state_module, "STATE_MIGRATIONS", MigrationChain(migrations))


def _memory_migration_0_to_current():
    return Migration(
        from_version=0,
        to_version=MEMORY_SCHEMA_VERSION,
        apply=lambda raw: {**raw, "version": MEMORY_SCHEMA_VERSION},
    )


def _state_migration_0_to_current():
    return Migration(
        from_version=0,
        to_version=STATE_SCHEMA_VERSION,
        apply=lambda raw: {**raw, "version": STATE_SCHEMA_VERSION},
    )


# ---------------------------------------------------------------------------
# 1. Approval is required, and cannot be assumed
# ---------------------------------------------------------------------------


class TestApprovalGate:
    def test_an_unapproved_plan_is_refused_regardless_of_status(self, tmp_path):
        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),),
        )
        result = execute_upgrade(
            plan, approved=False, memory_dir=tmp_path / "memory", state_path=tmp_path / "s.json"
        )
        assert result.success is False
        assert "not approved" in result.failure_reason
        assert result.migrations_attempted == ()

    def test_nothing_is_written_when_unapproved(self, tmp_path):
        memory_dir = tmp_path / "memory"
        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),),
        )
        execute_upgrade(plan, approved=False, memory_dir=memory_dir, state_path=tmp_path / "s.json")
        assert not memory_dir.exists()


class TestApprovalCannotBeAccidental:
    def test_approved_has_no_default(self):
        sig = inspect.signature(execute_upgrade)
        assert sig.parameters["approved"].default is inspect.Parameter.empty

    def test_approved_is_keyword_only(self):
        sig = inspect.signature(execute_upgrade)
        assert sig.parameters["approved"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_approved_cannot_be_supplied_positionally(self):
        plan = UpgradePlan(status=UpgradeStatus.CURRENT, reason="x")
        with pytest.raises(TypeError):
            execute_upgrade(plan, True)  # noqa: FBT003 - proving positional use is rejected

    def test_calling_with_no_approval_argument_at_all_raises(self):
        plan = UpgradePlan(status=UpgradeStatus.CURRENT, reason="x")
        with pytest.raises(TypeError):
            execute_upgrade(plan)


# ---------------------------------------------------------------------------
# 2. Only a plan that names executable migration work ever runs anything
# ---------------------------------------------------------------------------


class TestOnlyMigrationRequiredExecutes:
    def test_invalid_is_refused(self, tmp_path):
        plan = UpgradePlan(status=UpgradeStatus.INVALID, reason="bad request")
        result = execute_upgrade(
            plan, approved=True, memory_dir=tmp_path / "memory", state_path=tmp_path / "s.json"
        )
        assert result.success is False
        assert result.migrations_attempted == ()

    def test_unsupported_is_refused(self, tmp_path):
        plan = UpgradePlan(status=UpgradeStatus.UNSUPPORTED, reason="schema gap")
        result = execute_upgrade(
            plan, approved=True, memory_dir=tmp_path / "memory", state_path=tmp_path / "s.json"
        )
        assert result.success is False
        assert result.migrations_attempted == ()

    def test_current_succeeds_trivially(self, tmp_path):
        plan = UpgradePlan(status=UpgradeStatus.CURRENT, reason="nothing differs")
        result = execute_upgrade(
            plan, approved=True, memory_dir=tmp_path / "memory", state_path=tmp_path / "s.json"
        )
        assert result.success is True
        assert result.migrations_attempted == ()
        assert result.stores_affected == ()

    def test_upgrade_available_succeeds_trivially(self, tmp_path):
        plan = UpgradePlan(status=UpgradeStatus.UPGRADE_AVAILABLE, reason="system version differs only")
        result = execute_upgrade(
            plan, approved=True, memory_dir=tmp_path / "memory", state_path=tmp_path / "s.json"
        )
        assert result.success is True
        assert result.migrations_attempted == ()

    def test_current_and_upgrade_available_touch_no_files(self, tmp_path):
        memory_dir = tmp_path / "memory"
        for status in (UpgradeStatus.CURRENT, UpgradeStatus.UPGRADE_AVAILABLE):
            execute_upgrade(
                UpgradePlan(status=status, reason="x"),
                approved=True,
                memory_dir=memory_dir,
                state_path=tmp_path / "s.json",
            )
        assert not memory_dir.exists()


# ---------------------------------------------------------------------------
# 3. A real migration runs the store's own existing machinery
# ---------------------------------------------------------------------------


class TestRealMemoryMigration:
    def test_the_stores_own_machinery_runs_and_the_record_survives(self, tmp_path, monkeypatch):
        _register_memory_migration(monkeypatch, _memory_migration_0_to_current())
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        original = json.dumps(
            {
                "version": 0,
                "records": [
                    {
                        "id": "r1",
                        "content": "kept across the migration",
                        "kind": "note",
                        "tags": [],
                        "context": None,
                        "created_at": "2026-08-21T00:00:00+00:00",
                    }
                ],
            }
        )
        (memory_dir / f"{MANAGER}.json").write_text(original, encoding="utf-8")

        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),),
        )
        result = execute_upgrade(plan, approved=True, memory_dir=memory_dir, state_path=tmp_path / "s.json")

        assert result.success is True
        assert result.migrations_attempted == (RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),)
        assert result.migrations_completed == (RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),)
        assert result.stores_affected == ("memory",)

        on_disk = json.loads((memory_dir / f"{MANAGER}.json").read_text(encoding="utf-8"))
        assert on_disk["version"] == MEMORY_SCHEMA_VERSION

        checkpoint = memory_dir / f"{MANAGER}.v0-checkpoint"
        assert checkpoint.exists(), "the pre-migration file must be preserved, not discarded"
        assert checkpoint.read_text(encoding="utf-8") == original

        reloaded = PersistentMemory(memory_dir / f"{MANAGER}.json")
        assert len(reloaded) == 1
        assert reloaded.recall("kept")[0].content == "kept across the migration"


class TestRealStateMigration:
    def test_the_stores_own_machinery_runs(self, tmp_path, monkeypatch):
        _register_state_migration(monkeypatch, _state_migration_0_to_current())
        state_path = tmp_path / "state" / "processed.json"
        state_path.parent.mkdir(parents=True)
        original = json.dumps({"version": 0, "messages": {"m1": "2026-08-17T00:00:00+00:00"}})
        state_path.write_text(original, encoding="utf-8")

        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("state", 0, STATE_SCHEMA_VERSION),),
        )
        result = execute_upgrade(
            plan, approved=True, memory_dir=tmp_path / "memory", state_path=state_path
        )

        assert result.success is True
        assert result.stores_affected == ("state",)

        on_disk = json.loads(state_path.read_text(encoding="utf-8"))
        assert on_disk["version"] == STATE_SCHEMA_VERSION

        checkpoint = state_path.with_suffix(".v0-checkpoint")
        assert checkpoint.exists()
        assert checkpoint.read_text(encoding="utf-8") == original


class TestMultiStorePlan:
    def test_both_stores_migrate_from_a_single_plan(self, tmp_path, monkeypatch):
        _register_memory_migration(monkeypatch, _memory_migration_0_to_current())
        _register_state_migration(monkeypatch, _state_migration_0_to_current())

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": 0, "records": []}), encoding="utf-8"
        )
        state_path = tmp_path / "state" / "processed.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps({"version": 0, "messages": {}}), encoding="utf-8")

        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(
                RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),
                RequiredMigration("state", 0, STATE_SCHEMA_VERSION),
            ),
        )
        result = execute_upgrade(plan, approved=True, memory_dir=memory_dir, state_path=state_path)

        assert result.success is True
        assert set(result.stores_affected) == {"memory", "state"}
        assert len(result.migrations_completed) == 2


# ---------------------------------------------------------------------------
# 4. Idempotency: an already-migrated store is a no-op success
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_an_already_migrated_store_is_a_no_op_success(self, tmp_path, monkeypatch):
        _register_memory_migration(monkeypatch, _memory_migration_0_to_current())
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": MEMORY_SCHEMA_VERSION, "records": []}), encoding="utf-8"
        )

        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),),
        )
        result = execute_upgrade(plan, approved=True, memory_dir=memory_dir, state_path=tmp_path / "s.json")

        assert result.success is True
        assert result.stores_affected == ()
        assert result.migrations_completed == (RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),)

    def test_a_second_run_does_not_rewrite_the_file(self, tmp_path, monkeypatch):
        _register_memory_migration(monkeypatch, _memory_migration_0_to_current())
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        path = memory_dir / f"{MANAGER}.json"
        path.write_text(json.dumps({"version": 0, "records": []}), encoding="utf-8")

        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),),
        )
        first = execute_upgrade(plan, approved=True, memory_dir=memory_dir, state_path=tmp_path / "s.json")
        assert first.stores_affected == ("memory",)
        mtime_after_first = path.stat().st_mtime_ns

        second = execute_upgrade(plan, approved=True, memory_dir=memory_dir, state_path=tmp_path / "s.json")
        mtime_after_second = path.stat().st_mtime_ns

        assert second.success is True
        assert second.stores_affected == ()
        assert mtime_after_first == mtime_after_second, "an idempotent no-op must not rewrite the file"


# ---------------------------------------------------------------------------
# 5. Fails closed: on-disk reality must match what the plan expects
# ---------------------------------------------------------------------------


class TestFailsClosedOnUnexpectedOnDiskState:
    def test_refuses_when_on_disk_version_does_not_match_the_plans_expected_from(self, tmp_path, monkeypatch):
        _register_memory_migration(monkeypatch, _memory_migration_0_to_current())
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        path = memory_dir / f"{MANAGER}.json"
        original = json.dumps({"version": 5, "records": []})
        path.write_text(original, encoding="utf-8")

        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),),
        )
        result = execute_upgrade(plan, approved=True, memory_dir=memory_dir, state_path=tmp_path / "s.json")

        assert result.success is False
        assert "refusing to guess" in result.failure_reason
        assert path.read_text(encoding="utf-8") == original, "an unexpected file must be left untouched"
        assert not list(memory_dir.glob("*checkpoint*"))


class TestFailsClosedOnStaleTarget:
    def test_refuses_when_the_plans_target_does_not_match_the_installed_schema(self, tmp_path):
        # No store on this installation defines schema 99 as current - a
        # plan naming it must never be trusted, whatever the plan claims.
        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 1, 99),),
        )
        result = execute_upgrade(
            plan, approved=True, memory_dir=tmp_path / "memory", state_path=tmp_path / "s.json"
        )
        assert result.success is False
        assert "does not match the currently installed schema" in result.failure_reason


class TestUnknownStoreFailsClosed:
    def test_a_plan_naming_an_unknown_store_fails_closed(self, tmp_path):
        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("visual", 1, 2),),
        )
        result = execute_upgrade(
            plan, approved=True, memory_dir=tmp_path / "memory", state_path=tmp_path / "s.json"
        )
        assert result.success is False
        assert "not a store this executor can migrate" in result.failure_reason
        assert result.migrations_attempted == (RequiredMigration("visual", 1, 2),)


# ---------------------------------------------------------------------------
# 6. A migration the store itself cannot complete is reported as failure,
#    never mistaken for success - and the original is never lost
# ---------------------------------------------------------------------------


class TestFailureWhenTheStoresOwnMigrationCannotComplete:
    def test_a_migration_that_raises_is_reported_as_failure_not_success(self, tmp_path, monkeypatch):
        def broken(_raw):
            raise ValueError("boom")

        _register_memory_migration(
            monkeypatch, Migration(from_version=0, to_version=MEMORY_SCHEMA_VERSION, apply=broken)
        )
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": 0, "records": []}), encoding="utf-8"
        )

        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),),
        )
        result = execute_upgrade(plan, approved=True, memory_dir=memory_dir, state_path=tmp_path / "s.json")

        assert result.success is False
        assert result.migrations_completed == ()
        # PersistentMemory's own quarantine kept the original aside - a
        # failed migration must never look identical to a fresh, empty store.
        assert list(memory_dir.glob(f"{MANAGER}.corrupt-*")), "the original must be preserved, not discarded"


# ---------------------------------------------------------------------------
# 7. Structural boundaries: never plans, never duplicates the migration
#    engine, never touches identity or a provider
# ---------------------------------------------------------------------------


def _imported_names(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


class TestNeverPlansAndNeverDuplicatesTheMigrationEngine:
    def test_the_module_imports_no_planning_entrypoint(self):
        imported = _imported_names(executor_module)
        for forbidden in ("plan_upgrade", "UpgradeRequest"):
            assert not any(forbidden in name for name in imported), forbidden

    def test_the_module_imports_no_migration_engine_directly(self):
        imported = _imported_names(executor_module)
        for forbidden in ("MigrationChain", "MigrationUnavailable", "src.core.versioning.Migration"):
            assert not any(forbidden in name for name in imported), forbidden


class TestManagerIdentityAndProviderIndependence:
    def test_the_module_imports_nothing_identity_registry_or_provider_related(self):
        imported = _imported_names(executor_module)
        for forbidden in ("src.services.identity", "src.registry", "src.composition", "src.providers"):
            assert forbidden not in imported, forbidden

    def test_the_module_names_no_provider(self):
        source = inspect.getsource(executor_module).lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey"):
            assert forbidden not in source, forbidden

    def test_execute_upgrade_takes_no_agent_or_provider_parameter(self):
        params = inspect.signature(execute_upgrade).parameters.keys()
        for forbidden in ("agent", "provider", "identity"):
            assert not any(forbidden in p for p in params), forbidden

    def test_the_manager_store_is_addressed_by_the_stable_key_not_a_display_name(self, tmp_path, monkeypatch):
        _register_memory_migration(monkeypatch, _memory_migration_0_to_current())
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        # Written under MANAGER ("manager"), the stable key - not any
        # display name this codebase might show a user.
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": 0, "records": []}), encoding="utf-8"
        )
        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason="x",
            required_migrations=(RequiredMigration("memory", 0, MEMORY_SCHEMA_VERSION),),
        )
        result = execute_upgrade(plan, approved=True, memory_dir=memory_dir, state_path=tmp_path / "s.json")
        assert result.success is True


# ---------------------------------------------------------------------------
# 8. No automatic rollback - restoration stays a governed, human decision
# ---------------------------------------------------------------------------


class TestNoAutomaticRollback:
    def test_the_module_defines_no_rollback_or_restore_function(self):
        # Substring-matching the raw source would false-positive on the
        # module's own docstring, which *names* "rollback" to explain it
        # deliberately does not perform one - so this checks function and
        # call names via the AST instead, the same technique
        # test_upgrade_planner.py's independence tests use.
        tree = ast.parse(inspect.getsource(executor_module))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name.lower())
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id.lower())
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                names.add(node.func.attr.lower())

        for forbidden in ("rollback", "restore", "revert"):
            assert not any(forbidden in name for name in names), (forbidden, names)


# ---------------------------------------------------------------------------
# 9. The result is structured, contains no secrets, and is deterministic
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_no_field_name_suggests_a_secret_or_provider_value(self):
        forbidden = ("key", "secret", "token", "credential", "password", "provider", "model")
        for field_name in UpgradeResult.__dataclass_fields__:
            for word in forbidden:
                assert word not in field_name.lower(), (field_name, word)

    def test_to_dict_is_json_serialisable(self, tmp_path):
        result = execute_upgrade(
            UpgradePlan(status=UpgradeStatus.CURRENT, reason="x"),
            approved=True,
            memory_dir=tmp_path / "memory",
            state_path=tmp_path / "s.json",
        )
        json.dumps(result.to_dict())  # must not raise

    def test_upgrade_result_is_frozen(self):
        result = UpgradeResult(success=True, status=UpgradeStatus.CURRENT)
        with pytest.raises(Exception):
            result.success = False


class TestDeterministic:
    def test_the_same_plan_and_state_produce_an_equal_result_twice_over(self, tmp_path):
        state_path = tmp_path / "s.json"
        plan = UpgradePlan(status=UpgradeStatus.CURRENT, reason="x")
        first = execute_upgrade(plan, approved=True, memory_dir=tmp_path / "memory", state_path=state_path)
        second = execute_upgrade(plan, approved=True, memory_dir=tmp_path / "memory", state_path=state_path)
        assert first == second
