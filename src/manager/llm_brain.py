"""A ManagerBrain that reasons by asking a language model.

    LLMManagerBrain -> LLMProvider -> Completion -> BrainDecision

It is just another ManagerBrain implementation, so the Manager neither knows
nor cares that a model is involved - it still calls ``deliberate(task, agents,
notes)`` and receives a BrainDecision.

**This class thinks. It cannot act.** That is worth stating plainly, because it
is the property the whole design exists to guarantee. Look at what is imported:
a provider interface, a prompt builder, a parser. There is no registry, no
agent, no router, no mailbox, no socket. When the model answers "delegate to
email_agent", this class has no email_agent to call and no way to obtain one -
it returns a BrainDecision saying so and stops. Acting on that recommendation
is the orchestration layer's job, and the orchestration layer may decline.

That separation is not politeness. A component that both decides and executes
can be talked into executing by whatever it reads; one that can only describe
what it would do cannot. The prompt says the same thing, so the model's picture
of itself matches the code's.

Design rule, inherited from LLMRouter: **this brain never guesses.** A provider
failure, an empty response, unreadable JSON or a hallucinated agent name all
become an UNKNOWN decision that records why, never a plausible-looking
recommendation. An honest "I don't know" is worth more than a confident guess,
and the default NullProvider therefore works fine: it has no opinion, and this
brain reports exactly that.

The provider is injected and only the LLMProvider *interface* is imported. No
vendor SDK appears anywhere in this file.
"""

from __future__ import annotations

from typing import Sequence

from src.core.contracts import AgentSpec, Task
from src.manager.brain import BrainDecision, ManagerBrain
from src.manager.brain_prompts import (
    BRAIN_SYSTEM_PROMPT,
    build_deliberation_prompt,
    parse_decision_or_unknown,
)
from src.providers.base import LLMProvider
from src.providers.availability import Availability, AvailabilityState
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)


class LLMManagerBrain(ManagerBrain):
    """Reasons about what should happen next by consulting an LLMProvider."""

    def __init__(self, provider: LLMProvider) -> None:
        """The provider is injected - this class never constructs one.

        Same pattern as LLMRouter, OpportunityInterpreter and the mailbox: the
        component depends on the interface, and the composition root decides
        which implementation. It is what lets the entire brain be tested with a
        scripted double and no key, no network and no vendor installed.
        """
        self._provider = provider
        # UNKNOWN until a call is actually made.
        self._availability = Availability()

    @property
    def name(self) -> str:
        return f"llm:{self._provider.name}"

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

    def deliberate(
        self,
        task: Task,
        agents: Sequence[AgentSpec] = (),
        notes: Sequence[str] = (),
    ) -> BrainDecision:
        """Return the model's recommendation, or an honest "no view".

        Never raises. Three separate failures are each turned into an UNKNOWN
        decision that records why: the provider erroring, the provider
        returning nothing, and the provider returning something that cannot be
        read.
        """
        log = task_logger(logger, task.id)
        prompt = build_deliberation_prompt(task, agents, notes)

        try:
            completion = self._provider.complete(prompt, system=BRAIN_SYSTEM_PROMPT)
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
            # Same fault-isolation rule as ManagerAgent.take_action: a failing
            # dependency degrades this brain to "no view", it does not
            # propagate an exception up through the orchestrator.
            log.exception(
                "provider '%s' failed while deliberating: %s",
                self.provider_name,
                type(exc).__name__,
            )
            return BrainDecision.no_view(
                reason=f"the provider failed: {type(exc).__name__}: {exc}",
                source=self.name,
            )


        # A response arrived, so the model is reachable and the account can
        # buy tokens. Whether the *content* is usable is decided below; a
        # garbled body does not mean the model is unavailable.
        self._availability = Availability(
            state=AvailabilityState.AVAILABLE, reason="a call succeeded"
        )
        log.debug("model consulted (%s)", completion.describe())

        if completion.is_empty:
            return BrainDecision.no_view(
                reason="the model returned no text", source=self._source(completion)
            )

        # The roster is the set of agents the caller actually supplied. A
        # delegation naming anything else is refused by the parser rather than
        # passed on, so a hallucinated agent name can never reach the
        # orchestrator as a recommendation.
        valid_names = {spec.name for spec in agents}

        decision = parse_decision_or_unknown(
            completion.text,
            valid_names=valid_names,
            source=self._source(completion),
        )
        log.debug("deliberated: %s", decision.describe())
        return decision

    def _source(self, completion) -> str:
        """Provenance for the decision, taken from the completion.

        The model's own JSON is never consulted for this - see
        CALLER_OWNED_FIELDS. A recommendation must always be attributable to
        whatever actually produced it.
        """
        return f"llm:{completion.model or completion.provider}"
