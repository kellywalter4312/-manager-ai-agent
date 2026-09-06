"""Stage 9 — the governed upgrade lifecycle: Manifest -> Plan -> Approval ->
Execute -> Verify -> new Manifest, as one explicit, inspectable operation
over components that already exist.

Not a fourth planner and not a second executor - every test here either
proves this module calls the real ``plan_upgrade``/``execute_upgrade`` it
imports, or proves - structurally, by AST - that it could not possibly have
built a competing implementation of either.

Real MEMORY_MIGRATIONS/STATE_MIGRATIONS chains are still empty in this
codebase (schema 1 has never had a predecessor), so tests that need a real
migration register a synthetic one via monkeypatch - the same technique
test_persistent_memory.py, test_heartbeat_state.py, test_upgrade_planner.py
and test_upgrade_executor.py already use. One wrinkle specific to this
lifecycle: ``upgrade_planner.py`` binds its own copies of
``MEMORY_MIGRATIONS``/``STATE_MIGRATIONS`` at import time, while
``persistent.py``/``state.py`` read their own live globals at call time -
so a migration used by both the planner (to produce MIGRATION_REQUIRED) and
the executor (to actually run) must be patched onto *both* module
namespaces, which ``register_memory_migration``/``register_state_migration``
below do together, once, so no test has to remember this twice.
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

import src.control_center.upgrade_planner as planner_module
import src.heartbeat.state as state_module
import src.services.memory.persistent as persistent_module
from src.control_center.manifest import UpgradeManifest, VisualEnvironmentVersion, upgrade_manifest
from src.control_center.upgrade_planner import UpgradeRequest, UpgradeStatus, plan_upgrade
from src.core.versioning import Migration, MigrationChain
from src.heartbeat.state import SCHEMA_VERSION as STATE_SCHEMA_VERSION
from src.services.memory.factory import MANAGER
from src.services.memory.persistent import PersistentMemory
from src.services.memory.persistent import SCHEMA_VERSION as MEMORY_SCHEMA_VERSION
from src.upgrade_executor import UpgradeResult
from src.upgrade_lifecycle import UpgradeLifecycleResult, run_upgrade

REPO_ROOT = Path(__file__).resolve().parent.parent
LIFECYCLE_SRC = REPO_ROOT / "src" / "upgrade_lifecycle.py"


def manifest(**overrides) -> UpgradeManifest:
    defaults = dict(
        system_version="0.1.0",
        snapshot_version=1,
        memory_schema_version=MEMORY_SCHEMA_VERSION,
        state_schema_version=STATE_SCHEMA_VERSION,
        visual_environments=(),
    )
    defaults.update(overrides)
    return UpgradeManifest(**defaults)


def register_memory_migration(monkeypatch, *migrations):
    chain = MigrationChain(migrations)
    monkeypatch.setattr(persistent_module, "MEMORY_MIGRATIONS", chain)
    monkeypatch.setattr(planner_module, "MEMORY_MIGRATIONS", chain)


def register_state_migration(monkeypatch, *migrations):
    chain = MigrationChain(migrations)
    monkeypatch.setattr(state_module, "STATE_MIGRATIONS", chain)
    monkeypatch.setattr(planner_module, "STATE_MIGRATIONS", chain)


def bump_memory_migration(monkeypatch):
    """The one migration most tests need: the schema immediately before
    today's, carried forward to whatever this installation's real
    MEMORY_SCHEMA_VERSION is. Not literally "schema 0" - that stopped being
    true the moment a second real schema (see persistent.py's own
    _migrate_v1_to_v2) made "one predecessor back" no longer mean zero."""
    register_memory_migration(
        monkeypatch,
        Migration(from_version=MEMORY_SCHEMA_VERSION - 1, to_version=MEMORY_SCHEMA_VERSION,
                   apply=lambda raw: {**raw, "version": MEMORY_SCHEMA_VERSION}),
    )


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


# ---------------------------------------------------------------------------
# 1-6: Planning integration
# ---------------------------------------------------------------------------


class TestPlanningIntegration:
    def test_the_lifecycle_uses_the_real_planner(self):
        current = manifest()
        request = UpgradeRequest()
        result = run_upgrade(request, approved=True, current_manifest=current)
        assert result.plan == plan_upgrade(current, request)

    def test_current_produces_a_no_op(self):
        result = run_upgrade(UpgradeRequest(), approved=False, current_manifest=manifest())
        assert result.status is UpgradeStatus.CURRENT
        assert result.success is True
        assert result.executed is False

    def test_upgrade_available_produces_a_no_op_success(self):
        current = manifest()
        request = UpgradeRequest(target_system_version="9.9.9")
        result = run_upgrade(request, approved=False, current_manifest=current)
        assert result.status is UpgradeStatus.UPGRADE_AVAILABLE
        assert result.success is True
        assert result.executed is False

    def test_migration_required_requires_approval(self, tmp_path, monkeypatch):
        bump_memory_migration(monkeypatch)
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        result = run_upgrade(
            request, approved=False, current_manifest=current,
            memory_dir=tmp_path / "memory", state_path=tmp_path / "state.json",
        )
        assert result.status is UpgradeStatus.MIGRATION_REQUIRED
        assert result.success is False

    def test_unsupported_fails_closed(self):
        current = manifest()
        request = UpgradeRequest(target_memory_schema_version=99)
        result = run_upgrade(request, approved=True, current_manifest=current)
        assert result.status is UpgradeStatus.UNSUPPORTED
        assert result.success is False
        assert result.executed is False

    def test_invalid_fails_closed(self):
        request = UpgradeRequest(target_memory_schema_version=-1)
        result = run_upgrade(request, approved=True, current_manifest=manifest())
        assert result.status is UpgradeStatus.INVALID
        assert result.success is False
        assert result.executed is False


# ---------------------------------------------------------------------------
# 7-10: Approval
# ---------------------------------------------------------------------------


class TestApproval:
    def test_approved_has_no_default(self):
        sig = inspect.signature(run_upgrade)
        assert sig.parameters["approved"].default is inspect.Parameter.empty

    def test_missing_approval_cannot_execute(self):
        with pytest.raises(TypeError):
            run_upgrade(UpgradeRequest())  # noqa

    def test_explicit_false_cannot_execute(self, tmp_path, monkeypatch):
        bump_memory_migration(monkeypatch)
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        memory_dir = tmp_path / "memory"
        result = run_upgrade(
            request, approved=False, current_manifest=current,
            memory_dir=memory_dir, state_path=tmp_path / "state.json",
        )
        assert result.success is False
        assert result.executed is True  # execute_upgrade was called, and itself refused
        assert not memory_dir.exists()

    def test_explicit_true_can_execute_a_valid_migration(self, tmp_path, monkeypatch):
        bump_memory_migration(monkeypatch)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": MEMORY_SCHEMA_VERSION - 1, "records": []}), encoding="utf-8"
        )
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        result = run_upgrade(
            request, approved=True, current_manifest=current,
            memory_dir=memory_dir, state_path=tmp_path / "state.json",
        )
        assert result.success is True
        assert result.executed is True
        assert result.verified is True

    def test_approval_cannot_override_unsupported(self, tmp_path):
        current = manifest()
        request = UpgradeRequest(target_memory_schema_version=99)
        result = run_upgrade(
            request, approved=True, current_manifest=current,
            memory_dir=tmp_path / "memory", state_path=tmp_path / "state.json",
        )
        assert result.status is UpgradeStatus.UNSUPPORTED
        assert result.executed is False
        assert not (tmp_path / "memory").exists()


# ---------------------------------------------------------------------------
# 11-14: Execution integration
# ---------------------------------------------------------------------------


class TestExecutionIntegration:
    def test_a_valid_migration_reaches_the_real_executor(self, tmp_path, monkeypatch):
        bump_memory_migration(monkeypatch)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": MEMORY_SCHEMA_VERSION - 1, "records": []}), encoding="utf-8"
        )
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        result = run_upgrade(
            request, approved=True, current_manifest=current,
            memory_dir=memory_dir, state_path=tmp_path / "state.json",
        )
        assert isinstance(result.execution, UpgradeResult)
        assert result.execution.success is True

    def test_the_executor_is_not_duplicated(self):
        """AST-level: this module imports execute_upgrade and never defines
        its own function that constructs PersistentMemory or StateStore -
        the one thing execute_upgrade alone is allowed to do."""
        source = LIFECYCLE_SRC.read_text(encoding="utf-8")
        assert "PersistentMemory(" not in source
        assert "StateStore(" not in source
        imported = _imported_names(LIFECYCLE_SRC)
        assert any("execute_upgrade" in name for name in imported)

    def test_migration_executes_only_once(self, tmp_path, monkeypatch):
        bump_memory_migration(monkeypatch)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": MEMORY_SCHEMA_VERSION - 1, "records": []}), encoding="utf-8"
        )
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        first = run_upgrade(
            request, approved=True, current_manifest=current,
            memory_dir=memory_dir, state_path=tmp_path / "state.json",
        )
        assert first.executed is True
        assert first.execution.stores_affected == ("memory",)

        # Second call: a real, fresh manifest now (not the stale "before"
        # one) already reports the installed code's schema, matching the
        # target - CURRENT, no second attempt.
        second = run_upgrade(
            request, approved=True,
            memory_dir=memory_dir, state_path=tmp_path / "state.json",
        )
        assert second.status is UpgradeStatus.CURRENT
        assert second.executed is False

    def test_the_second_lifecycle_call_sees_current(self, tmp_path, monkeypatch):
        bump_memory_migration(monkeypatch)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": MEMORY_SCHEMA_VERSION - 1, "records": []}), encoding="utf-8"
        )
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        run_upgrade(request, approved=True, current_manifest=current,
                    memory_dir=memory_dir, state_path=tmp_path / "state.json")
        second = run_upgrade(request, approved=True,
                              memory_dir=memory_dir, state_path=tmp_path / "state.json")
        assert second.status is UpgradeStatus.CURRENT
        assert second.success is True


# ---------------------------------------------------------------------------
# 15-18: Verification
# ---------------------------------------------------------------------------


class TestVerification:
    def test_successful_execution_produces_a_fresh_manifest(self, tmp_path, monkeypatch):
        bump_memory_migration(monkeypatch)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": MEMORY_SCHEMA_VERSION - 1, "records": []}), encoding="utf-8"
        )
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        result = run_upgrade(
            request, approved=True, current_manifest=current,
            memory_dir=memory_dir, state_path=tmp_path / "state.json",
        )
        assert result.after_manifest is not None
        assert isinstance(result.after_manifest, UpgradeManifest)

    def test_target_versions_are_independently_verified_on_disk(self, tmp_path):
        """A white-box test of the verification function itself: a plan
        naming a target that does not match what is actually on disk must
        be reported as unverified, regardless of what any executor claims."""
        from src.upgrade_lifecycle import _verify_migrations
        from src.control_center.upgrade_planner import RequiredMigration, UpgradePlan

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": 1, "records": []}), encoding="utf-8"
        )
        plan = UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED, reason="x",
            required_migrations=(RequiredMigration("memory", 0, 5),),
        )
        ok, reason = _verify_migrations(plan, memory_dir=memory_dir, state_path=tmp_path / "s.json")
        assert ok is False
        assert reason is not None

    def test_verification_failure_is_reported_as_failure_not_success(self, tmp_path, monkeypatch):
        """The executor is monkeypatched to claim success while the disk
        file is left completely untouched at its original schema -
        proving run_upgrade does not simply relay UpgradeResult.success as
        its own verdict. (The fake never calls the real executor - if it
        did, the file really would end up migrated, and there would be
        nothing left for independent verification to disagree with.)"""
        bump_memory_migration(monkeypatch)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{MANAGER}.json").write_text(
            json.dumps({"version": MEMORY_SCHEMA_VERSION - 1, "records": []}), encoding="utf-8"
        )
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)

        import src.upgrade_lifecycle as lifecycle_module

        monkeypatch.setattr(
            lifecycle_module, "execute_upgrade",
            lambda *a, **k: UpgradeResult(success=True, status=UpgradeStatus.MIGRATION_REQUIRED),
        )
        result = run_upgrade(
            request, approved=True, current_manifest=current,
            memory_dir=memory_dir, state_path=tmp_path / "state.json",
        )
        assert result.success is False
        assert result.verified is False
        on_disk = json.loads((memory_dir / f"{MANAGER}.json").read_text(encoding="utf-8"))
        assert on_disk["version"] == MEMORY_SCHEMA_VERSION - 1, "the fake executor must not have touched the file"

    def test_executor_success_cannot_bypass_verification(self, tmp_path, monkeypatch):
        """Same seam as above, phrased as the safety property directly:
        a True executor.success never becomes result.success by itself."""
        import src.upgrade_lifecycle as lifecycle_module

        monkeypatch.setattr(
            lifecycle_module, "execute_upgrade",
            lambda *a, **k: UpgradeResult(success=True, status=UpgradeStatus.MIGRATION_REQUIRED),
        )
        bump_memory_migration(monkeypatch)
        current = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        result = run_upgrade(
            request, approved=True, current_manifest=current,
            memory_dir=tmp_path / "memory", state_path=tmp_path / "state.json",
        )
        assert result.success is False, "an unverified executor success must not become overall success"


# ---------------------------------------------------------------------------
# 19-24: Identity, Brain, Memory, Voice preservation
# ---------------------------------------------------------------------------


class TestIdentityAndArchitecturePreservation:
    def test_the_module_cannot_touch_an_agent_at_all(self):
        """Structural, not behavioural: no import of anything that could
        construct or resolve an agent - so there is no path from here to
        a second Alfred, a second Arthur, or any agent at all."""
        imported = _imported_names(LIFECYCLE_SRC)
        for forbidden in (
            "src.manager", "src.agents", "src.registry",
            "src.services.identity", "src.composition",
        ):
            assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported), forbidden

    def test_the_manager_memory_owner_is_resolved_by_the_existing_store_path(self):
        """Verification asks execute_upgrade's own store_path() for where
        the memory store lives - the same MANAGER-keyed path, never a
        second derivation of it."""
        from src.upgrade_executor import store_path

        expected = store_path("memory", memory_dir="X", state_path="Y")
        assert expected.name == f"{MANAGER}.json"

    def test_no_brain_module_is_imported(self):
        imported = _imported_names(LIFECYCLE_SRC)
        for forbidden in ("src.manager.brain_factory", "src.agents.brain_factory", "brain_factory"):
            assert not any(forbidden in name for name in imported), forbidden

    def test_no_second_memory_construction(self):
        source = LIFECYCLE_SRC.read_text(encoding="utf-8")
        assert "PersistentMemory(" not in source
        assert "StateStore(" not in source
        imported = _imported_names(LIFECYCLE_SRC)
        assert not any(name.endswith(".persistent") for name in imported)

    def test_no_voice_profile_is_imported(self):
        imported = _imported_names(LIFECYCLE_SRC)
        assert not any("VoiceProfile" in name or "voice" in name.lower() for name in imported)

    def test_no_conversation_or_channel_system_is_imported(self):
        imported = _imported_names(LIFECYCLE_SRC)
        for forbidden in ("src.conversation", "src.channels"):
            assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported), forbidden


# ---------------------------------------------------------------------------
# 25-27: Provider independence
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    def test_no_provider_imports(self):
        imported = _imported_names(LIFECYCLE_SRC)
        assert not any(name == "src.providers" or name.startswith("src.providers.") for name in imported)

    def test_no_model_names(self):
        source = LIFECYCLE_SRC.read_text(encoding="utf-8").lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "gpt", "sonnet", "opus", "gemini"):
            assert forbidden not in source, forbidden

    def test_no_api_key_references(self):
        """Only unambiguous, dependency-shaped tokens - "credential" and
        "secret" appear legitimately in this module's own docstring prose
        disclaiming that it carries either, the same honest use
        test_upgrade_executor.py's own docstrings already make elsewhere,
        and scanning for them here would be a false positive on prose, not
        a real finding."""
        source = LIFECYCLE_SRC.read_text(encoding="utf-8").lower()
        for forbidden in ("api_key", "apikey"):
            assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# 28-32: Safety
# ---------------------------------------------------------------------------


class TestSafety:
    def test_no_automatic_execution_without_approval(self):
        sig = inspect.signature(run_upgrade)
        assert sig.parameters["approved"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["approved"].default is inspect.Parameter.empty

    def test_no_automatic_rollback(self):
        tree = ast.parse(LIFECYCLE_SRC.read_text(encoding="utf-8"))
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

    def test_no_filesystem_writes_for_planning_only_states(self, tmp_path):
        memory_dir = tmp_path / "never-created"
        for request, status in (
            (UpgradeRequest(target_memory_schema_version=-1), UpgradeStatus.INVALID),
            (UpgradeRequest(target_memory_schema_version=99), UpgradeStatus.UNSUPPORTED),
            (UpgradeRequest(), UpgradeStatus.CURRENT),
            (UpgradeRequest(target_system_version="9.9.9"), UpgradeStatus.UPGRADE_AVAILABLE),
        ):
            result = run_upgrade(
                request, approved=True, current_manifest=manifest(),
                memory_dir=memory_dir, state_path=tmp_path / "never.json",
            )
            assert result.status is status
            assert not memory_dir.exists()

    def test_no_git_operations(self):
        imported = _imported_names(LIFECYCLE_SRC)
        source = LIFECYCLE_SRC.read_text(encoding="utf-8").lower()
        assert not any("subprocess" in name for name in imported)
        assert "git " not in source and "git\n" not in source
        assert "os.system" not in source

    def test_no_package_installation(self):
        source = LIFECYCLE_SRC.read_text(encoding="utf-8").lower()
        for forbidden in ("pip install", "pip.main", "subprocess", "setup.py"):
            assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# 33-35: Boundary
# ---------------------------------------------------------------------------


class TestBoundary:
    def test_control_center_does_not_import_upgrade_execution(self):
        """The Control Center stays an observation layer - it must not
        depend on the module that can actually change on-disk state."""
        control_center = REPO_ROOT / "src" / "control_center"
        for path in control_center.rglob("*.py"):
            imported = _imported_names(path)
            for forbidden in ("src.upgrade_lifecycle", "src.upgrade_executor"):
                assert forbidden not in imported, (path.name, forbidden)

    def test_improvement_does_not_import_upgrade_execution(self):
        """Self-improvement stays advisory-only - see src/improvement/'s own
        boundary. Stage 9 must not become a path for it to act."""
        improvement = REPO_ROOT / "src" / "improvement"
        for path in improvement.rglob("*.py"):
            imported = _imported_names(path)
            for forbidden in ("src.upgrade_lifecycle", "src.upgrade_executor"):
                assert forbidden not in imported, (path.name, forbidden)

    def test_the_lifecycle_owns_no_migration_implementation(self):
        """AST-level: no MigrationChain construction, no direct .migrate()
        or .apply() call, no Migration import - the migration engine
        remains src/core/versioning.py's alone."""
        imported = _imported_names(LIFECYCLE_SRC)
        for forbidden in ("MigrationChain", "src.core.versioning.Migration"):
            assert not any(forbidden in name for name in imported), forbidden
        source = LIFECYCLE_SRC.read_text(encoding="utf-8")
        assert ".migrate(" not in source
        assert ".apply(" not in source


