"""Stage 7 — the Upgrade Planner: is a proposed target reachable from here?

Not the migration engine (src/core/versioning.py, untouched by this
milestone beyond the new, read-only MigrationChain.path()), not an
executor - a deterministic answer, and nothing written to disk to produce
it.

Every real MEMORY_MIGRATIONS/STATE_MIGRATIONS chain in this codebase is
still empty (schema 1 has never had a predecessor), so most tests here
register a synthetic chain via monkeypatch - the same technique
test_persistent_memory.py::TestGovernedMigration and
test_heartbeat_state.py::TestGovernedMigration already use for exactly
the same reason.
"""

from __future__ import annotations

import inspect

import pytest

import src.control_center.upgrade_planner as planner_module
from src.control_center.manifest import UpgradeManifest, VisualEnvironmentVersion
from src.control_center.upgrade_planner import (
    RequiredMigration,
    UpgradePlan,
    UpgradeRequest,
    UpgradeStatus,
    plan_upgrade,
)
from src.core.versioning import Migration, MigrationChain


def manifest(**overrides) -> UpgradeManifest:
    defaults = dict(
        system_version="0.1.0",
        snapshot_version=1,
        memory_schema_version=1,
        state_schema_version=1,
        visual_environments=(
            VisualEnvironmentVersion("agent:core", "alfred-tactical-core", 1),
            VisualEnvironmentVersion("brain", "brain-node-lattice", 1),
        ),
    )
    defaults.update(overrides)
    return UpgradeManifest(**defaults)


def with_memory_migration(monkeypatch, *migrations):
    monkeypatch.setattr(planner_module, "MEMORY_MIGRATIONS", MigrationChain(migrations))


def with_state_migration(monkeypatch, *migrations):
    monkeypatch.setattr(planner_module, "STATE_MIGRATIONS", MigrationChain(migrations))


# ---------------------------------------------------------------------------
# 1. Current version produces a valid current plan
# ---------------------------------------------------------------------------


class TestCurrentPlan:
    def test_an_empty_request_resolves_to_current(self):
        plan = plan_upgrade(manifest(), UpgradeRequest())
        assert plan.status is UpgradeStatus.CURRENT
        assert plan.required_migrations == ()

    def test_a_request_matching_every_installed_value_resolves_to_current(self):
        current = manifest()
        plan = plan_upgrade(
            current,
            UpgradeRequest(
                target_system_version=current.system_version,
                target_memory_schema_version=current.memory_schema_version,
                target_state_schema_version=current.state_schema_version,
                target_visual_environment_versions={"brain": 1},
            ),
        )
        assert plan.status is UpgradeStatus.CURRENT


# ---------------------------------------------------------------------------
# 2. A supported migration path produces a supported (MIGRATION_REQUIRED) plan
# ---------------------------------------------------------------------------


class TestSupportedMigrationPath:
    def test_a_single_known_step_is_migration_required(self, monkeypatch):
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        plan = plan_upgrade(manifest(), UpgradeRequest(target_memory_schema_version=2))
        assert plan.status is UpgradeStatus.MIGRATION_REQUIRED
        assert plan.required_migrations == (RequiredMigration("memory", 1, 2),)

    def test_a_multi_step_known_path_names_every_step_in_order(self, monkeypatch):
        with_memory_migration(
            monkeypatch,
            Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2}),
            Migration(from_version=2, to_version=3, apply=lambda d: {**d, "version": 3}),
        )
        plan = plan_upgrade(manifest(), UpgradeRequest(target_memory_schema_version=3))
        assert plan.status is UpgradeStatus.MIGRATION_REQUIRED
        assert plan.required_migrations == (
            RequiredMigration("memory", 1, 2),
            RequiredMigration("memory", 2, 3),
        )

    def test_both_stores_can_require_migration_in_one_plan(self, monkeypatch):
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        with_state_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        plan = plan_upgrade(
            manifest(),
            UpgradeRequest(target_memory_schema_version=2, target_state_schema_version=2),
        )
        assert plan.status is UpgradeStatus.MIGRATION_REQUIRED
        assert set(plan.required_migrations) == {
            RequiredMigration("memory", 1, 2),
            RequiredMigration("state", 1, 2),
        }

    def test_the_planner_never_calls_apply(self, monkeypatch):
        """The migration is named, not run - proved by a migration whose
        apply() would raise, and confirming the plan still succeeds."""

        def exploding(_raw):
            raise AssertionError("apply() must never be called by the planner")

        with_memory_migration(monkeypatch, Migration(from_version=1, to_version=2, apply=exploding))
        plan = plan_upgrade(manifest(), UpgradeRequest(target_memory_schema_version=2))
        assert plan.status is UpgradeStatus.MIGRATION_REQUIRED


