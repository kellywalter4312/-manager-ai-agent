"""Durable, first-person facts a person states about themselves,
recognised without requiring an explicit "remember that" - "My name is
Sam. I was born March 3, 1998 and I attend a nearby university"
is three candidate facts, none of them preceded by a retention marker.

**Still deterministic, still a request for something specific - never a
guess about what mattered.** ``session.py``'s own docstring explains why
this project does not try to infer importance from ordinary conversation:
that needs a model and degrades quietly when wrong. This module does not
do that either. It recognises a small, fixed set of self-introduction
*grammars* - "my name is X", "I was born X", "I attend X" - the same way
``RETENTION_MARKERS`` recognises a small, fixed set of retention
*phrasings*. Nothing here reads meaning from arbitrary prose; a sentence
that does not match one of these shapes produces no candidate, exactly as
an unmarked sentence produces no candidate in ``session.py``.

**No name, school or date is hardcoded anywhere in this file.** Every
pattern captures whatever text fills the matched grammatical slot - the
same discipline every subject-word ban elsewhere in this project already
enforces, applied to values instead of routing.

Candidates, not memories, for the same reason ``retention_requests``
returns candidates: nothing here writes anything. The caller offers these
to an agent's own governed policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from src.services.memory.memory import MemoryRecord
from src.services.memory.temporal_facts import parse_month_day, parse_natural_date

# Every ``fact_type`` extract_declared_facts can write - named once so the
# canonical personal-context reader can present these facts beside
# everything else the user taught, instead of a second list of them.
DECLARED_FACT_TYPES: tuple[str, ...] = ("name", "date_of_birth", "school", "completion_date")

_WORD = r"[A-Za-z][\w'\-]*"
_NAME_GROUP = rf"({_WORD}(?:\s+{_WORD}){{0,3}})"

NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\bmy name is {_NAME_GROUP}", re.IGNORECASE),
    re.compile(rf"\bi(?:'m| am) called {_NAME_GROUP}", re.IGNORECASE),
)
DOB_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bi was born(?: on| in)?\s+([^.;!?]+)", re.IGNORECASE),
    re.compile(r"\bmy (?:date of birth|birthday|dob) is\s+([^.;!?]+)", re.IGNORECASE),
)
SCHOOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Case-insensitive on the trigger phrase only ("i" mid-sentence is
    # capitalised, "I attend..."); the captured name must still start
    # with a capital letter - a proper-noun heuristic that scoping the
    # flag this way preserves.
    re.compile(
        r"(?i:\bi (?:attend|go to|study at|am (?:a student|enrolled) at)\s+)"
        r"([A-Z][\w&'.\- ]*?)(?=\s+and\b|[.,;!?]|$)",
    ),
)
COMPLETION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bi (?:will |expect to )?graduate(?:\s+(?:from|on|in|by)\s+)?\s*([^.;!?]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmy (?:graduation|completion) date is\s+([^.;!?]+)", re.IGNORECASE
    ),
    re.compile(
        r"\bi(?:'m| am) expected to (?:graduate|complete)(?:\s+(?:from|on|in|by)\s+)?\s*([^.;!?]*)",
        re.IGNORECASE,
    ),
)

# Trailing conjunctions a captured group can pick up when the sentence
# keeps going without punctuation - "I attend <school> and I was born
# ..." - trimmed defensively rather than relied on to never occur, since
# real speech is not always neatly punctuated.
_TRAILING_JOINERS = re.compile(r"\s+and\b.*$", re.IGNORECASE)


def _clean(group: str) -> str:
    return _TRAILING_JOINERS.sub("", group).strip(" ,.:;")


def _first_match(patterns: Iterable[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        cleaned = _clean(match.group(1))
        if cleaned:
            return cleaned
    return None


def _fact_record(content: str, *, fact_type: str, date_value=None, source_kind: str) -> MemoryRecord:
    context: dict[str, object] = {"fact_type": fact_type, "source": "conversation"}
    tags = {fact_type}
    if date_value is not None:
        context["date"] = date_value.isoformat()
    return MemoryRecord(content=content, kind=source_kind, tags=tags, context=context)


def extract_declared_facts(
    text: str, *, kind: str = "user_context"
) -> tuple[MemoryRecord, ...]:
    """Candidate durable facts from a first-person, self-introduction
    statement - name, date of birth, school, expected completion date.

    Each candidate is independent: a sentence naming only a school
    produces only a school candidate. Nothing is written here - see this
    module's own docstring.
    """
    stripped = str(text or "")
    candidates: list[MemoryRecord] = []

    name = _first_match(NAME_PATTERNS, stripped)
    if name:
        candidates.append(
            _fact_record(
                f"The user's name is {name}.", fact_type="name", source_kind=kind
            )
        )

    dob_text = _first_match(DOB_PATTERNS, stripped)
    if dob_text:
        parsed = parse_natural_date(dob_text)
        candidates.append(
            _fact_record(
                f"The user's date of birth is {dob_text}.",
                fact_type="date_of_birth",
                date_value=parsed,
                source_kind=kind,
            )
        )

    school = _first_match(SCHOOL_PATTERNS, stripped)
    if school:
        candidates.append(
            _fact_record(
                f"The user attends {school}.", fact_type="school", source_kind=kind
            )
        )

    completion_text = _first_match(COMPLETION_PATTERNS, stripped)
    if completion_text:
        parsed = parse_natural_date(completion_text)
        candidates.append(
            _fact_record(
                f"The user's expected completion date is {completion_text}.",
                fact_type="completion_date",
                date_value=parsed,
                source_kind=kind,
            )
        )

    return tuple(candidates)


def structure_date_fact(content: str) -> tuple[str, object] | None:
    """When ``content`` (already destined to be retained - e.g. from an
    explicit "remember that I was born ...") itself states a birth or
    completion date, the ``(fact_type, date)`` pair to enrich that record
    with - so "remember that I was born March 3, 1998" becomes just
    as computable as the bare declarative form. Returns None when the
    content names no such date, which is the ordinary case.
    """
    dob = _first_match(DOB_PATTERNS, content)
    if dob:
        parsed = parse_natural_date(dob)
        if parsed:
            return "date_of_birth", parsed

    completion = _first_match(COMPLETION_PATTERNS, content)
    if completion:
        parsed = parse_natural_date(completion)
        if parsed:
            return "completion_date", parsed

    return None


# ---------------------------------------------------------------------------
# Stage 12 closure: other people's recurring dates - "my mom's birthday
# is March 24", including genuine uncertainty ("either March 24 or May
# 24, I can't remember which").
# ---------------------------------------------------------------------------
#
# **No relation or name is ever matched against a list.** "mom",
# "brother", "best friend" - whatever text fills "my ___'s birthday" is
# read as-is, the identical discipline this module's own docstring
# already states for name/school. A person is free text, stored in
# Memory; nothing here would even need to change for a relation nobody
# has used yet.
#
# **Structured, not yet a MemoryRecord.** This module has no read access
# to Memory and cannot know whether a person named here already has a
# stored, confirmed or unconfirmed date - so it stops at a plain,
# structured candidate. Deciding whether a statement is a brand new
# fact, the resolution of an existing uncertainty, or a deliberate
# change to an already-confirmed date is the governed capability's job
# (see src/manager/capabilities.py's own "personal recurring events"
# section), because only that capability can recall what is already
# known before deciding how to write.

_RELATION_GROUP = r"(?P<relation>[a-z][\w'\- ]*?)"
# Whose birthday it is: a relation the person owns ("my sister", "my sister
# Ada"), or simply a name ("Ada"). What makes this shape this capability's own
# request is the possessive plus "birthday is" plus a real date - "my" is one
# way of saying who, never the requirement. Found by the Phase 11 closure
# sweep: "Ada's birthday is on 3 March" reached no capability at all, while the
# same sentence with a relation word in front of it worked.
#
# The pronouns are excluded because "his birthday is ..." names nobody this
# module could record; the user's own birthday ("my birthday is ...") has no
# possessive after "my" and so has never matched here - it is user_facts' own
# first-person territory, and still is.
_BIRTHDAY_OWNER = (
    rf"(?:my\s+{_RELATION_GROUP}"
    r"|(?P<named>(?!my\b|his\b|her\b|hers\b|their\b|theirs\b|your\b|yours\b|our\b|ours\b|its\b|the\b)"
    r"[\w'’\-]+(?:\s+[\w'’\-]+)?))"
)
RELATIONAL_BIRTHDAY_PATTERN = re.compile(
    rf"\b{_BIRTHDAY_OWNER}'s birthday is\s+(?P<date>[^.!?]+)", re.IGNORECASE,
)
# "either March 24 or May 24" - the shape genuine, unresolved uncertainty
# takes. Matched against the captured date clause only, never the whole
# sentence, so "I can't remember which" (a separate clause, after the
# ordinary sentence-ending period this pattern already stops at) is
# never mistaken for a third alternative.
_EITHER_OR = re.compile(r"^either\s+(.+?)\s+or\s+(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class RelationalBirthdayCandidate:
    """One recognised statement about another person's recurring
    birthday - either confirmed (a single month/day, with a year when
    the statement named one) or uncertain (two or more named
    alternatives, none chosen).

    ``year`` is None whenever the statement did not name one - "a
    birthday without a birth year must still work correctly as a
    recurring birthday" (this stage's own requirement) is exactly what
    keeping this field optional, rather than guessing a placeholder
    here, makes representable.
    """

    person: str
    confirmed: bool
    month: int | None = None
    day: int | None = None
    year: int | None = None
    alternatives: tuple[tuple[int, int], ...] = ()  # (month, day) pairs, uncertain only


def relational_birthday_intent(text: str) -> bool:
    """Whether this text states (or restates) another person's
    birthday - the ``claims=`` predicate the "personal recurring
    events" capability recognises its own request shape with."""
    return bool(RELATIONAL_BIRTHDAY_PATTERN.search(str(text or "")))


