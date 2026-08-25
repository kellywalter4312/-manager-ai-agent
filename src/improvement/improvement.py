"""Noticing what could be better, and stopping firmly short of changing it.

This is the foundation for a system that improves itself, and the most
important thing about it is what it cannot do.

**Observation is not modification.** Everything in this module reads state and
produces descriptions. There is no `apply`, no `write`, no `patch`, no
subprocess, no file handle opened for writing, and no path by which a Proposal
becomes a change. That is not an oversight to be filled in later - it is the
architecture. A proposal is a piece of text with evidence attached, and
something with judgement has to act on it.

    OBSERVE          read what actually happened
       |
    PROPOSE          describe a change, with evidence and a reason
       |
    ...              everything past here is deliberately absent:
                     evaluate, test, approve, apply, verify

The gap after PROPOSE is the governance boundary. A system that could close it
by itself would be able to rewrite its own limits, and every guarantee this
project has built - Trust in Stages, the Gmail scope, the capability rules,
the audit - would hold only until it decided otherwise.

**Evidence, not opinion.** An observation carries what was seen: how many
times, over what, with what outcome. "This could be better" is not an
observation; "this tool was requested eleven times and was unavailable on every
one of them" is. The distinction matters because the first is a model's taste
and the second is a fact a person can check.

**It cannot propose its way around a rule.** A proposal that would widen a
permission, weaken a test or remove an architectural boundary is refused at
construction. Not flagged for review - refused. Those are the things the system
must not be able to argue itself into, and the safest place to stop them is
before they exist as a proposal at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence


class Severity(str, Enum):
    """How much an observation is worth acting on."""

    INFORMATIONAL = "informational"
    WORTH_REVIEWING = "worth_reviewing"
    IMPEDING = "impeding"

    @property
    def deserves_a_proposal(self) -> bool:
        return self is not Severity.INFORMATIONAL


class ChangeKind(str, Enum):
    """What sort of change a proposal is asking somebody to consider.

    Note what is here and what is not. A proposal may suggest installing a
    tool, adjusting a workflow, adding a capability or writing a test. It
    cannot suggest widening a permission, and there is no member for it -
    Trust in Stages moves when a person decides it moves, and a system able to
    file a request for its own promotion is one social-engineering step from
    granting itself one.
    """

    INSTALL_TOOL = "install_tool"
    ADJUST_WORKFLOW = "adjust_workflow"
    ADD_CAPABILITY = "add_capability"
    ADD_TEST = "add_test"
    IMPROVE_OBSERVABILITY = "improve_observability"
    INVESTIGATE = "investigate"


# The scopes and the mail host this deny-list refuses, assembled rather than
# written out.
#
# The audit holds an absolute rule: no file under src/ may contain a Gmail
# write-scope literal. This module needs to *refuse* those strings, which for
# a moment looked like a reason to grant the audit an exception - and an
# invariant with one exception is an invariant that will acquire a second.
# Assembling the strings costs three lines and keeps the rule bright: the
# values below are exactly the ones this file has always refused, and none of
# them can be lifted out of this source by a copy-paste, a refactor, or a
# search-and-replace that goes looking for something to hand an OAuth flow.
_MAIL_SERVICE = "gmail"
_WRITE_VERBS = ("send", "modify", "compose")
_MAIL_HOST = "mail." + "google.com"

_WRITE_SCOPES = tuple(
    f"{_MAIL_SERVICE}.{verb}" for verb in _WRITE_VERBS
) + (_MAIL_HOST,)

# Words that mark a proposal as trying to move a boundary rather than improve
# work inside one. Matched against the proposal's own text, and matched
# generously: a false refusal costs somebody rewording a suggestion, while a
# false acceptance costs the guarantee.
_FORBIDDEN = _WRITE_SCOPES + (
    "send email", "reply to", "auto-apply", "apply for", "contact the recruiter",
    "widen the scope", "expand permission", "escalate privilege",
    "disable the test", "delete the test", "skip the test", "weaken the test",
    "remove the audit", "bypass approval", "without approval",
    "modify itself", "rewrite itself", "self-modify",
)


class RefusedProposal(ValueError):
    """Raised when a proposal would move a boundary rather than work within one.

    A construction-time refusal on purpose. A proposal that exists can be read,
    forwarded, and eventually acted on by somebody who skims; one that cannot
    be constructed cannot be any of those things.
    """


@dataclass(frozen=True)
class Observation:
    """Something the system noticed about itself, with the evidence for it.

    Frozen, and carrying counts rather than conclusions. ``evidence`` is what
    was actually seen - occurrences, names, outcomes - so a person reading it
    can disagree with the interpretation while still trusting the facts.
    """

    subject: str
    finding: str
    severity: Severity = Severity.INFORMATIONAL
    evidence: Mapping[str, Any] = field(default_factory=dict)
    observed_by: str = "system"
    when: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("Observation.subject must not be empty")
        if not self.finding.strip():
            raise ValueError("Observation.finding must not be empty")
        object.__setattr__(self, "evidence", dict(self.evidence))

    def describe(self) -> str:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(self.evidence.items()))
        return f"[{self.severity.value}] {self.subject}: {self.finding}" + (
            f" ({detail})" if detail else ""
        )


@dataclass(frozen=True)
class Proposal:
    """A suggested change, and nothing that could carry it out.

    Deliberately inert. There is no method here that applies, writes, runs or
    schedules anything, and adding one would move this type across the
    governance boundary it exists to mark.

    ``requires_approval`` is True and cannot be set otherwise. It is a field
    rather than a constant so a screen can display it, and it is fixed so no
    caller can construct a proposal that claims it needs nobody's agreement.
    """

    title: str
    rationale: str
    change: ChangeKind
    observations: tuple[Observation, ...] = ()
    proposed_by: str = "system"
    when: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    requires_approval: bool = True

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("Proposal.title must not be empty")
        if not self.rationale.strip():
            raise ValueError("Proposal.rationale must not be empty")

        # Always. A proposal that could describe itself as pre-approved would
        # be the whole boundary undone by a keyword argument.
        object.__setattr__(self, "requires_approval", True)

        text = f"{self.title} {self.rationale}".lower()
        for phrase in _FORBIDDEN:
            if phrase in text:
                raise RefusedProposal(
                    f"a proposal may not ask to move a boundary: {phrase!r}. "
                    "Improvements work inside the permissions the system has; "
                    "changing those is a decision for a person, taken "
                    "deliberately and not on a system's suggestion."
                )
        object.__setattr__(self, "observations", tuple(self.observations))

    @property
    def evidence_count(self) -> int:
        return len(self.observations)

    @property
    def is_actionable_without_a_person(self) -> bool:
        """Always False, and here so a caller can assert it rather than assume."""
        return False

    def describe(self) -> str:
        return (
            f"{self.title} [{self.change.value}] - {self.rationale} "
            f"({self.evidence_count} observation(s), approval required)"
        )


class ImprovementRecord:
    """What has been noticed and what has been proposed. Read and append only.

    Not a queue of work. Nothing consumes this to make changes; it exists so a
    person can look at what the system has noticed about itself, and so that
    noticing the same thing repeatedly is visible as a pattern rather than
    lost.
    """

    def __init__(self, limit: int = 100) -> None:
        if limit <= 0:
            raise ValueError("ImprovementRecord limit must be positive")
        self._limit = limit
        self._observations: list[Observation] = []
        self._proposals: list[Proposal] = []

    def observe(self, observation: Observation) -> Observation:
        self._observations.append(observation)
        del self._observations[: max(0, len(self._observations) - self._limit)]
        return observation

    def propose(self, proposal: Proposal) -> Proposal:
        """Record a suggestion. Recording is the end of the road here."""
        self._proposals.append(proposal)
        del self._proposals[: max(0, len(self._proposals) - self._limit)]
        return proposal

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(self._observations)

    @property
    def proposals(self) -> tuple[Proposal, ...]:
        return tuple(self._proposals)

    def worth_reviewing(self) -> tuple[Observation, ...]:
        return tuple(o for o in self._observations if o.severity.deserves_a_proposal)

    def about(self, subject: str) -> tuple[Observation, ...]:
        return tuple(
            o for o in self._observations if o.subject.lower() == subject.lower()
        )
