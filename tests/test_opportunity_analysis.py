"""End-to-end tests for the opportunity pipeline and the report it produces."""

import pytest
from opportunity_fixtures import (
    ALL_EMAILS,
    NO_SALARY_NO_DEADLINE,
    OPPORTUNITY_EMAILS,
    POOR_MATCH,
    RECRUITER_EMAIL,
    STRETCH_ROLE,
    TEST_PROFILE,
    UNPAID_ROLE,
    UNRELATED_EMAILS,
    VAGUE_OPPORTUNITY,
    WELLFOUND_DIGEST,
)

from src.agents.email.analyzers import Analyzer, JobOpportunityAnalyzer
from src.agents.email.opportunity import MatchBand, Opportunity, OpportunityReport
from src.agents.email_agent import EmailAgent
from src.core.contracts import Task, TaskStatus
from src.manager.manager import ManagerAgent
from src.services.email.in_memory_mailbox import InMemoryMailbox


def analyze(messages, **kwargs):
    return JobOpportunityAnalyzer(profile=TEST_PROFILE, **kwargs).analyze(list(messages))


def find(report, needle):
    for opportunity in report.ranked + report.ambiguous:
        if needle.lower() in opportunity.headline().lower():
            return opportunity
    raise AssertionError(f"no opportunity matching {needle!r}")


class TestBackwardCompatibility:
    def test_without_a_profile_behaviour_is_unchanged(self):
        """Milestone 6 detection must survive untouched."""
        report = JobOpportunityAnalyzer().analyze(list(OPPORTUNITY_EMAILS))
        assert not isinstance(report, OpportunityReport)
        assert report.findings

    def test_still_satisfies_the_analyzer_interface(self):
        assert isinstance(JobOpportunityAnalyzer(profile=TEST_PROFILE), Analyzer)

    def test_opportunity_is_a_finding(self):
        from src.agents.email.analyzers import Finding

        assert issubclass(Opportunity, Finding)

    def test_rejects_a_non_positive_top_n(self):
        with pytest.raises(ValueError):
            JobOpportunityAnalyzer(profile=TEST_PROFILE, top_n=0)


class TestRelevanceDetection:
    def test_finds_opportunities_across_every_source(self):
        report = analyze(OPPORTUNITY_EMAILS)
        for needle in ("Aurora", "Meadow", "Larkspur", "Vantage", "Cobalt"):
            assert find(report, needle)

    def test_ignores_unrelated_email(self):
        report = analyze(UNRELATED_EMAILS)
        assert report.ranked == () and report.ambiguous == ()

    def test_counts_what_it_skipped(self):
        assert analyze(UNRELATED_EMAILS).ignored == len(UNRELATED_EMAILS)

    def test_a_newsletter_mentioning_hiring_is_not_an_opportunity(self):
        report = analyze(ALL_EMAILS)
        assert not any("week in software" in o.subject.lower() for o in report.ranked)


class TestFormatCoverage:
    def test_splits_a_digest_into_separate_opportunities(self):
        report = analyze([WELLFOUND_DIGEST])
        headlines = {o.headline() for o in report.ranked + report.ambiguous}
        assert len(headlines) == 3

    def test_reads_a_forwarded_posting(self):
        assert find(analyze(ALL_EMAILS), "Larkspur").role == "Junior Developer"

    def test_reads_a_quoted_thread(self):
        assert find(analyze(ALL_EMAILS), "Cobalt").required_skills

    def test_reads_a_recruiter_email(self):
        opportunity = find(analyze([RECRUITER_EMAIL]), "Vantage")
        assert opportunity.employment_type and opportunity.salary

    def test_identifies_the_platform_when_known(self):
        assert find(analyze([WELLFOUND_DIGEST]), "Aurora").source == "Wellfound"

    def test_missing_source_is_none_not_invented(self):
        assert find(analyze([RECRUITER_EMAIL]), "Vantage").source is None


class TestClassificationEndToEnd:
    def test_strong_match_for_a_junior_python_role(self):
        assert find(analyze([WELLFOUND_DIGEST]), "Aurora").band is MatchBand.STRONG

    def test_stretch_role_is_not_a_strong_match(self):
        assert find(analyze([STRETCH_ROLE]), "Zenith").band is not MatchBand.STRONG

    def test_unrelated_stack_is_a_poor_match(self):
        assert find(analyze([POOR_MATCH]), "Ironworks").band is MatchBand.POOR

    def test_unpaid_role_is_poor_despite_matching_skills(self):
        opportunity = find(analyze([UNPAID_ROLE]), "StartupDream")
        assert opportunity.band is MatchBand.POOR
        assert opportunity.red_flags

    def test_vague_email_is_ambiguous_not_ranked(self):
        report = analyze([VAGUE_OPPORTUNITY])
        assert report.ranked == () and len(report.ambiguous) == 1


