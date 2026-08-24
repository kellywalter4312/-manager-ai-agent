"""Which of the user's own declared abilities a piece of work actually names.

Alfred serves one person. Since Step 5 he holds that person's profile, but
holding it and *relating it to the work in hand* are different things, and only
the first existed. This module is the second: a deterministic faculty of the
Manager layer that reads a task and reports which of the user's declared
skills, targets and projects it names.

**This is not the matcher, and the difference is the whole design.**

    match_opportunity   "this posting requires X, Y, Z - does the user have
                         them?" Requirements come from the posting, so a
                         missing one is a real gap, and coverage means
                         something.

    recognise           "which of the user's own declared abilities does this
                         work name?" The vocabulary comes entirely from
                         USER.md, so nothing can be invented - but for the
                         same reason there is nothing to score.

That second point is why **Recognition carries no score, band, coverage or
percentage.** Feeding recognised skills into match_opportunity would compute
coverage as matched-over-required where every "required" skill is one the user
already has: coverage 1.0, band STRONG, every time. A band produced that way is
a tautology wearing a number, and it would be read as a judgement. Recognition
reports what it saw and leaves judging to whatever reasons about it.

**Closed set, always.** Every candidate word comes from the user's own
document, via the same normalisation and alias table the profile system already
uses. A skill is recognised only when the user declared it *and* the task names
it. There is no path here that could invent an ability, infer one from a task
that merely sounds related, or harvest a technology word out of prose - the
same rule extract_skills already lives under, for the same reason.

**Learning is context, never ability.** A LEARNING skill named by the task is
reported in its own field and never in `strong` or `working`. Being partway
through learning something is not having it, and that rule is enforced here as
it is in matching.py.

**Not a Brain.** Recognition reasons about nothing. It produces structured
evidence, and `notes()` renders that evidence as inert strings for Alfred's
existing Brain through the `deliberate(task, notes=...)` channel that has
always accepted caller context. Being deterministic, it works identically in
Half Brain - the recognition still happens and is still structured; the brain
simply answers that it has no view. Nothing here requires a model, and nothing
here is a second reasoning system.

**Information, never authority.** A recognised skill is a fact about a person.
It cannot become a capability, register a handler or a specialist, grant a tool
or a permission, widen a scope, or change which brain mode is in force - and
the type below has no field in which any of those could be expressed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from src.core.contracts import Task, tokenize
from src.services.profile.profile import (
    SKILL_ALIASES,
    SkillTier,
    UserProfile,
    normalize_skill,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Words too short or too common to carry meaning when matching a target
# phrase. Kept tiny and explicit: every entry here makes recognition slightly
# more willing to fire, which is the direction that risks a false positive.
_TARGET_NOISE: frozenset[str] = frozenset({"and", "the", "for", "with", "of", "in", "a"})


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Whole-phrase match, tolerant of extra whitespace inside the phrase.

    Boundaries are lookarounds on alphanumerics rather than ``\\b`` because
    real skill names end in punctuation - "c++", "node.js", ".net" - and a
    word boundary after "+" does not mean what it appears to. Without any
    boundary at all, "go" would match inside "algorithm" and the user would be
    credited with a language because a task mentioned sorting.
    """
    escaped = re.escape(phrase.strip()).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


def _candidate_spellings(declared: Iterable[str]) -> dict[str, str]:
    """Every spelling that means one of the user's declared skills.

    Maps spelling -> the normalised skill it stands for. Built from the
    profile's own skills plus the alias table the profile system already uses,
    so a task written as "JS" is recognised as the "javascript" the user
    declared. No spelling reaches this map unless it resolves to a skill the
    user actually has: the set stays closed.
    """
    known = set(declared)
    spellings = {skill: skill for skill in known}
    for alias, canonical in SKILL_ALIASES.items():
        if canonical in known:
            spellings.setdefault(alias, canonical)
    return spellings


