"""Tests for the deterministic job/opportunity analyzer.

Pure logic: constructed messages in, a report out. No mailbox, no agent, no
Manager, no model.
"""

from datetime import datetime, timedelta

import pytest

from src.agents.email.analyzers import (
    AnalysisReport,
    Analyzer,
    Finding,
    JobOpportunityAnalyzer,
)
from src.services.email.message import EmailMessage

BASE = datetime(2026, 8, 15, 9, 0, 0)


def msg(id_="m1", subject="Hello", body="Body", sender="someone@example.com", minutes=0):
    return EmailMessage(
        id=id_, sender=sender, subject=subject, body=body,
        received_at=BASE + timedelta(minutes=minutes),
    )


@pytest.fixture
def analyzer():
    return JobOpportunityAnalyzer()


class TestInterface:
    def test_is_an_analyzer(self, analyzer):
        assert isinstance(analyzer, Analyzer)

    def test_has_a_name_and_capabilities(self, analyzer):
        assert analyzer.name == "job_opportunity"
        assert "job" in analyzer.capabilities and "opportunity" in analyzer.capabilities

    def test_rejects_a_non_positive_threshold(self):
        with pytest.raises(ValueError):
            JobOpportunityAnalyzer(min_score=0)


class TestDetection:
    def test_finds_a_clear_job_email(self, analyzer):
        message = msg(
            subject="Senior Engineer at Acme",
            body="We are hiring. The salary is competitive. Apply by Friday.",
        )
        report = analyzer.analyze([message])
        assert len(report.findings) == 1
        assert report.findings[0].message_id == "m1"

    def test_ignores_ordinary_mail(self, analyzer):
        report = analyzer.analyze([msg(subject="Coffee?", body="Free on Saturday?")])
        assert report.is_empty

    def test_requires_more_than_one_kind_of_signal(self, analyzer):
        """A single passing mention should not look like a job email."""
        weak = msg(subject="This week in software", body="One startup is hiring.")
        assert analyzer.analyze([weak]).is_empty

    def test_a_lower_threshold_admits_weaker_matches(self):
        weak = msg(subject="This week in software", body="One startup is hiring.")
        assert not JobOpportunityAnalyzer(min_score=1).analyze([weak]).is_empty

    def test_scores_by_distinct_signal_kinds_not_repetition(self, analyzer):
        repetitive = msg("a", subject="Job job job", body="job job job jobs job")
        varied = msg("b", subject="Analyst role", body="Salary listed. Apply by Monday.")
        findings = {f.message_id: f.score for f in analyzer.analyze([repetitive, varied]).findings}
        assert findings.get("b", 0) > findings.get("a", 0)

    def test_matching_is_case_insensitive(self, analyzer):
        loud = msg(subject="SENIOR ENGINEER AT ACME", body="WE ARE HIRING. SALARY IS GREAT.")
        assert not analyzer.analyze([loud]).is_empty

    def test_respects_word_boundaries(self, analyzer):
        """'cv' must not match inside 'recover', or everything looks like a job."""
        message = msg(subject="Recovery plan", body="We will recover the discovery data.")
        assert analyzer.analyze([message]).is_empty

    def test_reports_which_signals_fired(self, analyzer):
        message = msg(subject="Engineer wanted", body="Salary is competitive.")
        signals = analyzer.analyze([message]).findings[0].signals
        assert "role" in signals and "compensation" in signals


class TestExtraction:
    def test_extracts_role_and_company(self, analyzer):
        message = msg(subject="Senior Python Engineer at Northwind Labs",
                      body="We are hiring. Salary competitive.")
        finding = analyzer.analyze([message]).findings[0]
        assert finding.role == "Senior Python Engineer"
        assert finding.company == "Northwind Labs"

    def test_strips_subject_noise(self, analyzer):
        message = msg(subject="Opportunity: Data Analyst at Brightpath",
                      body="A vacancy has opened. Compensation is competitive.")
        finding = analyzer.analyze([message]).findings[0]
        assert finding.role == "Data Analyst" and finding.company == "Brightpath"

    def test_returns_none_when_the_shape_is_unrecognised(self, analyzer):
        """An honest blank beats a confident wrong guess."""
        message = msg(subject="We are hiring!", body="Great salary, apply by Friday.")
        finding = analyzer.analyze([message]).findings[0]
        assert finding.role is None and finding.company is None

    def test_a_finding_still_reports_without_extraction(self, analyzer):
        message = msg(subject="We are hiring!", body="Great salary, apply by Friday.")
        assert "opportunity" in analyzer.analyze([message]).findings[0].describe()


class TestReport:
    def test_counts_everything_it_considered(self, analyzer):
        messages = [msg("a", subject="Coffee?"), msg("b", subject="Lunch?")]
        assert analyzer.analyze(messages).considered == 2

    def test_orders_strongest_first(self, analyzer):
        weak = msg("a", subject="Developer opening", body="Take a look.")
        strong = msg("b", subject="Engineer at Acme",
                     body="Hiring now. Salary competitive. Apply by Friday.")
        findings = analyzer.analyze([weak, strong]).findings
        assert findings[0].message_id == "b"

    def test_ordering_is_deterministic(self, analyzer):
        a = msg("a", subject="Engineer at Acme", body="Hiring. Salary competitive.")
        b = msg("b", subject="Analyst at Globex", body="Hiring. Salary competitive.")
        assert [f.message_id for f in analyzer.analyze([a, b]).findings] == [
            f.message_id for f in analyzer.analyze([b, a]).findings
        ]

    def test_summary_is_never_empty_even_with_no_findings(self, analyzer):
        """A COMPLETED result with empty output would be rejected downstream."""
        assert analyzer.analyze([msg(subject="Coffee?")]).summary().strip()

    def test_summary_names_the_analyzer_and_counts(self, analyzer):
        summary = analyzer.analyze([msg(subject="Coffee?")]).summary()
        assert "job_opportunity" in summary and "1 message" in summary

    def test_summary_never_includes_message_bodies(self, analyzer):
        message = msg(subject="Engineer at Acme",
                      body="Hiring now. Salary competitive. SENSITIVE-BODY-TEXT")
        assert "SENSITIVE-BODY-TEXT" not in analyzer.analyze([message]).summary()

    def test_handles_an_empty_message_list(self, analyzer):
        report = analyzer.analyze([])
        assert report.considered == 0 and report.is_empty and report.summary().strip()


class TestContracts:
    def test_finding_rejects_a_non_positive_score(self):
        with pytest.raises(ValueError):
            Finding(message_id="m", sender="s", subject="x", score=0, signals=())

    def test_report_rejects_a_negative_count(self):
        with pytest.raises(ValueError):
            AnalysisReport(analyzer="x", considered=-1)

    def test_findings_are_immutable(self):
        finding = Finding(message_id="m", sender="s", subject="x", score=1, signals=())
        with pytest.raises(Exception):
            finding.score = 5
