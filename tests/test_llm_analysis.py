"""Tests for the LLM judgement contract.

Pure data - no provider, no model, no network. The contract is the only thing
under test here; wiring it to a provider comes later.
"""

import dataclasses

import pytest

from src.agents.email.llm_analysis import (
    LLMAnalysisError,
    LLMJudgement,
    LLMVerdict,
)


def judgement(**overrides):
    defaults = dict(
        message_id="m1",
        verdict=LLMVerdict.CONSIDER,
        provider="anthropic",
        rationale="Junior backend role; the stack overlaps with your Python work.",
    )
    defaults.update(overrides)
    return LLMJudgement(**defaults)


class TestVerdict:
    def test_has_the_four_outcomes(self):
        assert {v.value for v in LLMVerdict} == {"pursue", "consider", "skip", "unclear"}

    def test_labels_are_human_readable(self):
        assert LLMVerdict.PURSUE.label == "WORTH PURSUING"

    def test_only_unclear_is_undecided(self):
        assert not LLMVerdict.UNCLEAR.is_decided
        for verdict in (LLMVerdict.PURSUE, LLMVerdict.CONSIDER, LLMVerdict.SKIP):
            assert verdict.is_decided

    def test_is_a_plain_string(self):
        assert LLMVerdict.SKIP == "skip"


class TestProvenanceIsMandatory:
    """A judgement must always be attributable to whatever produced it."""

    def test_provider_is_required(self):
        with pytest.raises(LLMAnalysisError):
            judgement(provider="   ")

    def test_provider_is_recorded(self):
        assert judgement(provider="anthropic").provider == "anthropic"

    def test_model_is_optional(self):
        assert judgement().model is None

    def test_attribution_prefers_the_model(self):
        assert judgement(model="claude-opus-5").attribution == "claude-opus-5"

    def test_attribution_falls_back_to_the_provider(self):
        assert judgement(model=None).attribution == "anthropic"


class TestAVerdictMustCarryAReason:
    def test_rationale_is_required(self):
        with pytest.raises(LLMAnalysisError) as exc:
            judgement(rationale="")
        assert "explain its verdict" in str(exc.value)

    def test_whitespace_only_rationale_is_rejected(self):
        with pytest.raises(LLMAnalysisError):
            judgement(rationale="   \n  ")

    def test_rationale_whitespace_is_collapsed(self):
        assert judgement(rationale="a   b\n c").rationale == "a b c"


class TestValidation:
    def test_message_id_is_required(self):
        with pytest.raises(LLMAnalysisError):
            judgement(message_id="  ")

    def test_message_id_is_trimmed(self):
        assert judgement(message_id="  m9 ").message_id == "m9"

    @pytest.mark.parametrize("value", [-0.1, 1.1, 2.0])
    def test_confidence_must_be_a_probability(self, value):
        with pytest.raises(LLMAnalysisError):
            judgement(confidence=value)

    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
    def test_confidence_bounds_are_inclusive(self, value):
        assert judgement(confidence=value).confidence == value

    def test_a_string_verdict_is_accepted(self):
        assert judgement(verdict="pursue").verdict is LLMVerdict.PURSUE

    def test_an_unknown_verdict_is_rejected(self):
        with pytest.raises(LLMAnalysisError):
            judgement(verdict="maybe-ish")

    def test_is_immutable(self):
        with pytest.raises(Exception):
            judgement().confidence = 0.9


class TestNormalisation:
    def test_requirements_are_lowercased(self):
        assert judgement(inferred_requirements=("Python", "APIs")).inferred_requirements == (
            "python",
            "apis",
        )

    def test_duplicates_are_removed(self):
        assert judgement(inferred_requirements=("python", "Python ")).inferred_requirements == (
            "python",
        )

    def test_blanks_are_dropped(self):
        assert judgement(concerns=("", "  ", "vague scope")).concerns == ("vague scope",)

    def test_order_is_preserved(self):
        assert judgement(concerns=("b", "a")).concerns == ("b", "a")

    def test_concerns_keep_their_casing(self):
        assert judgement(concerns=("No Salary stated",)).concerns == ("No Salary stated",)

    def test_seniority_is_normalised(self):
        assert judgement(inferred_seniority=" Junior ").inferred_seniority == "junior"

    def test_absent_seniority_stays_none(self):
        assert judgement(inferred_seniority="  ").inferred_seniority is None


