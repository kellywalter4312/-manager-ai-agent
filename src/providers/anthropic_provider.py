"""The first real LLM provider: Anthropic's Claude.

It implements the same LLMProvider interface as NullProvider and nothing else
in the system changes because of it. The Manager still knows only about Router;
the routers still know only about LLMProvider. This file is the single place
where a vendor SDK is allowed to appear.

Two deliberate choices about *when* things happen:

- The ``anthropic`` package is imported inside ``_build_client`` rather than at
  module scope, so the default (null) path never loads a vendor SDK, and the
  rest of the system still runs if the package is not installed at all.
- The client is constructed on first use, not in ``__init__``. Building the
  client requires a credential; deferring it means listing or constructing
  providers never demands a key.

No key is hard-coded here. It arrives from configuration, or - if none is
supplied - from the SDK's own ANTHROPIC_API_KEY lookup.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.providers.base import Completion, LLMProvider, ProviderResponseError
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The current default model. Kept here as a named constant so upgrading is a
# one-line change rather than a search through call sites.
DEFAULT_MODEL = "claude-opus-5"

# Room for the response *and* the model's own reasoning: on this model family
# thinking is on by default and counts against max_tokens, so an over-tight
# limit truncates mid-thought and yields no usable answer.
DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider(LLMProvider):
    """Talks to Claude through the official Anthropic SDK."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any | None = None,
    ) -> None:
        """Create a provider.

        ``client`` exists for testing: injecting a stand-in lets every test in
        this project run without a key, a network, or the SDK. It is the same
        dependency-injection move used for the registry, router and assessor.
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
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def analyze_image(
        self, image_bytes: bytes, prompt: str, *, media_type: str = "image/png"
    ) -> Completion:
        """Real visual reasoning over a real image - genuinely different
        from ``complete()``, never a fabricated extension of it. Claude's
        own Messages API accepts an image content block alongside text in
        the same user turn; this is that real call, nothing simulated.

        Deliberately **not** part of the ``LLMProvider`` contract
        (``base.py``'s own "one method, plain strings in and out" is
        stated as a deliberate choice, not an oversight) - adding an
        image parameter there would force OpenAI/xAI/Null to grow a
        parameter none of them can honour identically, and would touch
        every existing caller of ``complete()`` for a capability only one
        vendor here currently has real support for. This method exists
        beside the contract, reached only by a caller that has already
        confirmed (via ``hasattr``) that this specific provider offers
        it - see ``src/agents/tools/image_analysis.py`` for that caller
        and the honest refusal every other provider still gets.

        Raises on an empty prompt or empty image bytes, the identical
        rule ``complete()`` already holds for an empty prompt.
        """
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if not image_bytes:
            raise ValueError("image_bytes must not be empty")

        import base64

        encoded = base64.b64encode(image_bytes).decode("ascii")
        message = self._get_client().messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": encoded},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
        )

        if message is None:
            raise ProviderResponseError(
                "the Anthropic client returned no message object"
            )

        stop_reason = getattr(message, "stop_reason", None)
        text = "" if stop_reason == "refusal" else self._extract_text(message)

        usage = getattr(message, "usage", None)
        return Completion(
            text=text,
            provider=self.name,
            model=self._reported_model(message),
            input_tokens=self._token_count(usage, "input_tokens"),
            output_tokens=self._token_count(usage, "output_tokens"),
        )

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            # Adaptive thinking with low effort: the model decides how much to
            # reason, and low effort keeps a short classification-style task
            # cheap and fast. Note there is deliberately no temperature/top_p
            # here - this model family rejects sampling parameters outright.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "low"},
        }
        if system:
            request["system"] = system

        message = self._get_client().messages.create(**request)

        # Everything below treats the response as untrusted input. It arrives
        # from another process across a network; assuming its shape here is the
        # same mistake as assuming the model's *content* is well-formed.
        if message is None:
            raise ProviderResponseError(
                "the Anthropic client returned no message object"
            )

        # A safety classifier can decline a request. That arrives as an ordinary
        # successful response with stop_reason "refusal" and empty or partial
        # content - not as an exception - so it must be checked before reading
        # the content, or the caller silently treats a refusal as an answer.
        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "refusal":
            logger.warning("model declined the request (stop_reason=refusal)")
            text = ""
        else:
            text = self._extract_text(message)

        usage = getattr(message, "usage", None)
        return Completion(
            text=text,
            provider=self.name,
            model=self._reported_model(message),
            input_tokens=self._token_count(usage, "input_tokens"),
            output_tokens=self._token_count(usage, "output_tokens"),
        )

    @staticmethod
    def _field(block: Any, field: str) -> Any:
        """Read one field from a content block.

        Blocks normally arrive as SDK objects, but a raw or re-serialised
        response carries plain dicts. Reading both costs one branch and turns a
        silently-empty completion into a correct one.
        """
        if isinstance(block, Mapping):
            return block.get(field)
        return getattr(block, field, None)

    @classmethod
    def _extract_text(cls, message: Any) -> str:
        """Join the text blocks of a response, ignoring every other block type.

        A response is a *list* of typed content blocks, not a string: it may
        also carry thinking or tool-use blocks. Selecting by type rather than
        indexing position keeps this correct as block types are added.

        The two failure modes below are separated deliberately:

        - **No content** (absent or None) is emptiness, not corruption. A
          response can legitimately carry no text - a refusal does exactly
          that - so it yields an empty Completion, as it always has.
        - **Content of the wrong shape** is corruption, and raises. A bare
          string where a block list belongs would otherwise iterate character
          by character, match nothing, and return "" - a garbled response
          wearing the costume of a model with nothing to say.
        """
        content = getattr(message, "content", None)
        if content is None:
            return ""

        if isinstance(content, (str, bytes)) or not isinstance(content, Sequence):
            raise ProviderResponseError(
                "expected a sequence of content blocks, got "
                f"{type(content).__name__}"
            )

        parts: list[str] = []
        for block in content:
            if cls._field(block, "type") != "text":
                continue
            text = cls._field(block, "text")
            if not isinstance(text, str):
                # The block declared itself as text and carries none. Skipping
                # it would drop part of the answer without saying so.
                raise ProviderResponseError(
                    "a text content block carried no text (got "
                    f"{type(text).__name__})"
                )
            parts.append(text)
        return "".join(parts)

    def _reported_model(self, message: Any) -> str:
        """Which model actually answered, falling back to the one requested."""
        reported = getattr(message, "model", None)
        if isinstance(reported, str) and reported.strip():
            return reported.strip()
        return self._model

    @staticmethod
    def _token_count(usage: Any, field: str) -> int | None:
        """Read one usage counter, or None if it is missing or nonsensical.

        Unlike the content, bad usage metadata does not raise. Token counts are
        telemetry: losing one costs a number in a log line, and failing an
        otherwise-good completion over it would trade a real answer for a
        cosmetic detail. Completion rejects negative counts outright, so an
        implausible value is normalised to "unknown" here rather than being
        allowed to turn a successful call into a ValueError.

        The bool guard is not decoration - bool is a subclass of int, and True
        would otherwise be recorded as a token count of 1.
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
            import anthropic  # imported lazily - see the module docstring
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "The 'anthropic' package is required for the anthropic provider. "
                "Install it with: pip install anthropic"
            ) from exc

        if self._api_key:
            return anthropic.Anthropic(api_key=self._api_key)

        # No key configured: let the SDK resolve one itself (ANTHROPIC_API_KEY,
        # or a locally configured credential). It raises a clear error if it
        # cannot find one - better than us inventing a second failure message.
        return anthropic.Anthropic()
