"""The user profile: who the agent serves.

This is the AGENT framework's USER.md expressed as data. It exists so the
EmailAgent can answer "is this opportunity realistic for *this* person" rather
than "does this email mention Python".

The central design rule is honesty. Skills are held in three tiers, and the
tier decides what may be claimed:

    STRONG   - demonstrated; counts fully toward a match
    WORKING  - usable with support; counts partially
    LEARNING - in progress; NEVER counted as a match, only mentioned

A skill absent from the profile is missing, full stop. Nothing in this module
infers a skill from a job description, and nothing promotes a LEARNING skill
just because a posting wants it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping

# Seniority levels in ascending order. Used to judge whether a posting is a
# reasonable stretch or out of reach.
SENIORITY_ORDER: tuple[str, ...] = (
    "intern",
    "graduate",
    "junior",
    "mid",
    "senior",
    "lead",
    "principal",
)

# Different spellings of the same thing, normalized on the way in. Kept small
# and explicit: a wrong alias silently inflates a match, so this table only
# contains pairs that are genuinely the same skill.
SKILL_ALIASES: Mapping[str, str] = {
    "js": "javascript",
    "node": "node.js",
    "nodejs": "node.js",
    "py": "python",
    "python3": "python",
    "restful apis": "apis",
    "rest apis": "apis",
    "api": "apis",
    "env vars": "environment variables",
    "unit testing": "testing",
    "automated testing": "testing",
    "llms": "llm concepts",
    "large language models": "llm concepts",
    "prompt design": "prompt engineering",
    "ci/cd": "ci",
}


def normalize_skill(skill: str) -> str:
    """Lower-case, trim and de-alias a skill name."""
    cleaned = " ".join(skill.strip().lower().split())
    return SKILL_ALIASES.get(cleaned, cleaned)


def normalize_seniority(value: str | None) -> str | None:
    """Map free text onto a known seniority level, or None if unrecognised."""
    if not value:
        return None
    text = value.strip().lower()
    for level in SENIORITY_ORDER:
        if level in text:
            return level
    # Common synonyms that do not contain a level word.
    if any(word in text for word in ("entry level", "entry-level", "trainee")):
        return "junior"
    if any(word in text for word in ("experienced", "expert")):
        return "senior"
    return None


class SkillTier(str, Enum):
    """How well the user knows a skill, and therefore what may be claimed."""

    STRONG = "strong"
    WORKING = "working"
    LEARNING = "learning"

    @property
    def weight(self) -> float:
        """Contribution to a match score. LEARNING deliberately counts zero."""
        return {"strong": 1.0, "working": 0.5, "learning": 0.0}[self.value]


class ProfileError(Exception):
    """Raised when a profile is missing or cannot be understood."""


@dataclass(frozen=True)
class Project:
    """A piece of work that can serve as evidence for specific skills.

    ``skills`` is what this project actually demonstrates - not everything it
    touched. A project counts as evidence only for capabilities it really
    shows, which is what keeps "supporting projects" in a report truthful.
    """

    name: str
    description: str = ""
    skills: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Project.name must not be empty")
        object.__setattr__(
            self, "skills", frozenset(normalize_skill(s) for s in self.skills if s.strip())
        )

    def evidences(self, skill: str) -> bool:
        return normalize_skill(skill) in self.skills


@dataclass(frozen=True)
class UserProfile:
    """Everything the matcher is allowed to know about the user."""

    skills: Mapping[str, SkillTier] = field(default_factory=dict)
    projects: tuple[Project, ...] = ()
    seniority: str | None = None
    years: float | None = None
    targets: frozenset[str] = field(default_factory=frozenset)
    hard_filters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized = {
            normalize_skill(name): tier
            for name, tier in self.skills.items()
            if name.strip()
        }
        object.__setattr__(self, "skills", normalized)
        object.__setattr__(self, "seniority", normalize_seniority(self.seniority))
        object.__setattr__(
            self, "targets", frozenset(t.strip().lower() for t in self.targets if t.strip())
        )

    # ------------------------------------------------------------------
    # Queries the matcher uses
    # ------------------------------------------------------------------

    def tier_of(self, skill: str) -> SkillTier | None:
        """The tier for a skill, or None if the user does not have it at all."""
        return self.skills.get(normalize_skill(skill))

    def demonstrates(self, skill: str) -> bool:
        """True only for STRONG or WORKING. Learning is never 'has'."""
        tier = self.tier_of(skill)
        return tier in (SkillTier.STRONG, SkillTier.WORKING)

    def skills_in(self, tier: SkillTier) -> frozenset[str]:
        return frozenset(name for name, value in self.skills.items() if value is tier)

    def projects_evidencing(self, skills: Iterable[str]) -> tuple[str, ...]:
        """Names of projects that demonstrate at least one of these skills."""
        wanted = {normalize_skill(s) for s in skills}
        return tuple(
            project.name
            for project in self.projects
            if project.skills & wanted
        )

    def seniority_rank(self) -> int | None:
        if self.seniority is None:
            return None
        return SENIORITY_ORDER.index(self.seniority)

    @property
    def is_empty(self) -> bool:
        return not self.skills

    def describe(self) -> str:
        """A short, non-sensitive summary safe for logs."""
        return (
            f"{len(self.skills)} skill(s), {len(self.projects)} project(s), "
            f"seniority={self.seniority or 'unstated'}"
        )
