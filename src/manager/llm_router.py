"""A Router that asks a language model which agent should handle a task.

It is just another Router implementation, so the Manager neither knows nor
cares that a model is involved - it still calls ``route(task, agents)`` and
receives a RoutingDecision or None.

Design rule: **this router never guesses.** If the model answers with something
that is not a registered agent name, it returns None rather than picking
something plausible. Deciding what to do about that is the caller's job - see
FallbackRouter, which pairs this with CapabilityRouter so the system keeps
working when no model is available.

Two outcomes, deliberately not the same one:

    None                 asked, and the answer was unusable
    RoutingUnavailable   could not ask - the provider failed

Returning None for both is what let an exhausted API account look exactly like
a model with nothing to say. The provider failure is still caught here, so no
vendor exception escapes into the orchestrator; it is simply re-raised as
something a caller can recognise.

That is why the default NullProvider is not a problem: it has no opinion, this
router declines, and the fallback answers exactly as it does today.
"""

from __future__ import annotations

from typing import Sequence

from src.agents.base_agent import BaseAgent
from src.core.contracts import RoutingDecision, Task
from src.manager.prompts import (
    ROUTING_SYSTEM_PROMPT,
    build_routing_prompt,
    parse_routing_answer,
)
from src.manager.router import Router, RoutingUnavailable
from src.providers.base import LLMProvider
from src.providers.availability import Availability, AvailabilityState
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)


class LLMRouter(Router):
    """Routes by asking an LLMProvider to choose from the available agents.

    The provider is injected, so this class works identically with a null
    provider, a scripted test double, or a real model. It depends on the
    LLMProvider *interface* and never on any vendor SDK.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        # UNKNOWN until a call is actually made.
        self._availability = Availability()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def provider_availability(self) -> Availability:
        """What was observed the last time this component used the model.

        Read-only, and the only honest evidence there is. Full Brain claims
        that model reasoning is obtainable; no provider this project speaks to
        publishes a quota endpoint, so an exhausted balance looks exactly like
        a healthy one until a call is made. UNKNOWN until then - a real answer
        rather than a placeholder, because constructing a provider proves
        intent, never availability.

        Recorded here rather than in a wrapper because this is the component
        that actually makes the call, and it already wraps that call in a
        boundary catch. Observing costs nothing extra and works for every
        provider, including a test double nobody thought to wrap.
        """
        return self._availability

    def route(self, task: Task, agents: Sequence[BaseAgent]) -> RoutingDecision | None:
        log = task_logger(logger, task.id)

        if not agents:
            # Nothing to choose from. Returning early avoids spending a model
            # call - which costs money and time - to answer a settled question.
            log.debug("no agents available; not consulting the model")
            return None

        prompt = build_routing_prompt(task, agents)

        try:
            completion = self._provider.complete(prompt, system=ROUTING_SYSTEM_PROMPT)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            # Recorded before the existing handling, which is unchanged.
            # A failure here is the evidence that the model is not usable -
            # an exhausted balance, a bad key, an unreachable host - and it
            # is what stops assess_cognition reporting FULL BRAIN for a
            # model that cannot answer.
            self._availability = Availability(
                state=AvailabilityState.UNAVAILABLE,
                reason=str(exc) or type(exc).__name__,
                error_type=type(exc).__name__,
            )
            # Previously this returned None, which made a failing provider
            # indistinguishable from a model that simply had no opinion. The
            # boundary catch stays - a vendor exception must not escape into
            # the orchestrator - but the outcome is now typed, so whoever
            # handles disappointment can tell the two apart.
            #
            # `from exc` is load-bearing: it preserves the original traceback
            # and message, which is where the actual diagnosis lives (an
            # authentication error and an exhausted balance are both a
            # BadRequestError until you read the text).
            log.exception("provider '%s' failed: %s", self.provider_name, type(exc).__name__)
            raise RoutingUnavailable(
                f"provider '{self.provider_name}' failed: {type(exc).__name__}: {exc}"
            ) from exc


        # A response arrived, so the model is reachable and the account can
        # buy tokens. Whether the *content* is usable is decided below; a
        # garbled body does not mean the model is unavailable.
        self._availability = Availability(
            state=AvailabilityState.AVAILABLE, reason="a call succeeded"
        )
        log.debug("model consulted (%s)", completion.describe())

        valid_names = {agent.spec.name for agent in agents}
        chosen = parse_routing_answer(completion.text, valid_names)

        if chosen is None:
            log.debug(
                "model gave no usable answer (%d chars); declining to route",
                len(completion.text),
            )
            return None

        model_label = completion.model or completion.provider
        return RoutingDecision(
            agent_name=chosen,
            score=1,
            reason=f"selected by {model_label}",
        )
