"""Regression tests for the three extraction failures seen in the first real
Gmail run (2026-08-17).

The run analysed 25 real messages and produced three defects:

  1. Freelancer listings extracted no title at all -> "Untitled opportunity"
  2. Freelancer listings extracted no company
  3. A Wellfound subject absorbed the platform brand into the title, so
     "<role> at <company> - Wellfound" never split into role and company

IMPORTANT: these fixtures are RECONSTRUCTED from the report that run produced,
not captured from the raw messages - no further Gmail access was made. They
reproduce the observed *shapes* (marketplace listing with a bare title line;
platform-branded subject with a leading label), which is what the extractors
key on. The next real run is what confirms the fix against genuine mail.
"""

import pytest
from opportunity_fixtures import TEST_PROFILE, email

from src.agents.email import extractors
from src.agents.email.analyzers import JobOpportunityAnalyzer

# --------------------------------------------------------------------------
# Shape 1 - marketplace listing: a bare title line, no company, no "at"
# --------------------------------------------------------------------------

FREELANCER_BLOCK = """Website content writer and SEO specialist needed
$8 - $13 USD/hr
Skills: content writing, copywriting, data entry, web development, website design, website management
Bid on this project
https://www.freelancer.com/projects/12345

You are receiving this job alert because you subscribed to project notifications.
"""

FREELANCER_EMAIL = email(
    "fl-real-1",
    "Freelancer <noreply@notifications.freelancer.com>",
    "New projects matching your skills",
    FREELANCER_BLOCK,
)

# --------------------------------------------------------------------------
# Shape 2 - platform-branded subject with a leading label
# --------------------------------------------------------------------------

WELLFOUND_SUBJECT = (
    "New role for you: Intern BE Developer for P2P blockchain tutoring "
    "platform at APOLO - P2P CRYPTO LEARNING - Wellfound"
)

WELLFOUND_EMAIL = email(
    "wf-real-1",
    "Wellfound <team@hi.wellfound.com>",
    WELLFOUND_SUBJECT,
    "A startup is hiring. View the role and apply on Wellfound.\n"
    "Seniority: Intern\n",
    headers={"List-Id": "<jobs.wellfound.com>"},
)


class TestDefect1TitleFromMarketplaceListing:
    """A listing with no 'at' shape and no 'Title:' label must still name itself."""

    def test_first_line_becomes_the_title(self):
        title = extractors.first_line_title(FREELANCER_BLOCK)
        assert title == "Website content writer and SEO specialist needed"

    def test_extractor_no_longer_returns_none(self):
        title, _ = extractors.extract_title_and_company(
            FREELANCER_BLOCK, FREELANCER_EMAIL.subject, source="Freelancer"
        )
        assert title and "content writer" in title.lower()

    def test_end_to_end_is_no_longer_untitled(self):
        """The exact symptom from the real run."""
        report = JobOpportunityAnalyzer(profile=TEST_PROFILE).analyze([FREELANCER_EMAIL])
        headlines = [o.headline() for o in report.ranked + report.ambiguous]
        assert headlines and not any("Untitled" in h for h in headlines)

    def test_skills_and_rate_still_extract(self):
        """The parts that already worked must keep working."""
        required, _ = extractors.extract_skills(FREELANCER_BLOCK)
        assert "copywriting" in required
        assert "8" in extractors.extract_salary(FREELANCER_BLOCK)


class TestDefect2CompanyIsAbsentNotWrong:
    def test_no_company_is_reported_as_none(self):
        """A marketplace listing genuinely has no company - do not invent one."""
        _, company = extractors.extract_title_and_company(
            FREELANCER_BLOCK, FREELANCER_EMAIL.subject, source="Freelancer"
        )
        assert company is None

    def test_client_label_is_read_when_present(self):
        block = FREELANCER_BLOCK + "Client: Meadow Consulting\n"
        _, company = extractors.extract_title_and_company(block, "subject", source="Freelancer")
        assert company == "Meadow Consulting"


class TestDefect3PlatformBrandInSubject:
    def test_leading_label_is_stripped(self):
        cleaned = extractors.clean_subject(WELLFOUND_SUBJECT, source="Wellfound")
        assert not cleaned.lower().startswith("new role for you")

    def test_trailing_brand_is_stripped(self):
        cleaned = extractors.clean_subject(WELLFOUND_SUBJECT, source="Wellfound")
        assert not cleaned.endswith("Wellfound")

    def test_role_and_company_now_split(self):
        title, company = extractors.extract_title_and_company(
            "", WELLFOUND_SUBJECT, source="Wellfound"
        )
        assert title == "Intern BE Developer for P2P blockchain tutoring platform"
        assert company == "APOLO - P2P CRYPTO LEARNING"

    def test_brand_never_ends_up_in_the_company(self):
        _, company = extractors.extract_title_and_company(
            "", WELLFOUND_SUBJECT, source="Wellfound"
        )
        assert "wellfound" not in company.lower()

    def test_end_to_end_headline_is_clean(self):
        report = JobOpportunityAnalyzer(profile=TEST_PROFILE).analyze([WELLFOUND_EMAIL])
        headline = (report.ranked + report.ambiguous)[0].headline()
        assert "APOLO" in headline
        assert headline.count("Wellfound") == 1  # only the "(via Wellfound)" source tag

    @pytest.mark.parametrize(
        "subject",
        [
            "Backend Engineer at Acme - Wellfound",
            "Backend Engineer at Acme | Wellfound",
            "Backend Engineer at Acme — LinkedIn",
            "Jobs for you: Backend Engineer at Acme - Indeed",
        ],
    )
    def test_common_brand_decorations(self, subject):
        title, company = extractors.extract_title_and_company("", subject)
        assert title == "Backend Engineer" and company == "Acme"


