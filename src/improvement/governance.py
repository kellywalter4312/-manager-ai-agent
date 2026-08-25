"""Turning observations into proposals, and refusing to go one step further.

The Manager coordinates improvement the way he coordinates everything else: he
gathers what was noticed, decides what is worth raising, and hands it to
somebody with the authority to act. He does not act, and this module is where
that stops being a promise and becomes a shape.

    observations  ->  worth raising?  ->  proposal  ->  [ a person ]

The arrow after "proposal" points out of the system. There is no function here
that applies a change, and there is no argument anywhere that turns one on.
That is the difference between a system that improves and a system that
rewrites itself, and it is not a setting.

**What governance is allowed to decide:** whether an observation is worth
raising at all, and how to word the suggestion. That is it. It cannot decide
that something is safe, pre-approved, urgent enough to skip review, or exempt.

**What it can never produce:** a proposal to widen a permission, weaken a test,
remove an audit or modify the system's own limits. Those are refused when the
Proposal is constructed, before governance ever sees them - so this module
cannot argue its way past them either.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.improvement.improvement import (
    ChangeKind,
    ImprovementRecord,
    Observation,
    Proposal,
    RefusedProposal,
    Severity,
)
from src.improvement.observers import observe_system
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Which kind of change a finding suggests considering. Keyed by what was
# observed rather than by who observed it, so a specialist noticing a missing
# tool produces the same kind of suggestion the Manager would.
_SUGGESTS: dict[str, ChangeKind] = {
    "research": ChangeKind.INSTALL_TOOL,
    "coding": ChangeKind.INSTALL_TOOL,
    "reasoning": ChangeKind.INVESTIGATE,
}


def _change_for(observation: Observation) -> ChangeKind:
    """What sort of change this finding points at.

    Falls back to INVESTIGATE rather than guessing at something more specific.
    "Somebody should look at this" is an honest suggestion; naming a fix the
    evidence does not support is not.
    """
    if observation.evidence.get("tool"):
        return ChangeKind.INSTALL_TOOL
    return _SUGGESTS.get(observation.subject, ChangeKind.INVESTIGATE)


class ImprovementBoard:
    """Collects what the system noticed and prepares suggestions about it.

    Holds a record and nothing that could change anything. No file handle, no
    subprocess, no registry, no toolset, no write path of any kind - the board
    reads observations and produces text.
    """

    def __init__(self, record: ImprovementRecord | None = None) -> None:
        self._record = record if record is not None else ImprovementRecord()

    @property
    def record(self) -> ImprovementRecord:
        return self._record

    def review(self, manager: Any) -> tuple[Observation, ...]:
        """Look at the running system and note what stands out.

        A read. It runs the observers, records what they found, and returns it.
        Nothing here decides anything about the system, and running a review
        twice changes nothing except the record of having looked.
        """
        found = observe_system(manager)
        for observation in found:
            self._record.observe(observation)
        return found

    def propose(self, observations: Sequence[Observation] | None = None) -> tuple[Proposal, ...]:
        """Draft suggestions from findings worth raising.

        Informational findings produce nothing. A system that filed a proposal
        for everything it noticed would bury the one thing that mattered, which
        is the same failure as announcing every event.

        A refused proposal is logged and dropped rather than raised: something
        tried to suggest moving a boundary, and the correct outcome is that no
        such suggestion exists to be skimmed and approved.
        """
        findings = (
            tuple(observations)
            if observations is not None
            else self._record.worth_reviewing()
        )

        drafted: list[Proposal] = []
        for observation in findings:
            if not observation.severity.deserves_a_proposal:
                continue
            try:
                proposal = Proposal(
                    title=f"{observation.subject}: {observation.finding[:60]}",
                    rationale=observation.finding,
                    change=_change_for(observation),
                    observations=(observation,),
                    proposed_by=observation.observed_by,
                )
            except RefusedProposal as exc:
                logger.warning("a proposal was refused before it existed: %s", exc)
                continue
            drafted.append(self._record.propose(proposal))
        return tuple(drafted)

    def summarise(self) -> str:
        """One line for a status screen. Counts, never a recommendation."""
        observations = self._record.observations
        impeding = sum(1 for o in observations if o.severity is Severity.IMPEDING)
        return (
            f"{len(observations)} observation(s), {impeding} impeding, "
            f"{len(self._record.proposals)} proposal(s) awaiting a person"
        )


def improvement_is_advisory_only() -> bool:
    """States the boundary as something a test can assert rather than read.

    True, always. It exists so the guarantee is checkable from outside: if a
    future change ever gives this package a way to apply anything, this is the
    assertion that should have to be edited to let it through - and editing it
    is a decision somebody has to make on purpose.
    """
    return True
