"""Stage 11.A - the extension/capability update system.

Proves the governed lifecycle src/extensions.py implements:

    DISCOVER -> VALIDATE -> PLAN -> APPROVE -> APPLY -> VERIFY -> ACTIVATE

and the boundary the whole stage exists to guarantee: this ledger can
never touch a Brain, a Memory, a VoiceProfile, or any other agent -
structurally, because it never imports one, not merely by convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core.versioning import SYSTEM_VERSION
from src.extensions import (
    ActiveExtension,
    Extension,
    ExtensionActivationResult,
    ExtensionLedger,
    ExtensionPlan,
    ExtensionRegistry,
    ExtensionStatus,
    apply_extension,
    deactivate_extension,
    plan_extension_activation,
    run_extension_lifecycle,
    validate_extension,
    verify_extension_activation,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_extension(**overrides) -> Extension:
    fields = dict(
        key="research_agent",
        name="Research Specialist",
        kind="specialist_agent",
        version="1.0.0",
        provides=("research",),
    )
    fields.update(overrides)
    return Extension(**fields)


# ---------------------------------------------------------------------------
# Representation and validation
# ---------------------------------------------------------------------------


class TestAValidExtensionCanBeRepresentedAndValidated:
    def test_a_well_formed_extension_validates(self):
        result = validate_extension(make_extension())
        assert result.valid is True

    def test_provides_and_requires_are_normalised_to_tuples(self):
        ext = make_extension(provides=["a", "b"], requires=["c"])
        assert ext.provides == ("a", "b")
        assert ext.requires == ("c",)

    def test_metadata_is_carried_opaquely(self):
        ext = make_extension(metadata={"module": "src.providers.future_provider"})
        assert ext.metadata == {"module": "src.providers.future_provider"}

    def test_to_dict_is_json_safe(self):
        d = make_extension().to_dict()
        assert d["key"] == "research_agent"
        assert isinstance(d["provides"], list)

    @pytest.mark.parametrize(
        "overrides,expected_fragment",
        [
            ({"key": "   "}, "key"),
            ({"name": ""}, "name"),
            ({"kind": " "}, "kind"),
            ({"version": ""}, "version"),
            ({"min_system_version": "  "}, "min_system_version"),
        ],
    )
    def test_malformed_declarations_are_rejected(self, overrides, expected_fragment):
        result = validate_extension(make_extension(**overrides))
        assert result.valid is False
        assert expected_fragment in result.reason

    def test_an_extension_cannot_require_itself(self):
        result = validate_extension(make_extension(requires=("research_agent",)))
        assert result.valid is False


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------


class TestIncompatibleExtensionsAreRejected:
    def test_below_minimum_system_version_is_incompatible(self):
        ext = make_extension(min_system_version="99.0.0")
        plan = plan_extension_activation(ext, ledger=ExtensionLedger())
        assert plan.status is ExtensionStatus.INCOMPATIBLE

    def test_above_maximum_system_version_is_incompatible(self):
        ext = make_extension(max_system_version="0.0.1")
        plan = plan_extension_activation(ext, ledger=ExtensionLedger())
        assert plan.status is ExtensionStatus.INCOMPATIBLE

    def test_within_bounds_is_not_incompatible(self):
        ext = make_extension(min_system_version="0.0.1", max_system_version="99.0.0")
        plan = plan_extension_activation(ext, ledger=ExtensionLedger())
        assert plan.status is not ExtensionStatus.INCOMPATIBLE

    def test_no_bounds_means_compatible_with_anything(self):
        ext = make_extension()
        plan = plan_extension_activation(ext, ledger=ExtensionLedger(), system_version="123.456.789")
        assert plan.status is ExtensionStatus.READY

    def test_an_incompatible_extension_can_never_be_applied_even_if_approved(self):
        ext = make_extension(min_system_version="99.0.0")
        plan = plan_extension_activation(ext, ledger=ExtensionLedger())
        result = apply_extension(plan, approved=True, ledger=ExtensionLedger())
        assert result.success is False
        assert result.ledger.active == ()

    def test_the_currently_installed_system_version_is_used_by_default(self):
        """Reuses src.core.versioning.SYSTEM_VERSION - never a second,
        invented version concept for extensions."""
        ext = make_extension(min_system_version=SYSTEM_VERSION)
        plan = plan_extension_activation(ext, ledger=ExtensionLedger())
        assert plan.status is ExtensionStatus.READY


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class TestDependenciesAreEnforced:
    def test_a_missing_dependency_blocks_activation(self):
        ext = make_extension(requires=("some_other_extension",))
        plan = plan_extension_activation(ext, ledger=ExtensionLedger())
        assert plan.status is ExtensionStatus.MISSING_DEPENDENCY

    def test_a_satisfied_dependency_allows_activation(self):
        prerequisite = ActiveExtension(key="base_capability", version="1.0.0", kind="capability")
        ledger = ExtensionLedger(active=(prerequisite,))
        ext = make_extension(requires=("base_capability",))
        plan = plan_extension_activation(ext, ledger=ledger)
        assert plan.status is ExtensionStatus.READY

    def test_a_missing_dependency_can_never_be_applied_even_if_approved(self):
        ext = make_extension(requires=("missing",))
        plan = plan_extension_activation(ext, ledger=ExtensionLedger())
        result = apply_extension(plan, approved=True, ledger=ExtensionLedger())
        assert result.success is False


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


class TestVersionsAreHandledCorrectly:
    def test_same_key_same_version_is_already_active(self):
        active = ActiveExtension(key="research_agent", version="1.0.0", kind="specialist_agent")
        ledger = ExtensionLedger(active=(active,))
        plan = plan_extension_activation(make_extension(version="1.0.0"), ledger=ledger)
        assert plan.status is ExtensionStatus.ALREADY_ACTIVE

    def test_same_key_different_version_is_a_replacement(self):
        active = ActiveExtension(key="research_agent", version="1.0.0", kind="specialist_agent")
        ledger = ExtensionLedger(active=(active,))
        plan = plan_extension_activation(make_extension(version="2.0.0"), ledger=ledger)
        assert plan.status is ExtensionStatus.REPLACEMENT_READY

    def test_version_comparison_is_numeric_not_lexical(self):
        """The classic bug: '0.10.0' must be treated as greater than
        '0.9.0', which a plain string comparison would get backwards."""
        ext = make_extension(min_system_version="0.9.0")
        plan = plan_extension_activation(ext, ledger=ExtensionLedger(), system_version="0.10.0")
        assert plan.status is ExtensionStatus.READY

    def test_a_replacement_upserts_rather_than_duplicates_the_ledger_entry(self):
        active = ActiveExtension(key="research_agent", version="1.0.0", kind="specialist_agent")
        ledger = ExtensionLedger(active=(active,))
        plan = plan_extension_activation(make_extension(version="2.0.0"), ledger=ledger)
        result = apply_extension(plan, approved=True, ledger=ledger)
        assert len(result.ledger.active) == 1
        assert result.ledger.get("research_agent").version == "2.0.0"


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class TestApprovalIsRequiredBeforeApplication:
    def test_apply_refuses_a_ready_plan_without_approval(self):
        plan = plan_extension_activation(make_extension(), ledger=ExtensionLedger())
        result = apply_extension(plan, approved=False, ledger=ExtensionLedger())
        assert result.success is False
        assert result.failure_reason == "not approved"
        assert result.ledger.active == ()

    def test_apply_has_no_default_for_approved(self):
        import inspect

        params = inspect.signature(apply_extension).parameters
        assert params["approved"].default is inspect.Parameter.empty

    def test_run_extension_lifecycle_has_no_default_for_approved(self):
        import inspect

        params = inspect.signature(run_extension_lifecycle).parameters
        assert params["approved"].default is inspect.Parameter.empty

    def test_the_full_lifecycle_never_activates_without_approval(self):
        result = run_extension_lifecycle(make_extension(), approved=False, ledger=ExtensionLedger())
        assert result.activated is False
        assert result.after_ledger.active == ()

    def test_approval_alone_does_not_bypass_compatibility(self):
        ext = make_extension(min_system_version="99.0.0")
        result = run_extension_lifecycle(ext, approved=True, ledger=ExtensionLedger())
        assert result.activated is False
        assert result.status is ExtensionStatus.INCOMPATIBLE


# ---------------------------------------------------------------------------
# Activation only after successful verification
# ---------------------------------------------------------------------------


class TestActivationOccursOnlyAfterSuccessfulVerification:
    def test_a_normal_successful_activation_is_verified(self):
        result = run_extension_lifecycle(make_extension(), approved=True, ledger=ExtensionLedger())
        assert result.verified is True
        assert result.activated is True

    def test_verification_never_ran_means_activated_is_false(self):
        result = run_extension_lifecycle(make_extension(), approved=False, ledger=ExtensionLedger())
        assert result.verified is None
        assert result.activated is False

    def test_if_apply_reports_success_but_the_ledger_disagrees_activation_is_refused(self, monkeypatch):
        """The exact defensive claim upgrade_lifecycle.py already makes for
        migrations: 'the executor returned success' and 'the system is
        correct' are different claims. Simulated by making apply_extension
        report success while returning a ledger that does not actually
        contain the extension - verify_extension_activation must catch it
        independently, not just relay apply's own report."""
        import src.extensions as extensions_module

        def fake_apply(plan, *, approved, ledger):
            from src.extensions import ExtensionApplyResult

            return ExtensionApplyResult(True, None, ledger)  # unchanged ledger, false success

        monkeypatch.setattr(extensions_module, "apply_extension", fake_apply)
        result = extensions_module.run_extension_lifecycle(
            make_extension(), approved=True, ledger=ExtensionLedger()
        )
        assert result.verified is False
        assert result.activated is False
        assert result.success is False

    def test_verify_extension_activation_checks_the_version_too(self):
        wrong_version = ExtensionLedger(
            active=(ActiveExtension(key="research_agent", version="9.9.9", kind="specialist_agent"),)
        )
        verified, reason = verify_extension_activation(make_extension(version="1.0.0"), wrong_version)
        assert verified is False
        assert "9.9.9" in reason


