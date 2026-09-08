"""Which cognitive capabilities the Manager actually has right now.

The distinction this module exists to make:

    FULL BRAIN   a language model is available, so the Manager can also
                 deliberate - weigh an ambiguous situation and advise
    HALF BRAIN   no model is available, so the Manager reasons with rules

Neither is a failure state, and the naming is the point. Half Brain is not
"broken" or "stupid": routing, delegation, self-handling, assessment and memory
recall are all deterministic and all still work. The model is an *additional*
cognitive capability layered on top of a system that already reasons. Losing it
removes depth, not function - which is why both modes let the Manager keep
operating and why this module reports a mode rather than an error.

Read-only by construction. Nothing here builds, wires, calls or changes
anything; it observes collaborators that already exist and classifies them.
That is what lets it answer the capability question without ManagerAgent, the
brain contract, the provider contract or any factory being touched.

Why not ManagerAgent.can_reason, which already exists? It answers a different
question - "was a brain injected?" - and gets this one wrong:

    LLMManagerBrain(NullProvider())   can_reason: True    but there is no model

A brain with no model behind it is a reasoning shell. Capability has to be read
through the brain to its provider, which is what happens below.

**Configured is not available.** A provider name, an API key and a
constructed brain prove that somebody intended model reasoning; none of them
proves any is obtainable. Anthropic publishes no quota endpoint, so a balance
with nothing left in it looks exactly like a healthy one right up until a call
is made. Full Brain is therefore reported only after a real reasoning call has
succeeded - the call the system was going to make anyway, never an extra one
made to ask. Until then the honest answer is Half Brain, which is not a
degraded state: every deterministic capability below is still present.

Two sources of model cognition are considered, because either can supply it:

    the brain      MANAGER_BRAIN=llm   - deliberation
    the router     MANAGER_ROUTER=llm  - model-driven agent selection

And one thing can take it away at run time: the degradation recorded by
FallbackRouter in the previous milestone. A provider that is configured but
failing - an exhausted balance, a network fault - is *unavailable*, not a model
that chose to say nothing. Half Brain is the honest description of that state,
and reporting it as such is the whole reason the degradation was made
observable rather than swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.providers.availability import Availability, availability_of

# The provider name meaning "no model". Compared as a string for the same
# reason coherence.py does it: asking the provider registry would couple this
# observer to the thing it observes.
NO_MODEL = "null"

# How far to follow nested routers looking for a provider. FallbackRouter holds
# a primary which may itself be composite; the bound stops a cyclic or
# pathologically nested wiring from hanging the check.
_MAX_ROUTER_DEPTH = 5

# What the Manager can do regardless of whether a model is reachable. This
# tuple is the concrete answer to "is Half Brain crippled?" - it is not.
CORE_CAPABILITIES: tuple[str, ...] = (
    "routing",
    "delegation",
    "self-handling",
    "assessment",
    "memory recall",
)

# What a model adds on top. Absent in Half Brain; nothing in CORE depends on it.
MODEL_CAPABILITIES: tuple[str, ...] = (
    "deliberation",
    "judgement under ambiguity",
    "model-driven routing",
)


class CognitiveMode(str, Enum):
    """How much cognitive capability is available."""

    FULL_BRAIN = "full_brain"
    HALF_BRAIN = "half_brain"

    @property
    def label(self) -> str:
        return {"full_brain": "FULL BRAIN", "half_brain": "HALF BRAIN"}[self.value]

    @property
    def has_model(self) -> bool:
        return self is CognitiveMode.FULL_BRAIN


@dataclass(frozen=True)
class CognitiveProfile:
    """What the Manager can currently think with, and why.

    Frozen like every other contract here: it describes capability at a moment,
    and a caller acting on it must not be able to edit the finding.
    """

    mode: CognitiveMode
    reason: str
    brain_name: str
    brain_provider: str | None = None
    router_provider: str | None = None
    degraded: Any | None = None
    # What was last observed about the model itself, as opposed to what was
    # configured. Carried so a caller can tell an unproven claim from a
    # verified one without re-deriving it - see the `verified` property.
    availability: Availability = Availability()

    @property
    def is_full_brain(self) -> bool:
        return self.mode is CognitiveMode.FULL_BRAIN

    @property
    def verified(self) -> bool:
        """Whether a call to the model has actually succeeded.

        Supporting state, not the mode itself: FULL_BRAIN already implies this,
        because an unverified provider is reported as Half Brain. It is exposed
        so a caller can distinguish *why* a Half Brain is a Half Brain - no
        model configured, one configured but unproven, or one proven broken -
        without re-deriving it from the reason string.
        """
        return self.availability.is_usable

    @property
    def is_degraded(self) -> bool:
        """True when a model was configured but is not currently usable.

        Distinct from simply being Half Brain. A deliberately deterministic
        deployment is Half Brain and perfectly healthy; this one was meant to
        have a model and does not, which is worth someone's attention.
        """
        return self.degraded is not None

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Everything available in this mode - core always, model when present."""
        if self.mode is CognitiveMode.FULL_BRAIN:
            return CORE_CAPABILITIES + MODEL_CAPABILITIES
        return CORE_CAPABILITIES

    @property
    def missing(self) -> tuple[str, ...]:
        """What a model would add. Empty in Full Brain."""
        return () if self.mode is CognitiveMode.FULL_BRAIN else MODEL_CAPABILITIES

    def describe(self) -> str:
        """One line for a log or a startup banner. Never carries a credential:
        it reports provider *names*, which are configuration, not secrets.

        Full Brain is only ever reported once a call has actually succeeded,
        so the label itself is the claim - there is no unverified Full Brain to
        annotate. An API key and a constructed object are evidence that someone
        intended model reasoning, not that any is available, and a banner that
        could not tell those apart is how an exhausted credit balance goes
        unnoticed for a week.
        """
        parts = [self.mode.label, f"({self.reason})"]
        if self.is_degraded:
            parts.append("[degraded]")
        return " ".join(parts)