# ---------------------------------------------------------------------------
# 3 / 4 / 6. Missing steps and unknown versions fail closed
# ---------------------------------------------------------------------------


class TestFailsClosed:
    def test_a_partial_path_is_unsupported_not_attempted(self, monkeypatch):
        """The directive's own worked example: 1->2 known, 2->3 missing,
        target 3 requested - must be UNSUPPORTED, never "try anyway"."""
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        plan = plan_upgrade(manifest(), UpgradeRequest(target_memory_schema_version=3))
        assert plan.status is UpgradeStatus.UNSUPPORTED
        assert plan.required_migrations == ()

    def test_no_migration_registered_at_all_is_unsupported(self):
        """The real, current state of every store in this codebase today -
        schema 1 has never had a predecessor, so a target of anything else
        must fail closed."""
        plan = plan_upgrade(manifest(), UpgradeRequest(target_memory_schema_version=2))
        assert plan.status is UpgradeStatus.UNSUPPORTED

    def test_a_downgrade_request_is_unsupported(self, monkeypatch):
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        plan = plan_upgrade(
            manifest(memory_schema_version=2), UpgradeRequest(target_memory_schema_version=1)
        )
        assert plan.status is UpgradeStatus.UNSUPPORTED

    def test_an_unsupported_plan_names_which_store_and_which_versions(self, monkeypatch):
        plan = plan_upgrade(manifest(), UpgradeRequest(target_state_schema_version=2))
        assert "state" in plan.reason
        assert "1" in plan.reason and "2" in plan.reason

    def test_blocked_takes_priority_over_a_migration_required_elsewhere(self, monkeypatch):
        """One unsupported dimension must sink the whole plan - never a
        partial success that quietly runs some migrations and skips the
        risky one."""
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        # state has no migration registered at all
        plan = plan_upgrade(
            manifest(),
            UpgradeRequest(target_memory_schema_version=2, target_state_schema_version=2),
        )
        assert plan.status is UpgradeStatus.UNSUPPORTED


# ---------------------------------------------------------------------------
# Malformed requests
# ---------------------------------------------------------------------------


class TestInvalidRequests:
    def test_a_blank_target_system_version_is_invalid(self):
        plan = plan_upgrade(manifest(), UpgradeRequest(target_system_version="   "))
        assert plan.status is UpgradeStatus.INVALID

    def test_a_non_positive_schema_target_is_invalid(self):
        for bad in (0, -1, -100):
            plan = plan_upgrade(manifest(), UpgradeRequest(target_memory_schema_version=bad))
            assert plan.status is UpgradeStatus.INVALID, bad

    def test_an_unknown_visual_surface_is_invalid(self):
        plan = plan_upgrade(
            manifest(), UpgradeRequest(target_visual_environment_versions={"does-not-exist": 2})
        )
        assert plan.status is UpgradeStatus.INVALID

    def test_invalid_is_checked_before_anything_else(self, monkeypatch):
        """A malformed request is refused even when a migration path would
        otherwise exist - the request could not be asked, so nothing else
        about it is evaluated."""
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        plan = plan_upgrade(
            manifest(),
            UpgradeRequest(target_memory_schema_version=2, target_state_schema_version=-1),
        )
        assert plan.status is UpgradeStatus.INVALID


# ---------------------------------------------------------------------------
# Visual environments and system version - no migration concept, never blocking
# ---------------------------------------------------------------------------


