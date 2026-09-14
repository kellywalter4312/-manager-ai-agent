"""Local, evidence-only examination of whether an image may have been edited
or generated - no vendor, key, network or new dependency (Pillow is already a
project dependency).

**Evidence, never a verdict.** Nothing here can prove an image is genuine or
manipulated; a trained forensic model is not available. What is reported is
what the file itself honestly shows:

- the metadata it carries - camera make/model, capture time, and the software
  that last wrote it (an editor such as Photoshop, GIMP or a phone's editor);
- provenance markers written by generators and signing tools - a Content
  Credentials (C2PA/JUMBF) manifest, Stable-Diffusion-style ``parameters`` text,
  or a generator named in the metadata;
- for a JPEG, error-level analysis: re-saving at a known quality and measuring
  how unevenly the image recompresses, cell by cell. A region changed after the
  last save often recompresses differently from the rest - but ordinary
  re-saving, text overlays and sharp edges do too, so it is reported as a
  pattern to look at, never as proof.

Verified against real JPEGs written with and without an edited region and an
editor's Software tag; photographs from a physical camera and outputs of real
generators have not been examined, and nothing here claims they were.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

# Editors and generators as they write themselves into metadata - names of
# tools, never of subjects.
_EDITOR_NAMES = (
    "photoshop", "lightroom", "gimp", "affinity", "pixelmator", "paint.net", "snapseed", "canva",
    "picsart", "facetune", "photopea", "luminar", "capture one", "darktable", "krita", "illustrator",
)
_GENERATOR_NAMES = (
    "dall-e", "dall·e", "openai", "midjourney", "stable diffusion", "stability", "firefly", "imagen",
    "novelai", "leonardo", "ideogram", "flux", "comfyui", "automatic1111", "invokeai", "pollinations",
)
_ELA_QUALITY = 90
_ELA_CELL = 32
# A cell recompressing this many times more than the image's median is uneven.
_ELA_HOT_FACTOR = 3.0


class ImageForensicsError(ValueError):
    """The file could not be examined as an image."""


@dataclass(frozen=True)
class ImageEvidence:
    filename: str
    format: str
    width: int
    height: int
    camera: str = ""
    captured_at: str = ""
    software: str = ""
    editors_named: tuple[str, ...] = ()
    generators_named: tuple[str, ...] = ()
    content_credentials: bool = False
    generation_parameters: bool = False
    ela_available: bool = False
    ela_median: float = 0.0
    ela_peak: float = 0.0
    ela_hot_fraction: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def uneven_recompression(self) -> bool:
        return self.ela_available and self.ela_hot_fraction > 0 and self.ela_peak >= _ELA_HOT_FACTOR * max(self.ela_median, 0.5)

    def describe(self) -> str:
        lines = [f"Here is what I can check in '{self.filename}' - evidence, not a verdict:"]
        lines.append(f"- File: {self.format} image, {self.width}x{self.height}.")
        if self.camera or self.captured_at:
            captured = f", captured {self.captured_at}" if self.captured_at else ""
            lines.append(f"- Camera metadata: {self.camera or 'a camera time only'}{captured}.")
        else:
            lines.append("- No camera metadata. Many apps and messengers strip it, so this alone means little.")
        if self.software:
            lines.append(f"- Last written by: {self.software}.")
        if self.editors_named:
            lines.append(f"- An image editor is recorded ({', '.join(self.editors_named)}) - it was at least opened and saved in one.")
        if self.generators_named or self.generation_parameters:
            named = ", ".join(self.generators_named) if self.generators_named else "generation settings"
            lines.append(f"- Generator traces are recorded ({named}) - consistent with an AI-generated image.")
        if self.content_credentials:
            lines.append("- It carries a Content Credentials (C2PA) manifest - its provenance can be checked with a C2PA verifier.")
        if self.ela_available:
            if self.uneven_recompression:
                lines.append(
                    f"- Error-level analysis: {self.ela_hot_fraction:.0%} of the image recompresses much more than the rest "
                    f"(peak {self.ela_peak:.1f} vs median {self.ela_median:.1f}) - a pattern often left where part of an image "
                    "was changed after it was last saved, though text, sharp edges and re-saving can cause it too."
                )
            else:
                lines.append("- Error-level analysis: the image recompresses evenly - no region stands out.")
        for note in self.notes:
            lines.append(f"- {note}")
        lines.append(
            "I can't tell you for certain whether it was manipulated or AI-generated - that needs a trained "
            "forensic model, which isn't available here."
        )
        return "\n".join(lines)


def _names_in(text: str, names) -> tuple[str, ...]:
    lowered = str(text or "").lower()
    return tuple(name for name in names if name in lowered)


def examine_image(path: "str | Path") -> ImageEvidence:
    """Examine one image file and return what it honestly shows."""
    from PIL import Image, ImageChops
    from PIL.ExifTags import TAGS

    target = Path(path)
    try:
        raw = target.read_bytes()
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (OSError, ValueError) as exc:
        raise ImageForensicsError(f"'{target.name}' could not be opened as an image: {exc}") from exc

    exif = {TAGS.get(key, key): value for key, value in (image.getexif() or {}).items()}
    try:
        sub = image.getexif().get_ifd(0x8769)
        exif.update({TAGS.get(key, key): value for key, value in sub.items()})
    except (AttributeError, KeyError, ValueError):
        pass
    make = str(exif.get("Make") or "").strip()
    model = str(exif.get("Model") or "").strip()
    if make and model and model.lower().startswith(make.lower()):
        camera = model
    else:
        camera = " ".join(part for part in (make, model) if part)
    captured = str(exif.get("DateTimeOriginal") or exif.get("DateTime") or "").strip()
    software = str(exif.get("Software") or "").strip()

    text_chunks = " ".join(f"{k} {v}" for k, v in (getattr(image, "text", None) or {}).items())
    info_text = " ".join(f"{k} {v}" for k, v in image.info.items() if isinstance(v, (str, bytes)) and len(str(v)) < 5000)
    metadata_text = " ".join((software, text_chunks, info_text, str(exif.get("ImageDescription") or ""), str(exif.get("Artist") or "")))

    content_credentials = b"c2pa" in raw or b"jumb" in raw
    generation_parameters = "parameters" in (getattr(image, "text", None) or {}) or "prompt" in (getattr(image, "text", None) or {})

    notes: list[str] = []
    ela_available = False
    median = peak = hot_fraction = 0.0
    if (image.format or "").upper() in ("JPEG", "MPO"):
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, "JPEG", quality=_ELA_QUALITY)
        diff = ImageChops.difference(rgb, Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")).convert("L")
        width, height = diff.size
        cells = []
        for top in range(0, height, _ELA_CELL):
            for left in range(0, width, _ELA_CELL):
                region = diff.crop((left, top, min(left + _ELA_CELL, width), min(top + _ELA_CELL, height)))
                histogram = region.histogram()
                count = sum(histogram) or 1
                cells.append(sum(level * n for level, n in enumerate(histogram)) / count)
        if cells:
            ordered = sorted(cells)
            median = ordered[len(ordered) // 2]
            peak = ordered[-1]
            threshold = _ELA_HOT_FACTOR * max(median, 0.5)
            hot_fraction = sum(1 for value in cells if value >= threshold) / len(cells)
            ela_available = True
    else:
        notes.append(f"Error-level analysis applies to JPEG images; this is {image.format or 'an unknown format'}.")

    return ImageEvidence(
        filename=target.name, format=image.format or "unknown", width=image.width, height=image.height,
        camera=camera, captured_at=captured, software=software,
        editors_named=_names_in(metadata_text, _EDITOR_NAMES),
        generators_named=_names_in(metadata_text, _GENERATOR_NAMES),
        content_credentials=content_credentials, generation_parameters=generation_parameters,
        ela_available=ela_available, ela_median=round(median, 2), ela_peak=round(peak, 2),
        ela_hot_fraction=round(hot_fraction, 3), notes=tuple(notes),
    )
