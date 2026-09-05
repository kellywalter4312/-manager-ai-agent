"""Turning a description into a real image file.

Provider-neutral, the identical shape every other provider family in
this project already takes: an abstract contract, an honest null
default, and now two real implementations - a local, deterministic one
and a genuine AI-generation one, swappable behind the identical
contract with nothing about ``MediaProductionTool``, the
``media_production`` capability, or the objective loop changing either
way, the same guarantee a second ``LLMProvider`` already proved for the
Brain.

**The two real implementations, and what each genuinely is.**

``LocalTitleCardImageProvider`` is not, and has never claimed to be, AI
image generation: it is a genuine, local, deterministic renderer that
draws the given text onto a plain background using Pillow - a real
image file, immediately useful for narrated-slideshow-style video, but
a title card, not an illustration.

``PollinationsImageProvider`` (AI media-generation checkpoint) is
genuine AI image generation - a real diffusion model, reached over the
network through pollinations.ai's free, keyless public API. Investigated
and chosen specifically because this environment's hardware (no CUDA
GPU; ~8GB total system RAM) makes running any local diffusion model
impractical - see this project's own hardware check, recorded in
ARCHITECTURE.md, before this provider was written. Requires no
credential of any kind for this tier, which is documented here rather
than silently assumed; a future paid tier or a different AI vendor
would use this project's existing ``ProviderAccount``/``CredentialStore``
architecture exactly as OpenAI/xAI already do, never a second
credential system invented for this one provider.
"""

from __future__ import annotations

import textwrap
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


class ImageError(RuntimeError):
    """Raised when an image could not be produced."""


@dataclass(frozen=True)
class ImageAsset:
    """A real, complete image file's bytes - never a placeholder.

    ``provider``/``model`` (empty string when a provider has none to
    report - ``LocalTitleCardImageProvider`` names itself and nothing
    else) are provenance, the identical role ``Completion.model``
    already plays for a language-model response: evidence of what
    actually produced this asset, never trusted from the prompt or
    invented by this dataclass itself.
    """

    data: bytes = field(repr=False)
    format: str
    width: int
    height: int
    provider: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.data:
            raise ImageError("ImageAsset.data must not be empty")
        if self.width <= 0 or self.height <= 0:
            raise ImageError("ImageAsset dimensions must be positive")


