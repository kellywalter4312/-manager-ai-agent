"""Instruction-based image editing - a person's own words turned into an ordered
plan of real, parametric edits, each applied to the pixels and then checked.

    "rotate it 90 degrees clockwise, crop to a square, then add a 12px navy border"
      -> rotate(-90) ; crop(square) ; border(12, navy)

**One vocabulary, open.** An ``EditOperation`` is data plus three functions: a
parser that reads one clause of an instruction into parameters (or declines),
an ``apply`` that edits the image, and a ``verify`` that measures the result and
says whether the edit really happened (the dimensions a crop promised, a
brightness change in the direction asked, a mirror that really is one). Every
technique in ``image_techniques`` (sharpen, deblur, a governed pipeline, one
registered next year) is also a step, matched by its own phrases. Adding an
operation is ``register_edit_operation`` - no parser elsewhere learns it.

**Nothing is example-coded.** Numbers, directions, fractions, aspect ratios,
colours (any colour name or hex code Pillow knows) and quoted text come from the
instruction; nothing about a particular photo or request is written here.

**Honest about what it did not understand.** ``parse_edit_instruction`` returns
the steps it understood *and* every clause it could not read. A caller never
applies half an instruction silently: it asks a Brain to restate the instruction
in this vocabulary (then parses that the same way), or refuses naming what was
not understood. Nothing is invented into a picture: every step is a
deterministic operation, and text is drawn only when the person quoted it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from src.providers.media.image_techniques import (
    MAX_OUTPUT_PIXELS,
    ImageTechnique,
    ImageTechniqueError,
    open_image,
    png_bytes,
    technique_for_request,
)


@dataclass(frozen=True)
class EditStep:
    op: str
    params: "Mapping[str, Any]"
    clause: str

    def to_dict(self) -> dict:
        return {"op": self.op, "params": dict(self.params), "clause": self.clause}


@dataclass(frozen=True)
class EditPlan:
    steps: "tuple[EditStep, ...]"
    not_understood: "tuple[str, ...]" = ()

    @property
    def complete(self) -> bool:
        return bool(self.steps) and not self.not_understood


@dataclass(frozen=True)
class EditOperation:
    op_id: str
    grammar: str  # how the operation is said, shown to a Brain and in refusals
    parse: "Callable[[str], dict | None]" = field(repr=False, compare=False)
    apply: "Callable[[Any, Mapping[str, Any]], tuple[Any, str]]" = field(repr=False, compare=False)
    verify: "Callable[[Any, Any, Mapping[str, Any]], str | None]" = field(repr=False, compare=False)


# ---------------------------------------------------------------------------
# Reading numbers, amounts and colours out of words
# ---------------------------------------------------------------------------

_NUMBER = r"(\d+(?:\.\d+)?)"
_SMALL = re.compile(r"\b(?:slightly|a\s+(?:little\s+)?bit|a\s+touch|a\s+little|somewhat|a\s+tad)\b")
_LARGE = re.compile(r"\b(?:much|a\s+lot|very|way|significantly|dramatically|strongly|heavily)\b")
_FRACTIONS = {"half": 0.5, "third": 1 / 3, "quarter": 0.25, "fourth": 0.25, "two thirds": 2 / 3, "three quarters": 0.75}


def _amount_percent(clause: str, default: float = 25.0) -> float:
    found = re.search(_NUMBER + r"\s*(?:%|percent|per\s*cent)", clause)
    if found:
        return float(found.group(1))
    if _SMALL.search(clause):
        return 12.0
    if _LARGE.search(clause):
        return 50.0
    return default


def _colour(clause: str, default: "tuple[int, int, int] | None" = None) -> "tuple[int, int, int] | None":
    from PIL import ImageColor

    hexed = re.search(r"#[0-9a-f]{3,8}\b", clause)
    if hexed:
        try:
            return ImageColor.getrgb(hexed.group(0))[:3]
        except ValueError:
            pass
    words = re.findall(r"[a-z]+", clause)
    for size in (2, 1):  # "light blue" before "blue"
        for index in range(len(words) - size + 1):
            name = "".join(words[index:index + size])
            try:
                return ImageColor.getrgb(name)[:3]
            except ValueError:
                continue
    return default


def _luminance(image) -> float:
    from PIL import ImageStat

    return ImageStat.Stat(image.convert("L")).mean[0]


def _rgb(image):
    return image.convert("RGB")


# ---------------------------------------------------------------------------
# The operations
# ---------------------------------------------------------------------------


def _parse_rotate(clause: str) -> "dict | None":
    if re.search(r"\bupside\s+down\b", clause) and re.search(r"\b(?:turn|rotate|put)\b", clause):
        return {"degrees": 180.0}
    if not re.search(r"\b(?:rotat\w*|turn|spin)\b", clause):
        return None
    found = re.search(_NUMBER + r"\s*(?:°|degrees?|deg)\b", clause)
    if found:
        degrees = float(found.group(1))
    elif re.search(r"\bquarter\s+turn\b", clause):
        degrees = 90.0
    elif re.search(r"\bhalf\s+turn\b", clause):
        degrees = 180.0
    elif re.search(r"\b(?:rotat\w*)\b.*\b(?:left|right|clockwise|counter-?clockwise|anti-?clockwise)\b", clause):
        degrees = 90.0
    else:
        return None
    counter = bool(re.search(r"\b(?:counter-?\s?clockwise|anti-?\s?clockwise|to\s+the\s+left|left)\b", clause))
    return {"degrees": degrees if counter else -degrees}


def _apply_rotate(image, params):
    degrees = float(params["degrees"])
    fill = (0, 0, 0) if image.mode == "RGB" else 0
    rotated = image.rotate(degrees, expand=True, fillcolor=fill)
    way = "counter-clockwise" if degrees > 0 else "clockwise"
    return rotated, f"rotated {abs(degrees):g}° {way}"


def _verify_rotate(before, after, params):
    degrees = abs(float(params["degrees"])) % 360
    if degrees in (90, 270) and after.size != (before.height, before.width):
        return f"expected {before.height}x{before.width}, got {after.width}x{after.height}"
    if degrees in (0, 180) and after.size != before.size:
        return f"expected {before.width}x{before.height}, got {after.width}x{after.height}"
    return None


def _parse_flip(clause: str) -> "dict | None":
    if not re.search(r"\b(?:flip\w*|mirror\w*)\b", clause):
        return None
    if re.search(r"\b(?:vertical\w*|top\s+to\s+bottom|upside\s+down|up\s+and\s+down)\b", clause):
        return {"axis": "vertical"}
    return {"axis": "horizontal"}


def _apply_flip(image, params):
    from PIL import ImageOps

    if params["axis"] == "vertical":
        return ImageOps.flip(image), "flipped top to bottom"
    return ImageOps.mirror(image), "mirrored left to right"


def _verify_flip(before, after, params):
    from PIL import ImageOps

    expected = ImageOps.flip(before) if params["axis"] == "vertical" else ImageOps.mirror(before)
    return None if expected.tobytes() == after.tobytes() else "the result is not a true mirror image"


def _parse_crop(clause: str) -> "dict | None":
    if not re.search(r"\b(?:crop\w*|trim\w*|cut\s+(?:it\s+)?(?:down|out)|keep\s+only)\b", clause):
        return None
    trim = re.search(r"\b(?:trim|crop|cut)\w*\s+" + _NUMBER + r"\s*(?:px|pixels?)\s+(?:off\s+|from\s+)?(?:the\s+)?(?P<edge>each\s+side|all\s+sides|every\s+side|edges|left|right|top|bottom)?", clause)
    if trim:
        edge = (trim.group("edge") or "each side").split()[0]
        return {"mode": "trim", "pixels": int(float(trim.group(1))), "edge": edge if edge in ("left", "right", "top", "bottom") else "all"}
    ratio = re.search(r"\b(\d+)\s*[:x×]\s*(\d+)\b", clause)
    if ratio and re.search(r"\b(?:aspect|ratio)\b|\d+\s*:\s*\d+", clause):
        return {"mode": "ratio", "ratio": [int(ratio.group(1)), int(ratio.group(2))]}
    if ratio:
        return {"mode": "size", "size": [int(ratio.group(1)), int(ratio.group(2))]}
    if re.search(r"\bsquare\b", clause):
        return {"mode": "ratio", "ratio": [1, 1]}
    region = re.search(r"\b(?P<side>left|right|top|bottom|upper|lower|middle|centre|center|central)\s+(?P<fraction>two\s+thirds|three\s+quarters|half|third|quarter|fourth)\b", clause)
    if region:
        side = {"upper": "top", "lower": "bottom", "centre": "center", "middle": "center", "central": "center"}.get(region.group("side"), region.group("side"))
        return {"mode": "region", "side": side, "fraction": _FRACTIONS[" ".join(region.group("fraction").split())]}
    return None


def _crop_box(image, params):
    width, height = image.size
    mode = params["mode"]
    if mode == "trim":
        n = int(params["pixels"])
        edge = params["edge"]
        left = n if edge in ("all", "left") else 0
        right = width - (n if edge in ("all", "right") else 0)
        top = n if edge in ("all", "top") else 0
        bottom = height - (n if edge in ("all", "bottom") else 0)
        return left, top, right, bottom
    if mode == "ratio":
        rw, rh = params["ratio"]
        if rw <= 0 or rh <= 0:
            raise ImageTechniqueError("an aspect ratio needs two positive numbers")
        target_w = min(width, round(height * rw / rh))
        target_h = min(height, round(target_w * rh / rw))
        left, top = (width - target_w) // 2, (height - target_h) // 2
        return left, top, left + target_w, top + target_h
    if mode == "size":
        cw, ch = (int(v) for v in params["size"])
        if cw > width or ch > height:
            raise ImageTechniqueError(f"can't crop {width}x{height} to {cw}x{ch} - that is larger than the image")
        left, top = (width - cw) // 2, (height - ch) // 2
        return left, top, left + cw, top + ch
    fraction, side = float(params["fraction"]), params["side"]
    if side in ("left", "right"):
        keep = round(width * fraction)
        return (0, 0, keep, height) if side == "left" else (width - keep, 0, width, height)
    if side in ("top", "bottom"):
        keep = round(height * fraction)
        return (0, 0, width, keep) if side == "top" else (0, height - keep, width, height)
    keep_w, keep_h = round(width * fraction), round(height * fraction)
    left, top = (width - keep_w) // 2, (height - keep_h) // 2
    return left, top, left + keep_w, top + keep_h


def _apply_crop(image, params):
    box = _crop_box(image, params)
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        raise ImageTechniqueError("that crop would leave nothing of the image")
    cropped = image.crop(box)
    return cropped, f"cropped to {cropped.width}x{cropped.height} (box {box})"


def _verify_crop(before, after, params):
    box = _crop_box(before, params)
    expected = (box[2] - box[0], box[3] - box[1])
    return None if after.size == expected else f"expected {expected[0]}x{expected[1]}, got {after.width}x{after.height}"


def _parse_resize(clause: str) -> "dict | None":
    if not re.search(r"\b(?:resiz\w*|scal\w*|shrink\w*|enlarg\w*|smaller|bigger|larger|downsiz\w*|size)\b", clause):
        return None
    box = re.search(r"\b(\d+)\s*(?:x|×|by)\s*(\d+)\b", clause)
    if box:
        return {"width": int(box.group(1)), "height": int(box.group(2))}
    width = re.search(r"\b(\d+)\s*(?:px|pixels?)?\s*(?:wide|width|across)\b|\bwidth\s+(?:of\s+|to\s+)?(\d+)", clause)
    height = re.search(r"\b(\d+)\s*(?:px|pixels?)?\s*(?:tall|high|height)\b|\bheight\s+(?:of\s+|to\s+)?(\d+)", clause)
    if width or height:
        return {"width": int(next(g for g in width.groups() if g)) if width else None,
                "height": int(next(g for g in height.groups() if g)) if height else None}
    percent = re.search(_NUMBER + r"\s*(?:%|percent)", clause)
    if percent:
        return {"scale": float(percent.group(1)) / 100}
    for word, factor in (("half", 0.5), ("a third", 1 / 3), ("a quarter", 0.25), ("double", 2.0), ("twice", 2.0), ("triple", 3.0), ("three times", 3.0)):
        if re.search(r"\b" + word + r"\b", clause):
            return {"scale": factor}
    # "bigger"/"smaller" with no size named: a plain doubling or halving.
    if re.search(r"\b(?:bigger|larger|enlarg\w*)\b", clause):
        return {"scale": 2.0}
    if re.search(r"\b(?:smaller|shrink\w*|downsiz\w*)\b", clause):
        return {"scale": 0.5}
    return None


def _target_size(image, params):
    width, height = image.size
    if params.get("scale"):
        return max(1, round(width * params["scale"])), max(1, round(height * params["scale"]))
    w, h = params.get("width"), params.get("height")
    if w and h:
        return int(w), int(h)
    if w:
        return int(w), max(1, round(height * int(w) / width))
    return max(1, round(width * int(h) / height)), int(h)


def _apply_resize(image, params):
    from PIL import Image

    size = _target_size(image, params)
    if size[0] * size[1] > MAX_OUTPUT_PIXELS or min(size) < 1:
        raise ImageTechniqueError(f"a {size[0]}x{size[1]} image is outside what I'll produce")
    return image.resize(size, Image.LANCZOS), f"resized from {image.width}x{image.height} to {size[0]}x{size[1]}"


def _verify_resize(before, after, params):
    expected = _target_size(before, params)
    return None if after.size == expected else f"expected {expected[0]}x{expected[1]}, got {after.width}x{after.height}"


_ADJUST_PROPERTIES = (
    ("brightness", r"\bbright\w*|\blight(?:er|en)\b|\bdark\w*|\bdim(?:mer)?\b|\bexposure\b"),
    ("contrast", r"\bcontrast\w*\b"),
    ("saturation", r"\b(?:de)?saturat\w*|\bcolou?rful\b|\bvivid\b|\bvibrant\b|\bmuted\b|\bwashed[\s-]out\b|\bcolou?r\s+intensity\b"),
)
_DOWN = re.compile(r"\b(?:decreas\w*|reduc\w*|lower\w*|less|dark(?:er|en)|dim\w*|tone\s+down|desaturat\w*|muted|washed[\s-]out|drop|cut|soften)\b")
_UP = re.compile(r"\b(?:increas\w*|boost\w*|rais\w*|more|bright(?:er|en)|light(?:er|en)|up|enhanc\w*|vivid|vibrant|colou?rful|punch\w*|pump)\b")


def _parse_adjust(clause: str) -> "dict | None":
    for prop, pattern in _ADJUST_PROPERTIES:
        if not re.search(pattern, clause):
            continue
        down, up = bool(_DOWN.search(clause)), bool(_UP.search(clause))
        if prop == "brightness" and re.search(r"\bdark\w*|\bdim\w*", clause):
            down, up = True, False
        if not (down or up):
            continue
        amount = _amount_percent(clause, 100.0 if re.search(r"\bdesaturat\w*\b", clause) and not re.search(r"\d", clause) else 25.0)
        return {"property": prop, "percent": -amount if down else amount}
    return None


def _apply_adjust(image, params):
    from PIL import ImageEnhance

    enhancer = {"brightness": ImageEnhance.Brightness, "contrast": ImageEnhance.Contrast,
                "saturation": ImageEnhance.Color}[params["property"]]
    factor = max(0.0, 1 + float(params["percent"]) / 100)
    base = image if params["property"] != "saturation" else _rgb(image)
    return enhancer(base).enhance(factor), f"{params['property']} {'+' if params['percent'] >= 0 else ''}{params['percent']:g}%"


def _measure(image, prop):
    from PIL import ImageStat

    if prop == "brightness":
        return _luminance(image)
    if prop == "contrast":
        return ImageStat.Stat(image.convert("L")).stddev[0]
    return ImageStat.Stat(_rgb(image).convert("HSV")).mean[1]


def _verify_adjust(before, after, params):
    b, a = _measure(before, params["property"]), _measure(after, params["property"])
    wanted_up = params["percent"] > 0
    if abs(a - b) < 0.25:
        return f"{params['property']} did not measurably change ({b:.1f} -> {a:.1f}) - the image may already be at its limit"
    if (a > b) != wanted_up:
        return f"{params['property']} moved the wrong way ({b:.1f} -> {a:.1f})"
    return None


def _parse_temperature(clause: str) -> "dict | None":
    found = re.search(r"\b(warm\w*|cool(?:er|ing)?|cold(?:er)?)\b", clause)
    if not found or re.search(r"\bcool\s+(?:looking|effect)\b", clause) and not re.search(r"\b(?:make|more|tone)\b", clause):
        return None
    amount = _amount_percent(clause, 20.0)
    return {"percent": amount if found.group(1).startswith("warm") else -amount}


def _apply_temperature(image, params):
    from PIL import Image

    rgb = _rgb(image)
    shift = float(params["percent"]) / 100 * 60
    r, g, b = rgb.split()
    r = r.point(lambda v: max(0, min(255, round(v + shift))))
    b = b.point(lambda v: max(0, min(255, round(v - shift))))
    return Image.merge("RGB", (r, g, b)), f"{'warmer' if shift > 0 else 'cooler'} by {abs(params['percent']):g}%"


def _warmth(image):
    from PIL import ImageStat

    mean = ImageStat.Stat(_rgb(image)).mean
    return mean[0] - mean[2]


def _verify_temperature(before, after, params):
    b, a = _warmth(before), _warmth(after)
    if abs(a - b) < 0.25 or (a > b) != (params["percent"] > 0):
        return f"colour temperature did not move as asked ({b:.1f} -> {a:.1f})"
    return None


def _parse_filter(clause: str) -> "dict | None":
    if re.search(r"\bsepia\b|\bvintage\b|\bold[\s-]fashioned\b", clause):
        return {"filter": "sepia"}
    if re.search(r"\binvert\w*\b|\bnegative\b", clause):
        return {"filter": "invert"}
    posterize = re.search(r"\bposteri[sz]\w*\b", clause)
    if posterize:
        levels = re.search(r"\b(\d+)\s*(?:levels?|colou?rs?|bits?)\b", clause)
        return {"filter": "posterize", "levels": int(levels.group(1)) if levels else 4}
    pixelate = re.search(r"\bpixel+at\w*\b|\bmosaic\b", clause)
    if pixelate:
        size = re.search(r"\b(\d+)\s*(?:px|pixels?)?\s*(?:blocks?|squares?|cells?)?\b", clause)
        return {"filter": "pixelate", "block": int(size.group(1)) if size else 12}
    if re.search(r"\b(?:de-?blur|unblur|remove\s+(?:the\s+)?blur|reduce\s+(?:the\s+)?blur|less\s+blurry)\b", clause):
        return None  # reconstruction, a technique - never its opposite
    if re.search(r"\b(?:blur|soften|gaussian)\b", clause):
        radius = re.search(_NUMBER + r"\s*(?:px|pixels?)?\s*(?:radius)?", clause)
        return {"filter": "blur", "radius": float(radius.group(1)) if radius else (1.0 if _SMALL.search(clause) else 4.0 if _LARGE.search(clause) else 2.0)}
    return None


def _apply_filter(image, params):
    from PIL import Image, ImageFilter, ImageOps

    kind = params["filter"]
    rgb = _rgb(image)
    if kind == "sepia":
        grey = ImageOps.grayscale(rgb)
        return ImageOps.colorize(grey, black=(40, 26, 13), white=(255, 240, 192)), "sepia tone"
    if kind == "invert":
        return ImageOps.invert(rgb), "colours inverted"
    if kind == "posterize":
        levels = max(2, min(256, int(params["levels"])))
        bits = max(1, min(8, (levels - 1).bit_length()))
        return ImageOps.posterize(rgb, bits), f"posterized to {2 ** bits} levels per channel"
    if kind == "pixelate":
        block = max(2, int(params["block"]))
        small = rgb.resize((max(1, rgb.width // block), max(1, rgb.height // block)), Image.BILINEAR)
        return small.resize(rgb.size, Image.NEAREST), f"pixelated in {block}px blocks"
    radius = max(0.1, float(params["radius"]))
    return rgb.filter(ImageFilter.GaussianBlur(radius)), f"gaussian blur, radius {radius:g}px"


def _verify_filter(before, after, params):
    from PIL import ImageChops, ImageStat

    if after.size != before.size:
        return "the filter changed the image size"
    difference = ImageStat.Stat(ImageChops.difference(_rgb(before), _rgb(after))).mean
    if max(difference) < 0.05:
        return f"the {params['filter']} filter made no measurable change"
    return None


def _parse_border(clause: str) -> "dict | None":
    if not re.search(r"\b(?:border|frame|outline\s+around|margin|padding)\b", clause):
        return None
    width = re.search(_NUMBER + r"\s*(?:px|pixels?)", clause)
    return {"width": int(float(width.group(1))) if width else None, "colour": list(_colour(clause, (255, 255, 255)))}


def _border_width(image, params):
    return int(params["width"]) if params.get("width") else max(2, round(min(image.size) * 0.04))


def _apply_border(image, params):
    from PIL import ImageOps

    width = _border_width(image, params)
    colour = tuple(params["colour"])
    return ImageOps.expand(_rgb(image), border=width, fill=colour), f"{width}px border in rgb{colour}"


def _verify_border(before, after, params):
    width = _border_width(before, params)
    expected = (before.width + 2 * width, before.height + 2 * width)
    if after.size != expected:
        return f"expected {expected[0]}x{expected[1]}, got {after.width}x{after.height}"
    return None if _rgb(after).getpixel((0, 0)) == tuple(params["colour"]) else "the border is not the colour asked for"


_QUOTED = re.compile(r"[\"“”‘’']([^\"“”‘’']{1,120})[\"“”‘’']")


def _parse_text(clause: str) -> "dict | None":
    quoted = _QUOTED.search(clause)
    if not quoted:
        return None
    # Everything but the quoted words is read case-insensitively; the words
    # themselves are drawn exactly as the person wrote them.
    without_text = clause.replace(quoted.group(0), " ").lower()
    if not re.search(r"\b(?:add|put|write|overlay|caption|label|text|stamp|title|watermark|words?)\w*\b", without_text):
        return None
    vertical = "top" if re.search(r"\b(?:top|upper|above|header)\b", without_text) else "bottom" if re.search(r"\b(?:bottom|lower|below|footer|caption)\b", without_text) else "center"
    horizontal = "left" if re.search(r"\bleft\b", without_text) else "right" if re.search(r"\bright\b", without_text) else "center"
    size = re.search(r"\b(\d+)\s*(?:px|pt|point)\b", without_text)
    return {"text": quoted.group(1), "vertical": vertical, "horizontal": horizontal,
            "colour": list(_colour(without_text, (255, 255, 255))), "size": int(size.group(1)) if size else None}


def _text_geometry(image, params):
    from PIL import ImageDraw, ImageFont

    size = int(params.get("size") or max(12, round(min(image.size) * 0.08)))
    font = ImageFont.load_default(size=size)
    left, top, right, bottom = ImageDraw.Draw(image).textbbox((0, 0), params["text"], font=font)
    tw, th = right - left, bottom - top
    margin = max(4, round(min(image.size) * 0.03))
    x = {"left": margin, "right": image.width - tw - margin, "center": (image.width - tw) // 2}[params["horizontal"]]
    y = {"top": margin, "bottom": image.height - th - margin, "center": (image.height - th) // 2}[params["vertical"]]
    return font, (x - left, y - top), (x, y, x + tw, y + th)


def _apply_text(image, params):
    from PIL import ImageDraw

    out = _rgb(image).copy()
    font, origin, _box = _text_geometry(out, params)
    colour = tuple(params["colour"])
    shadow = (0, 0, 0) if sum(colour) > 382 else (255, 255, 255)
    draw = ImageDraw.Draw(out)
    draw.text((origin[0] + 1, origin[1] + 1), params["text"], font=font, fill=shadow)
    draw.text(origin, params["text"], font=font, fill=colour)
    return out, f"text {params['text']!r} at {params['vertical']} {params['horizontal']}"


def _verify_text(before, after, params):
    from PIL import ImageChops

    _font, _origin, box = _text_geometry(_rgb(before), params)
    region = tuple(max(0, v) for v in box)
    changed = ImageChops.difference(_rgb(before).crop(region), _rgb(after).crop(region)).getbbox()
    return None if changed else "no text was drawn where it was asked for"


BUILT_IN_OPERATIONS: "tuple[EditOperation, ...]" = (
    EditOperation("rotate", "rotate <n> degrees [clockwise|counter-clockwise] / turn upside down", _parse_rotate, _apply_rotate, _verify_rotate),
    EditOperation("flip", "flip horizontally|vertically / mirror", _parse_flip, _apply_flip, _verify_flip),
    EditOperation("crop", "crop to a square | to <w>:<h> | to <w>x<h> | the left/right/top/bottom/center half|third|quarter | trim <n>px from each side|left|right|top|bottom",
                  _parse_crop, _apply_crop, _verify_crop),
    EditOperation("resize", "resize to <w>x<h> | to <n> pixels wide|tall | to <n>% | to half/double the size", _parse_resize, _apply_resize, _verify_resize),
    EditOperation("adjust", "increase|decrease brightness|contrast|saturation [by <n>%|slightly|a lot] / make it brighter|darker",
                  _parse_adjust, _apply_adjust, _verify_adjust),
    EditOperation("temperature", "make it warmer|cooler [by <n>%]", _parse_temperature, _apply_temperature, _verify_temperature),
    EditOperation("filter", "sepia | invert | posterize to <n> levels | pixelate [<n>px] | blur [<n>px]", _parse_filter, _apply_filter, _verify_filter),
    EditOperation("border", "add a [<n>px] <colour> border|frame", _parse_border, _apply_border, _verify_border),
    EditOperation("text", "add the text \"<words>\" at the top|bottom|center [left|right] in <colour>", _parse_text, _apply_text, _verify_text),
)
_REGISTERED: "dict[str, EditOperation]" = {}


def register_edit_operation(operation: EditOperation) -> None:
    """Add an edit operation - parsed, applied and verified like the built-in ones."""
    _REGISTERED[operation.op_id] = operation


def edit_operations() -> "tuple[EditOperation, ...]":
    known = {op.op_id: op for op in BUILT_IN_OPERATIONS}
    known.update(_REGISTERED)
    return tuple(known.values())


# ---------------------------------------------------------------------------
# Instruction -> plan
# ---------------------------------------------------------------------------

_EDIT_VERBS = (
    r"rotat\w*|turn|flip\w*|mirror\w*|crop\w*|trim\w*|resiz\w*|scal\w*|shrink\w*|enlarg\w*|make|add|put|write|convert|"
    r"increas\w*|decreas\w*|boost\w*|reduc\w*|lower|rais\w*|apply|give|blur|soften|sharpen\w*|invert|posteri[sz]e|"
    r"pixelat\w*|brighten|darken|warm|cool|frame|remove|denoise|deblur|enhance|change|set|tone|desaturat\w*|upscale|cut|keep|overlay|caption"
)
_SPLIT = re.compile(
    r"\s*(?:;|\.\s+|,?\s*\b(?:and\s+)?then\b|,?\s*\bafter\s+that\b|,?\s*\bfinally\b|,\s*and\s+(?=(?:" + _EDIT_VERBS + r")\b)"
    r"|,\s*(?=(?:" + _EDIT_VERBS + r")\b)|\s+and\s+(?=(?:" + _EDIT_VERBS + r")\b)|\s+plus\s+)\s*",
    re.IGNORECASE,
)
_FILLER = frozenset(
    "please can could would will you me for the this that it its image photo picture pic photograph screenshot selfie scan "
    "a an of my to and then now also just make do i want need like d edit change so some result copy version save "
    "keep original new one bit little let us let's go ahead okay ok hey alfred thanks thank with in on by".split()
)


def _protect(text: str, phrases: "Sequence[str]") -> "tuple[str, dict[str, str]]":
    """Hide quoted text and multi-word technique phrases from clause splitting."""
    kept: "dict[str, str]" = {}

    def hide(value: str) -> str:
        key = f"\ue000{len(kept)}\ue000"
        kept[key] = value
        return key

    text = _QUOTED.sub(lambda m: hide(m.group(0)), text)
    for phrase in sorted((p for p in phrases if " " in p), key=len, reverse=True):
        text = re.sub(r"\b" + re.escape(phrase) + r"\b", lambda m: hide(m.group(0)), text, flags=re.IGNORECASE)
    return text, kept


def _restore(text: str, kept: "Mapping[str, str]") -> str:
    for key, value in kept.items():
        text = text.replace(key, value)
    return text


def _meaningful(clause: str) -> bool:
    words = re.findall(r"[a-z']+", clause.lower().replace("'d", " d"))
    return any(word not in _FILLER for word in words)


def parse_edit_instruction(text: str, techniques: "Sequence[ImageTechnique]" = ()) -> EditPlan:
    """The ordered edits an instruction asks for, and every clause it could not
    read. Each clause is one edit; a clause no operation or technique reads is
    kept as not understood, never dropped."""
    phrases = [w for t in techniques for w in t.words]
    protected, kept = _protect(" ".join(str(text or "").split()), phrases)
    steps: "list[EditStep]" = []
    unknown: "list[str]" = []
    for piece in _SPLIT.split(protected):
        clause = _restore(piece or "", kept).strip(" ,.!?")
        if not clause:
            continue
        lowered = clause.lower()
        step = None
        technique = technique_for_request(lowered, tuple(techniques)) if techniques else None
        for operation in edit_operations():
            params = operation.parse(lowered if operation.op_id != "text" else clause)
            if params is not None:
                step = EditStep(operation.op_id, params, clause)
                break
        # A named technique owns its own phrases ("make it bigger" is upscale),
        # unless the clause gave the operation a real number to work with.
        if technique is not None and (step is None or (step.op == "resize" and not re.search(r"\d", clause))):
            step = EditStep("technique", {"technique": technique.technique_id}, clause)
        if step is not None:
            steps.append(step)
        elif _meaningful(clause):
            unknown.append(clause)
    return EditPlan(tuple(steps), tuple(unknown))


def vocabulary(techniques: "Sequence[ImageTechnique]" = ()) -> "tuple[str, ...]":
    lines = [op.grammar for op in edit_operations()]
    if techniques:
        lines.append(" | ".join(t.words[0] for t in techniques) + " (named techniques)")
    return tuple(lines)


def describe_step(step: EditStep) -> str:
    return step.clause if step.op == "technique" else f"{step.op} {dict(step.params)}"


# ---------------------------------------------------------------------------
# Plan -> image
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditResult:
    data: bytes
    width: int
    height: int
    applied: "tuple[dict, ...]"


def apply_edit_plan(image_bytes: bytes, steps: "Sequence[EditStep | Mapping[str, Any]]",
                    techniques: "Sequence[ImageTechnique]" = ()) -> EditResult:
    """Every step applied in order, each verified by measurement before the next
    runs. Any step that cannot run or whose result fails verification stops the
    whole edit with the reason - no half-edited image is returned."""
    operations = {op.op_id: op for op in edit_operations()}
    by_technique = {t.technique_id: t for t in techniques}
    image = open_image(image_bytes)
    applied = []
    for number, raw in enumerate(steps, 1):
        step = raw if isinstance(raw, EditStep) else EditStep(str(raw["op"]), dict(raw.get("params") or {}), str(raw.get("clause", "")))
        before = image
        if step.op == "technique":
            technique = by_technique.get(str(step.params.get("technique")))
            if technique is None:
                raise ImageTechniqueError(f"step {number}: no technique '{step.params.get('technique')}' is available now")
            image, detail = technique.run(before)
            problem = None if (image.size != before.size or image.tobytes() != before.tobytes()) else "made no change"
        else:
            operation = operations.get(step.op)
            if operation is None:
                raise ImageTechniqueError(f"step {number}: no edit operation called '{step.op}'")
            try:
                image, detail = operation.apply(before, step.params)
            except (KeyError, TypeError, ValueError) as exc:
                raise ImageTechniqueError(f"step {number} ({step.clause or step.op}) could not run: {exc}") from exc
            problem = operation.verify(before, image, step.params)
        if problem:
            raise ImageTechniqueError(f"step {number} ({step.clause or step.op}) did not do what was asked: {problem}. Nothing was saved.")
        if image.width * image.height > MAX_OUTPUT_PIXELS:
            raise ImageTechniqueError(f"step {number} would produce an image larger than {MAX_OUTPUT_PIXELS} pixels")
        applied.append({"step": number, "op": step.op, "clause": step.clause, "detail": detail,
                        "size": [image.width, image.height], "verified": True})
    return EditResult(png_bytes(image), image.width, image.height, tuple(applied))