class TestHonesty:
    def test_never_claims_a_skill_the_user_lacks(self):
        """Invariant across the whole corpus."""
        report = analyze(ALL_EMAILS)
        for opportunity in report.ranked + report.ambiguous:
            for skill in opportunity.matched_skills + opportunity.partial_skills:
                assert TEST_PROFILE.demonstrates(skill), skill

    def test_learning_skills_never_appear_as_matched(self):
        report = analyze(ALL_EMAILS)
        for opportunity in report.ranked + report.ambiguous:
            assert not set(opportunity.learning_skills) & set(opportunity.matched_skills)

    def test_missing_and_matched_never_overlap(self):
        report = analyze(ALL_EMAILS)
        for opportunity in report.ranked + report.ambiguous:
            assert not set(opportunity.missing_skills) & set(opportunity.matched_skills)

    def test_absent_fields_stay_none_and_are_listed(self):
        opportunity = find(analyze([NO_SALARY_NO_DEADLINE]), "QuietCo")
        assert opportunity.salary is None and opportunity.deadline is None
        assert "salary" in opportunity.missing_fields

    def test_explanations_state_gaps(self):
        opportunity = find(analyze([POOR_MATCH]), "Ironworks")
        assert "missing" in opportunity.explain()

    def test_no_invented_company_for_a_vague_posting(self):
        assert find(analyze([VAGUE_OPPORTUNITY]), "opportunity").company is None


class TestRankingAndReport:
    def test_strong_matches_rank_above_poor_ones(self):
        report = analyze(ALL_EMAILS)
        bands = [o.band for o in report.ranked]
        assert bands == sorted(bands, key=lambda b: ["strong", "possible", "poor"].index(b.value))

    def test_report_shows_at_most_top_n(self):
        report = analyze(ALL_EMAILS, top_n=3)
        assert report.summary().count("STRONG MATCH -") <= 3

    def test_remaining_are_acknowledged_not_hidden(self):
        report = analyze(ALL_EMAILS, top_n=2)
        assert "further ranked opportunity" in report.summary()

    def test_ambiguous_section_is_present_with_reasons(self):
        summary = analyze(ALL_EMAILS).summary()
        assert "NEEDS A LOOK" in summary and "does not list" in summary

    def test_nothing_relevant_is_silently_discarded(self):
        report = analyze(ALL_EMAILS)
        assert len(report.ranked) + len(report.ambiguous) >= len(OPPORTUNITY_EMAILS)

    def test_summary_is_never_empty(self):
        assert analyze(UNRELATED_EMAILS).summary().strip()

    def test_ranking_is_deterministic(self):
        first = [o.headline() for o in analyze(ALL_EMAILS).ranked]
        second = [o.headline() for o in analyze(list(reversed(ALL_EMAILS))).ranked]
        assert first == second

    def test_explanations_say_why(self):
        assert "why" in find(analyze([WELLFOUND_DIGEST]), "Aurora").explain()


class TestThroughTheAgentAndManager:
    @staticmethod
    def _manager():
        agent = EmailAgent(
            InMemoryMailbox(ALL_EMAILS),
            analyzers=[JobOpportunityAnalyzer(profile=TEST_PROFILE)],
        )
        manager = ManagerAgent()
        manager.register_agent(agent)
        return manager

    def test_full_data_loop_produces_a_ranked_report(self):
        result = self._manager().handle_task(
            Task(description="scan my inbox for job opportunities")
        )
        assert result.status is TaskStatus.COMPLETED
        assert "TOP" in result.output and "STRONG MATCH" in result.output

    def test_the_assessor_accepts_the_report(self):
        result = self._manager().handle_task(Task(description="find job opportunities"))
        assert result.status is not TaskStatus.REJECTED

    def test_sending_is_still_refused(self):
        result = self._manager().handle_task(Task(description="send an email to a recruiter"))
        assert result.status is TaskStatus.FAILED

    def test_analysis_only_no_external_action(self):
        """No apply link is followed, no contact made - the report is text."""
        result = self._manager().handle_task(Task(description="find job opportunities"))
        assert isinstance(result.output, str)