class ImageProvider(ABC):
    """A source of real images from a text description."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used in logs and configuration."""

    @abstractmethod
    def generate(self, prompt: str, *, width: int = 1280, height: int = 720) -> ImageAsset:
        """Return a real image for ``prompt``.

        Implementations must raise ``ImageError`` on an empty prompt or
        a genuine generation failure - never return a blank or
        placeholder image standing in for one that was never produced.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


class NullImageProvider(ImageProvider):
    """The default: no image engine configured."""

    @property
    def name(self) -> str:
        return "null"

    def generate(self, prompt: str, *, width: int = 1280, height: int = 720) -> ImageAsset:
        raise ImageError(
            "no image-generation engine is configured, so I cannot produce "
            "a real image - I would rather say so than invent a placeholder"
        )


# A small, fixed palette so successive title cards in the same production
# are visually distinct without any randomness a test would have to
# tolerate - genuinely deterministic, not merely "looks varied".
_BACKGROUND_PALETTE = (
    (24, 28, 38), (33, 47, 61), (46, 33, 61), (61, 40, 33), (30, 56, 45),
)


class LocalTitleCardImageProvider(ImageProvider):
    """A real, local, deterministic renderer: the given text, word-wrapped
    and centred on a plain coloured background - see this module's own
    docstring for exactly what this is and is not."""

    @property
    def name(self) -> str:
        return "local-title-card"

    def generate(self, prompt: str, *, width: int = 1280, height: int = 720) -> ImageAsset:
        text = prompt.strip()
        if not text:
            raise ImageError("an image prompt must not be empty")

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImageError(
                "the 'Pillow' package is required for local title-card "
                "images. Install it with: pip install Pillow"
            ) from exc

        background = _BACKGROUND_PALETTE[abs(hash(text)) % len(_BACKGROUND_PALETTE)]
        image = Image.new("RGB", (width, height), color=background)
        draw = ImageDraw.Draw(image)

        try:
            font = ImageFont.truetype("arial.ttf", size=48)
        except OSError:
            font = ImageFont.load_default()

        wrapped = textwrap.fill(text, width=32)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center")
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        position = ((width - text_width) / 2, (height - text_height) / 2)
        draw.multiline_text(position, wrapped, font=font, fill=(240, 240, 240), align="center")

        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return ImageAsset(
            data=buffer.getvalue(), format="png", width=width, height=height, provider=self.name
        )


class PollinationsImageProvider(ImageProvider):
    """Real AI image generation via pollinations.ai's free, keyless
    public image endpoint - see this module's own docstring for why
    this vendor was chosen and what it genuinely is.

    ``fetch`` is injectable, the identical dependency-injection seam
    ``AnthropicProvider``/``OpenAIProvider`` already use for their own
    SDK clients: a real network call in production, a controlled double
    in every test - this project's test suite blocks all outbound
    network unconditionally (``tests/conftest.py::_no_network``), so
    nothing here can be verified through pytest with a genuine HTTP
    request; that verification happens live, outside the suite, exactly
    as every unfunded LLM vendor's own real call already does.
    """

    BASE_URL = "https://image.pollinations.ai/prompt"
    DEFAULT_TIMEOUT = 60.0

    def __init__(
        self,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        fetch: "Callable[[str], tuple[bytes, str, str]] | None" = None,
    ) -> None:
        """``model`` optionally names which backend model pollinations
        should route to (its own ``model=`` query parameter); left
        unset, the service picks one itself, reported back afterward
        (see ``_generate`` below) rather than assumed in advance -
        pollinations may change its own default, and this provider must
        never claim a model name it did not actually observe being
        used.
        """
        self._model = model
        self._timeout = timeout
        self._fetch = fetch or self._real_fetch

    @property
    def name(self) -> str:
        return "pollinations"

    def _build_url(self, prompt: str, width: int, height: int) -> str:
        query = {"width": str(width), "height": str(height), "nologo": "true"}
        if self._model:
            query["model"] = self._model
        encoded_prompt = urllib.parse.quote(prompt, safe="")
        return f"{self.BASE_URL}/{encoded_prompt}?{urllib.parse.urlencode(query)}"

    def _real_fetch(self, url: str) -> "tuple[bytes, str, str]":
        request = urllib.request.Request(url, headers={"User-Agent": "AlfredOS/1.0"})
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            # x-model-used is the service's own, real report of which
            # backend model actually generated this image - read here
            # rather than assumed from the ``model`` this provider may
            # have requested, since a request can be honoured by a
            # different model than the one named.
            model_used = response.headers.get("x-model-used", "")
            return response.read(), response.headers.get("Content-Type", ""), model_used

    def generate(self, prompt: str, *, width: int = 1280, height: int = 720) -> ImageAsset:
        prompt = prompt.strip()
        if not prompt:
            raise ImageError("an image prompt must not be empty")

        url = self._build_url(prompt, width, height)
        try:
            data, content_type, model_used = self._fetch(url)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            raise ImageError(
                f"pollinations image generation failed: {type(exc).__name__}: {exc}"
            ) from exc

        if not content_type.startswith("image/"):
            raise ImageError(
                f"pollinations returned non-image content ({content_type or 'unknown'}) - "
                "the service may be unavailable or the request was refused"
            )

        # The response is untrusted input, exactly like a vendor's own
        # completion text - decoded and verified here rather than
        # assumed correct because the content-type header said so.
        try:
            from PIL import Image
            import io

            with Image.open(io.BytesIO(data)) as decoded:
                decoded.verify()
            with Image.open(io.BytesIO(data)) as decoded:
                actual_width, actual_height = decoded.size
                image_format = (decoded.format or "unknown").lower()
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            raise ImageError(
                f"pollinations returned data that could not be decoded as an image: {exc}"
            ) from exc

        return ImageAsset(
            data=data,
            format=image_format,
            width=actual_width,
            height=actual_height,
            provider=self.name,
            model=model_used,
        )
