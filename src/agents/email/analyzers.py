"""Email analyzers - what the EmailAgent actually *does* with messages.

An analyzer takes messages and returns a report. It performs no I/O: it does
not fetch, does not send, and does not know where the messages came from. That
is what makes analysis logic testable with a handful of constructed messages
and no mailbox at all.

``Analyzer`` is an interface for the same reason ``Router`` and ``Assessor``
are: the first implementation here is deterministic (rules and keyword
signals), and a later LLM-backed implementation can replace it without the
EmailAgent changing. Third use of the Strategy pattern in this codebase.

Each analyzer also declares its own ``capabilities``. The EmailAgent folds
those into its AgentSpec, so registering a new analyzer widens what the
existing CapabilityRouter will route to the agent - with no routing changes.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from typing import TYPE_CHECKING

from src.services.email.message import EmailMessage

if TYPE_CHECKING:  # avoids a circular import at runtime
    from src.services.profile.profile import UserProfile


@dataclass(frozen=True)
class Finding:
    """One message an analyzer considered noteworthy, and why."""

    message_id: str
    sender: str
    subject: str
    score: int
    signals: tuple[str, ...]
    role: str | None = None
    company: str | None = None

    def __post_init__(self) -> None:
        if self.score <= 0:
            raise ValueError("Finding.score must be positive")

    def describe(self) -> str:
        """One line, safe to print or log - never includes the message body."""
        what = self.role or "opportunity"
        where = f" at {self.company}" if self.company else ""
        return (
            f"[score {self.score}] {what}{where} - {self.subject!r} "
            f"from {self.sender} (signals: {', '.join(self.signals)})"
        )


@dataclass(frozen=True)
class AnalysisReport:
    """The result of running an analyzer over a set of messages."""

    analyzer: str
    considered: int
    findings: tuple[Finding, ...] = ()

    def __post_init__(self) -> None:
        if self.considered < 0:
            raise ValueError("AnalysisReport.considered must not be negative")

    @property
    def is_empty(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        """Human-readable text, always non-empty.

        Always returning something matters: the Assessor rejects a COMPLETED
        result with empty output, so "found nothing" has to be stated rather
        than represented by silence.
        """
        if self.is_empty:
            return (
                f"{self.analyzer}: no matches among {self.considered} message(s)."
            )
        lines = [
            f"{self.analyzer}: {len(self.findings)} match(es) "
            f"among {self.considered} message(s)."
        ]
        lines.extend(f"  - {finding.describe()}" for finding in self.findings)
        return "\n".join(lines)


class Analyzer(ABC):
    """Turns a set of messages into a report."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used in reports and logs."""

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[str]:
        """Task words that should route to this analyzer."""

    @abstractmethod
    def analyze(self, messages: Sequence[EmailMessage]) -> AnalysisReport:
        """Examine ``messages`` and report what was found."""


# --------------------------------------------------------------------------
# The first analyzer: job / opportunity detection
# --------------------------------------------------------------------------

# Signals are grouped into categories rather than counted individually, so a
# message repeating one word many times does not outrank a message showing
# several independent indicators. The score is "how many *kinds* of evidence",
# which is a far better proxy for "is this really a job email" than raw
# keyword frequency.
_SIGNAL_GROUPS: dict[str, tuple[str, ...]] = {
    "role": (
        "engineer", "developer", "position", "role", "opening", "vacancy",
        "internship", "analyst", "designer", "manager",
    ),
    "hiring": (
        "hiring", "recruiter", "recruitment", "talent", "interview", "apply",
        "application", "candidate", "resume", "cv", "job", "jobs",
        "opportunity", "opportunities",
    ),
    "compensation": (
        "salary", "compensation", "package", "equity", "stipend", "day rate",
    ),
    "deadline": (
        "deadline", "apply by", "closing date", "closes on", "expires",
    ),
    # Structural vocabulary: the labelled fields a posting uses to state its
    # own terms. A well-formed posting can carry no "hiring" language and no
    # salary at all ("Role: X / Company: Y / Required skills: Z") - without
    # this group such a message scores 1 and is silently skipped.
    "structure": (
        "required skills", "job title", "job description", "employment type",
        "seniority", "responsibilities", "qualifications", "contract type",
        "nice to have", "must have", "apply now", "job type",
        # Marketplace listings label their requirements with a bare "Skills:"
        # rather than "Required skills:". Without this a real Freelancer
        # listing carries only one signal group and is skipped entirely.
        "skills",
    ),
}


def _compile(phrase: str) -> re.Pattern[str]:
    """Whole-word match, tolerant of extra whitespace inside a phrase.

    Word boundaries matter: without them "cv" would match inside "recover" and
    every message would look like a job ad.
    """
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    group: tuple(_compile(p) for p in phrases)
    for group, phrases in _SIGNAL_GROUPS.items()
}

# Strip common prefixes before trying to read a role out of a subject line.
_SUBJECT_NOISE = re.compile(r"^\s*(re|fwd|fw|opportunity|job alert)\s*[:\-]\s*", re.IGNORECASE)

