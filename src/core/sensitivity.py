"""The sensitivity scale (GM-13) - one ordered vocabulary every store that
holds information about a person or their world shares: Memory records,
observations, captures.

A scale, never a list of subjects. Which information sits at which level is
the owner's declaration (rules over sources and words, see
``src/services/memory/sensitivity.py``) or the person's own words ("remember
privately that ..."), never a topic this code decided was delicate. The
levels only say how carefully something is handled:

    ordinary     no special handling (the default; stored as "")
    personal     about the person; kept to the agent that holds it
    sensitive    kept, but never shared with another agent, never sent to a
                 cloud reasoning provider unless the owner allowed it, and
                 shown redacted on management surfaces
    restricted   as sensitive, and only ever returned when the person asks
                 for it directly

Credentials sit above the scale: ``src/services/memory/secrets.py`` refuses to
store them at all.
"""

from __future__ import annotations

LEVELS: "tuple[str, ...]" = ("ordinary", "personal", "sensitive", "restricted")
_ORDER = {"": 0, "ordinary": 0, "personal": 1, "sensitive": 2, "restricted": 3}


def normalise_level(level: object) -> str:
    """A level as stored: "" for ordinary, otherwise the level's own name.
    Raises ValueError for a word that is not on the scale."""
    text = " ".join(str(level or "").split()).lower()
    if text in ("", "ordinary", "normal", "none"):
        return ""
    if text in ("private", "privately"):
        return "personal"
    if text not in _ORDER:
        raise ValueError(f"'{level}' is not a sensitivity level; the scale is: {', '.join(LEVELS)}")
    return text


def rank(level: object) -> int:
    """Position on the scale; an unknown stored word ranks above every
    level, so something unreadable is handled as the most careful case."""
    text = " ".join(str(level or "").split()).lower()
    return _ORDER.get(text, len(LEVELS))


def at_most(level: object, ceiling: object) -> bool:
    return rank(level) <= rank(ceiling)


def higher(first: object, second: object) -> str:
    """The more careful of two levels."""
    return normalise_level(first if rank(first) >= rank(second) else second) if (first or second) else ""


def label(level: object) -> str:
    text = " ".join(str(level or "").split()).lower()
    return text or "ordinary"
