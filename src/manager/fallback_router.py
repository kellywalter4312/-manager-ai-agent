"""A Router that tries one router, then another.

This is the Composite pattern: a Router made of Routers, which is itself a
Router. The Manager cannot tell the difference - it sees one collaborator
satisfying one interface, exactly as before.

Why it exists: LLMRouter deliberately declines rather than guessing when a
model is missing, broken or unhelpful. Something has to decide what happens
next, and putting that decision in its own tiny class keeps both parts honest -
LLMRouter asks a model, FallbackRouter handles disappointment.

The result is graceful degradation. With the default NullProvider the LLM leg
always declines and CapabilityRouter always answers, so the system behaves
today exactly as it did before any of this existed.

**Graceful is not the same as silent.** Three different disappointments arrive
here and they are no longer treated alike:

    None                  the model was asked and had no usable opinion
    RoutingUnavailable    the model could not be reached at all
    any other Exception   the primary router is broken

Only the first is ordinary. The other two mean something is wrong that nobody
would otherwise notice, because the fallback answers so well that the system
looks healthy from the outside - the same routing decisions, the same results,
no errors. An exhausted API balance could run for weeks that way. So a failure
still falls back (availability is the point of this class), but it is logged at
a level that carries, recorded on the router for callers who want to inspect
rather than grep, and written into the reason of the decision it produced.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from src.agents.base_agent import BaseAgent
from src.core.contracts import RoutingDecision, Task
from src.manager.router import Router, RoutingUnavailable
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Degradation:
    """A record of the primary router failing, kept for the caller to read.

    Frozen and small, for the same reason RoutingDecision is: it is evidence
    about something that already happened, and evidence that can be edited
    after the fact is not worth keeping.

    ``detail`` deliberately holds the exception's *text*, not the exception
    object. The text is what distinguishes an exhausted balance from a
    revoked key - both arrive as the same exception type - and holding the
    string rather than the object keeps a traceback and its frames from being
    retained indefinitely on a long-lived router.
    """

    task_id: str
    error_type: str
    detail: str
    expected: bool

    def describe(self) -> str:
        kind = "unavailable" if self.expected else "unexpected error"
        return f"llm {kind}: {self.error_type}"


class FallbackRouter(Router):
    """Asks ``primary`` first and falls back to ``fallback`` if it declines."""

    def __init__(self, primary: Router, fallback: Router) -> None:
        self._primary = primary
        self._fallback = fallback
        self._last_degradation: Degradation | None = None

    @property
    def primary(self) -> Router:
        return self._primary

    @property
    def fallback(self) -> Router:
        return self._fallback

    @property
    def last_degradation(self) -> Degradation | None:
        """The most recent primary failure, or None if the last route was clean.

        Additive: nothing that used this class before needs to know it exists.
        It is here so a caller can *ask* whether the model leg is working
        instead of reading log output, which is the difference between an
        outage that can be surfaced and one that has to be noticed.

        Cleared by any route() that does not fail, so it always describes the
        latest attempt rather than accumulating history. This router is not a
        metrics store, and pretending otherwise would invite someone to rely
        on it as one.
        """
        return self._last_degradation

    def route(self, task: Task, agents: Sequence[BaseAgent]) -> RoutingDecision | None:
        log = task_logger(logger, task.id)
        primary_name = type(self._primary).__name__
        degradation: Degradation | None = None

        try:
            decision = self._primary.route(task, agents)
        except RoutingUnavailable as exc:
            # Expected in the sense that we know how it happens and have a
            # plan for it - not in the sense that it is fine. WARNING, because
            # the system is about to carry on looking perfectly healthy.
            degradation = Degradation(
                task_id=task.id,
                error_type=type(exc.__cause__ or exc).__name__,
                detail=str(exc),
                expected=True,
            )
            log.warning(
                "%s unavailable (%s); falling back to %s - "
                "routing is no longer model-driven",
                primary_name,
                degradation.error_type,
                type(self._fallback).__name__,
            )
            decision = None
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            # A broken primary must not take the whole system down when a
            # working alternative is sitting right here. Distinct from the
            # branch above: this one nobody anticipated, so it keeps the
            # traceback and the louder level.
            degradation = Degradation(
                task_id=task.id,
                error_type=type(exc).__name__,
                detail=str(exc),
                expected=False,
            )
            log.exception("%s raised %s; falling back", primary_name, type(exc).__name__)
            decision = None

        self._last_degradation = degradation

        if decision is not None:
            return decision

        if degradation is None:
            # The ordinary path: asked, no opinion, nothing wrong.
            log.debug(
                "%s declined; falling back to %s",
                primary_name,
                type(self._fallback).__name__,
            )

        return self._annotate(self._fallback.route(task, agents), degradation)

    @staticmethod
    def _annotate(
        decision: RoutingDecision | None, degradation: Degradation | None
    ) -> RoutingDecision | None:
        """Mark a decision that was reached only because the primary failed.

        The note goes in ``reason`` rather than in a new field on
        RoutingDecision: the reason already exists to explain what drove a
        choice, and "the model was unreachable" is exactly that. Reusing it
        keeps the contract untouched, and means the note appears wherever
        routing is already reported - including the Manager's own log line -
        without another component learning that any of this happened.
        """
        if decision is None or degradation is None:
            return decision
        return replace(decision, reason=f"{decision.reason} [{degradation.describe()}]")
