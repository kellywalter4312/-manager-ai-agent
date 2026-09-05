"""The second real LLM provider: OpenAI.

Same shape as ``src/providers/anthropic_provider.py`` on purpose - a second
implementation of the identical ``LLMProvider`` interface, so nothing outside
this file changes because it exists. The Manager still knows only Router; the
Brain still knows only ``LLMProvider``. This is the only place the ``openai``
vendor SDK is allowed to appear.

The same two deliberate timing choices as the Anthropic provider, for the same
reasons:

- The ``openai`` package is imported inside ``_build_client``, not at module
  scope, so the default (null) path - and the anthropic path - never load it.
- The client is constructed on first use, not in ``__init__``, so listing or
  constructing providers never demands a key.

No key is hard-coded here. It arrives from configuration, or - if none is
supplied - from the SDK's own ``OPENAI_API_KEY`` lookup. It never reads the
Anthropic credential or ``Settings.api_key``: that field is documented as
anthropic-specific (``src/providers/factory.py``), and this provider owning
its own credential end to end is the whole point of Phase 1.4's isolation
requirement.
"""

from __future__ import annotations

from typing import Any

from src.providers.base import Completion, LLMProvider, ProviderResponseError
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Kept as a named constant for the same reason AnthropicProvider.DEFAULT_MODEL
# is: upgrading is a one-line change here rather than a search through call
# sites. Settings.openai_model carries the same default independently, so a
# caller that never touches configuration still gets a sensible model.
DEFAULT_MODEL = "gpt-5"

DEFAULT_MAX_TOKENS = 4096


class OpenAIProvider(LLMProvider):
    """Talks to OpenAI through the official ``openai`` SDK."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any | None = None,
    ) -> None:
        """Create a provider.

        ``client`` exists for testing, the same dependency-injection move
        used by every provider, router and assessor in this project: a
        stand-in lets every test run without a key, a network, or the SDK.
        """
        if not model.strip():
            raise ValueError("model must not be empty")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

        self._model = model.strip()
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._client = client

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._get_client().chat.completions.create(
            model=self._model,
            max_completion_tokens=self._max_tokens,
            messages=messages,
        )

        # Everything below treats the response as untrusted input, the same
        # discipline AnthropicProvider._extract_text already lives under: it
        # arrived from another process across a network.
        if response is None:
            raise ProviderResponseError("the OpenAI client returned no response object")

        choices = getattr(response, "choices", None)
        if not choices:
            raise ProviderResponseError("the OpenAI response carried no choices")

        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        message = getattr(choice, "message", None)

        # A safety refusal arrives as an ordinary successful response with a
        # populated ``refusal`` field instead of ``content`` - not as an
        # exception - so it is checked before reading content, exactly as
        # AnthropicProvider checks stop_reason == "refusal" first.
        refusal = getattr(message, "refusal", None) if message is not None else None
        if refusal:
            logger.warning("model declined the request (refusal)")
            text = ""
        else:
            text = self._extract_text(message)

        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            provider=self.name,
            model=self._reported_model(response),
            input_tokens=self._token_count(usage, "prompt_tokens"),
            output_tokens=self._token_count(usage, "completion_tokens"),
        )

    @staticmethod
    def _extract_text(message: Any) -> str:
        """Read the assistant message's text content.

        Unlike Anthropic's block list, a chat completion's content is a
        plain string - but a message with no content at all (an empty,
        non-refusal turn) is emptiness, not corruption, and yields "" the
        same way AnthropicProvider treats absent content.
        """
        if message is None:
            return ""
        content = getattr(message, "content", None)
        if content is None:
            return ""
        if not isinstance(content, str):
            raise ProviderResponseError(
                f"expected message content to be text, got {type(content).__name__}"
            )
        return content

    def _reported_model(self, response: Any) -> str:
        """Which model actually answered, falling back to the one requested."""
        reported = getattr(response, "model", None)
        if isinstance(reported, str) and reported.strip():
            return reported.strip()
        return self._model

    @staticmethod
    def _token_count(usage: Any, field: str) -> int | None:
        """Read one usage counter, or None if missing or nonsensical - the
        identical rule AnthropicProvider._token_count applies, so losing a
        telemetry number never turns a successful completion into an error.
        """
        value = getattr(usage, field, None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        try:
            import openai  # imported lazily - see the module docstring
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "The 'openai' package is required for the openai provider. "
                "Install it with: pip install openai"
            ) from exc

        if self._api_key:
            return openai.OpenAI(api_key=self._api_key)

        # No key configured: let the SDK resolve one itself (OPENAI_API_KEY).
        # It raises a clear error if it cannot find one.
        return openai.OpenAI()