class TestNoMigrationConceptFieldsNeverBlock:
    def test_a_system_version_only_difference_is_upgrade_available(self):
        plan = plan_upgrade(manifest(), UpgradeRequest(target_system_version="0.2.0"))
        assert plan.status is UpgradeStatus.UPGRADE_AVAILABLE
        assert plan.required_migrations == ()

    def test_a_visual_environment_only_difference_is_upgrade_available(self):
        plan = plan_upgrade(
            manifest(), UpgradeRequest(target_visual_environment_versions={"brain": 2})
        )
        assert plan.status is UpgradeStatus.UPGRADE_AVAILABLE

    def test_a_visual_environment_matching_current_is_current(self):
        plan = plan_upgrade(
            manifest(), UpgradeRequest(target_visual_environment_versions={"brain": 1})
        )
        assert plan.status is UpgradeStatus.CURRENT

    def test_combined_with_a_real_migration_the_stronger_status_wins(self, monkeypatch):
        """UPGRADE_AVAILABLE fields tag along under MIGRATION_REQUIRED
        rather than the other way around - never let the trivial part of
        a request hide that real data would move."""
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        plan = plan_upgrade(
            manifest(),
            UpgradeRequest(target_system_version="0.2.0", target_memory_schema_version=2),
        )
        assert plan.status is UpgradeStatus.MIGRATION_REQUIRED


# ---------------------------------------------------------------------------
# 5. Zero filesystem writes
# ---------------------------------------------------------------------------


class TestNoFilesystemWrites:
    def test_planning_creates_no_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        plan_upgrade(manifest(), UpgradeRequest(target_memory_schema_version=2))
        assert list(tmp_path.iterdir()) == []

    def test_the_module_opens_no_file_and_writes_nothing(self):
        """Structural: no os/Path write call anywhere in the module's own
        source - not merely "none happened to run" in one test."""
        source = inspect.getsource(planner_module)
        for forbidden in ("write_text(", "os.replace(", ".replace(", "open(", "mkdir("):
            assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# 6. Deterministic
# ---------------------------------------------------------------------------


class TestDeterministic:
    def test_two_calls_with_the_same_inputs_produce_an_equal_plan(self, monkeypatch):
        with_memory_migration(
            monkeypatch, Migration(from_version=1, to_version=2, apply=lambda d: {**d, "version": 2})
        )
        request = UpgradeRequest(target_memory_schema_version=2)
        current = manifest()
        assert plan_upgrade(current, request) == plan_upgrade(current, request)

    def test_to_dict_is_equal_across_calls(self):
        request = UpgradeRequest(target_system_version="0.2.0")
        current = manifest()
        assert plan_upgrade(current, request).to_dict() == plan_upgrade(current, request).to_dict()


# ---------------------------------------------------------------------------
# 7 / 8. No agent display names, no LLM providers
# ---------------------------------------------------------------------------


class TestAgentAndProviderIndependence:
    def test_the_module_imports_nothing_identity_or_provider_related(self):
        import ast

        tree = ast.parse(inspect.getsource(planner_module))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)

        for forbidden in (
            "src.registry", "src.services.identity", "src.composition", "src.providers",
        ):
            assert forbidden not in imported, forbidden

    def test_no_dataclass_has_an_agent_name_or_provider_field(self):
        for cls in (UpgradeRequest, RequiredMigration, UpgradePlan):
            field_names = cls.__dataclass_fields__.keys()
            for forbidden in ("agent", "name", "identity", "owner", "provider", "model"):
                assert not any(forbidden in f for f in field_names), (cls.__name__, forbidden)

    def test_the_module_names_no_provider(self):
        source = inspect.getsource(planner_module).lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey"):
            assert forbidden not in source, forbidden

    def test_plan_upgrade_takes_no_agent_or_provider_parameter(self):
        params = inspect.signature(plan_upgrade).parameters.keys()
        for forbidden in ("agent", "provider", "identity"):
            assert not any(forbidden in p for p in params), forbidden


# ---------------------------------------------------------------------------
# 12. The Upgrade Manifest this planner consumes is unaffected
# ---------------------------------------------------------------------------


class TestManifestUntouched:
    def test_upgrade_manifest_is_reused_not_recomputed(self):
        """The planner takes an UpgradeManifest as its "current" input
        rather than re-deriving system/schema/visual-environment facts
        itself - checked by confirming it imports the manifest type
        rather than declaring a competing one."""
        assert planner_module.UpgradeManifest.__module__ == "src.control_center.manifest"

    def test_the_real_manifest_still_plans_correctly_against_itself(self):
        from src.control_center.manifest import upgrade_manifest

        real_current = upgrade_manifest(snapshot_version=1)
        plan = plan_upgrade(real_current, UpgradeRequest())
        assert plan.status is UpgradeStatus.CURRENT
