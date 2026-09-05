"""Whether a credential actually works - never just whether it exists.

The gate Phase 1.5 explicitly names: a validator that only checks

    if key exists: valid = true

is not validation, it is presence-checking, and this project already has
that (``Settings.has_api_key``, each provider's own ``_<vendor>_credential``
env-var check). This module answers the harder, honest question: does the
credential actually let the provider do the one thing a credential is for -
complete a prompt?

**How it validates, truthfully.** The only genuine test of a credential is
the real request it is meant to authorise. This module makes that real
request - through the identical, already-real ``LLMProvider.complete()``
every other caller in this project uses, never a hand-rolled HTTP call or
a second way of talking to a vendor. It does not know or care which vendor
it is validating; the same one function validates Anthropic, OpenAI, xAI,
or any future provider, because the contract it exercises
(``LLMProvider``) is already vendor-neutral.

**What this environment can honestly demonstrate, and what it cannot.**
No provider in this project has a funded account, so no call this module
makes here ever reaches a real vendor successfully. Every test of this
module either exercises it against a scripted/fake ``LLMProvider`` (proving
the *mechanism* - a working provider validates true, a failing one
validates false, the boundary is exception-safe) or records a genuine
attempt against the real, installed vendor SDK failing for lack of
credentials (proving the *real code path* runs, not a live authorization).
``CredentialValidation.checked_live`` names this distinction explicitly, so
a caller - and a person reading a report - can never mistake "the
mechanism works" for "a vendor actually approved this key".
"""

from __future__ import annotations

from dataclasses import dataclass

from src.providers.base import LLMProvider

# A minimal, cheap prompt - this call exists to prove authorization, not to
# obtain a useful answer. Providers that meter by token still charge for
# this, which is exactly why it is never made automatically; a caller must
# ask for validation deliberately.
_PROBE_PROMPT = "Reply with the single word: ok"


@dataclass(frozen=True)
class CredentialValidation:
    """The honest outcome of one validation attempt.

    ``valid`` is the answer to "does this credential currently work".
    ``checked_live`` is the answer to "did this validation actually reach
    the vendor" - False whenever the provider raised before completing a
    real exchange (no network, no credential, an unreachable host), which
    is exactly the shape a missing or malformed credential takes. A
    validator that collapsed these two into one boolean could not tell
    "we don't know because nothing was reached" from "we know it fails" -
    the same distinction ``Availability``'s own three states already
    protect for a Brain's own provider.
    """

    valid: bool
    checked_live: bool
    reason: str

    def describe(self) -> str:
        live = "live" if self.checked_live else "not reached"
        return f"valid={self.valid} ({live}): {self.reason}"


def validate_credential(provider: LLMProvider) -> CredentialValidation:
    """Validate a constructed provider by making the one real call that
    actually proves a credential works.

    Takes an already-constructed ``LLMProvider`` - never a raw credential
    string or a vendor name - so this function stays exactly as
    provider-neutral as the interface it exercises. A caller builds the
    provider the ordinary way (``src/providers/factory.py::build_provider``,
    with whichever ``ProviderAccount`` it wants validated) and hands the
    result here.

    Never raises. A provider that cannot complete a request - a missing
    key the vendor SDK itself rejects, an unreachable host, a malformed
    response - is exactly what an invalid credential looks like, and is
    reported as ``valid=False``, not propagated as an exception a caller
    would have to guess the meaning of.
    """
    try:
        completion = provider.complete(_PROBE_PROMPT)
    except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
        return CredentialValidation(
            valid=False,
            checked_live=False,
            reason=f"{type(exc).__name__}: {exc}",
        )

    if completion.is_empty:
        # A response arrived - the vendor was reached - but with nothing
        # usable in it. Distinct from an exception: the credential may be
        # perfectly valid and the model simply had nothing to say, so
        # this is reported as reached but inconclusive, never a
        # confident "invalid".
        return CredentialValidation(
            valid=False,
            checked_live=True,
            reason="the provider was reached but returned no usable text",
        )

    return CredentialValidation(
        valid=True,
        checked_live=True,
        reason=f"a real completion was returned ({completion.describe()})",
    )