def _provider_name(component: Any, depth: int = 0) -> str | None:
    """The provider a component reasons with, or None if it has none.

    Duck-typed on purpose. It reads ``provider_name``, which LLMRouter and
    LLMManagerBrain already publish, and follows ``primary`` through composite
    routers. Asking by attribute rather than by isinstance means this observer
    does not have to import - or be updated for - every component it inspects.
    """
    if component is None or depth > _MAX_ROUTER_DEPTH:
        return None

    name = getattr(component, "provider_name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()

    return _provider_name(getattr(component, "primary", None), depth + 1)


def _has_model(provider: str | None) -> bool:
    return provider is not None and provider != NO_MODEL


def _chain(component: Any, depth: int = 0) -> list[Availability]:
    """Every availability record reachable through nested composite routers.

    Follows ``primary`` the same way _provider_name does, and with the same
    bound: FallbackRouter holds a primary that may itself be composite, and a
    reading that stopped at the first level would miss the model in a
    router-wrapped-in-a-router wiring - reporting Half Brain for a model that
    had just answered.
    """
    if component is None or depth > _MAX_ROUTER_DEPTH:
        return []
    return [availability_of(component)] + _chain(
        getattr(component, "primary", None), depth + 1
    )


def _availability(brain: Any, router: Any) -> Availability:
    """The strongest evidence any component can offer about the model.

    A known failure outranks everything: if anything has actually been told the
    model is unusable, that is the fact, and a second component that has simply
    never tried does not soften it. A verified success outranks silence for the
    same reason in the other direction.
    """
    found = _chain(brain) + _chain(router)

    for candidate in found:
        if candidate.is_known_broken:
            return candidate
    for candidate in found:
        if candidate.is_usable:
            return candidate
    return Availability()


def assess_cognition(brain: Any, router: Any = None) -> CognitiveProfile:
    """Classify the cognitive capability of an assembled Manager.

    Takes the collaborators rather than the Manager so it can be used on a
    brain alone, on a pair before a Manager exists, or on a live one via
    ``assess_manager``. Nothing is called on either argument - no deliberation
    is triggered, no provider is invoked, and so no request is ever made.

    Order matters. Degradation is checked first because a model that is
    configured but unreachable is *not* available, however well configured it
    looks, and calling that Full Brain would reintroduce exactly the blindness
    the previous milestone removed.
    """
    brain_name = str(getattr(brain, "name", "") or "unknown")
    brain_provider = _provider_name(brain)
    router_provider = _provider_name(router)
    degraded = getattr(router, "last_degradation", None)

    configured = [p for p in (brain_provider, router_provider) if _has_model(p)]

    # What the model itself last did, from whichever component has been used.
    # The router's degradation record only ever caught the router's failures;
    # a brain whose provider ran out of credit reported nothing at all, and the
    # system went on calling itself Full Brain.
    availability = _availability(brain, router)

    if degraded is not None and configured:
        detail = getattr(degraded, "error_type", None) or "provider failure"
        return CognitiveProfile(
            mode=CognitiveMode.HALF_BRAIN,
            reason=f"the model is configured but unavailable: {detail}",
            brain_name=brain_name,
            brain_provider=brain_provider,
            router_provider=router_provider,
            degraded=degraded,
            availability=availability,
        )

    # A model that has been tried and failed is unavailable, whatever the
    # configuration says. Checked before anything else for the same reason the
    # degradation check is: a configured-but-unusable model must never be
    # reported as full reasoning.
    #
    # General weakness-audit repair: `degraded` here used to stay whatever
    # the router-only check above left it as - None whenever the failure
    # was the *brain's* own (an LLMManagerBrain's converse()/deliberate()
    # call genuinely failing, with no FallbackRouter involved at all).
    # CognitiveProfile.is_degraded ("True when a model was configured but
    # is not currently usable") and Control Center's own RED/AMBER
    # indicator (BrainView.degraded) both read that field, so a brain-level
    # failure - proven live: a scripted provider whose complete() raises -
    # was reported exactly like a healthy, deliberately-Half-Brain
    # deployment: AMBER, "not degraded". `availability` is the genuine
    # broken evidence this branch is keyed on to begin with, and is never
    # None here (the `is_known_broken` check above already proves it is a
    # real Availability record) - so it is the correct fallback whenever no
    # router degradation was separately recorded.
    if availability.is_known_broken and configured:
        return CognitiveProfile(
            mode=CognitiveMode.HALF_BRAIN,
            reason=f"the model is configured but unusable: {availability.describe()}",
            brain_name=brain_name,
            brain_provider=brain_provider,
            router_provider=router_provider,
            degraded=degraded if degraded is not None else availability,
            availability=availability,
        )

    if configured and availability.is_usable:
        via = ", ".join(sorted(set(configured)))
        return CognitiveProfile(
            mode=CognitiveMode.FULL_BRAIN,
            reason=f"model reasoning verified usable via {via}",
            brain_name=brain_name,
            brain_provider=brain_provider,
            router_provider=router_provider,
            availability=availability,
        )

    if configured:
        # Configured, and never yet proven. **This is Half Brain**, and that is
        # the whole correction: a key that exists, a model name that parses and
        # an LLMManagerBrain that was constructed are evidence that somebody
        # intended model reasoning, not that any is available. Anthropic
        # publishes no quota endpoint, so an exhausted balance is
        # indistinguishable from a healthy one until a call is made - and a
        # mode that cannot tell those apart is a label, not a fact.
        #
        # Nothing here makes a request to find out. The evidence is the real
        # reasoning call the system was going to make anyway; until it happens
        # the honest answer is that full reasoning is unproven, and the
        # deterministic capabilities below are what the Manager actually has.
        via = ", ".join(sorted(set(configured)))
        return CognitiveProfile(
            mode=CognitiveMode.HALF_BRAIN,
            reason=(
                f"model configured via {via} but not yet verified usable; "
                "reasoning deterministically until a call succeeds"
            ),
            brain_name=brain_name,
            brain_provider=brain_provider,
            router_provider=router_provider,
            availability=availability,
        )

    # No model anywhere. Two shapes of this, reported differently because they
    # are different mistakes - or, in the first case, not a mistake at all.
    if brain_provider is None and router_provider is None:
        reason = "no model is configured; reasoning deterministically"
    else:
        reason = "a reasoning component is configured but no model backs it"

    return CognitiveProfile(
        mode=CognitiveMode.HALF_BRAIN,
        reason=reason,
        brain_name=brain_name,
        brain_provider=brain_provider,
        router_provider=router_provider,
    )


def assess_manager(manager: Any) -> CognitiveProfile:
    """Convenience wrapper for an assembled ManagerAgent.

    Reads the two public attributes the Manager already exposes. It is a
    read-only observer of a Manager, never a part of one - which is why
    ManagerAgent needs no knowledge that this module exists.
    """
    return assess_cognition(getattr(manager, "brain", None), getattr(manager, "router", None))
