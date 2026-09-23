"""A SpecialistBrain that reasons by asking a language model.

    LLMSpecialistBrain -> LLMProvider -> Completion -> Insight

Just another SpecialistBrain implementation, so the agent holding it neither
knows nor cares that a model is involved - it still calls ``consider(task,
notes)`` and receives an Insight.

**It reasons about one area of work, and it cannot direct anyone.** That is the
difference from LLMManagerBrain, and it is structural rather than a matter of
prompt wording. Look at what is missing: no roster of agents is ever built,
because ``consider`` is not given one; there is no delegation target to
validate, because an Insight has no field for one; and a response that tries to
smuggle in an ``action`` or ``delegate_to`` key is refused by the parser rather
than trimmed. A specialist that wanted to reassign its work would have nowhere
to write the request.

Deliberately generic. The domain is injected as a sentence at construction, so
this one class serves the mail specialist today and any specialist added later
without either of them appearing in this file. Nothing here knows what an email
is.

Design rule, inherited from LLMRouter and LLMManagerBrain: **this brain never
guesses.** A provider failure, an empty response, or unreadable JSON all become
an Insight with zero confidence that records why - never a plausible-looking
conclusion. That is also why the default NullProvider is fine: it has no
opinion, and this brain reports exactly that.

The provider is injected and only the LLMProvider *interface* is imported. No
vendor SDK appears anywhere in this file.
"""

from __future__ import annotations

from typing import Sequence

from src.agents.brain import Insight, SpecialistBrain
from src.agents.brain_prompts import (
    SPECIALIST_CONVERSE_SYSTEM_PROMPT,
    SPECIALIST_SYSTEM_PROMPT,
    build_consideration_prompt,
    build_conversation_prompt,
    parse_insight_or_none,
)
from src.core.contracts import Task
from src.providers.base import LLMProvider
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.personality import AgentPersonality, personality_prompt_fragment
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)

# What a specialist says about itself when its owner did not say. Neutral on
# purpose - inventing a domain would be inventing a fact about the agent.
DEFAULT_DOMAIN = ""


