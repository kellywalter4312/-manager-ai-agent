"""Stage 11.B - governed self-improvement / evolution.

Proves the lifecycle src/evolution.py implements:

    PROPOSE -> EVALUATE -> APPROVE -> APPLY -> VERIFY -> ACTIVATE
                                                  |
                                                  v
                                          ROLL BACK (if needed)

and the boundary the whole stage exists to guarantee: "Alfred thinks this
is better" is a proposal, never an authorization - proven both by
behaviour (every gate refuses without explicit approval) and structurally
(this module never imports a Brain, a Memory, a VoiceProfile, or Stage
11.A itself).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.evolution import (
    ActivationResult,
    ApplyResult,
    ApprovalResult,
    EvaluationResult,
    EvolutionLedger,
    EvolutionLifecycleResult,
    EvolutionProposal,
    EvolutionStatus,
    RiskLevel,
    RollbackResult,
    activate_proposal,
    apply_proposal,
    approve_proposal,
    evaluate_proposal,
    rollback_proposal,
    run_evolution_lifecycle,
    validate_proposal,
    verify_proposal,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_proposal(**overrides) -> EvolutionProposal:
    fields = dict(
        proposal_id="evo-001",
        target="brain.reasoning_prompt",
        current_version="1.0",
        proposed_version="1.1",
        proposed_change="Add a clarifying-question step before answering an ambiguous request.",
        reason="Users repeat themselves when Alfred guesses wrong",
        expected_benefit="fewer repeated requests",
    )
    fields.update(overrides)
    return EvolutionProposal(**fields)


def evaluated_ledger_for(proposal: EvolutionProposal) -> EvolutionLedger:
    return EvolutionLedger(current_versions={proposal.target: proposal.current_version})


# ---------------------------------------------------------------------------
# Representation and validation
# ---------------------------------------------------------------------------


class TestValidProposalRepresentation:
    def test_a_well_formed_proposal_carries_every_required_field(self):
        p = make_proposal(risk_level=RiskLevel.HIGH, dependencies=("evo-000",))
        assert p.proposal_id == "evo-001"
        assert p.target == "brain.reasoning_prompt"
        assert p.current_version == "1.0"
        assert p.proposed_version == "1.1"
        assert p.proposed_change
        assert p.reason
        assert p.expected_benefit
        assert p.risk_level is RiskLevel.HIGH
        assert p.dependencies == ("evo-000",)
        assert p.status is EvolutionStatus.PROPOSED

    def test_dependencies_are_normalised_to_a_tuple(self):
        p = make_proposal(dependencies=["a", "b"])
        assert p.dependencies == ("a", "b")

    def test_to_dict_is_json_safe(self):
        d = make_proposal().to_dict()
        assert d["status"] == "proposed"
        assert isinstance(d["dependencies"], list)

    def test_default_risk_level_is_low(self):
        assert make_proposal().risk_level is RiskLevel.LOW


class TestValidation:
    def test_a_well_formed_proposal_validates(self):
        assert validate_proposal(make_proposal()).valid is True

    @pytest.mark.parametrize(
        "overrides",
        [
            {"proposal_id": "  "},
            {"target": ""},
            {"current_version": " "},
            {"proposed_version": ""},
            {"proposed_change": ""},
            {"reason": ""},
            {"expected_benefit": ""},
        ],
    )
    def test_blank_required_fields_are_rejected(self, overrides):
        assert validate_proposal(make_proposal(**overrides)).valid is False

    def test_a_no_op_proposal_is_rejected(self):
        """current_version == proposed_version describes no change at all."""
        result = validate_proposal(make_proposal(proposed_version="1.0"))
        assert result.valid is False

    def test_a_proposal_cannot_depend_on_itself(self):
        result = validate_proposal(make_proposal(dependencies=("evo-001",)))
        assert result.valid is False


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


class TestLifecycleTransitions:
    def test_propose_to_evaluate(self):
        p = make_proposal()
        result = evaluate_proposal(p, ledger=evaluated_ledger_for(p))
        assert result.outcome is EvolutionStatus.EVALUATED
        assert result.proposal.status is EvolutionStatus.EVALUATED

    def test_evaluate_to_approve(self):
        p = evaluate_proposal(make_proposal(), ledger=evaluated_ledger_for(make_proposal())).proposal
        result = approve_proposal(p, approved=True)
        assert result.proposal.status is EvolutionStatus.APPROVED

    def test_approve_to_apply(self):
        p = make_proposal()
        p = evaluate_proposal(p, ledger=evaluated_ledger_for(p)).proposal
        p = approve_proposal(p, approved=True).proposal
        result = apply_proposal(p, approved=True, ledger=evaluated_ledger_for(p))
        assert result.proposal.status is EvolutionStatus.APPLIED

    def test_apply_to_verified_and_active(self):
        p = make_proposal()
        ledger = evaluated_ledger_for(p)
        p = evaluate_proposal(p, ledger=ledger).proposal
        p = approve_proposal(p, approved=True).proposal
        applied = apply_proposal(p, approved=True, ledger=ledger)
        verified, reason = verify_proposal(applied.proposal, ledger=applied.ledger)
        assert verified is True
        activation = activate_proposal(applied.proposal, ledger=applied.ledger)
        assert activation.proposal.status is EvolutionStatus.ACTIVE

    def test_active_to_rolled_back(self):
        p = make_proposal()
        result = run_evolution_lifecycle(p, approved=True, ledger=evaluated_ledger_for(p))
        rollback = rollback_proposal(result.proposal, approved=True, ledger=result.after_ledger)
        assert rollback.proposal.status is EvolutionStatus.ROLLED_BACK

    def test_malformed_proposal_is_rejected_at_evaluation(self):
        p = make_proposal(reason="")
        result = evaluate_proposal(p, ledger=EvolutionLedger())
        assert result.outcome is EvolutionStatus.REJECTED
        assert result.proposal.status is EvolutionStatus.REJECTED


class TestInvalidTransitions:
    def test_cannot_approve_a_proposal_that_was_never_evaluated(self):
        result = approve_proposal(make_proposal(), approved=True)
        assert result.success is False
        assert result.proposal.status is EvolutionStatus.PROPOSED  # unchanged

    def test_cannot_apply_a_proposal_that_was_never_approved(self):
        result = apply_proposal(make_proposal(), approved=True, ledger=EvolutionLedger())
        assert result.success is False

    def test_cannot_verify_a_proposal_that_was_never_applied(self):
        verified, reason = verify_proposal(make_proposal(), ledger=EvolutionLedger())
        assert verified is False
        assert "APPLIED" in reason

    def test_cannot_activate_a_proposal_that_was_never_applied(self):
        result = activate_proposal(make_proposal(), ledger=EvolutionLedger())
        assert result.success is False

    def test_cannot_roll_back_a_proposal_that_was_never_activated(self):
        result = rollback_proposal(make_proposal(), approved=True, ledger=EvolutionLedger())
        assert result.success is False

    def test_cannot_re_evaluate_an_already_evaluated_proposal(self):
        p = make_proposal()
        ledger = evaluated_ledger_for(p)
        evaluated = evaluate_proposal(p, ledger=ledger).proposal
        second = evaluate_proposal(evaluated, ledger=ledger)
        assert second.outcome is EvolutionStatus.EVALUATED  # unchanged, reported honestly
        assert "must be PROPOSED" in second.reason

    def test_cannot_apply_a_rejected_proposal_even_if_approved(self):
        rejected = evaluate_proposal(make_proposal(reason=""), ledger=EvolutionLedger()).proposal
        assert rejected.status is EvolutionStatus.REJECTED
        approval = approve_proposal(rejected, approved=True)
        assert approval.success is False


# ---------------------------------------------------------------------------
# Explicit approval enforcement
# ---------------------------------------------------------------------------


class TestExplicitApprovalEnforcement:
    def test_approve_proposal_has_no_default_for_approved(self):
        import inspect

        assert inspect.signature(approve_proposal).parameters["approved"].default is inspect.Parameter.empty

    def test_apply_proposal_has_no_default_for_approved(self):
        import inspect

        assert inspect.signature(apply_proposal).parameters["approved"].default is inspect.Parameter.empty

    def test_rollback_proposal_has_no_default_for_approved(self):
        import inspect

        assert inspect.signature(rollback_proposal).parameters["approved"].default is inspect.Parameter.empty

    def test_run_evolution_lifecycle_has_no_default_for_approved(self):
        import inspect

        assert inspect.signature(run_evolution_lifecycle).parameters["approved"].default is inspect.Parameter.empty

    def test_evaluation_finding_a_proposal_viable_is_not_approval(self):
        """The core principle, checked directly: EVALUATED is not
        APPROVED, and nothing moves a proposal between them but an
        explicit approved=True."""
        p = make_proposal()
        evaluated = evaluate_proposal(p, ledger=evaluated_ledger_for(p)).proposal
        assert evaluated.status is EvolutionStatus.EVALUATED
        assert evaluated.status is not EvolutionStatus.APPROVED

    def test_approved_false_refuses_without_changing_status(self):
        p = evaluate_proposal(make_proposal(), ledger=evaluated_ledger_for(make_proposal())).proposal
        result = approve_proposal(p, approved=False)
        assert result.success is False
        assert result.proposal.status is EvolutionStatus.EVALUATED

    def test_the_full_lifecycle_never_activates_without_approval(self):
        p = make_proposal()
        result = run_evolution_lifecycle(p, approved=False, ledger=evaluated_ledger_for(p))
        assert result.activated is False
        assert result.after_ledger.active_proposals == ()


# ---------------------------------------------------------------------------
# Proposal != execution
# ---------------------------------------------------------------------------


class TestProposalIsNotExecution:
    def test_constructing_a_proposal_changes_nothing(self):
        ledger_before = EvolutionLedger(current_versions={"brain.reasoning_prompt": "1.0"})
        make_proposal()  # constructed, never handed to any lifecycle function
        assert ledger_before.version_of("brain.reasoning_prompt") == "1.0"

    def test_a_proposal_object_has_no_apply_or_execute_method(self):
        p = make_proposal()
        assert not hasattr(p, "apply")
        assert not hasattr(p, "execute")
        assert not hasattr(p, "run")

    def test_proposed_change_is_a_plain_string_never_callable(self):
        assert isinstance(make_proposal().proposed_change, str)


# ---------------------------------------------------------------------------
# Evaluation != approval
# ---------------------------------------------------------------------------


class TestEvaluationIsNotApproval:
    def test_evaluate_proposal_takes_no_approved_argument(self):
        import inspect

        assert "approved" not in inspect.signature(evaluate_proposal).parameters

    def test_a_beneficial_looking_proposal_still_requires_approval(self):
        p = make_proposal(expected_benefit="dramatically improves everything")
        evaluated = evaluate_proposal(p, ledger=evaluated_ledger_for(p))
        assert evaluated.outcome is EvolutionStatus.EVALUATED
        apply_attempt = apply_proposal(evaluated.proposal, approved=True, ledger=EvolutionLedger())
        assert apply_attempt.success is False  # EVALUATED, not APPROVED - apply still refuses


# ---------------------------------------------------------------------------
# Apply success != verified activation
# ---------------------------------------------------------------------------


class TestApplySuccessIsNotVerifiedActivation:
    def test_apply_succeeding_does_not_itself_set_active(self):
        p = make_proposal()
        ledger = evaluated_ledger_for(p)
        p = evaluate_proposal(p, ledger=ledger).proposal
        p = approve_proposal(p, approved=True).proposal
        applied = apply_proposal(p, approved=True, ledger=ledger)
        assert applied.success is True
        assert applied.proposal.status is EvolutionStatus.APPLIED
        assert applied.proposal.status is not EvolutionStatus.ACTIVE

    def test_if_apply_reports_success_but_the_ledger_disagrees_activation_is_refused(self, monkeypatch):
        """The exact defensive claim this stage requires: 'apply
        succeeded' and 'the change is verified' are different facts.
        Simulated the same way Stage 11.A's own suite proves it - forcing
        apply to report success while handing back a ledger that does not
        actually reflect the change."""
        import src.evolution as evolution_module

        def fake_apply(proposal, *, approved, ledger):
            applied = evolution_module.replace(proposal, status=evolution_module.EvolutionStatus.APPLIED)
            return evolution_module.ApplyResult(True, None, applied, ledger)  # ledger NOT updated

        monkeypatch.setattr(evolution_module, "apply_proposal", fake_apply)
        p = make_proposal()
        ledger = evaluated_ledger_for(p)
        p = evolution_module.evaluate_proposal(p, ledger=ledger).proposal
        p = evolution_module.approve_proposal(p, approved=True).proposal
        applied = evolution_module.apply_proposal(p, approved=True, ledger=ledger)
        activation = evolution_module.activate_proposal(applied.proposal, ledger=applied.ledger)
        assert activation.success is False
        assert activation.proposal.status is EvolutionStatus.FAILED


# ---------------------------------------------------------------------------
# Rejected proposals and failed application
# ---------------------------------------------------------------------------


class TestRejectedProposals:
    def test_a_proposal_with_an_unsatisfied_dependency_is_rejected(self):
        p = make_proposal(dependencies=("evo-000",))
        result = evaluate_proposal(p, ledger=evaluated_ledger_for(p))
        assert result.outcome is EvolutionStatus.REJECTED
        assert "evo-000" in result.reason

    def test_a_proposal_whose_assumed_current_version_is_stale_is_rejected(self):
        p = make_proposal(current_version="1.0")
        stale_ledger = EvolutionLedger(current_versions={"brain.reasoning_prompt": "1.5"})
        result = evaluate_proposal(p, ledger=stale_ledger)
        assert result.outcome is EvolutionStatus.REJECTED

    def test_a_satisfied_dependency_allows_evaluation_to_proceed(self):
        base = EvolutionLedger(
            current_versions={"brain.reasoning_prompt": "1.0"}, active_proposals=("evo-000",)
        )
        p = make_proposal(dependencies=("evo-000",))
        result = evaluate_proposal(p, ledger=base)
        assert result.outcome is EvolutionStatus.EVALUATED


class TestFailedApplication:
    def test_apply_without_approval_fails_and_leaves_ledger_untouched(self):
        p = make_proposal()
        ledger = evaluated_ledger_for(p)
        p = evaluate_proposal(p, ledger=ledger).proposal
        p = approve_proposal(p, approved=True).proposal
        result = apply_proposal(p, approved=False, ledger=ledger)
        assert result.success is False
        assert result.ledger is ledger


# ---------------------------------------------------------------------------
# Independent verification
# ---------------------------------------------------------------------------


class TestIndependentVerification:
    def test_verify_confirms_a_correctly_applied_change(self):
        p = make_proposal()
        ledger = evaluated_ledger_for(p)
        p = evaluate_proposal(p, ledger=ledger).proposal
        p = approve_proposal(p, approved=True).proposal
        applied = apply_proposal(p, approved=True, ledger=ledger)
        verified, reason = verify_proposal(applied.proposal, ledger=applied.ledger)
        assert verified is True

    def test_verify_checks_the_proposed_version_specifically(self):
        wrong = EvolutionLedger(current_versions={"brain.reasoning_prompt": "9.9"}, active_proposals=("evo-001",))
        applied_proposal = make_proposal(status=EvolutionStatus.APPLIED)
        verified, reason = verify_proposal(applied_proposal, ledger=wrong)
        assert verified is False
        assert "9.9" in reason

    def test_verify_checks_the_proposal_is_recorded_active(self):
        ledger = EvolutionLedger(current_versions={"brain.reasoning_prompt": "1.1"}, active_proposals=())
        applied_proposal = make_proposal(status=EvolutionStatus.APPLIED)
        verified, reason = verify_proposal(applied_proposal, ledger=ledger)
        assert verified is False


# ---------------------------------------------------------------------------
# Rollback representation
# ---------------------------------------------------------------------------


class TestRollbackRepresentation:
    def test_rollback_requires_the_active_status(self):
        p = evaluate_proposal(make_proposal(), ledger=evaluated_ledger_for(make_proposal())).proposal
        result = rollback_proposal(p, approved=True, ledger=EvolutionLedger())
        assert result.success is False

    def test_rollback_requires_explicit_approval(self):
        p = make_proposal()
        activation_result = run_evolution_lifecycle(p, approved=True, ledger=evaluated_ledger_for(p))
        result = rollback_proposal(activation_result.proposal, approved=False, ledger=activation_result.after_ledger)
        assert result.success is False
        assert result.proposal.status is EvolutionStatus.ACTIVE  # unchanged

    def test_a_successful_rollback_restores_the_pre_change_version(self):
        p = make_proposal()
        activation_result = run_evolution_lifecycle(p, approved=True, ledger=evaluated_ledger_for(p))
        rollback = rollback_proposal(activation_result.proposal, approved=True, ledger=activation_result.after_ledger)
        assert rollback.success is True
        assert rollback.ledger.version_of("brain.reasoning_prompt") == "1.0"
        assert rollback.ledger.is_proposal_active("evo-001") is False

    def test_rollback_never_touches_a_filesystem(self):
        """Structural: rollback_proposal's own source contains no file,
        path, or subprocess operation - it is pure ledger bookkeeping."""
        import inspect

        source = inspect.getsource(rollback_proposal)
        for forbidden in ("open(", "Path(", "subprocess", "os.remove", "shutil"):
            assert forbidden not in source


# ---------------------------------------------------------------------------
# Isolation - structural, proven by AST import scans
# ---------------------------------------------------------------------------


class TestIsolationFromEverythingItGoverns:
    @pytest.mark.parametrize(
        "forbidden_prefix,label",
        [
            ("src.manager", "Brain / orchestration"),
            ("src.services.memory", "Memory"),
            ("src.services.identity", "VoiceProfile / Personality / specialist identity"),
            ("src.channels", "speech provider abstraction"),
            ("src.providers", "LLM provider / speech provider implementations"),
            ("src.control_center", "3D / environment / Control Center / Conversation Room architecture"),
            ("src.extensions", "Stage 11.A"),
            ("src.conversation", "Conversation Room"),
        ],
    )
    def test_evolution_module_does_not_import(self, forbidden_prefix, label):
        source = (REPO_ROOT / "src" / "evolution.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefix), (label, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefix), (label, alias.name)

    def test_stage_11a_is_gone_and_not_replaced_by_another_parallel_ledger(self):
        """Stage 11.A (``src/extensions.py``) governed the same question this
        module does - may a declaration be marked active - in its own ledger,
        with no production caller. It was removed on 2026-09-15 rather than
        wired in, and nothing may quietly take its place."""
        assert not (REPO_ROOT / "src" / "extensions.py").exists()
        governed = {
            path.name for path in (REPO_ROOT / "src" / "services" / "evolution").glob("*.py")
        }
        assert "ecosystem_evolution.py" in governed and "handlers.py" in governed

    def test_no_brain_memory_or_voiceprofile_class_is_ever_constructed_here(self):
        source = (REPO_ROOT / "src" / "evolution.py").read_text(encoding="utf-8")
        for name in (
            "Memory(", "Brain(", "PersistentMemory(", "SpecialistBrain(", "LLMManagerBrain(",
            "VoiceProfile(", "AgentPersonality(", "SpecialistIdentity(",
        ):
            assert name not in source

    def test_a_real_managers_brain_memory_voice_and_personality_are_unaffected(self):
        """Behavioural, not just structural: a real Alfred/Arthur pair,
        driven through a full evolution lifecycle including a rollback,
        end up with the exact same Brain, Memory, VoiceProfile and
        Personality objects they started with."""
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        alfred_brain = manager.brain
        alfred_memory = manager.memory
        alfred_voice = manager.own_skills.voice
        alfred_personality = manager.own_skills.personality
        arthur_before = manager.registry.get("Arthur")
        arthur_brain = arthur_before.brain
        arthur_memory = arthur_before.memory
        arthur_voice = arthur_before.voice

        p = make_proposal()
        result = run_evolution_lifecycle(p, approved=True, ledger=evaluated_ledger_for(p))
        rollback_proposal(result.proposal, approved=True, ledger=result.after_ledger)

        assert manager.brain is alfred_brain
        assert manager.memory is alfred_memory
        assert manager.own_skills.voice == alfred_voice
        assert manager.own_skills.personality == alfred_personality
        arthur_after = manager.registry.get("Arthur")
        assert arthur_after is arthur_before
        assert arthur_after.brain is arthur_brain
        assert arthur_after.memory is arthur_memory
        assert arthur_after.voice == arthur_voice


# ---------------------------------------------------------------------------
# Future / unknown target categories, no hardcoded branching
# ---------------------------------------------------------------------------


class TestFutureTargetCategoriesNeedNoCoreChange:
    @pytest.mark.parametrize(
        "target",
        [
            "brain.reasoning_prompt",
            "memory.retrieval_strategy",
            "capability.research",
            "provider.new_llm_backend",
            "speech.new_tts_provider",
            "interface.control_center_layout",
            "a_target_category_nobody_has_invented_yet",
        ],
    )
    def test_every_target_goes_through_the_identical_governed_path(self, target):
        p = make_proposal(target=target)
        result = run_evolution_lifecycle(p, approved=True, ledger=evaluated_ledger_for(p))
        assert result.activated is True
        assert result.after_ledger.version_of(target) == "1.1"

    def test_no_core_lifecycle_function_branches_on_target(self):
        source = (REPO_ROOT / "src" / "evolution.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        core_functions = {
            "validate_proposal", "evaluate_proposal", "approve_proposal", "apply_proposal",
            "verify_proposal", "activate_proposal", "rollback_proposal", "run_evolution_lifecycle",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in core_functions:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Compare):
                        candidates = [inner.left, *inner.comparators]
                        for candidate in candidates:
                            if isinstance(candidate, ast.Attribute) and candidate.attr == "target":
                                pytest.fail(f"{node.name} branches on .target")

    def test_no_core_lifecycle_function_branches_on_risk_level(self):
        """The stage's own principle, checked structurally: risk_level is
        descriptive, never a decision input for this module."""
        source = (REPO_ROOT / "src" / "evolution.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        core_functions = {
            "validate_proposal", "evaluate_proposal", "approve_proposal", "apply_proposal",
            "verify_proposal", "activate_proposal", "rollback_proposal", "run_evolution_lifecycle",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in core_functions:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Attribute) and inner.attr == "risk_level":
                        pytest.fail(f"{node.name} reads .risk_level")
