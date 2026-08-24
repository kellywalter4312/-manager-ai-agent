"""Whether a model can actually be reached, as opposed to configured.

The distinction this module exists to make:

    configured   a provider name, a key, an object that was constructed
    usable       a call to it has actually succeeded

Those are not the same claim, and until now the system made the first and
reported the second. `assess_cognition` read a provider *name* off a brain and
called that FULL BRAIN - so an API key with an exhausted credit balance was
reported as full model reasoning, confidently, forever, right up until someone
asked a question and got nothing.

**Why this cannot be answered by asking.** Anthropic's API exposes no quota or
credit-balance endpoint. There is no call that means "have I any credit left".
You discover an exhausted balance the way you discover anything about a remote
service: you use it, and it tells you. So the only truthful signal available is
an *observed* one, and this module records observations rather than inventing a
lookup that does not exist.

That is also why the recording lives on whatever *made* the call rather than
inside `assess_cognition`. The assessor is a read-only observer - its docstring
promises that nothing is called and no request is ever made - and it must stay
that way. Something has to actually use the model to learn anything about it,
and a brain or a router already wraps its own `complete()` in a boundary catch.
Recording there costs nothing, works for every provider including a test
double, and needs no wrapper applied at every call site.

Three states, deliberately, because "we have not tried" is a real answer and
pretending otherwise is how the original defect happened:

    UNKNOWN       constructed, never called. Nothing is known either way.
    AVAILABLE     a call succeeded. This is the only evidence of usability
                  that exists.
    UNAVAILABLE   a call failed. Carries why, so a person can tell an
                  exhausted balance from a typo in a key.

Note what this module does **not** do. It grants nothing, gates nothing and
decides nothing. It records what happened when the model was last used, and
`assess_cognition` decides what that means for a Brain mode. Availability is
evidence; a mode is a conclusion drawn from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.utils.logging import get_logger

logger = get_logger(__name__)


class AvailabilityState(str, Enum):
    """What is known about whether this provider can be used."""

    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


# Words a provider uses when the problem is billing rather than the request.
# Matched case-insensitively against the error text purely to *describe* the
# failure better in a log or a banner - the state is UNAVAILABLE either way,
# so a phrasing this table does not recognise costs nothing but a vaguer
# message. It must never be used to decide whether something failed.
_BILLING_MARKERS: tuple[str, ...] = (
    "credit balance",
    "insufficient",
    "quota",
    "billing",
    "payment",
    "exceeded your current",
)


@dataclass(frozen=True)
class Availability:
    """What was last observed about a provider. Evidence, not a verdict."""

    state: AvailabilityState = AvailabilityState.UNKNOWN
    reason: str = "not yet used"
    error_type: str | None = None

    @property
    def is_usable(self) -> bool:
        """True only when a call has actually succeeded.

        Deliberately not "not unavailable". UNKNOWN is not usable - it is
        unproven, and treating unproven as usable is the exact mistake this
        module was written to stop.
        """
        return self.state is AvailabilityState.AVAILABLE

    @property
    def is_known_broken(self) -> bool:
        """True when a call has actually failed."""
        return self.state is AvailabilityState.UNAVAILABLE

    @property
    def looks_like_billing(self) -> bool:
        """Whether the last failure reads like an exhausted balance.

        Descriptive only. It changes no state and gates nothing; it exists so
        a person reading a log can tell "you are out of credit" from "your key
        is wrong" without opening a stack trace.
        """
        lowered = self.reason.lower()
        return self.is_known_broken and any(m in lowered for m in _BILLING_MARKERS)

    def describe(self) -> str:
        if self.state is AvailabilityState.AVAILABLE:
            return "verified usable"
        if self.state is AvailabilityState.UNAVAILABLE:
            kind = "no usable credit" if self.looks_like_billing else "unusable"
            return f"{kind}: {self.reason}"
        return "configured but not yet verified"


def availability_of(component: object) -> Availability:
    """What is known about the model behind a component, if anything.

    Duck-typed and forgiving, in the same spirit as `_provider_name` in
    cognition.py: it asks for `provider_availability` or `availability` and
    accepts UNKNOWN when neither exists. A component that cannot report is not
    a component that is broken - it is one nobody has taught to answer, and an
    honest UNKNOWN is the right result.
    """
    for attribute in ("provider_availability", "availability"):
        found = getattr(component, attribute, None)
        if isinstance(found, Availability):
            return found
    return Availability()