class TestQueries:
    def test_pursue_and_consider_are_actionable(self):
        assert judgement(verdict=LLMVerdict.PURSUE).is_actionable
        assert judgement(verdict=LLMVerdict.CONSIDER).is_actionable

    def test_skip_and_unclear_are_not_actionable(self):
        assert not judgement(verdict=LLMVerdict.SKIP).is_actionable
        assert not judgement(verdict=LLMVerdict.UNCLEAR).is_actionable

    def test_confidence_threshold(self):
        assert judgement(confidence=0.8).is_confident()
        assert not judgement(confidence=0.2).is_confident()

    def test_an_undecided_verdict_is_never_confident(self):
        """High confidence in 'I cannot tell' is a contradiction."""
        assert not judgement(verdict=LLMVerdict.UNCLEAR, confidence=0.99).is_confident()


class TestUnavailable:
    def test_produces_a_valid_judgement(self):
        result = LLMJudgement.unavailable("m1", provider="null")
        assert result.verdict is LLMVerdict.UNCLEAR
        assert result.provider == "null"

    def test_carries_no_confidence(self):
        assert LLMJudgement.unavailable("m1", provider="null").confidence == 0.0

    def test_says_why(self):
        result = LLMJudgement.unavailable("m1", provider="null", reason="provider failed")
        assert result.rationale == "provider failed"

    def test_records_the_absence_as_an_unknown(self):
        assert LLMJudgement.unavailable("m1", provider="null").unknowns

    def test_is_not_actionable(self):
        assert not LLMJudgement.unavailable("m1", provider="null").is_actionable


class TestLogSafety:
    def test_describe_excludes_model_prose(self):
        """Summaries and rationales paraphrase email content; logs must not."""
        result = judgement(
            rationale="SENSITIVE-RATIONALE-TEXT", summary="SENSITIVE-SUMMARY-TEXT"
        )
        described = result.describe()
        assert "SENSITIVE-RATIONALE-TEXT" not in described
        assert "SENSITIVE-SUMMARY-TEXT" not in described

    def test_describe_reports_the_useful_facts(self):
        described = judgement(model="claude-opus-5", confidence=0.75).describe()
        assert "consider" in described and "0.75" in described and "claude-opus-5" in described

    def test_describe_is_one_line(self):
        assert "\n" not in judgement().describe()


class TestStaysASeparateLayer:
    """The contract must not become a second copy of the domain model."""

    FIELDS = {f.name for f in dataclasses.fields(LLMJudgement)}

    @pytest.mark.parametrize(
        "owned_by_opportunity",
        [
            "role", "company", "source", "salary", "deadline", "location",
            "employment_type", "apply_link", "band", "match_score",
            "matched_skills", "missing_skills", "supporting_projects",
            "required_skills", "red_flags",
        ],
    )
    def test_does_not_duplicate_opportunity_fields(self, owned_by_opportunity):
        assert owned_by_opportunity not in self.FIELDS

    def test_joins_to_the_domain_by_message_id_only(self):
        assert "message_id" in self.FIELDS

    def test_does_not_import_the_opportunity_model(self):
        """Keeping the layers decoupled is structural, not a convention."""
        import src.agents.email.llm_analysis as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "from src.agents.email.opportunity import" not in source

    def test_the_deterministic_analyzer_is_untouched_by_this_module(self):
        import src.agents.email.analyzers as analyzers

        source = open(analyzers.__file__, encoding="utf-8").read()
        assert "llm_analysis" not in source
