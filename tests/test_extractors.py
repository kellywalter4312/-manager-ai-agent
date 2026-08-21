"""Tests for opportunity extraction across varied email formats.

Pure functions in, values out. No agent, no mailbox, no profile.
"""

import pytest
from opportunity_fixtures import (
    FORWARDED_POSTING,
    FREELANCE_PLATFORM,
    NEWSLETTER,
    QUOTED_THREAD,
    RECRUITER_EMAIL,
    WELLFOUND_DIGEST,
    email,
)

from src.agents.email import extractors


class TestSourceDetection:
    def test_reads_the_platform_from_a_header(self):
        """List-Id is set by the platform and survives forwarding."""
        assert extractors.detect_source(WELLFOUND_DIGEST) == "Wellfound"

    def test_falls_back_to_the_sender(self):
        assert extractors.detect_source(FREELANCE_PLATFORM) == "Upwork"

    def test_unknown_source_is_none_not_a_guess(self):
        assert extractors.detect_source(RECRUITER_EMAIL) is None

    def test_no_platform_is_hard_coded_as_required(self):
        """A recruiter email with no platform must still be processable."""
        assert extractors.readable_text(RECRUITER_EMAIL).strip()


class TestFormatHandling:
    def test_detects_a_forwarded_message(self):
        assert extractors.is_forwarded(FORWARDED_POSTING)

    def test_plain_email_is_not_forwarded(self):
        assert not extractors.is_forwarded(RECRUITER_EMAIL)

    def test_quoted_content_is_kept_not_discarded(self):
        """In a reply thread the opportunity often lives inside the quote."""
        text = extractors.readable_text(QUOTED_THREAD)
        assert "Automation Developer" in text
        assert ">" not in text  # decoration stripped, content kept

    def test_subject_is_searchable_with_the_body(self):
        assert "Opportunity" in extractors.readable_text(RECRUITER_EMAIL)


class TestDigestSplitting:
    def test_splits_a_digest_into_one_block_per_posting(self):
        text = extractors.readable_text(WELLFOUND_DIGEST)
        blocks = extractors.split_opportunity_blocks(text)
        assert len(blocks) == 3

    def test_each_block_holds_one_role(self):
        blocks = extractors.split_opportunity_blocks(extractors.readable_text(WELLFOUND_DIGEST))
        assert "Aurora Systems" in blocks[0] and "Ironclad" not in blocks[0]

    def test_a_single_opportunity_email_stays_one_block(self):
        text = extractors.readable_text(RECRUITER_EMAIL)
        assert len(extractors.split_opportunity_blocks(text)) == 1

    def test_ordinary_email_stays_one_block(self):
        assert len(extractors.split_opportunity_blocks("hello there")) == 1


class TestFieldExtraction:
    @pytest.fixture
    def text(self):
        return extractors.readable_text(RECRUITER_EMAIL)

    def test_reads_title_and_company(self, text):
        title, company = extractors.extract_title_and_company(text, RECRUITER_EMAIL.subject)
        assert title == "Python Engineer" and company == "Vantage Health"

    def test_reads_required_skills(self, text):
        required, _ = extractors.extract_skills(text)
        assert set(required) == {"python", "testing", "apis"}

    def test_reads_preferred_skills_separately(self):
        text = extractors.readable_text(FREELANCE_PLATFORM)
        required, preferred = extractors.extract_skills(text)
        assert "docker" in preferred and "docker" not in required

    def test_reads_experience(self, text):
        assert "1-2 years" in extractors.extract_experience(text)

    def test_reads_rate(self, text):
        assert "250" in extractors.extract_salary(text)

    def test_reads_employment_type(self, text):
        assert extractors.extract_employment_type(text) == "Contract"

    def test_reads_location(self, text):
        assert "remote" in extractors.extract_location(text).lower()

    def test_reads_deadline_when_present(self):
        text = extractors.readable_text(FREELANCE_PLATFORM)
        assert "30 August" in extractors.extract_deadline(text)

    def test_reads_an_apply_link(self):
        text = extractors.readable_text(FREELANCE_PLATFORM)
        assert extractors.extract_apply_link(text).startswith("https://")

    def test_extracts_from_a_forwarded_posting(self):
        text = extractors.readable_text(FORWARDED_POSTING)
        title, company = extractors.extract_title_and_company(text, FORWARDED_POSTING.subject)
        assert title == "Junior Developer" and company == "Larkspur Digital"


class TestMissingInformationIsNeverInvented:
    def test_missing_salary_is_none(self):
        message = email("x", "a@b.example", "Role", "Role: Developer\nCompany: X\n")
        assert extractors.extract_salary(extractors.readable_text(message)) is None

    def test_missing_deadline_is_none(self):
        message = email("x", "a@b.example", "Role", "Role: Developer\n")
        assert extractors.extract_deadline(extractors.readable_text(message)) is None

    def test_missing_skills_is_empty_not_guessed(self):
        """Harvesting tech words from prose would invent requirements."""
        message = email(
            "x", "a@b.example", "Developer wanted",
            "We use Python and Django here and love clean code.",
        )
        required, _ = extractors.extract_skills(extractors.readable_text(message))
        assert required == ()

    def test_placeholder_values_are_treated_as_absent(self):
        message = email("x", "a@b.example", "Role", "Salary: not specified\n")
        assert extractors.extract_salary(extractors.readable_text(message)) is None

    def test_unrelated_email_yields_no_title(self):
        title, company = extractors.extract_title_and_company(
            extractors.readable_text(NEWSLETTER), NEWSLETTER.subject
        )
        assert title is None and company is None
