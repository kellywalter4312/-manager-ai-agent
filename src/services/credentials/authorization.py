"""Whether a credential actually works - the one question construction
cannot answer.

**Why this exists beside the ecosystem registry's own ``validate``.** That
one attempts real *construction* (``ValidationOutcome``: "never a claim about
a live external call"), which proves the backend exists, the SDK is installed
and the credential is present and well-formed. It cannot prove the vendor
would accept it, and it deliberately never tries. The two questions are
genuinely different, and this project has been blocked on the second one for
months: the owner has an Anthropic key with no credit, and no way to ask "does
it work now?" that is not a guess.

The only honest test of a credential is the request it is meant to authorise,
so this makes that request - through the identical, already-real
``LLMProvider.complete()`` every other caller uses, never a hand-rolled HTTP
call. It is vendor-neutral for the same reason: the contract it exercises
already is.

**It is never made automatically.** A metered vendor charges for this call, and
it leaves the machine. It is reached only from the Control Center's
``provider <category> check <backend> [account-id]``, which is a confirmed
administrative write like every other outward action - described first, run
only after an explicit yes, and recorded in the durable audit trail.

**Two booleans, not one.** ``authorized`` answers "does this credential
currently work". ``reached_vendor`` answers "did this check actually get
there" - False whenever the provider raised before completing an exchange (no
network, no credential, an unreachable host), which is exactly the shape a
missing or rejected credential takes. Collapsing them would lose the
difference between "we know it fails" and "we never found out", the same
distinction ``Availability``'s three states protect for a Brain's provider.

Migrated (2026-09-15) from ``src/services/credentials/validation.py``, which
held this mechanism with no caller anywhere in the running system.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.providers.base import LLMProvider

# A minimal, cheap prompt - this call exists to prove authorization, not to
# obtain a useful answer.
PROBE_PROMPT = "Reply with the single word: ok"


@dataclass(frozen=True)
class AuthorizationCheck:
    """The honest outcome of one live authorization check."""

    authorized: bool
    reached_vendor: bool
    reason: str

    def describe(self) -> str:
        where = "reached the vendor" if self.reached_vendor else "never reached the vendor"
        return f"authorized={self.authorized} ({where}): {self.reason}"


def check_authorization(provider: LLMProvider) -> AuthorizationCheck:
    """Make the one real call that proves a credential works.

    Takes an already-constructed ``LLMProvider`` - never a raw credential
    string or a vendor name - so this stays exactly as provider-neutral as
    the interface it exercises. The caller builds the provider the ordinary
    way (``src/providers/factory.py::build_provider`` with whichever
    ``ProviderAccount`` is being checked) and hands the result here.

    Never raises. A provider that cannot complete a request - a key the vendor
    rejects, an unreachable host, a malformed response - is what an unusable
    credential looks like, and is reported, not propagated.
    """
    try:
        completion = provider.complete(PROBE_PROMPT)
    except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
        return AuthorizationCheck(
            authorized=False,
            reached_vendor=False,
            reason=f"{type(exc).__name__}: {exc}",
        )

    if completion.is_empty:
        # A response arrived - the vendor was reached - but with nothing
        # usable in it. The credential may be perfectly good and the model
        # simply had nothing to say, so this is reported as reached and
        # inconclusive, never a confident "unauthorized".
        return AuthorizationCheck(
            authorized=False,
            reached_vendor=True,
            reason="the provider was reached but returned no usable text",
        )

    return AuthorizationCheck(
        authorized=True,
        reached_vendor=True,
        reason=f"a real completion was returned ({completion.describe()})",
    )


def check_speech_authorization(adapter: object) -> AuthorizationCheck:
    """The speech-provider analogue of ``check_authorization`` above -
    closure campaign, 2026-09-16. Found live (not by reading code): the
    Control Center's own ``check`` action refused every category except
    ``llm_provider`` with an honest "no completion call to prove one with"
    message, which was literally true for most categories but not for
    ElevenLabs speech, which already had a real, live, vendor-backed call
    (``probe_authorization``) sitting unused for exactly this purpose.

    Vendor-neutral over whatever adapter is given, the identical spirit
    ``check_authorization`` already keeps for ``LLMProvider`` - reached
    through ``probe_authorization()`` when the adapter has one (a real
    credentialed vendor), and reported honestly, never guessed, when it
    does not (a local/free engine - Windows SAPI, Kokoro, none of these -
    has no credential to check in the first place).
    """
    probe = getattr(adapter, "probe_authorization", None)
    name = getattr(adapter, "name", type(adapter).__name__)
    if probe is None:
        return AuthorizationCheck(
            authorized=False,
            reached_vendor=False,
            reason=f"'{name}' is not a credentialed vendor - it has no authorization to check",
        )
    try:
        count = probe()
    except Exception as exc:  # noqa: BLE001 - deliberate boundary catch, mirrors check_authorization
        return AuthorizationCheck(
            authorized=False,
            reached_vendor=False,
            reason=f"{type(exc).__name__}: {exc}",
        )
    return AuthorizationCheck(
        authorized=True,
        reached_vendor=True,
        reason=f"the vendor returned {count} real voice(s)",
    )
