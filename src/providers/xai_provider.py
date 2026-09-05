"""The third real LLM provider: xAI's Grok.

Same shape, same interface, same file-scoping rule as
``src/providers/anthropic_provider.py`` and ``openai_provider.py``: nothing
outside this file changes because it exists.

**Why this imports the ``openai`` package and not a separate ``xai`` one.**
xAI ships no SDK of its own; its own documentation is to point the OpenAI
client at xAI's endpoint (``base_url="https://api.x.ai/v1"``) with an xAI key,
because the wire format is OpenAI-compatible. That is a detail of how one
HTTP client library happens to be reused to speak a compatible protocol - it
is not "copying OpenAI's provider" or a second Brain architecture. XAIProvider
is its own class, implements the same ``LLMProvider`` contract independently,
owns its own credential (``XAI_API_KEY``, never ``OPENAI_API_KEY`` or
``Settings.api_key``), its own model configuration (``Settings.xai_model``),
and its own errors. Nothing in ``src/manager/`` or ``src/providers/base.py``
knows this provider exists, exactly like every other one.
"""

from __future__ import annotations

from typing import Any

from src.providers.base import Completion, LLMProvider, ProviderResponseError
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "grok-4"
DEFAULT_MAX_TOKENS = 4096
BASE_URL = "https://api.x.ai/v1"


class XAIProvider(LLMProvider):
    """Talks to xAI's Grok through the OpenAI-compatible SDK, xAI's own base URL."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any | None = None,
    ) -> None:
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
        return "xai"

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

        if response is None:
            raise ProviderResponseError("the xAI client returned no response object")

        choices = getattr(response, "choices", None)
        if not choices:
            raise ProviderResponseError("the xAI response carried no choices")

        choice = choices[0]
        message = getattr(choice, "message", None)

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
        reported = getattr(response, "model", None)
        if isinstance(reported, str) and reported.strip():
            return reported.strip()
        return self._model

    @staticmethod
    def _token_count(usage: Any, field: str) -> int | None:
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
                "The 'openai' package is required for the xai provider "
                "(xAI's API is accessed through the OpenAI-compatible SDK). "
                "Install it with: pip install openai"
            ) from exc

        if self._api_key:
            return openai.OpenAI(api_key=self._api_key, base_url=BASE_URL)

        # No key configured: let the SDK resolve XAI_API_KEY-shaped defaults
        # itself is not possible (the SDK only knows OPENAI_API_KEY), so a
        # missing key here would silently try the wrong variable. The
        # factory's own credential check (src/providers/factory.py) always
        # runs before this is reached, so in practice this branch is not
        # taken with no key - kept only so construction with an injected
        # test client never requires one.
        return openai.OpenAI(api_key="", base_url=BASE_URL)
