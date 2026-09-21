"""The model-provider contract.

This is the same abstraction move made three times already in this codebase -
BaseAgent for agents, Router for routing, Assessor for assessment - now applied
to model access. Anything that wants text from a language model depends on this
interface, never on a vendor SDK.

Why it matters here: the entire orchestrator can be built, tested and demoed
without any model at all, and a real backend becomes an additive change rather
than a rewrite. Nothing under src/manager/ imports this module, so the Manager
stays provider-agnostic by construction rather than by discipline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Completion:
    """What a provider returns: the text, plus who produced it.

    Lives here rather than in src/core/contracts.py on purpose. The core
    contracts describe tasks and agents - concepts that exist whether or not a
    language model is ever involved. A Completion is a provider-domain concept,
    so keeping it in this package stops the core from learning that LLMs exist.

    The metadata fields are optional because not every provider can supply
    them: NullProvider has no model and no token counts.
    """

    text: str
    provider: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("Completion.provider must not be empty")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"Completion.{name} must not be negative")

    @property
    def is_empty(self) -> bool:
        """True when the model returned nothing usable."""
        return not self.text.strip()

    def describe(self) -> str:
        """A short summary safe to log.

        Deliberately excludes ``text``. Completions routinely end up in log
        files, and the generated text may restate task content that should not
        be duplicated there. Callers that want the text must ask for it.
        """
        parts = [f"provider={self.provider}"]
        if self.model:
            parts.append(f"model={self.model}")
        if self.input_tokens is not None or self.output_tokens is not None:
            parts.append(f"tokens={self.input_tokens or 0}/{self.output_tokens or 0}")
        parts.append(f"chars={len(self.text)}")
        return " ".join(parts)


class ProviderResponseError(RuntimeError):
    """Raised when a provider returns a response it cannot make sense of.

    A sibling of ProviderConfigurationError in the factory, and deliberately
    additive: the LLMProvider contract and Completion are unchanged, so every
    existing provider and every existing caller keeps working untouched.

    It exists because model output is untrusted, and that distrust has to start
    at the wire. A vendor response whose shape is wrong - content that is not a
    list of blocks, a block that claims to be text and carries none - must not
    surface deep inside the orchestrator as a raw AttributeError or TypeError
    from somebody else's SDK.

    Subclasses RuntimeError so it travels the path that already exists: the
    routers and brains wrap ``complete()`` in a boundary catch and degrade to
    "no view". A garbled response is exactly that - the model said nothing
    usable - and it is reported rather than quietly rendered as empty text,
    which would be indistinguishable from a model that genuinely had no answer.
    """


class LLMProvider(ABC):
    """A source of text completions.

    The interface is deliberately narrow: one method, plain strings in and out.
    A narrow interface is cheap to implement, which is what makes alternative
    implementations (a null one, a fake one, a real one) practical.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and configuration, e.g. "null"."""

    @abstractmethod
    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        """Return a Completion for ``prompt``.

        ``system`` is an optional instruction describing the role the model
        should adopt - the "Give it an identity" step of the AGENT framework.
        It is keyword-only so call sites read unambiguously at a glance.

        Implementations must raise ValueError on an empty prompt rather than
        silently sending nothing.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


def repeats_request(answer: str, question: str) -> bool:
    """Whether a model's "answer" is only the request said back (the same
    words, or the same words with a couple added) - no answer at all."""
    def words(text: str) -> "list[str]":
        return [w for w in "".join(ch.lower() if ch.isalnum() else " " for ch in str(text)).split()]

    said, asked = words(answer), words(question)
    return bool(asked) and (said == asked or (len(said) <= len(asked) + 2 and set(said) <= set(asked)))