class LLMSpecialistBrain(SpecialistBrain):
    """Forms a view about one area of work by consulting an LLMProvider."""

    def __init__(
        self,
        provider: LLMProvider,
        domain: str = DEFAULT_DOMAIN,
        *,
        personality: AgentPersonality | None = None,
    ) -> None:
        """The provider is injected - this class never constructs one.

        Same pattern as LLMRouter, LLMManagerBrain and OpportunityInterpreter:
        the component depends on the interface, and the composition root
        decides which implementation. It is what lets this be tested with a
        scripted double and no key, no network and no vendor installed.

        ``domain`` is one sentence describing this specialist's area, supplied
        by whoever builds the agent. It keeps the class reusable across
        specialists without any of them being named here.

        ``personality`` is optional and additive, and reaches only
        ``converse()``'s prompt below - never ``consider()``'s. The same
        reasoning ``LLMManagerBrain.discern_retention`` states for why its
        own prompt stays personality-free applies to ``consider()`` here in
        reverse: an Insight is a judgement about the person's own work,
        read back as evidence, not a reply presented to them - a
        personality's tone has no business shaping it. An open-ended
        answer to the person, by contrast, is exactly what should sound
        like this specialist rather than a generic raw model response.
        """
        self._provider = provider
        # UNKNOWN until a call is actually made.
        self._availability = Availability()
        self._domain = " ".join(str(domain).split())
        self._converse_system_prompt = SPECIALIST_CONVERSE_SYSTEM_PROMPT
        fragment = personality_prompt_fragment(personality)
        if fragment:
            self._converse_system_prompt = f"{SPECIALIST_CONVERSE_SYSTEM_PROMPT}\n\n{fragment}"

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

    @property
    def domain(self) -> str:
        return self._domain

    def consider(self, task: Task, notes: Sequence[str] = ()) -> Insight:
        """Return the model's view of this work, or an honest "no view".

        Never raises. Three separate failures each become a zero-confidence
        Insight recording why: the provider erroring, the provider returning
        nothing, and the provider returning something unreadable.
        """
        log = task_logger(logger, task.id)
        prompt = build_consideration_prompt(task, self._domain, notes)

        try:
            completion = self._provider.complete(prompt, system=SPECIALIST_SYSTEM_PROMPT)
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
            # The same fault-isolation rule the agent applies to its brain and
            # the Manager applies to its agents: a failing dependency degrades
            # this to "no view", it does not propagate upward.
            log.exception(
                "provider '%s' failed while considering: %s",
                self.provider_name,
                type(exc).__name__,
            )
            return Insight.none(
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

        source = f"llm:{completion.model or completion.provider}"

        if completion.is_empty:
            return Insight.none(reason="the model returned no text", source=source)

        insight = parse_insight_or_none(completion.text, source=source)
        log.debug("considered: %s", insight.describe())
        return insight

    def converse(self, task: Task, notes: Sequence[str] = ()) -> Insight:
        """Compose a natural, open-ended answer via the same provider
        ``consider()`` uses - a different prompt, the same call, the
        same honesty rules. See ``SpecialistBrain.converse``'s own
        docstring for the question this answers and why it differs from
        domain consideration.

        Never raises, for the same three reasons ``consider()`` does
        not: a failing provider, an empty response, and (here) nothing
        further to parse - the model's own words are the answer, so
        there is no JSON to fail to read.
        """
        log = task_logger(logger, task.id)
        prompt = build_conversation_prompt(task, notes)

        try:
            completion = self._provider.complete(prompt, system=self._converse_system_prompt)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            self._availability = Availability(
                state=AvailabilityState.UNAVAILABLE,
                reason=str(exc) or type(exc).__name__,
                error_type=type(exc).__name__,
            )
            log.exception(
                "provider '%s' failed while conversing: %s",
                self.provider_name,
                type(exc).__name__,
            )
            return Insight.none(
                reason=f"the provider failed: {type(exc).__name__}: {exc}",
                source=self.name,
            )

        self._availability = Availability(
            state=AvailabilityState.AVAILABLE, reason="a call succeeded"
        )
        log.debug("conversed (%s)", completion.describe())

        if completion.is_empty:
            return Insight.none(reason="the model returned no text", source=self.name)

        source = f"llm:{completion.model or completion.provider}"
        # Full confidence: this is the model's direct, composed answer to
        # the person, not a partial judgement with room to be more sure -
        # the identical stance LLMManagerBrain.converse takes returning
        # action=REPORT unconditionally rather than a graded confidence.
        return Insight(summary=completion.text, confidence=1.0, source=source)

    def analyze_image(self, image_bytes: bytes, prompt: str) -> Insight:
        """Genuine visual reasoning via this brain's own provider, when -
        and only when - that provider genuinely offers it. Mirrors
        ``LLMManagerBrain.analyze_image``'s own honesty rules: a real,
        additive per-provider method, checked via ``hasattr`` and
        refused by name rather than silently answering with a
        fabricated description. Never raises, the same three-outcome
        shape ``consider()``/``converse()`` already hold themselves to.

        Before this, a specialist's own Brain had no vision-analysis
        method at all - a specialist granted ``image_analysis`` could
        never reach a real, vision-capable provider through its own
        reasoning, only the shared local-vision fallback, even with one
        configured and selected.
        """
        analyze = getattr(self._provider, "analyze_image", None)
        if analyze is None:
            return Insight.none(
                reason=(
                    f"the configured provider ('{self.provider_name}') cannot "
                    "perform visual reasoning - no real vision-capable "
                    "provider is currently selected"
                ),
                source=self.name,
            )

        try:
            completion = analyze(image_bytes, prompt)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            self._availability = Availability(
                state=AvailabilityState.UNAVAILABLE,
                reason=str(exc) or type(exc).__name__,
                error_type=type(exc).__name__,
            )
            logger.exception(
                "provider '%s' failed while analyzing an image: %s",
                self.provider_name,
                type(exc).__name__,
            )
            return Insight.none(
                reason=f"the provider failed: {type(exc).__name__}: {exc}",
                source=self.name,
            )

        self._availability = Availability(
            state=AvailabilityState.AVAILABLE, reason="a call succeeded"
        )

        if completion.is_empty:
            return Insight.none(
                reason="the provider returned no visual analysis",
                source=f"llm:{completion.model or completion.provider}",
            )

        source = f"llm:{completion.model or completion.provider}"
        return Insight(summary=completion.text, confidence=1.0, source=source)
