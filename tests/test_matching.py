"""Tests for skill matching, classification and the honesty rules."""

import pytest
from opportunity_fixtures import TEST_PROFILE

from src.agents.email.matching import find_red_flags, match_opportunity
from src.agents.email.opportunity import MatchBand


def match(required=(), **kwargs):
    return match_opportunity(profile=TEST_PROFILE, required_skills=required, **kwargs)


class TestSkillBuckets:
    def test_strong_skills_are_matched(self):
        assert match(("python",)).matched == ("python",)

    def test_working_skills_are_partial_not_full(self):
        result = match(("javascript",))
        assert result.partial == ("javascript",) and result.matched == ()

    def test_learning_skills_are_never_matched(self):
        """The central honesty rule."""
        result = match(("docker",))
        assert result.learning == ("docker",)
        assert result.matched == () and result.partial == ()

    def test_unknown_skills_are_missing(self):
        assert match(("rust",)).missing == ("rust",)

    def test_buckets_are_disjoint_and_exhaustive(self):
        required = ("python", "javascript", "docker", "rust")
        result = match(required)
        allocated = result.matched + result.partial + result.learning + result.missing
        assert sorted(allocated) == sorted(required)
        assert len(set(allocated)) == len(allocated)

    def test_aliases_resolve_before_matching(self):
        assert match(("JS",)).partial == ("javascript",)


class TestClassification:
    def test_full_coverage_at_or_below_level_is_strong(self):
        result = match(("python", "testing", "apis"), title="Python Developer", seniority="junior")
        assert result.band is MatchBand.STRONG

    def test_partial_coverage_is_possible(self):
        result = match(("python", "rust"), title="Developer", seniority="junior")
        assert result.band is MatchBand.POSSIBLE

    def test_no_coverage_is_poor(self):
        result = match(("rust", "go", "terraform"), title="Engineer", seniority="junior")
        assert result.band is MatchBand.POOR

    def test_far_above_seniority_is_poor_even_with_the_skills(self):
        result = match(("python", "testing", "apis"), title="Python Engineer", seniority="principal")
        assert result.band is MatchBand.POOR

    def test_one_level_up_is_a_stretch_not_a_rejection(self):
        result = match(("python", "testing", "apis"), title="Python Developer", seniority="mid")
        assert result.band in (MatchBand.STRONG, MatchBand.POSSIBLE)
        assert any("stretch" in reason for reason in result.reasons)

    def test_no_required_skills_is_ambiguous_not_poor(self):
        """Unknown and unsuitable are different answers."""
        result = match((), title="Developer")
        assert result.band is MatchBand.AMBIGUOUS

    def test_ambiguous_records_why(self):
        result = match((), title="Developer")
        assert any("does not list" in note for note in result.uncertainties)


class TestHardFilters:
    def test_a_hard_filter_vetoes_a_perfect_skill_match(self):
        result = match(
            ("python", "testing", "apis"),
            title="Python Developer",
            seniority="junior",
            text="This is an unpaid position for exposure.",
        )
        assert result.band is MatchBand.POOR
        assert any("hard filter" in reason for reason in result.reasons)

    def test_unrelated_text_does_not_trip_a_filter(self):
        result = match(("python",), title="Python Developer", text="Paid role, £40,000.")
        assert not any("hard filter" in reason for reason in result.reasons)


class TestScoring:
    def test_score_combines_more_than_keywords(self):
        """Same skills, different seniority fit must score differently."""
        junior = match(("python",), title="Python Developer", seniority="junior")
        senior = match(("python",), title="Python Developer", seniority="lead")
        assert junior.score > senior.score

    def test_target_alignment_affects_the_score(self):
        on_target = match(("python",), title="Python Development", seniority="junior")
        off_target = match(("python",), title="Marketing Coordinator", seniority="junior")
        assert on_target.score > off_target.score

    def test_confidence_rises_with_available_information(self):
        thin = match((), title=None)
        rich = match(("python", "testing"), title="Python Developer")
        assert rich.confidence > thin.confidence

    def test_scores_are_deterministic(self):
        args = dict(title="Python Developer", seniority="junior")
        assert match(("python", "testing"), **args).score == match(("python", "testing"), **args).score


class TestEvidence:
    def test_supporting_projects_are_reported(self):
        assert "Orchestrator" in match(("python", "testing")).supporting_projects

    def test_no_evidence_is_claimed_for_missing_skills(self):
        assert match(("rust",)).supporting_projects == ()


class TestExplanations:
    def test_missing_skills_are_stated_explicitly(self):
        result = match(("python", "rust"), title="Developer")
        assert any("not demonstrated" in reason for reason in result.reasons)

    def test_learning_skills_are_flagged_as_not_counted(self):
        result = match(("docker", "python"), title="Developer")
        assert any("not counted as a match" in reason for reason in result.reasons)

    def test_unknown_fields_are_listed(self):
        result = match(("python",), title="Dev", missing_fields=("salary", "deadline"))
        assert any("salary" in note for note in result.uncertainties)


class TestRedFlags:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("This is an unpaid internship", "unpaid"),
            ("Commission-only role", "commission"),
            ("Equity-only compensation", "equity"),
            ("You must pay a fee to apply", "payment"),
            ("Urgent! Apply immediately", "urgency"),
        ],
    )
    def test_detects_warning_signs(self, text, expected):
        assert any(expected in flag for flag in find_red_flags(text))

    def test_ordinary_posting_has_no_flags(self):
        assert find_red_flags("Python developer, £40,000, remote, apply by Friday") == ()
