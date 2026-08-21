"""Reads USER.md into a UserProfile.

Markdown rather than JSON or YAML on purpose: this is a document the user
edits by hand and re-reads, not a config file. Updating the profile means
editing the file and re-running - no code change, no restart ritual.

The parser is deliberately forgiving about formatting (case, spacing, bullet
style) and strict about meaning: an unreadable file raises ProfileError with
the line that confused it, rather than silently producing an empty profile
that would make every opportunity look like a poor match.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.services.profile.profile import (
    Project,
    ProfileError,
    SkillTier,
    UserProfile,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_PROFILE_PATH = "USER.md"

_HEADING = re.compile(r"^(#{2,3})\s*(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_KEY_VALUE = re.compile(r"^\s*([A-Za-z ]+)\s*:\s*(.+?)\s*$")
# "Name - description [skills: a, b]"
_PROJECT_SKILLS = re.compile(r"\[\s*skills?\s*:\s*(.*?)\s*\]", re.IGNORECASE)

_TIER_BY_NAME = {
    "strong": SkillTier.STRONG,
    "demonstrated": SkillTier.STRONG,
    "working": SkillTier.WORKING,
    "familiar": SkillTier.WORKING,
    "learning": SkillTier.LEARNING,
    "in progress": SkillTier.LEARNING,
}


def _split_list(text: str) -> list[str]:
    """Split a comma-separated line into clean items."""
    return [part.strip() for part in text.split(",") if part.strip()]


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> UserProfile:
    """Load a UserProfile from a markdown file.

    Raises ProfileError if the file is missing or contains no skills - a
    profile with nothing in it cannot support honest matching, so failing
    loudly beats ranking everything as a poor match.
    """
    profile_file = Path(path)
    if not profile_file.exists():
        raise ProfileError(
            f"No profile found at '{profile_file}'. Copy USER.example.md to "
            f"'{profile_file}' and fill in your own skills and projects."
        )

    try:
        text = profile_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"Could not read profile at '{profile_file}': {exc}") from exc

    profile = parse_profile(text)
    if profile.is_empty:
        raise ProfileError(
            f"The profile at '{profile_file}' lists no skills. Add a '## Skills' "
            f"section with at least one '### Strong' or '### Working' entry."
        )

    logger.info("loaded user profile: %s", profile.describe())
    return profile


def parse_profile(text: str) -> UserProfile:
    """Parse profile markdown. Exposed separately so tests need no file."""
    skills: dict[str, SkillTier] = {}
    projects: list[Project] = []
    targets: list[str] = []
    hard_filters: list[str] = []
    seniority: str | None = None
    years: float | None = None

    section = ""
    tier: SkillTier | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        heading = _HEADING.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip().lower()
            if level == 2:
                section, tier = title, None
            else:
                tier = _TIER_BY_NAME.get(title)
                if section.startswith("skill") and tier is None:
                    raise ProfileError(
                        f"Unknown skill tier '{title}'. Expected one of: "
                        f"{', '.join(sorted(_TIER_BY_NAME))}."
                    )
            continue

        if section.startswith("skill"):
            if tier is None:
                # Skills listed without a tier heading are ambiguous, and
                # guessing a tier is exactly the kind of inflation to avoid.
                raise ProfileError(
                    f"Skill line '{line.strip()}' appears before any tier heading. "
                    f"Put it under '### Strong', '### Working' or '### Learning'."
                )
            for item in _split_list(_BULLET.sub(r"\1", line)):
                skills[item] = tier

        elif section.startswith("project"):
            bullet = _BULLET.match(line)
            if not bullet:
                continue
            projects.append(_parse_project(bullet.group(1)))

        elif section.startswith("experience"):
            pair = _KEY_VALUE.match(line)
            if not pair:
                continue
            key, value = pair.group(1).strip().lower(), pair.group(2).strip()
            if key == "seniority":
                seniority = value
            elif key == "years":
                years = _parse_years(value)

        elif section.startswith("target") or section.startswith("looking"):
            content = _BULLET.sub(r"\1", line)
            pair = _KEY_VALUE.match(content)
            targets.extend(_split_list(pair.group(2) if pair else content))

        elif section.startswith("hard filter") or section.startswith("filters"):
            bullet = _BULLET.match(line)
            if bullet:
                hard_filters.append(bullet.group(1).strip())

    return UserProfile(
        skills=skills,
        projects=tuple(projects),
        seniority=seniority,
        years=years,
        targets=frozenset(targets),
        hard_filters=tuple(hard_filters),
    )


def _parse_project(entry: str) -> Project:
    """Parse 'Name - description [skills: a, b]'."""
    skills: list[str] = []
    match = _PROJECT_SKILLS.search(entry)
    if match:
        skills = _split_list(match.group(1))
        entry = _PROJECT_SKILLS.sub("", entry).strip()

    # Accept an em dash, en dash or hyphen as the name/description separator.
    parts = re.split(r"\s+[—–-]\s+", entry, maxsplit=1)
    name = parts[0].strip()
    description = parts[1].strip() if len(parts) > 1 else ""
    if not name:
        raise ProfileError(f"Project entry has no name: '{entry}'")
    return Project(name=name, description=description, skills=frozenset(skills))


def _parse_years(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None