class TestFirstLineFallbackIsGuarded:
    """The fallback must not turn arbitrary text into a job title."""

    def test_greetings_are_skipped(self):
        block = "Hi Kelly,\nSenior Python Developer needed\nSkills: python\n"
        assert extractors.first_line_title(block) == "Senior Python Developer needed"

    def test_labelled_fields_are_skipped(self):
        block = "Skills: python, apis\nData Engineer wanted\n"
        assert extractors.first_line_title(block) == "Data Engineer wanted"

    def test_urls_are_skipped(self):
        block = "https://example.com/jobs/1\nPlatform Engineer needed\n"
        assert extractors.first_line_title(block) == "Platform Engineer needed"

    def test_decoration_is_skipped(self):
        block = "-----\n***\nQA Engineer required\n"
        assert extractors.first_line_title(block) == "QA Engineer required"

    def test_single_words_are_skipped(self):
        assert extractors.first_line_title("Jobs\nMobile Developer needed\n") == (
            "Mobile Developer needed"
        )

    def test_overlong_lines_are_skipped(self):
        block = "x" * 200 + "\nBackend Developer needed\n"
        assert extractors.first_line_title(block) == "Backend Developer needed"

    def test_empty_text_yields_none(self):
        assert extractors.first_line_title("") is None

    def test_nothing_usable_yields_none(self):
        assert extractors.first_line_title("https://x.example\n***\n") is None


class TestDefect4NewsletterMastheadByline:
    """Third real Gmail run (2026-08-18) surfaced a false positive:

        "Deputy International Editor: Adam Pasick"

    A newsletter masthead credit was reported as an opportunity. Two causes,
    both fixed here:

      - the posting-cue check matched by substring, so "intern" matched inside
        "International"
      - nothing recognised "<role>: <person name>" as a byline rather than a
        job title
    """

    BYLINE = "Deputy International Editor: Adam Pasick"

    def test_byline_is_not_a_title(self):
        assert extractors.first_line_title(self.BYLINE) is None

    def test_byline_in_a_subject_is_not_a_title(self):
        title, company = extractors.extract_title_and_company("", self.BYLINE)
        assert title is None and company is None

    def test_byline_in_a_body_is_not_a_title(self):
        title, _ = extractors.extract_title_and_company(
            f"{self.BYLINE}\nThis week's stories.", "Newsletter"
        )
        assert title is None

    def test_intern_no_longer_matches_inside_international(self):
        """The root cause: whole-word matching, not substring."""
        assert extractors.first_line_title("International Business Update") is None

    def test_genuine_intern_role_still_matches(self):
        assert extractors.first_line_title("Intern Backend Developer needed") is not None

    @pytest.mark.parametrize(
        "line",
        [
            "Managing Editor: Jane Doe",
            "Chief Correspondent: Mary Jane Watson",
            "Senior Reporter: Adam Pasick",
        ],
    )
    def test_other_masthead_bylines_are_rejected(self, line):
        assert extractors.first_line_title(line) is None

    @pytest.mark.parametrize(
        "line",
        [
            "Senior Engineer: Platform Team",
            "Backend Developer: Acme Systems",
            "Role: Python Engineer",
        ],
    )
    def test_role_then_organisation_is_not_a_byline(self, line):
        """A colon before a team, company or role is not a person byline.

        Asserted against the guard itself: whether such a line is *also*
        skipped by the separate pre-existing "looks like a labelled field"
        heuristic is a different question, untouched by this fix.
        """
        assert extractors._is_person_byline(line) is False

    def test_end_to_end_masthead_is_not_reported(self):
        newsletter = email(
            "nl-real-1",
            "The Daily <news@dailyexample.example>",
            "This week's edition",
            f"{self.BYLINE}\nSkills: reading\nYou are receiving this job alert.\n",
        )
        report = JobOpportunityAnalyzer(profile=TEST_PROFILE).analyze([newsletter])
        headlines = [o.headline() for o in report.ranked + report.ambiguous]
        assert not any("Adam Pasick" in h for h in headlines)


class TestNoRegressionInPreviouslyWorkingFormats:
    """Formats that already extracted correctly must be untouched."""

    def test_labelled_role_and_company(self):
        block = "Role: Python Engineer\nCompany: Vantage Health\n"
        assert extractors.extract_title_and_company(block, "subject") == (
            "Python Engineer",
            "Vantage Health",
        )

    def test_role_at_company_headline(self):
        block = "Senior Python Engineer at Northwind Labs\nWe are hiring.\n"
        title, company = extractors.extract_title_and_company(block, "subject")
        assert title == "Senior Python Engineer" and company == "Northwind Labs"

    def test_subject_with_opportunity_prefix(self):
        title, company = extractors.extract_title_and_company(
            "A vacancy has opened.", "Opportunity: Data Analyst at Brightpath"
        )
        assert title == "Data Analyst" and company == "Brightpath"

    def test_a_title_containing_a_colon_survives(self):
        """'Senior Engineer: Platform' must not lose its first half."""
        cleaned = extractors.clean_subject("Senior Engineer: Platform Team")
        assert cleaned.startswith("Senior Engineer")
