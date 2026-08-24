"""Reads SCHEDULE.md into a UserSchedule.

Markdown for the same reason USER.md is: a document the user edits by hand and
re-reads, not a config file. Updating it means editing the file and re-running.

Parsed by ``parse_sections`` - the section splitter already shared by
IDENTITY.md and the specialist identity documents - rather than a third parser
written to look like the others. Two parsers drifting apart is how documents
that appear to share a format quietly stop doing so.

Nothing here resolves a time. Lines are trimmed and stored as written, because
turning "09:00-17:00" into an instant needs a timezone and a clock, and both
are owned by services/memory/day.py.
"""

from __future__ import annotations

from pathlib import Path

from src.services.identity.identity import parse_sections
from src.services.schedule.schedule import ScheduleError, UserSchedule
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_SCHEDULE_PATH = "SCHEDULE.md"

# Headings this document understands. Anything else is ignored, exactly as the
# profile parser ignores sections it does not know - which is what lets a user
# keep notes in the file, and what stops anything written there reaching the
# system by accident.
_WORKING = ("working hours", "working", "hours", "availability")
_QUIET = ("quiet hours", "quiet", "unavailable", "do not disturb")
_COMMITMENTS = ("commitments", "commitment", "busy", "recurring")


def _lines(sections: dict[str, list[str]], keys: tuple[str, ...]) -> tuple[str, ...]:
    """Every line under any of these headings, bullet markers stripped.

    Whole lines rather than a comma split: "monday-friday 09:00-17:00" is one
    statement, and splitting it would turn a person's week into fragments that
    mean nothing on their own. That is the opposite of how the capabilities
    section is read, and deliberately so - one is a list, this is prose.
    """
    collected: list[str] = []
    for key in keys:
        for line in sections.get(key, []):
            cleaned = line.lstrip("-*").strip()
            if cleaned:
                collected.append(cleaned)
    return tuple(dict.fromkeys(collected))


def parse_schedule(text: str) -> UserSchedule:
    """Parse schedule markdown. Exposed separately so tests need no file."""
    sections = parse_sections(text)
    return UserSchedule(
        working_hours=_lines(sections, _WORKING),
        quiet_hours=_lines(sections, _QUIET),
        commitments=_lines(sections, _COMMITMENTS),
        # Parsed at all means a document was supplied, even an empty one.
        # "Written and left blank" is a statement; "absent" is not.
        configured=True,
    )


def load_schedule(path: str | Path = DEFAULT_SCHEDULE_PATH) -> UserSchedule:
    """Load a UserSchedule from disk, or an unconfigured one if absent.

    A missing file is not an error and was never meant to be: most people will
    never write one, and refusing to run without it would make an optional
    convenience compulsory. An *unreadable* file does raise, because a document
    that exists is meant to take effect.
    """
    schedule_file = Path(path)
    if not schedule_file.exists():
        logger.debug("no schedule document at '%s'", schedule_file)
        return UserSchedule()

    try:
        text = schedule_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScheduleError(
            f"Could not read schedule at '{schedule_file}': {exc}"
        ) from exc

    schedule = parse_schedule(text)
    logger.info("loaded user schedule: %s", schedule.describe())
    return schedule
