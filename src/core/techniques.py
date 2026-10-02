"""Technique families - one governance for every open registry of techniques.

A *technique* is data plus one function: an id, a label, what kind of work it
is (``purpose``), the words a person uses for it, and something it does. Image
techniques (what can be done to a picture) were the first family; series
techniques (what can be found in a history of readings, GM-09) are the second.
Each family keeps its own registry and its own notion of what a technique
does, and registers itself here so that Ecosystem Evolution governs all of
them the same way - one handler, one inventory listing - never a copy of the
governance per family:

    <category>.<technique_id> = enabled | disabled
                              | pipeline:<step>,<step>|<label>|<phrase>;<phrase>

``enabled``/``disabled`` withdraws or restores a technique; a pipeline adds a
new one composed from existing ones, with no code. A family says what its
techniques are right now given the governed values (``available``) and
whether a value is a valid state for one of them (``validate``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

ENABLED = "enabled"
DISABLED = "disabled"
PIPELINE_PREFIX = "pipeline:"


@dataclass(frozen=True)
class TechniqueFamily:
    category: str
    available: "Callable[[Mapping[str, str]], Sequence[Any]]"
    validate: "Callable[[str, str], str | None]"
    describes: str = ""

    @property
    def prefix(self) -> str:
        return f"{self.category}."

    def owns(self, component_id: str) -> bool:
        return component_id.startswith(self.prefix) and component_id.count(".") == 1

    def technique_id(self, component_id: str) -> str:
        return component_id[len(self.prefix):]

    def known_ids(self) -> "set[str]":
        return {t.technique_id for t in self.available({})}


_FAMILIES: "dict[str, TechniqueFamily]" = {}


def register_technique_family(family: TechniqueFamily) -> None:
    _FAMILIES[family.category] = family


def technique_families() -> "tuple[TechniqueFamily, ...]":
    return tuple(_FAMILIES.values())


def family_for(component_id: str) -> "TechniqueFamily | None":
    return next((f for f in _FAMILIES.values() if f.owns(component_id)), None)


def parse_pipeline(value: str) -> "tuple[tuple[str, ...], str, tuple[str, ...]]":
    """``pipeline:<step>,<step>|<label>|<phrase>;<phrase>`` -> (steps, label,
    phrases). "_" stands for a space: a governed value travels as one
    whitespace-free token."""
    body = str(value)[len(PIPELINE_PREFIX):]
    steps_part, _sep, rest = body.partition("|")
    label, _sep, words_part = rest.partition("|")
    steps = tuple(step.strip().lower() for step in steps_part.split(",") if step.strip())
    words = tuple(word.replace("_", " ").strip().lower() for word in words_part.split(";") if word.strip())
    return steps, label.strip(), words


def phrase_in(text: str, phrases: "Sequence[str]") -> int:
    """The length of the longest of ``phrases`` said as whole words in
    ``text`` (0 when none is) - how every family matches a request to a
    technique, so the longest, most specific phrase wins."""
    lowered = " ".join(str(text or "").lower().split())
    best = 0
    for phrase in phrases:
        start = 0
        while True:
            position = lowered.find(phrase, start)
            if position == -1:
                break
            before = lowered[position - 1] if position > 0 else " "
            end = position + len(phrase)
            after = lowered[end] if end < len(lowered) else " "
            if not before.isalnum() and not after.isalnum():
                best = max(best, len(phrase))
                break
            start = position + 1
    return best