def _parse_single_date(text: str) -> tuple[int, int, int | None] | None:
    """``(month, day, year-or-None)`` from free text - a full date's own
    year when one was stated, otherwise just month/day."""
    full = parse_natural_date(text)
    if full is not None:
        return full.month, full.day, full.year
    month_day = parse_month_day(text)
    if month_day is not None:
        return month_day[0], month_day[1], None
    return None


def extract_relational_birthdays(text: str) -> tuple[RelationalBirthdayCandidate, ...]:
    """Every recognised third-party-birthday statement in ``text``.

    Never fabricates: a clause matching the "my ___'s birthday is ..."
    shape whose date half cannot be parsed at all produces no candidate,
    the same "no guess, ever" rule ``parse_natural_date`` itself already
    keeps.
    """
    stripped = str(text or "")
    candidates: list[RelationalBirthdayCandidate] = []

    for match in RELATIONAL_BIRTHDAY_PATTERN.finditer(stripped):
        person = _clean(match.group("relation") or match.group("named") or "")
        if not person:
            continue
        rest = match.group("date").strip()

        either = _EITHER_OR.match(rest)
        if either is not None:
            first = _parse_single_date(either.group(1))
            second = _parse_single_date(either.group(2))
            if first is None or second is None:
                continue
            candidates.append(
                RelationalBirthdayCandidate(
                    person=person,
                    confirmed=False,
                    alternatives=((first[0], first[1]), (second[0], second[1])),
                )
            )
            continue

        parsed = _parse_single_date(rest)
        if parsed is None:
            continue
        month, day, year = parsed
        candidates.append(
            RelationalBirthdayCandidate(person=person, confirmed=True, month=month, day=day, year=year)
        )

    return tuple(candidates)