# ---------------------------------------------------------------------------
# Removal / replacement safety
# ---------------------------------------------------------------------------


class TestRemovalAndReplacementDoNotTouchBrainOrMemory:
    def test_extensions_module_imports_nothing_agent_related(self):
        source = (REPO_ROOT / "src" / "extensions.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = ("src.manager", "src.services.memory", "src.services.identity",
                     "src.channels", "src.providers", "src.registry")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for prefix in forbidden:
                    assert not node.module.startswith(prefix), node.module

    def test_no_brain_or_memory_class_is_ever_constructed_here(self):
        source = (REPO_ROOT / "src" / "extensions.py").read_text(encoding="utf-8")
        for name in ("Memory(", "Brain(", "PersistentMemory(", "SpecialistBrain(", "LLMManagerBrain("):
            assert name not in source

    def test_deactivating_an_extension_leaves_other_active_entries_untouched(self):
        ledger = ExtensionLedger(
            active=(
                ActiveExtension(key="a", version="1.0.0", kind="capability"),
                ActiveExtension(key="b", version="1.0.0", kind="capability"),
            )
        )
        after = deactivate_extension("a", ledger=ledger)
        assert after.get("a") is None
        assert after.get("b") is not None

    def test_a_real_managers_brain_and_memory_are_unaffected_by_extension_activity(self):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        brain_before = manager.brain
        memory_before = manager.memory
        arthur_before = manager.registry.get("Arthur")
        arthur_brain_before = arthur_before.brain
        arthur_memory_before = arthur_before.memory

        ledger = ExtensionLedger()
        result = run_extension_lifecycle(make_extension(), approved=True, ledger=ledger)
        deactivate_extension("research_agent", ledger=result.after_ledger)

        assert manager.brain is brain_before
        assert manager.memory is memory_before
        arthur_after = manager.registry.get("Arthur")
        assert arthur_after is arthur_before
        assert arthur_after.brain is arthur_brain_before
        assert arthur_after.memory is arthur_memory_before


