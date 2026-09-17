"""Personal guidance - helping a person think through their life, their week and
what is weighing on them - as reasoning over what Alfred already holds, never a
separate "therapist" with a memory of its own.

Three pieces, each deterministic so Half Brain is genuinely useful, and each a
set of notes a Brain can reason over when one is configured:

    what is going on    "remember what I'm dealing with right now: ..." kept as
                        the person's current situation - one canonical
                        personal-context topic, superseded and kept as history
    the week            a plan across sources: calendar items and their
                        recurrences, reminders falling due, open tasks, weekly
                        commitments still to meet, and the goals they serve
    what to do first    the same sources ranked: a missed reminder, then work
                        that moves a stated goal, then the oldest open task

**Nothing about anyone's life is written here.** Situations, goals, tasks and
commitments are whatever the person said. A link between a goal and what serves
it carries its evidence, strongest first, and is said to be exactly that:

    told        the person said so ("my running is for my half marathon goal") -
                kept as a personal-context fact, superseded when they say
                otherwise ("... isn't for ...")
    shared words  the commitment or task and the goal share a word
    suggested   a Brain, given the real goals, commitments and tasks, proposed
                it - validated against those sources and offered for the person
                to confirm, never stored as if they had said it

With no model, links the words do not show are asked about, never guessed: the
local model measured unreliable at this (see REQUIREMENT_LEDGER.md).

**Safety first.** A message that speaks of ending one's life or self-harm is
answered with care and a plain pointer to real help before anything else - a
coaching tool is not a crisis service, and says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

from src.services.memory.coaching import AT_MOST, Commitment, stems
from src.services.memory.personal_context import STATUS_CURRENT, PersonalContextCandidate

SITUATION_TOPIC = "current situation"
SITUATION_CATEGORY = "situation"

_SITUATION = re.compile(
    r"^\s*(?:(?:please|also|so|ok(?:ay)?)\s*,?\s*)?(?:"
    r"(?:remember|note|keep\s+in\s+mind)\s+(?:that\s+)?what\s+i(?:'m|\s+am)\s+(?:dealing|going\s+through|struggling)\s+with"
    r"(?:\s+(?:right\s+now|at\s+the\s+moment|these\s+days|currently|lately))?\s*(?:is|:|-)\s*"
    r"|what\s+i(?:'m|\s+am)\s+(?:dealing|going\s+through|struggling)\s+with(?:\s+(?:right\s+now|at\s+the\s+moment|these\s+days|currently))?\s+is\s*:?\s*"
    r"|i(?:'m|\s+am)\s+(?:currently\s+)?(?:dealing|struggling|going\s+through)\s+(?:with\s+)?"
    r"|(?:the\s+thing\s+|what(?:'s|\s+is)\s+)?(?:bothering|worrying|stressing)\s+me\s+(?:right\s+now\s+|at\s+the\s+moment\s+)?(?:is|:)\s*"
    r")(?P<situation>[^?]{6,400}?)\s*[.!]*\s*$",
    re.IGNORECASE,
)


def extract_situation(text: str) -> "PersonalContextCandidate | None":
    match = _SITUATION.match(str(text or ""))
    if match is None:
        return None
    situation = " ".join(match.group("situation").split()).strip(" .,:;")
    if len(situation) < 6:
        return None
    third = re.sub(r"\bI\b", "they", re.sub(r"\bmy\b", "their", situation, flags=re.IGNORECASE))
    return PersonalContextCandidate(
        topic=SITUATION_TOPIC, content=f"The user is dealing with: {third}.",
        category=SITUATION_CATEGORY, status=STATUS_CURRENT, value=situation,
    )


_CRISIS = re.compile(
    r"\b(?:kill(?:ing)?\s+myself|suicid\w*|end(?:ing)?\s+my\s+life|take\s+my\s+(?:own\s+)?life|self[-\s]?harm|"
    r"hurt(?:ing)?\s+myself|(?:don'?t|do\s+not)\s+want\s+to\s+(?:live|be\s+alive|be\s+here)|want\s+to\s+die|better\s+off\s+dead)\b",
    re.IGNORECASE,
)


def crisis_signal(text: str) -> bool:
    return bool(_CRISIS.search(str(text or "")))


CRISIS_REPLY = (
    "I'm really glad you told me, and I want you to be safe. I'm not able to give the help you deserve right now - "
    "please reach someone who can, today: call your local emergency number if you might act on these thoughts, or a "
    "crisis line in your country (for example 988 in the US, 116 123 Samaritans in the UK and Ireland; "
    "findahelpline.com lists lines everywhere). If you can, tell someone you trust what you told me. "
    "I'm still here to talk, and I'll remember what you're going through if you want me to."
)


# The shapes of asking for guidance. Grammar only - no problem is named.
_LIFE_DIRECTION = re.compile(
    r"\b(?:figur(?:e|ing)\s+out\s+what\s+(?:i(?:'m|\s+am)\s+doing|to\s+do)\s+with\s+my\s+life|what\s+(?:should\s+i|do\s+i)\s+do\s+with\s+my\s+life|"
    r"(?:i\s+feel|i'?m|i\s+am)\s+(?:so\s+|really\s+|completely\s+)?(?:lost|stuck|directionless|aimless)\b|"
    r"guide\s+me\s+through|help\s+me\s+(?:think|work)\s+(?:this|it|things)\s+through|help\s+me\s+decide|what\s+should\s+i\s+do\s+about)",
    re.IGNORECASE,
)
_TALK = re.compile(
    r"\b(?:i\s+(?:need|want)\s+to\s+(?:talk|vent)|can\s+(?:we|i)\s+talk|something(?:'s|\s+is|\s+has\s+been)?\s+(?:bothering|worrying|upsetting|eating\s+at)\s+me|"
    r"i(?:'m|\s+am)\s+(?:feeling\s+)?(?:overwhelmed|stressed\s+out|anxious|down|low|burnt\s+out|burned\s+out))\b",
    re.IGNORECASE,
)
_PLAN_WEEK = re.compile(
    r"\b(?:(?:help\s+me\s+)?(?:organi[sz]e|plan|structure|sort\s+out|map\s+out)\s+my\s+(?:week|days?|schedule)|"
    r"(?:plan|schedule)\s+for\s+(?:this|the|my)\s+week|what\s+(?:does|do)\s+my\s+week\s+look\s+like)\b",
    re.IGNORECASE,
)
_PRIORITY = re.compile(
    r"\b(?:what\s+should\s+i\s+(?:do|work\s+on|focus\s+on|tackle)\s+(?:first|next|now|today)|"
    r"what(?:'s|\s+is)\s+(?:my\s+)?(?:top\s+)?priority|prioriti[sz]e\s+my\s+(?:tasks|week|day))\b",
    re.IGNORECASE,
)
_GOAL_LINK = re.compile(
    r"\b(?:how\s+(?:do|does)\s+my\s+(?:\w+\s+){0,3}(?:relate|connect|line\s+up|fit)|"
    r"(?:are|is)\s+my\s+(?:\w+\s+){0,3}(?:helping|serving|moving)\s+(?:me\s+towards\s+)?my\s+goals?|"
    r"do\s+my\s+(?:\w+\s+){0,3}match\s+my\s+goals?)\b",
    re.IGNORECASE,
)

_TEACH_LINK = re.compile(
    r"^\s*(?:(?:please|also|so|ok(?:ay)?|note(?:\s+that)?|remember(?:\s+that)?|fyi)\s*,?\s*)*"
    r"(?:my\s+)?(?P<thing>[^.?!]{3,120}?)\s+"
    r"(?P<neg>is\s+not|isn't|are\s+not|aren't|does\s+not|doesn't|do\s+not|don't|has\s+nothing\s+to\s+do\s+with)?\s*"
    r"(?:(?:is|are)\s+)?(?:(?:really|all|mainly|mostly|actually)\s+)?"
    r"(?:for|part\s+of|towards?|serves?|supports?|feeds?\s+into|counts?\s+towards?|helps?\s+(?:me\s+)?(?:with|towards?|reach|achieve|get\s+to)?)\s+"
    r"(?:(?:my|the|our)\s+)?(?:(?:goal|aim|ambition)\s+(?:of\s+|to\s+|:\s*)?(?P<goal_after>[^.?!]{2,120}?)|(?P<goal_before>[^.?!]{2,120}?)\s+(?:goal|aim|ambition))"
    r"\s*[.!]*\s*$",
    re.IGNORECASE,
)


def parse_goal_link(text: str) -> "tuple[str, str, bool] | None":
    """``(thing, goal phrase, negated)`` when the person says what serves - or
    does not serve - one of their goals. The word "goal" (or aim/ambition) is
    required, so "this gift is for my mum" is never read as one."""
    match = _TEACH_LINK.match(str(text or ""))
    if match is None:
        return None
    thing = " ".join(match.group("thing").split()).strip(" ,:;")
    goal = " ".join((match.group("goal_after") or match.group("goal_before") or "").split()).strip(" ,:;")
    if not thing or not goal or not stems(thing) or not stems(goal):
        return None
    return thing, goal, bool(match.group("neg"))


def best_match(phrase: str, candidates: Sequence[str]) -> "str | None":
    """The one candidate sharing the most words with ``phrase`` - None when
    nothing shares a word, or two candidates tie."""
    wanted = stems(phrase)
    scored = sorted(((len(wanted & stems(c)), c) for c in candidates), key=lambda row: -row[0])
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


LINK_CATEGORY = "goal_link"


def goal_link_candidate(thing: str, goal: str, negated: bool) -> PersonalContextCandidate:
    verb = "does not serve" if negated else "serves"
    return PersonalContextCandidate(
        topic=f"link: {thing.lower()} -> {goal.lower()}", content=f"The user said {thing} {verb} their goal: {goal}.",
        category=LINK_CATEGORY, status=STATUS_CURRENT, value=("not:" if negated else "") + goal,
    )


@dataclass(frozen=True)
class GoalLink:
    item: str
    goal: str
    evidence: str  # "told", "shared words: ...", "suggested"


def taught_links(records: Iterable) -> "tuple[tuple[str, str, bool], ...]":
    """Every current told link: ``(thing, goal, negated)``."""
    found = []
    for record in records:
        if getattr(record, "category", "") != LINK_CATEGORY or getattr(record, "status", "") != STATUS_CURRENT:
            continue
        topic = str(record.topic)
        if not topic.startswith("link: ") or " -> " not in topic:
            continue
        thing = topic[len("link: "):].split(" -> ", 1)[0]
        value = str(record.value or "")
        found.append((thing, value[len("not:"):] if value.startswith("not:") else value, value.startswith("not:")))
    return tuple(found)


def goal_connections(
    goals: Sequence[str], items: Sequence[str], taught: "Sequence[tuple[str, str, bool]]" = (),
) -> "dict[str, list[GoalLink]]":
    """For each goal, what serves it and on what evidence - told links first,
    then shared words; a told "does not serve" removes a shared-words link."""
    links: "dict[str, list[GoalLink]]" = {goal: [] for goal in goals}
    denied = set()
    for thing, goal_phrase, negated in taught:
        goal = goal_phrase if goal_phrase in links else best_match(goal_phrase, goals)
        if goal is None:
            continue
        item = best_match(thing, items) or thing
        if negated:
            denied.add((item.lower(), goal))
        elif all(link.item.lower() != item.lower() for link in links[goal]):
            links[goal].append(GoalLink(item, goal, "told"))
    for goal in goals:
        for item in items:
            shared = stems(item) & stems(goal)
            if not shared or (item.lower(), goal) in denied or any(link.item.lower() == item.lower() for link in links[goal]):
                continue
            links[goal].append(GoalLink(item, goal, "shared words: " + ", ".join(sorted(shared))))
    return links


_SUGGESTION = re.compile(r"\s*(?P<item>[^;=]{2,160}?)\s*=>\s*(?P<goal>[^;]{2,160}?)\s*(?:;|$)")


def validated_suggestions(answer: str, goals: Sequence[str], items: Sequence[str],
                          known: "dict[str, list[GoalLink]]") -> "list[GoalLink]":
    """Links a Brain proposed as ``item => goal ; ...``, kept only when both ends
    are one of the person's real items and goals and the link is not already
    known - a suggestion is never a new fact."""
    kept = []
    for match in _SUGGESTION.finditer(str(answer or "")):
        # Exact names only: the Brain was given them, and a near-miss ("buy a
        # yacht" for "buy a wetsuit") is a different thing, not a typo.
        item = next((i for i in items if i.lower() == match.group("item").strip().lower()), None)
        goal = next((g for g in goals if g.lower() == match.group("goal").strip().lower()), None)
        if item is None or goal is None:
            continue
        if any(link.item.lower() == item.lower() for link in known.get(goal, [])) or any(k.item == item and k.goal == goal for k in kept):
            continue
        kept.append(GoalLink(item, goal, "suggested"))
    return kept


LIFE, TALK, PLAN, PRIORITY, GOALS, SITUATION, CRISIS = "life", "talk", "plan_week", "priority", "goal_link", "situation", "crisis"
TEACH_LINK = "teach_goal_link"


def guidance_kind(text: str) -> "str | None":
    working = str(text or "")
    if crisis_signal(working):
        return CRISIS
    if extract_situation(working) is not None:
        return SITUATION
    if parse_goal_link(working) is not None:
        return TEACH_LINK
    if _GOAL_LINK.search(working):
        return GOALS
    if _PLAN_WEEK.search(working):
        return PLAN
    if _PRIORITY.search(working):
        return PRIORITY
    if _LIFE_DIRECTION.search(working):
        return LIFE
    if _TALK.search(working):
        return TALK
    return None


# ---------------------------------------------------------------------------
# Across sources.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenTask:
    title: str
    created: "datetime | None"
    due: "datetime | None"


@dataclass(frozen=True)
class DatedItem:
    day: date
    label: str


def linked_goals(text: str, goals: Sequence[str], taught: "Sequence[tuple[str, str, bool]]" = ()) -> "tuple[str, ...]":
    """The goals ``text`` serves: what the person told me first, then shared words."""
    connections = goal_connections(goals, [text], taught)
    return tuple(goal for goal in goals if connections[goal])


def priorities(
    *, now: datetime, tasks: Sequence[OpenTask], goals: Sequence[str], limit: int = 5,
    taught: "Sequence[tuple[str, str, bool]]" = (),
) -> "list[tuple[str, str]]":
    """``(task, why)`` ranked: missed reminders first, then reminders due in the
    next day, then tasks sharing words with a stated goal, then the oldest."""
    ranked = []
    for task in tasks:
        if task.due is not None and task.due < now:
            ranked.append((0, task.due.timestamp(), task.title, f"a reminder you missed ({task.due:%a %d %b %H:%M})"))
        elif task.due is not None and task.due < now + timedelta(days=1):
            ranked.append((1, task.due.timestamp(), task.title, f"due {task.due:%a %H:%M}"))
        elif linked_goals(task.title, goals, taught):
            ranked.append((2, task.created.timestamp() if task.created else 0.0, task.title,
                           f"it moves your goal: {linked_goals(task.title, goals, taught)[0]}"))
        else:
            age = (now - task.created).days if task.created else 0
            ranked.append((3, task.created.timestamp() if task.created else 0.0, task.title,
                           f"open for {age} day(s)" if age else "still open"))
    ranked.sort(key=lambda row: (row[0], row[1]))
    return [(title, why) for _rank, _order, title, why in ranked[:limit]]


def plan_week(
    *, today: date, now: datetime, items: Sequence[DatedItem], tasks: Sequence[OpenTask],
    commitments: Sequence[Commitment], done: "dict[str, int]", goals: Sequence[str],
) -> "list[str]":
    """A day-by-day plan for the next seven days, from the person's own
    calendar, reminders, open tasks and commitments still to meet."""
    days = [today + timedelta(days=offset) for offset in range(7)]
    load = {day: [] for day in days}
    for item in items:
        if item.day in load:
            load[item.day].append(item.label)
    for task in tasks:
        if task.due is not None and task.due.date() in load:
            load[task.due.date()].append(f"reminder: {task.title} ({task.due:%H:%M})")
    undated = [title for title, _why in priorities(now=now, tasks=[t for t in tasks if t.due is None], goals=goals, limit=20)]
    placed = {day: [] for day in days}
    for title in undated:
        day = min(days, key=lambda d: (len(load[d]) + len(placed[d]), days.index(d)))
        placed[day].append(f"do: {title}")
    sessions = {day: [] for day in days}
    for commitment in commitments:
        if commitment.kind == AT_MOST:
            continue
        remaining = max(0, commitment.target - done.get(commitment.area, 0))
        if not remaining:
            continue
        step = max(1, len(days) // remaining)
        for index in range(remaining):
            sessions[days[min(len(days) - 1, index * step)]].append(commitment.area)
    lines = []
    for day in days:
        parts = load[day] + placed[day] + [f"{area} (non-negotiable)" for area in sessions[day]]
        lines.append(f"{day:%A %d %b}: " + ("; ".join(parts) if parts else "free"))
    limits = [c for c in commitments if c.kind == AT_MOST]
    for commitment in limits:
        lines.append(f"All week: {commitment.describe} (so far {done.get(commitment.area, 0)}).")
    return lines