# "<role> at <Company>" - the most common shape of a job subject line.
_ROLE_AT_COMPANY = re.compile(
    r"^(?P<role>[\w/\-+#. ]{3,60}?)\s+(?:at|@|with)\s+(?P<company>[\w&.\-' ]{2,60})$",
    re.IGNORECASE,
)


class JobOpportunityAnalyzer(Analyzer):
    """Finds job and opportunity emails using deterministic signal matching.

    Deterministic on purpose: it needs no model, no key and no network, so it
    works in the default configuration and its tests can assert exact results.
    An LLM-backed analyzer can implement the same interface later; this one
    stays as the offline fallback.
    """

    def __init__(
        self,
        min_score: int = 2,
        profile: "UserProfile | None" = None,
        top_n: int = 5,
        interpreter: object | None = None,
        profile_source: "Callable[[], UserProfile | None] | None" = None,
    ) -> None:
        """Detection only without a profile; full analysis with one.

        Passing no profile keeps the Milestone 6 behaviour exactly: detect job
        emails and report them. Passing a profile switches on extraction,
        matching and ranking. Both paths satisfy the same Analyzer interface,
        so nothing upstream has to know which is in use.

        ``interpreter`` is forwarded to analyze_opportunities, which has always
        accepted one. It is added here because that was the missing link: the
        seam existed one layer down and the caller had no way to reach it, so
        the real OpportunityInterpreter could not be supplied from any
        composition root. Optional and defaulting to None, so the deterministic
        pipeline is unchanged when no model is configured.

        Typed loosely on purpose. Importing OpportunityInterpreter here would
        pull the LLM machinery into the detection-only path, which the analyze
        method is careful to avoid - the duck type is ``interpret(opportunity,
        profile)``, and analyze_opportunities already guards it.
        """
        if min_score <= 0:
            raise ValueError("min_score must be positive")
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        self._min_score = min_score
        self._profile = profile
        self._top_n = top_n
        self._interpreter = interpreter
        # Pre-Stage-9 Campaign 1: when given, the live canonical profile
        # (USER.md plus everything taught since), asked on every analysis.
        self._profile_source = profile_source

    @property
    def name(self) -> str:
        return "job_opportunity"

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {"job", "jobs", "opportunity", "opportunities", "role", "roles",
             "hiring", "recruiter", "career", "careers", "vacancy"}
        )

    @property
    def min_score(self) -> int:
        return self._min_score

    @property
    def interpreter(self) -> object | None:
        """The model-backed interpreter, if one was supplied.

        Readable so a test can prove the production composition root actually
        reached this analyzer, rather than proving only that an interpreter can
        be constructed by hand.
        """
        return self._interpreter

    def current_profile(self) -> "UserProfile | None":
        """The profile this analysis would use right now."""
        if self._profile_source is not None:
            return self._profile_source()
        return self._profile

    def analyze(self, messages: Sequence[EmailMessage]) -> AnalysisReport:
        profile = self.current_profile()
        if profile is not None:
            # Imported here so the detection-only path never loads the
            # extraction/matching machinery it does not use.
            from src.agents.email.opportunity_analysis import analyze_opportunities

            return analyze_opportunities(
                messages,
                profile=profile,
                analyzer_name=self.name,
                min_score=self._min_score,
                top_n=self._top_n,
                interpreter=self._interpreter,
            )

        findings: list[Finding] = []

        for message in messages:
            matched = self._matched_groups(message.text)
            score = len(matched)
            if score < self._min_score:
                continue

            role, company = self._extract_role_and_company(message.subject)
            findings.append(
                Finding(
                    message_id=message.id,
                    sender=message.sender,
                    subject=message.subject,
                    score=score,
                    signals=matched,
                    role=role,
                    company=company,
                )
            )

        # Strongest first; ties break on message id so the ordering is total
        # and repeatable rather than dependent on input order.
        findings.sort(key=lambda f: (-f.score, f.message_id))

        return AnalysisReport(
            analyzer=self.name,
            considered=len(messages),
            findings=tuple(findings),
        )

    @staticmethod
    def _matched_groups(text: str) -> tuple[str, ...]:
        return tuple(
            group
            for group, patterns in _COMPILED.items()
            if any(pattern.search(text) for pattern in patterns)
        )

    @staticmethod
    def _extract_role_and_company(subject: str) -> tuple[str | None, str | None]:
        """Best-effort read of the role and company from a subject line.

        Returns None rather than guessing when the shape is not recognised. A
        wrong extraction presented confidently is worse than an honest blank -
        the finding itself is still reported either way.
        """
        cleaned = _SUBJECT_NOISE.sub("", subject).strip()
        match = _ROLE_AT_COMPANY.match(cleaned)
        if not match:
            return None, None

        role = " ".join(match.group("role").split())
        company = " ".join(match.group("company").split()).rstrip(".,")
        return (role or None), (company or None)