# ---------------------------------------------------------------------------
# Existing Alfred/Arthur behavior is unchanged
# ---------------------------------------------------------------------------


class TestExistingAlfredAndArthurBehaviorIsUnchanged:
    def test_alfred_still_answers_status_after_extension_lifecycle_calls(self):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.core.contracts import Task
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        before = manager.handle_task(Task(description="status"))

        run_extension_lifecycle(make_extension(), approved=True, ledger=ExtensionLedger())
        run_extension_lifecycle(
            make_extension(kind="provider", key="other"), approved=False, ledger=ExtensionLedger()
        )

        after = manager.handle_task(Task(description="status"))
        assert before.succeeded == after.succeeded
        assert before.agent_name == after.agent_name == "Alfred"

    def test_arthur_still_handles_email_after_extension_lifecycle_calls(self):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.core.contracts import Task
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        run_extension_lifecycle(make_extension(), approved=True, ledger=ExtensionLedger())

        result = manager.handle_task(Task(description="check my email"))
        assert result.agent_name == "Arthur"


# ---------------------------------------------------------------------------
# A future extension, introduced without modifying the core
# ---------------------------------------------------------------------------


class TestAFutureExtensionNeedsNoCoreChange:
    @pytest.mark.parametrize(
        "kind",
        ["capability", "specialist_agent", "provider", "interface", "environment",
         "connection", "multimodal_feature", "a_kind_nobody_has_invented_yet"],
    )
    def test_every_kind_goes_through_the_identical_governed_path(self, kind):
        """No branch in the core lifecycle functions is keyed on `kind` -
        proven by literally exercising one nobody has invented yet."""
        ext = make_extension(key=f"future_{kind}", kind=kind)
        result = run_extension_lifecycle(ext, approved=True, ledger=ExtensionLedger())
        assert result.activated is True
        assert result.after_ledger.get(f"future_{kind}").kind == kind

    def test_no_core_lifecycle_function_branches_on_kind(self):
        source = (REPO_ROOT / "src" / "extensions.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        core_functions = {
            "validate_extension", "plan_extension_activation", "apply_extension",
            "verify_extension_activation", "run_extension_lifecycle",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in core_functions:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Compare):
                        for comparator in inner.comparators:
                            if isinstance(comparator, ast.Attribute) and comparator.attr == "kind":
                                pytest.fail(f"{node.name} branches on .kind")
                            left = inner.left
                            if isinstance(left, ast.Attribute) and left.attr == "kind":
                                pytest.fail(f"{node.name} branches on .kind")


