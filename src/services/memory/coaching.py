"""The coach's own grammar and arithmetic - weekly non-negotiables, what the
person reports doing, and an honest weekly review - over the one canonical
personal-context store. Nothing here writes, and nothing here is a second
store: every commitment, report and review is an ordinary personal-context
statement (``personal_context.py``), superseded and kept as history like any
other, so Alfred and every specialist read the same record.

**No personal circumstances are known here.** A commitment is whatever the
person names ("exercise 4 times", "no takeaway food", "call my mum twice") -
parsed by shape (a thing to do, how often, at least or at most). A report is
matched to a commitment by the words they share, compared by stem, never by a
list of activities.

**Honest grading.** A score is arithmetic on what was reported against what
was committed - 10 for meeting it, proportionally less for falling short, and
for an "at most" commitment points off for every time over. An area with
nothing reported scores nothing: silence is not progress. The two lowest areas
are named with the numbers behind them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

from src.services.memory.personal_context import (
    STATUS_CURRENT,
    PersonalContextCandidate,
    PersonalContextRecord,
)

COMMITMENT_CATEGORY = "commitment"
PROGRESS_CATEGORY = "progress"
REVIEW_CATEGORY = "review"
AT_LEAST = "at_least"
AT_MOST = "at_most"
REVIEW_REMINDER_TITLE = "weekly review with your coach"

_NUMBER_WORDS = {
    "zero": 0, "no": 0, "never": 0, "none": 0, "once": 1, "one": 1, "twice": 2, "two": 2, "three": 3,
    "thrice": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "daily": 7, "every day": 7, "each day": 7,
}
_COUNT = (
    r"(?P<count>\d{1,2}|zero|once|one|twice|two|three|thrice|four|five|six|seven|eight|nine|ten|"
    r"daily|every\s+day|each\s+day)"
)
_FILLER = frozenset({
    "a", "an", "the", "my", "to", "of", "for", "and", "on", "in", "at", "with", "up", "it", "this", "that",
    "i", "me", "week", "weekly", "times", "time", "per", "least", "most", "day", "days", "every", "each",
    "had", "have", "has", "did", "do", "does", "got", "get", "went", "go", "some", "any", "more", "than",
    "no", "not", "never", "only", "just", "also", "am", "was", "were", "be", "been", "zero", "once", "twice",
})


@dataclass(frozen=True)
class Commitment:
    area: str
    target: int
    kind: str
    phrase: str

    @property
    def describe(self) -> str:
        if self.kind == AT_MOST:
            return f"no {self.area}" if self.target == 0 else f"{self.area} at most {self.target} time(s) a week"
        return f"{self.area} {self.target} time(s) a week"


def stems(text: str) -> "frozenset[str]":
    found = set()
    for word in re.findall(r"[a-z0-9']+", str(text or "").lower()):
        if word in _FILLER or word.isdigit() or word in _NUMBER_WORDS:
            continue
        for suffix, keep in (("ies", "y"), ("ied", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)] + keep
                break
        if len(word) > 3 and word.endswith("e"):
            word = word[:-1]
        found.add(word)
    return frozenset(found)


def _count(value: str) -> int:
    value = " ".join(str(value).lower().split())
    return int(value) if value.isdigit() else _NUMBER_WORDS.get(value, 1)


def _clauses(text: str) -> "list[str]":
    body = re.sub(r"\s+", " ", str(text or "")).strip(" .!")
    return [part.strip(" ,.;") for part in re.split(r",|;|\band\b|\bplus\b", body) if part.strip(" ,.;")]


# "My weekly non-negotiables are: ...", "my non-negotiable is ...", "add a
# non-negotiable: ...", "my non negotiables this week are ..."
_NON_NEGOTIABLES = re.compile(
    r"^\s*(?:(?:also|and|ok(?:ay)?)\s*,?\s*)?(?:(?:my|our)\s+(?:weekly\s+)?non[-\s]?negotiables?(?:\s+(?:this|every|each)\s+week)?"
    r"\s+(?:are|is)|(?:add|set|new)\s+(?:a\s+|another\s+)?(?:weekly\s+)?non[-\s]?negotiables?)\s*[:\-]?\s*(?P<items>.+)$",
    re.IGNORECASE,
)


def parse_commitment(clause: str) -> "Commitment | None":
    text = " ".join(str(clause or "").split()).strip(" .")
    if not text:
        return None
    limit = re.match(r"^(?:no|zero|never|not\s+any)\s+(?P<area>.+)$", text, re.IGNORECASE)
    if limit:
        return Commitment(area=limit.group("area").strip(), target=0, kind=AT_MOST, phrase=text)
    at_most = re.match(rf"^(?P<area>.+?)\s+(?:at\s+most|no\s+more\s+than|max(?:imum)?)\s+{_COUNT}(?:\s+times?)?\b.*$", text, re.IGNORECASE)
    if at_most:
        return Commitment(area=at_most.group("area").strip(), target=_count(at_most.group("count")), kind=AT_MOST, phrase=text)
    counted = re.match(
        rf"^(?:at\s+least\s+)?(?P<area>.+?)\s+(?:at\s+least\s+)?{_COUNT}(?:\s*(?:times?|x|sessions?))?(?:\s+(?:a|per|every)\s+week)?$",
        text, re.IGNORECASE,
    )
    if counted:
        return Commitment(area=counted.group("area").strip(), target=max(1, _count(counted.group("count"))), kind=AT_LEAST, phrase=text)
    return Commitment(area=text, target=1, kind=AT_LEAST, phrase=text)


def parse_non_negotiables(text: str) -> "tuple[Commitment, ...] | None":
    match = _NON_NEGOTIABLES.match(str(text or ""))
    if match is None:
        return None
    commitments = tuple(c for c in (parse_commitment(part) for part in _clauses(match.group("items"))) if c is not None)
    return commitments or None


def commitment_candidate(commitment: Commitment) -> PersonalContextCandidate:
    return PersonalContextCandidate(
        topic=f"non-negotiable: {commitment.area}",
        content=f"Every week the user commits to: {commitment.describe}.",
        category=COMMITMENT_CATEGORY, status=STATUS_CURRENT,
        value=f"{commitment.kind}:{commitment.target}", activity=commitment.area,
    )


def commitments_from(records: Iterable[PersonalContextRecord]) -> "tuple[Commitment, ...]":
    found = []
    for record in records:
        if record.category != COMMITMENT_CATEGORY or record.status != STATUS_CURRENT:
            continue
        kind, _sep, target = str(record.value).partition(":")
        area = record.activity or record.topic.split(":", 1)[-1].strip()
        try:
            found.append(Commitment(area=area, target=int(target), kind=kind or AT_LEAST, phrase=area))
        except ValueError:
            continue
    return tuple(found)


def week_id(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


# "This week I exercised 2 times, studied Spanish 3 times and had takeaway twice."
_REPORT_SHAPE = re.compile(
    rf"^\s*(?:(?:so|ok(?:ay)?|well|update)\s*[,:]?\s*)?(?:this\s+week\s*,?\s*|so\s+far\s+this\s+week\s*,?\s*|last\s+week\s*,?\s*)?i\s+(?!am\b|'m\b|want\b|need\b|will\b)[a-z]+[^.?!]*?\b(?:{_COUNT[len('(?P<count>'):-1]}|never)\b(?:\s+times?)?",
    re.IGNORECASE,
)


def progress_report_shape(text: str) -> bool:
    working = str(text or "").strip()
    return bool(_REPORT_SHAPE.search(working)) and ("week" in working.lower() or bool(re.search(r"\btimes?\b|\bonce\b|\btwice\b|\bnever\b", working, re.IGNORECASE)))


def parse_progress(text: str, commitments: Sequence[Commitment]) -> "dict[str, int]":
    """``{area: count}`` for every commitment a report clause names."""
    working = re.sub(r"^\s*(?:(?:so|ok(?:ay)?|well|update)\s*[,:]?\s*)?(?:(?:so\s+far\s+)?this\s+week|last\s+week)\s*,?\s*", "", str(text or ""), flags=re.IGNORECASE)
    reported: "dict[str, int]" = {}
    for clause in _clauses(working):
        clause_stems = stems(clause)
        if not clause_stems:
            continue
        best = None
        for commitment in commitments:
            overlap = len(clause_stems & stems(commitment.area))
            if overlap and (best is None or overlap > best[0]):
                best = (overlap, commitment)
        if best is None:
            continue
        count_match = re.search(rf"\b{_COUNT}\b(?:\s+times?)?", clause, re.IGNORECASE)
        if re.search(r"\b(?:never|didn'?t|did\s+not|no)\b", clause, re.IGNORECASE) and count_match is None:
            count = 0
        elif count_match is not None:
            count = _count(count_match.group("count"))
        else:
            count = 1
        reported[best[1].area] = count
    return reported


def progress_candidate(week: str, area: str, count: int) -> PersonalContextCandidate:
    return PersonalContextCandidate(
        topic=f"progress {week}: {area}", content=f"In week {week} the user reported {area}: {count} time(s).",
        category=PROGRESS_CATEGORY, status=STATUS_CURRENT, value=str(count), activity=area,
    )


def progress_for_week(records: Iterable[PersonalContextRecord], week: str) -> "dict[str, int]":
    prefix = normalize(f"progress {week}:")
    found: "dict[str, int]" = {}
    for record in records:
        if record.category == PROGRESS_CATEGORY and normalize(record.topic).startswith(prefix):
            try:
                found.setdefault(record.activity, int(record.value))
            except ValueError:
                continue
    return found


def normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())


@dataclass(frozen=True)
class AreaGrade:
    area: str
    score: float
    detail: str

    @property
    def letter(self) -> str:
        return "A" if self.score >= 9 else "B" if self.score >= 7.5 else "C" if self.score >= 6 else "D" if self.score >= 4 else "F"


def grade(commitment: Commitment, reported: "int | None") -> AreaGrade:
    if reported is None:
        return AreaGrade(commitment.area, 0.0, f"nothing reported against {commitment.describe}")
    if commitment.kind == AT_MOST:
        over = max(0, reported - commitment.target)
        score = max(0.0, 10.0 - over * 3.5)
        return AreaGrade(commitment.area, score, f"{reported} against a limit of {commitment.target}")
    score = min(1.0, reported / max(1, commitment.target)) * 10.0
    return AreaGrade(commitment.area, score, f"{reported} of {commitment.target}")


def review_candidate(week: str, grades: Sequence[AreaGrade]) -> PersonalContextCandidate:
    listed = "; ".join(f"{g.area} {g.score:.1f}" for g in grades)
    return PersonalContextCandidate(
        topic=f"weekly review {week}", content=f"Weekly review {week}: {listed}.",
        category=REVIEW_CATEGORY, status=STATUS_CURRENT, value=listed,
    )


def previous_scores(records: Iterable[PersonalContextRecord], before_week: str) -> "dict[str, float]":
    reviews = sorted(
        (r for r in records if r.category == REVIEW_CATEGORY and normalize(r.topic) < normalize(f"weekly review {before_week}")),
        key=lambda r: normalize(r.topic), reverse=True,
    )
    if not reviews:
        return {}
    scores = {}
    for part in reviews[0].value.split(";"):
        area, _sep, score = part.strip().rpartition(" ")
        try:
            scores[area] = float(score)
        except ValueError:
            continue
    return scores


_REVIEW_ASK = re.compile(
    r"\b(?:(?:do|run|start|give\s+me)\s+(?:my\s+|the\s+|a\s+)?(?:weekly|week'?s?)\s+review|weekly\s+review|review\s+my\s+week|grade\s+my\s+week|"
    r"how\s+did\s+i\s+do\s+this\s+week|how\s+was\s+my\s+week|score\s+my\s+week|rate\s+my\s+week)\b",
    re.IGNORECASE,
)


def review_intent(text: str) -> bool:
    return bool(_REVIEW_ASK.search(str(text or "")))


def next_review_moment(now: datetime) -> datetime:
    """The next Sunday at 18:00 in the person's own clock, strictly after now."""
    days = (6 - now.weekday()) % 7
    moment = (now + timedelta(days=days)).replace(hour=18, minute=0, second=0, microsecond=0)
    return moment if moment > now else moment + timedelta(days=7)