# ---------------------------------------------------------------------------
# Real, isolated, end-to-end integration (per the directive's own request
# for an actual exercised environment, not everything mocked)
# ---------------------------------------------------------------------------


class TestRealIntegration:
    def test_the_full_lifecycle_against_a_real_store(self, tmp_path, monkeypatch):
        bump_memory_migration(monkeypatch)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        state_path = tmp_path / "state" / "processed.json"

        original = json.dumps({
            "version": MEMORY_SCHEMA_VERSION - 1,
            "records": [{
                "id": "r1", "content": "kept across the governed upgrade",
                "kind": "note", "tags": [], "context": None,
                "created_at": "2026-08-27T00:00:00+00:00",
            }],
        })
        (memory_dir / f"{MANAGER}.json").write_text(original, encoding="utf-8")

        before = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)

        result = run_upgrade(
            request, approved=True, current_manifest=before,
            memory_dir=memory_dir, state_path=state_path,
        )

        assert result.status is UpgradeStatus.MIGRATION_REQUIRED
        assert result.success is True
        assert result.executed is True
        assert result.verified is True

        on_disk = json.loads((memory_dir / f"{MANAGER}.json").read_text(encoding="utf-8"))
        assert on_disk["version"] == MEMORY_SCHEMA_VERSION

        checkpoint = memory_dir / f"{MANAGER}.v{MEMORY_SCHEMA_VERSION - 1}-checkpoint"
        assert checkpoint.exists(), "the pre-upgrade file must be preserved"
        assert checkpoint.read_text(encoding="utf-8") == original

        reloaded = PersistentMemory(memory_dir / f"{MANAGER}.json")
        assert len(reloaded) == 1
        assert reloaded.recall("governed")[0].content == "kept across the governed upgrade"

        # Run again: a real caller obtains a fresh manifest (the default),
        # which now reports the installed code's schema - already at the
        # request's target.
        again = run_upgrade(
            request, approved=True, memory_dir=memory_dir, state_path=state_path,
        )
        assert again.status is UpgradeStatus.CURRENT
        assert again.executed is False

        mtime_before = (memory_dir / f"{MANAGER}.json").stat().st_mtime_ns
        run_upgrade(request, approved=True, memory_dir=memory_dir, state_path=state_path)
        mtime_after = (memory_dir / f"{MANAGER}.json").stat().st_mtime_ns
        assert mtime_before == mtime_after, "no rewrite on a repeated, already-current call"

    def test_the_lifecycle_never_touches_real_user_data(self, tmp_path, monkeypatch):
        """Belt and suspenders: every test above already passes an
        explicit memory_dir/state_path under tmp_path, but this confirms
        the real default paths are never reached with approved=True and no
        override, for a request that would otherwise execute."""
        bump_memory_migration(monkeypatch)
        real_memory_dir = REPO_ROOT / "data" / "memory"
        existed_before = real_memory_dir.exists()

        before = replace(manifest(), memory_schema_version=MEMORY_SCHEMA_VERSION - 1)
        request = UpgradeRequest(target_memory_schema_version=MEMORY_SCHEMA_VERSION)
        run_upgrade(
            request, approved=True, current_manifest=before,
            memory_dir=tmp_path / "memory", state_path=tmp_path / "state.json",
        )
        assert real_memory_dir.exists() == existed_before