# ---------------------------------------------------------------------------
# Existing Upgrade Manifest / Planner / Executor architecture remains intact
# ---------------------------------------------------------------------------


class TestExistingUpgradeArchitectureRemainsIntact:
    def test_extensions_module_does_not_import_the_upgrade_system(self):
        """Two different questions - can persisted data move to a new
        schema, and can a new feature be marked active - deliberately kept
        as two small, separate modules rather than one shoehorned into the
        other. Checked as an actual import, not a docstring mention: this
        module's own prose names ``upgrade_planner._malformed`` by way of
        comparison, which is not coupling."""
        source = (REPO_ROOT / "src" / "extensions.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = ("upgrade_planner", "upgrade_executor", "upgrade_lifecycle", "control_center.manifest")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                names = ", ".join(getattr(n, "name", "") for n in node.names)
                for forbidden_name in forbidden:
                    assert forbidden_name not in module and forbidden_name not in names

    def test_the_upgrade_system_does_not_import_extensions(self):
        for relative in (
            "src/control_center/manifest.py",
            "src/control_center/upgrade_planner.py",
            "src/upgrade_executor.py",
            "src/upgrade_lifecycle.py",
        ):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    names = ", ".join(getattr(n, "name", "") for n in node.names)
                    assert "extensions" not in module and "extensions" not in names

    def test_upgrade_manifest_still_reports_the_same_shape(self):
        """A smoke check, not a duplicate of test_upgrade_manifest.py's own
        suite: this stage did not touch that module."""
        from src.control_center.manifest import upgrade_manifest

        manifest = upgrade_manifest(snapshot_version=1)
        assert manifest.system_version == SYSTEM_VERSION

    def test_upgrade_planner_and_executor_still_import_cleanly(self):
        import src.control_center.upgrade_planner  # noqa: F401
        import src.upgrade_executor  # noqa: F401
        import src.upgrade_lifecycle  # noqa: F401