@dataclass(frozen=True)
class Recognition:
    """What a task named from the user's own declared context.

    Frozen, and holding only names the user wrote down. Note the fields that
    are deliberately absent: no score, no band, no coverage, no percentage, no
    capability, no permission, no tool, no specialist, no brain mode. There is
    nowhere in this type to put authority, which is what makes "user skills are
    data, not permissions" structural rather than a rule to remember.
    """

    strong: tuple[str, ...] = ()
    working: tuple[str, ...] = ()
    # Named because the user is learning it and the work touches it. Never an
    # ability, never counted as one, and reported separately so that nothing
    # downstream can mistake the two.
    learning: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    # False when no profile is configured, or when the profile has no skills.
    # An empty profile cannot support honest reasoning about a person, and
    # saying so beats behaving as though it could.
    known_user: bool = False

    @property
    def is_empty(self) -> bool:
        """Whether the task named nothing from the user's context."""
        return not (
            self.strong or self.working or self.learning or self.targets or self.projects
        )

    @property
    def demonstrated(self) -> tuple[str, ...]:
        """Skills the user genuinely has that this work names.

        STRONG and WORKING only. Learning is excluded here as deliberately as
        it is excluded from a match: this property exists precisely so that a
        caller wanting "what can they actually do" cannot reach for something
        that quietly includes what they cannot.
        """
        return tuple(sorted(self.strong + self.working))

    def describe(self) -> str:
        """A short line safe for logs and payloads.

        Counts only. The profile is the user's, not the system's to display,
        and this string reaches structured payloads the same way
        UserProfile.describe() does - so it carries how much was recognised
        and never what.
        """
        if not self.known_user:
            return "no profile configured"
        return (
            f"{len(self.strong)} strong, {len(self.working)} working, "
            f"{len(self.learning)} learning skill(s); "
            f"{len(self.targets)} target(s); {len(self.projects)} project(s)"
        )

    def notes(self) -> tuple[str, ...]:
        """Inert evidence for a brain, phrased so it cannot be misread.

        Named skills appear here because evidence without names is not
        evidence - and a brain has always been shown the user's skills, as
        llm_interpreter already does when judging an opportunity. What this
        method is careful about is *claims*: the learning line says plainly
        that it is not an ability, so a model reading it cannot fold it into
        what the user can do.

        Strings, and only strings. A brain receives no object it could act
        through.
        """
        if not self.known_user:
            return ("no user profile is configured, so nothing is known about who this is for",)

        notes: list[str] = []
        if self.strong:
            notes.append(
                f"the user has demonstrated {', '.join(self.strong)}, which this work names"
            )
        if self.working:
            notes.append(
                f"the user can work with {', '.join(self.working)} with support"
            )
        if self.learning:
            notes.append(
                f"the user is currently learning {', '.join(self.learning)} - "
                "context only, not an ability they have"
            )
        if self.targets:
            notes.append(f"this aligns with a stated target: {', '.join(self.targets)}")
        if self.projects:
            notes.append(f"supporting work of theirs: {', '.join(self.projects)}")
        if not notes:
            notes.append(
                "nothing in the user's declared skills, targets or projects is named here"
            )
        return tuple(notes)


def recognise(task: Task, profile: UserProfile | None) -> Recognition:
    """Read a task against the user's declared context.

    Deterministic and side-effect free: the same task and profile always give
    the same answer, nothing is written, and the profile is only read. No model
    is consulted and none is needed, which is what lets this run unchanged in
    Half Brain.
    """
    if profile is None or profile.is_empty:
        return Recognition(known_user=False)

    text = task.description
    spellings = _candidate_spellings(profile.skills)

    found: dict[str, SkillTier] = {}
    for spelling, canonical in spellings.items():
        if canonical in found:
            continue
        if _phrase_pattern(spelling).search(text):
            tier = profile.tier_of(canonical)
            if tier is not None:
                found[canonical] = tier

    by_tier: dict[SkillTier, list[str]] = {tier: [] for tier in SkillTier}
    for skill, tier in found.items():
        by_tier[tier].append(skill)

    strong = tuple(sorted(by_tier[SkillTier.STRONG]))
    working = tuple(sorted(by_tier[SkillTier.WORKING]))
    learning = tuple(sorted(by_tier[SkillTier.LEARNING]))

    # Projects are evidence for what the user can actually do, so a LEARNING
    # skill must not pull one in. Reuses the profile's own project/skill
    # relationship rather than deciding here what a project demonstrates.
    projects = profile.projects_evidencing(strong + working)

    return Recognition(
        strong=strong,
        working=working,
        learning=learning,
        targets=_recognised_targets(text, profile),
        projects=tuple(sorted(projects)),
        known_user=True,
    )


def _recognised_targets(text: str, profile: UserProfile) -> tuple[str, ...]:
    """The user's declared targets that this task genuinely names.

    Every significant word of the target must appear. That is stricter than
    the partial-overlap rule matching.py uses when scoring a posting, and the
    asymmetry is intended: a scored match reports its own confidence, while a
    recognition is handed to a brain as a plain statement of fact. A target
    claimed on partial evidence would be a false fact rather than a weak score.
    """
    tokens = tokenize(text)
    recognised = []
    for target in sorted(profile.targets):
        words = {w for w in tokenize(target) if w not in _TARGET_NOISE and len(w) > 2}
        if words and words <= tokens:
            recognised.append(target)
    return tuple(recognised)


def normalised_vocabulary(profile: UserProfile | None) -> frozenset[str]:
    """Every skill name recognition is willing to see. Exposed for tests.

    The point of being able to ask: the answer is always exactly the user's own
    declared skills, so a test can assert the set is closed rather than trusting
    that it is.
    """
    if profile is None:
        return frozenset()
    return frozenset(normalize_skill(skill) for skill in profile.skills)
