"""Recognising a correction - the grammar of "that is wrong", and nothing else.

A correction is consequential: it changes or removes something already
believed about the person. So it is recognised only by its *shape* - an
explicit statement that something is wrong, a replacement given against a
named old value, or a stated change of preference - and never inferred from
two sentences merely sharing a word. What the correction is *about* is
resolved afterwards, against what is actually stored (see
``src/manager/capabilities.py::correct_memory``), and when more than one
stored fact could be meant the person is asked; nothing here guesses.

No subject, value or topic appears below and none may be added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.services.memory.learning import strip_trailing_filler, strip_vocative_address

ACTION_REPLACE = "replace"
ACTION_INVALIDATE = "invalidate"


@dataclass(frozen=True)
class CorrectionStatement:
    """One recognised correction.

    ``replacement`` is what should now be believed, exactly as said ("green",
    "my favourite colour is green"); empty for a pure invalidation.
    ``old_value`` is the value said to be wrong when the person named it
    ("not blue", "I don't like blue anymore"). ``preference`` marks a stated
    change of preference ("I prefer X now"), whose target is a preference-
    shaped fact rather than whatever was said last."""

    action: str
    replacement: str = ""
    old_value: str = ""
    preference: bool = False


# Something already said is wrong - "that's wrong", "you remembered that
# incorrectly", "you got it wrong", "that is not right".
_WRONGNESS = (
    r"(?:that'?s|that\s+is|it'?s|it\s+is|this\s+is|you'?re|you\s+are)\s+"
    r"(?:(?:all\s+)?wrong|incorrect|not\s+(?:right|correct|true|accurate)|mistaken)"
    r"|you(?:'ve|\s+have)?\s+(?:got|remembered|recorded|noted|stored|written)\s+(?:that|it|this)\s+"
    r"(?:wrong|incorrectly|wrongly|down\s+wrong)"
    r"|(?:that'?s|that\s+is)\s+not\s+what\s+i\s+said"
)
_WRONG_THEN_REPLACEMENT = re.compile(
    rf"^(?:no\s*[,.!]?\s*|nope\s*[,.!]?\s*)?(?:{_WRONGNESS})\s*[,.:;!\-]*\s*(?P<replacement>.*)$",
    re.IGNORECASE,
)
_CORRECTION_PREFIX = re.compile(
    r"^(?:correction|to\s+correct\s+(?:that|you))\s*[:,\-]\s*(?P<replacement>.+)$", re.IGNORECASE,
)
# "Actually it's green, not blue", "no, it's green", "it's green, not blue".
_ACTUALLY_ITS = re.compile(
    r"^(?P<lead>(?:no|nope)\s*[,.!]?\s*(?:actually\s*,?\s*)?|actually\s*,?\s*)?"
    r"(?:it'?s|it\s+is|that'?s|that\s+is|they'?re|they\s+are)\s+(?P<replacement>[^,.;!?]+?)"
    r"(?:\s*,?\s*(?:and\s+)?not\s+(?P<old>[^,.;!?]+))?\s*[.!]?$",
    re.IGNORECASE,
)
# "No, not blue - green", "nope, not blue but green". The leading "no" is
# required: without it "not now, later" would read as a correction.
_NOT_OLD_BUT_NEW = re.compile(
    r"^(?:no|nope)\s*[,.!]?\s*not\s+(?P<old>[^,]+?)\s*(?:,|-|\bbut\b)\s*(?:it'?s\s+|it\s+is\s+)?(?P<replacement>[^.!?]+?)\s*[.!]?$",
    re.IGNORECASE,
)
# "I don't like blue anymore", "I no longer prefer tea".
_PREFERENCE_ENDED = (
    re.compile(
        r"\bi\s+(?:don'?t|do\s+not)\s+(?:like|love|prefer|want|enjoy)\s+(?P<old>[^.!?]+?)\s+any\s*more\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bi\s+no\s+longer\s+(?:like|love|prefer|want|enjoy)\s+(?P<old>[^.!?]+)", re.IGNORECASE),
)
# "Actually, I prefer green now", "I like green instead", "I prefer green now, not blue".
_PREFERENCE_NOW = re.compile(
    r"^(?:actually\s*,?\s*|no\s*,?\s*)?i\s+(?:now\s+)?(?:prefer|like|love|want)\s+(?P<replacement>[^,.!?]+?)"
    r"\s+(?:now|instead|these\s+days|more)(?:\s*,?\s*(?:not|rather\s+than|instead\s+of)\s+(?P<old>[^.!?]+))?\s*[.!]?$",
    re.IGNORECASE,
)


# A bare replacement is often a whole clause ("it's green"); the value is
# what follows the copula. Grammar only.
_LEADING_COPULA = re.compile(r"^(?:actually\s*,?\s*)?(?:it'?s|it\s+is|that'?s|that\s+is|they'?re|they\s+are)\s+", re.IGNORECASE)


def _clean(text: "str | None") -> str:
    return strip_trailing_filler(" ".join(str(text or "").split()).strip(" ,.;:!?\"'"))


def _value(text: "str | None") -> str:
    return _clean(_LEADING_COPULA.sub("", _clean(text)))


def extract_correction(text: str) -> "CorrectionStatement | None":
    """The correction ``text`` makes, or None. Tried in order of how explicit
    the shape is; the first genuine match wins.

    The text is read as said first, and only then without a leading "Name, "
    address: the address stripper is shape-based, so on its own it would also
    swallow an opening "That's wrong, " or "Actually it's green, " as if it
    were a name."""
    said = " ".join(str(text or "").split()).strip()
    if not said:
        return None
    found = _extract(said)
    if found is None:
        addressed = strip_vocative_address(said)
        if addressed != said:
            found = _extract(addressed)
    return found


def _extract(working: str) -> "CorrectionStatement | None":
    match = _CORRECTION_PREFIX.match(working)
    if match:
        replacement = _value(match.group("replacement"))
        return CorrectionStatement(ACTION_REPLACE, replacement) if replacement else None

    match = _WRONG_THEN_REPLACEMENT.match(working)
    if match:
        rest = _clean(match.group("replacement"))
        if not rest:
            return CorrectionStatement(ACTION_INVALIDATE)
        # "You remembered that incorrectly. It's green, not blue." - the
        # replacement is itself often an "it's X, not Y" clause.
        inner = _extract(rest)
        if inner is not None and inner.action == ACTION_REPLACE:
            return inner
        return CorrectionStatement(ACTION_REPLACE, _value(rest))

    match = _NOT_OLD_BUT_NEW.match(working)
    if match:
        return CorrectionStatement(ACTION_REPLACE, _value(match.group("replacement")), _value(match.group("old")))

    match = _ACTUALLY_ITS.match(working)
    # The lead ("no," / "actually") is what makes "it's X" a correction at
    # all: without it, "it's either Tuesday or Wednesday, not sure" or "it's
    # fine, not a problem" would read as one.
    if match and match.group("lead"):
        replacement = _value(match.group("replacement"))
        if replacement:
            return CorrectionStatement(ACTION_REPLACE, replacement, _clean(match.group("old")))

    match = _PREFERENCE_NOW.match(working)
    if match:
        return CorrectionStatement(
            ACTION_REPLACE, _value(match.group("replacement")), _value(match.group("old")), preference=True,
        )

    for pattern in _PREFERENCE_ENDED:
        match = pattern.search(working)
        if match:
            return CorrectionStatement(ACTION_INVALIDATE, old_value=_value(match.group("old")), preference=True)
    return None


def correction_statement_intent(text: str) -> bool:
    return extract_correction(text) is not None
