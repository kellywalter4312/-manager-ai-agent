"""Image intelligence techniques - what can be done *to* a real image, and how
two real images compare. One open registry, never a subsystem per technique.

**A technique is data plus one function.** ``ImageTechnique`` names itself
(``technique_id``, ``label``), says what kind of work it is (``purpose``:
transform, enhance, reconstruct - an open label, never enumerated), declares
the words a person uses for it (``words``), and turns real image bytes into
real image bytes (``apply``). Nothing elsewhere knows what "sharpen" or
"deblur" is: the capability matches a request against the registry's words
and runs whatever technique owns them.

**Deconvolution is one entry, not a feature.** ``deblur`` below reconstructs
detail lost to blur with Richardson-Lucy deconvolution - one row among the
others, registered exactly as a future technique would be. A newly developed
reconstruction, super-resolution or computational-imaging method joins in one
of two governed ways, neither of which redesigns anything:

    1. ``register_image_technique(technique)`` - a new implementation (a local
       model, a vendor-backed provider) added in code, then governed like every
       other component (``image_technique.<id>`` in Ecosystem Evolution).
    2. A pipeline, entirely through Ecosystem Evolution with no code at all:
       ``image_technique.<id>`` = ``pipeline:<step>,<step>|<label>|<word>;<word>``
       composes existing techniques ("restore" = denoise, deblur, sharpen).

The owner can also withdraw any technique (``disabled``) the same way. The
registry is rebuilt from the governed values on every call, so an evolution is
live in the running session.

**Evidence, never invention.** Every technique is a deterministic operation on
the pixels it was given. Nothing is hallucinated into an image: a deblurred
photo shows detail recovered from the blur model, and says which model that
was. ``compare_images`` reports measurements (identical bytes, perceptual-hash
distance, pixel difference, where it differs), never a guess at meaning.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Callable, Mapping

TECHNIQUE_COMPONENT_PREFIX = "image_technique."
PIPELINE_PREFIX = "pipeline:"
DISABLED = "disabled"
ENABLED = "enabled"
MAX_OUTPUT_PIXELS = 4096 * 4096


class ImageTechniqueError(ValueError):
    """A plain, person-facing reason a technique could not run."""


@dataclass(frozen=True)
class TechniqueOutput:
    data: bytes
    width: int
    height: int
    detail: str


@dataclass(frozen=True)
class ImageTechnique:
    technique_id: str
    label: str
    purpose: str
    words: "tuple[str, ...]"
    run: "Callable[[object], tuple[object, str]]" = field(repr=False, compare=False)

    def apply(self, image_bytes: bytes) -> TechniqueOutput:
        image = open_image(image_bytes)
        result, detail = self.run(image)
        if result.width * result.height > MAX_OUTPUT_PIXELS:
            raise ImageTechniqueError(f"{self.label} would produce an image larger than {MAX_OUTPUT_PIXELS} pixels")
        return TechniqueOutput(data=png_bytes(result), width=result.width, height=result.height, detail=detail)


def open_image(image_bytes: bytes):
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageTechniqueError(f"that file is not an image this machine can open ({exc})") from exc
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    return image


def png_bytes(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The local techniques this machine can run with no model and no network.
# ---------------------------------------------------------------------------


def _grayscale(image):
    from PIL import ImageOps

    return ImageOps.grayscale(image), "converted to greyscale (luminance only)"


def _sharpen(image):
    from PIL import ImageFilter

    return image.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=2)), "unsharp mask (radius 2, 160%)"


def _denoise(image):
    from PIL import ImageFilter

    return image.filter(ImageFilter.MedianFilter(size=3)), "3x3 median filter - removes speckle while keeping edges"


def _enhance(image):
    from PIL import ImageEnhance, ImageOps

    adjusted = ImageOps.autocontrast(image, cutoff=1)
    if adjusted.mode == "RGB":
        adjusted = ImageEnhance.Color(adjusted).enhance(1.15)
    return adjusted, "auto-contrast (1% clip) and a gentle colour lift"


def _upscale(image):
    from PIL import Image

    resized = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    return resized, f"upscaled 2x with Lanczos resampling ({image.width}x{image.height} -> {resized.width}x{resized.height})"


def _gaussian_psf(shape: "tuple[int, int]", sigma: float):
    import numpy as np

    rows, cols = shape
    y = np.fft.fftfreq(rows)[:, None] * rows
    x = np.fft.fftfreq(cols)[None, :] * cols
    psf = np.exp(-(x ** 2 + y ** 2) / (2.0 * sigma ** 2))
    return psf / psf.sum()


def richardson_lucy(channel, sigma: float, iterations: int = 15, pad: int = 16):
    """Richardson-Lucy deconvolution of one channel (floats in 0..1) under a
    Gaussian blur of ``sigma`` pixels, by FFT convolution. The channel is
    reflect-padded first (an FFT treats the picture as wrapping around, which
    otherwise rings at every border) and the estimate starts from what was
    observed. Parameters were chosen by measurement: at sigma 1.0 and 15
    iterations the restored image was closer to the unblurred original than
    the blurred input for every tested blur strength (1.0-2.5 px) and scene."""
    import numpy as np

    padded = np.pad(channel.astype(np.float64), pad, mode="reflect")
    otf = np.fft.fft2(_gaussian_psf(padded.shape, sigma))
    otf_conj = np.conj(otf)
    observed = np.clip(padded, 1e-6, 1.0)
    estimate = observed.copy()
    for _ in range(iterations):
        blurred = np.real(np.fft.ifft2(np.fft.fft2(estimate) * otf))
        ratio = observed / np.clip(blurred, 1e-6, None)
        estimate *= np.real(np.fft.ifft2(np.fft.fft2(ratio) * otf_conj))
        estimate = np.clip(estimate, 0.0, 1.0)
    return estimate[pad:-pad, pad:-pad]


def _deblur(image, sigma: float = 1.0, iterations: int = 15):
    import numpy as np
    from PIL import Image

    array = np.asarray(image, dtype=np.float64) / 255.0
    channels = [array] if array.ndim == 2 else [array[:, :, index] for index in range(array.shape[2])]
    restored = [richardson_lucy(channel, sigma, iterations) for channel in channels]
    stacked = restored[0] if len(restored) == 1 else np.stack(restored, axis=2)
    result = Image.fromarray(np.uint8(np.clip(stacked * 255.0 + 0.5, 0, 255)), mode=image.mode)
    return result, (
        f"Richardson-Lucy deconvolution, {iterations} iterations, assuming a Gaussian blur of sigma {sigma} px - "
        "detail recovered from that blur model, nothing painted in"
    )


BUILT_IN_TECHNIQUES: "tuple[ImageTechnique, ...]" = (
    ImageTechnique("grayscale", "Black and white", "transform",
                   ("black and white", "grayscale", "greyscale", "monochrome", "grey scale", "gray scale"), _grayscale),
    ImageTechnique("sharpen", "Sharpen", "enhance", ("sharpen", "sharper", "crisper", "crisp up"), _sharpen),
    ImageTechnique("denoise", "Remove noise", "enhance",
                   ("denoise", "de-noise", "remove noise", "reduce noise", "remove the noise", "less grainy", "remove grain"),
                   _denoise),
    ImageTechnique("enhance", "Enhance", "enhance",
                   ("enhance", "auto-enhance", "improve the quality", "improve this", "more contrast",
                    "fix the lighting", "make it pop", "brighten up"), _enhance),
    ImageTechnique("upscale", "Upscale", "transform",
                   ("upscale", "enlarge", "higher resolution", "increase the resolution", "make it bigger"), _upscale),
    ImageTechnique("deblur", "Deblur (deconvolution)", "reconstruct",
                   ("deblur", "de-blur", "unblur", "remove the blur", "remove blur", "reduce blur", "less blurry",
                    "fix the blur", "reconstruct", "clear up", "make it clearer", "unblurry"), _deblur),
)

_REGISTERED: "dict[str, ImageTechnique]" = {}
# Where the running system's governed values come from - bound once by the
# composition (``scripts/control_center.py::manager_tools``) so recognising a
# request and running it read the same, current techniques.
_GOVERNED_SOURCE: "Callable[[], Mapping[str, str]]" = lambda: {}


def bind_governed_values(source: "Callable[[], Mapping[str, str]]") -> None:
    global _GOVERNED_SOURCE
    _GOVERNED_SOURCE = source


def current_techniques() -> "tuple[ImageTechnique, ...]":
    try:
        values = dict(_GOVERNED_SOURCE())
    except Exception:  # noqa: BLE001 - an unreadable config still leaves the built-in techniques
        values = {}
    return image_techniques(values)


def register_image_technique(technique: ImageTechnique) -> None:
    """Add a newly developed technique - the code half of the governed path;
    it is then enabled, disabled or composed through Ecosystem Evolution."""
    _REGISTERED[technique.technique_id] = technique


def _pipeline(technique_id: str, spec: str, known: "Mapping[str, ImageTechnique]") -> "ImageTechnique | None":
    body = spec[len(PIPELINE_PREFIX):]
    steps_part, _sep, rest = body.partition("|")
    label, _sep, words_part = rest.partition("|")
    steps = tuple(step.strip().lower() for step in steps_part.split(",") if step.strip())
    # "_" stands for a space: a governed value travels as one whitespace-free token.
    words = tuple(word.replace("_", " ").strip().lower() for word in words_part.split(";") if word.strip())
    if not steps or not words or any(step not in known or step == technique_id for step in steps):
        return None
    chain = tuple(known[step] for step in steps)

    def run(image):
        details = []
        for step in chain:
            image, detail = step.run(image)
            details.append(f"{step.label}: {detail}")
        return image, "; then ".join(details)

    return ImageTechnique(technique_id, label.strip() or technique_id, "pipeline", words, run)


def validate_technique_value(technique_id: str, value: str) -> "str | None":
    """Why ``value`` is not a governable state for ``technique_id``, or None."""
    value = str(value or "").strip()
    if value in (ENABLED, DISABLED):
        return None
    if value.startswith(PIPELINE_PREFIX):
        known = {t.technique_id: t for t in (*BUILT_IN_TECHNIQUES, *_REGISTERED.values())}
        if _pipeline(technique_id, value, known) is None:
            return (
                "a pipeline needs existing steps and at least one phrase: "
                "pipeline:<step>,<step>|<label>|<phrase>;<phrase> with steps from " + ", ".join(sorted(known))
            )
        return None
    return f"expected '{ENABLED}', '{DISABLED}' or 'pipeline:...'"


def image_techniques(governed: "Mapping[str, str] | None" = None) -> "tuple[ImageTechnique, ...]":
    """Every technique available right now: built in, registered, and composed
    through governance, minus every one the owner disabled."""
    values = dict(governed or {})
    known: "dict[str, ImageTechnique]" = {t.technique_id: t for t in BUILT_IN_TECHNIQUES}
    known.update(_REGISTERED)
    for key, value in values.items():
        if not str(key).startswith(TECHNIQUE_COMPONENT_PREFIX):
            continue
        technique_id = str(key)[len(TECHNIQUE_COMPONENT_PREFIX):]
        if str(value).startswith(PIPELINE_PREFIX):
            composed = _pipeline(technique_id, str(value), known)
            if composed is not None:
                known[technique_id] = composed
    return tuple(
        technique for technique_id, technique in known.items()
        if values.get(TECHNIQUE_COMPONENT_PREFIX + technique_id) != DISABLED
    )


def technique_for_request(text: str, techniques: "tuple[ImageTechnique, ...]") -> "ImageTechnique | None":
    """The technique whose declared phrase the request uses - the longest
    phrase wins, so "remove the blur" beats a shorter word it contains."""
    lowered = " ".join(str(text or "").lower().split())
    best: "tuple[int, ImageTechnique] | None" = None
    for technique in techniques:
        for word in technique.words:
            position = lowered.find(word)
            if position == -1:
                continue
            before = lowered[position - 1] if position > 0 else " "
            after = lowered[position + len(word)] if position + len(word) < len(lowered) else " "
            if before.isalnum() or after.isalnum():
                continue
            if best is None or len(word) > best[0]:
                best = (len(word), technique)
    return best[1] if best is not None else None


# ---------------------------------------------------------------------------
# Comparison - measurements, never a verdict about meaning.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageComparison:
    first_size: "tuple[int, int]"
    second_size: "tuple[int, int]"
    identical_bytes: bool
    hash_distance: int
    mean_difference: float
    changed_fraction: float
    changed_box: "tuple[int, int, int, int] | None"
    first_brightness: float
    second_brightness: float

    @property
    def verdict(self) -> str:
        if self.identical_bytes:
            return "the same file, byte for byte"
        if self.hash_distance <= 4 and self.mean_difference < 4.0:
            return "visually the same picture (re-saved, resized or lightly compressed)"
        if self.hash_distance <= 12 and self.changed_fraction < 0.25:
            return "the same picture with part of it changed"
        return "different pictures"

    def describe(self, first_name: str, second_name: str) -> str:
        lines = [
            f"Comparing '{first_name}' with '{second_name}' - measurements, not a guess:",
            f"- Result: {self.verdict}.",
            f"- Sizes: {self.first_size[0]}x{self.first_size[1]} and {self.second_size[0]}x{self.second_size[1]}.",
            f"- Perceptual-hash distance: {self.hash_distance} of 64 bits (0 means the same overall structure).",
            f"- Average pixel difference: {self.mean_difference:.1f} of 255; "
            f"{self.changed_fraction * 100:.1f}% of the picture differs noticeably.",
        ]
        if self.changed_box is not None and 0 < self.changed_fraction < 0.95:
            left, top, right, bottom = self.changed_box
            lines.append(f"- The differences sit in the region x {left}-{right}, y {top}-{bottom} (on a 256x256 grid).")
        if abs(self.first_brightness - self.second_brightness) >= 8:
            brighter = first_name if self.first_brightness > self.second_brightness else second_name
            lines.append(f"- '{brighter}' is brighter overall ({self.first_brightness:.0f} vs {self.second_brightness:.0f}).")
        return "\n".join(lines)


def _difference_hash(image) -> int:
    from PIL import Image

    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(small.tobytes())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if pixels[row * 9 + col] > pixels[row * 9 + col + 1] else 0)
    return bits


def compare_images(first: bytes, second: bytes) -> ImageComparison:
    import numpy as np
    from PIL import Image

    one, two = open_image(first), open_image(second)
    grid = (256, 256)
    a = np.asarray(one.convert("RGB").resize(grid, Image.Resampling.LANCZOS), dtype=np.float64)
    b = np.asarray(two.convert("RGB").resize(grid, Image.Resampling.LANCZOS), dtype=np.float64)
    difference = np.abs(a - b).mean(axis=2)
    changed = difference > 24.0
    ys, xs = np.nonzero(changed)
    box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if xs.size else None
    return ImageComparison(
        first_size=(one.width, one.height),
        second_size=(two.width, two.height),
        identical_bytes=hashlib.sha256(first).digest() == hashlib.sha256(second).digest(),
        hash_distance=bin(_difference_hash(one) ^ _difference_hash(two)).count("1"),
        mean_difference=float(difference.mean()),
        changed_fraction=float(changed.mean()),
        changed_box=box,
        first_brightness=float(a.mean()),
        second_brightness=float(b.mean()),
    )


# GM-09: images are one technique family among others - governed through the
# one family handler in Ecosystem Evolution (``src/core/techniques.py``).
from src.core.techniques import TechniqueFamily, register_technique_family  # noqa: E402

register_technique_family(TechniqueFamily(
    "image_technique", image_techniques, validate_technique_value,
    "how a real image can be transformed, enhanced or reconstructed",
))
