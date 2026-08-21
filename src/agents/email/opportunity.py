"""The opportunity contract: what was extracted, and how well it fits.

``Opportunity`` subclasses the Milestone 6 ``Finding``, adding every new field
with a default. The Analyzer and AnalysisReport contracts are untouched, so
existing analyzers, tests and callers keep working unchanged.

Two honesty rules are encoded in the data model rather than left to
convention:

- Every extracted field is Optional and defaults to None. A field that could
  not be read stays None and is named in ``missing_fields``. Nothing is
  invented to fill a gap.
- Skills are reported in four separate buckets - matched, partial, learning,
  missing - so a report can never quietly fold "I'm learning this" into
  "I have this".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from src.agents.email.analyzers import AnalysisReport, Finding

if TYPE_CHECKING:  # annotation only - no runtime dependency on the LLM layer
    from src.agents.email.llm_analysis import LLMJudgement


class MatchBand(str, Enum):
    """How well an opportunity fits the user."""

    STRONG = "strong"
    POSSIBLE = "possible"
    POOR = "poor"
    AMBIGUOUS = "ambiguous"

    @property
    def label(self) -> str:
        return {
            "strong": "STRONG MATCH",
            "possible": "POSSIBLE / STRETCH",
            "poor": "POOR MATCH",
            "ambiguous": "AMBIGUOUS - not enough information",
        }[self.value]


# Fields the extractor tries to read. Anything absent is reported, not guessed.
EXTRACTED_FIELDS: tuple[str, ...] = (
    "title",
    "company",
    "source",
    "seniority",
    "required_skills",
    "preferred_skills",
    "experience_required",
    "salary",
    "deadline",
    "location",
    "employment_type",
    "apply_link",
)


@dataclass(frozen=True)
class Opportunity(Finding):
    """One opportunity: what it is, and how it compares to the user.

    Inherits message_id / sender / subject / score / signals / role / company
    from Finding. ``role`` doubles as the job title and ``company`` as the
    hiring organisation, so those are not duplicated here.
    """

    # --- extraction -------------------------------------------------
    source: str | None = None
    seniority: str | None = None
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    experience_required: str | None = None
    salary: str | None = None
    deadline: str | None = None
    location: str | None = None
    employment_type: str | None = None
    apply_link: str | None = None
    description: str = ""
    missing_fields: tuple[str, ...] = ()

    # --- matching ---------------------------------------------------
    band: MatchBand = MatchBand.AMBIGUOUS
    match_score: float = 0.0
    confidence: float = 0.0
    matched_skills: tuple[str, ...] = ()
    partial_skills: tuple[str, ...] = ()
    learning_skills: tuple[str, ...] = ()
    missing_skills: tuple[str, ...] = ()
    supporting_projects: tuple[str, ...] = ()
    red_flags: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()

    # --- optional second opinion -------------------------------------
    # A reference to a model's judgement, never a copy of its contents: the
    # judgement keeps its own fields and this keeps the rules' fields.
    #
    # It is held here rather than in a message_id -> judgement map because one
    # email can produce several opportunities - a digest advertising five roles
    # splits into five - so a message id does not identify an opportunity. The
    # object reference is the only association that cannot mix them up.
    #
    # None means no interpretation was attempted. An attempted-but-failed one
    # is an UNCLEAR judgement, which is a different and visible thing.
    judgement: "LLMJudgement | None" = None

    @property
    def title(self) -> str | None:
        """The job title. Stored in Finding.role; named clearly here."""
        return self.role

    @property
    def has_judgement(self) -> bool:
        """Whether a model offered an opinion on this opportunity."""
        return self.judgement is not None

    def headline(self) -> str:
        what = self.title or "Untitled opportunity"
        where = f" at {self.company}" if self.company else ""
        via = f" (via {self.source})" if self.source else ""
        return f"{what}{where}{via}"

    def explain(self) -> str:
        """Full reasoning for this opportunity, as shown to the user.

        Gaps are stated before the fit is sold: missing skills and unknowns
        appear in every explanation where they exist, not only when the
        verdict is negative.
        """
        lines = [f"{self.band.label} - {self.headline()}"]
        lines.append(f"  from      : {self.sender}")

        facts = [
            ("seniority", self.seniority),
            ("experience", self.experience_required),
            ("salary/rate", self.salary),
            ("deadline", self.deadline),
            ("location", self.location),
            ("type", self.employment_type),
        ]
        known = [f"{name}: {value}" for name, value in facts if value]
        lines.append(f"  details   : {'; '.join(known) if known else 'none stated'}")

        if self.matched_skills:
            lines.append(f"  you have  : {', '.join(self.matched_skills)}")
        if self.partial_skills:
            lines.append(f"  partial   : {', '.join(self.partial_skills)} (working level)")
        if self.learning_skills:
            lines.append(
                f"  learning  : {', '.join(self.learning_skills)} "
                f"(in progress - not counted as a match)"
            )
        if self.missing_skills:
            lines.append(f"  missing   : {', '.join(self.missing_skills)}")
        if self.supporting_projects:
            lines.append(f"  evidence  : {', '.join(self.supporting_projects)}")
        if self.red_flags:
            lines.append(f"  red flags : {'; '.join(self.red_flags)}")
        if self.uncertainties:
            lines.append(f"  unknown   : {'; '.join(self.uncertainties)}")
        for reason in self.reasons:
            lines.append(f"  why       : {reason}")

        return "\n".join(lines)


@dataclass(frozen=True)
class OpportunityReport(AnalysisReport):
    """An AnalysisReport that renders ranked opportunities.

    Subclasses rather than replaces AnalysisReport, so an EmailAgent that only
    knows the Milestone 6 contract still handles it correctly.
    """

    top_n: int = 5
    ambiguous: tuple[Opportunity, ...] = ()
    ignored: int = 0

    @property
    def ranked(self) -> tuple[Opportunity, ...]:
        """Confident findings, strongest first (already sorted by the analyzer)."""
        return tuple(f for f in self.findings if isinstance(f, Opportunity))

    def summary(self) -> str:
        """Top N with full reasoning, plus a low-confidence section.

        Ambiguous opportunities are listed rather than dropped: silently
        discarding a possible opportunity is a worse failure than showing a
        short "couldn't tell" list.
        """
        ranked = self.ranked
        if not ranked and not self.ambiguous:
            return (
                f"{self.analyzer}: no opportunities found among "
                f"{self.considered} message(s)."
            )

        lines = [
            f"{self.analyzer}: {len(ranked)} opportunity(ies) and "
            f"{len(self.ambiguous)} unclear among {self.considered} message(s)."
        ]

        shown = ranked[: self.top_n]
        if shown:
            lines.append("")
            lines.append(f"TOP {len(shown)}:")
            for index, opportunity in enumerate(shown, start=1):
                lines.append(f"{index}. {opportunity.explain()}")

        remaining = len(ranked) - len(shown)
        if remaining > 0:
            lines.append(f"({remaining} further ranked opportunity(ies) not shown)")

        if self.ambiguous:
            lines.append("")
            lines.append("NEEDS A LOOK (not enough information to rank):")
            for opportunity in self.ambiguous:
                why = "; ".join(opportunity.uncertainties) or "insufficient detail"
                lines.append(f"  - {opportunity.headline()} - {why}")

        return "\n".join(lines)
