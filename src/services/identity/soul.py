"""Loads SOUL.md - how the Manager behaves.

Carries **soft** policy only: verbosity, quiet runs, retry counts, reporting
limits. It deliberately cannot grant a capability.

The hard boundaries live in code and in the OAuth scope:

    - the Gmail token carries gmail.readonly and physically cannot write
    - no implementation of the Outbox interface exists in the project
    - EmailAgent.can_send is False and send-style requests are refused

``BehaviourPolicy.forbids()`` therefore reports policy intent; it is not what
stops the system from sending. A safety property that could be edited away in
a markdown file would not be a safety property, and there is a test asserting
that editing SOUL.md cannot enable sending.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_SOUL_PATH = "SOUL.md"

_HEADING = re.compile(r"^#{2,3}\s*(.+?)\s*$")
_KEY_VALUE = re.compile(r"^\s*([A-Za-z_ ]+)\s*:\s*(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}

# Actions this project never performs. Kept as a constant so the default holds
# even if SOUL.md is emptied or deleted.
DEFAULT_NEVER: tuple[str, ...] = (
    "send", "reply", "compose", "draft", "forward", "delete", "trash",
    "modify", "label", "mark-read", "archive", "apply", "bid", "contact",
)


class SoulError(Exception):
    """Raised when a behaviour document cannot be understood."""


@dataclass(frozen=True)
class BehaviourPolicy:
    """Soft behavioural policy for the Manager."""

    report_only: bool = True
    report_when_nothing_new: bool = False
    max_attempts_per_mission: int = 1
    max_opportunities_reported: int = 5
    include_ambiguous: bool = True
    never: tuple[str, ...] = DEFAULT_NEVER
    principles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_attempts_per_mission < 1:
            raise SoulError("max_attempts_per_mission must be at least 1")
        if self.max_opportunities_reported < 1:
            raise SoulError("max_opportunities_reported must be at least 1")
        # The never-list may be extended by SOUL.md but never shortened below
        # the built-in default: policy can tighten, never loosen.
        merged = tuple(dict.fromkeys(tuple(self.never) + DEFAULT_NEVER))
        object.__setattr__(self, "never", merged)

    def forbids(self, action: str) -> bool:
        """Whether policy declares this action off-limits.

        Advisory. The structural guarantees are elsewhere - see the module
        docstring.
        """
        return action.strip().lower() in self.never

    def describe(self) -> str:
        return (
            f"report_only={self.report_only} "
            f"quiet_runs={not self.report_when_nothing_new} "
            f"max_report={self.max_opportunities_reported} "
            f"never={len(self.never)} action(s)"
        )


def _as_bool(value: str, key: str) -> bool:
    text = value.strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise SoulError(f"'{key}' must be true or false, got {value!r}")


def _as_int(value: str, key: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise SoulError(f"'{key}' must be a whole number, got {value!r}") from None


def parse_soul(text: str) -> BehaviourPolicy:
    """Parse SOUL.md. Exposed separately so tests need no file."""
    section = ""
    policy: dict[str, object] = {}
    never: list[str] = []
    principles: list[str] = []

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(1).strip().lower()
            continue

        if section == "policy":
            pair = _KEY_VALUE.match(line)
            if not pair:
                continue
            key, value = pair.group(1).strip().lower().replace(" ", "_"), pair.group(2)
            if key in ("report_only", "report_when_nothing_new", "include_ambiguous"):
                policy[key] = _as_bool(value, key)
            elif key in ("max_attempts_per_mission", "max_opportunities_reported"):
                policy[key] = _as_int(value, key)

        elif section == "never":
            never.extend(p.strip().lower() for p in line.split(",") if p.strip())

        elif section == "principles":
            bullet = _BULLET.match(line)
            if bullet:
                principles.append(bullet.group(1).strip())

    return BehaviourPolicy(
        never=tuple(never) if never else DEFAULT_NEVER,
        principles=tuple(principles),
        **policy,  # type: ignore[arg-type]
    )


def load_soul(path: str | Path = DEFAULT_SOUL_PATH) -> BehaviourPolicy:
    """Load behaviour policy from disk.

    A missing SOUL.md is not fatal: the built-in defaults are the safe ones
    (report only, quiet runs, no external action), so the system degrades to
    its most conservative behaviour rather than refusing to run.
    """
    soul_file = Path(path)
    if not soul_file.exists():
        logger.warning(
            "no behaviour document at '%s'; using conservative defaults", soul_file
        )
        return BehaviourPolicy()

    try:
        text = soul_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise SoulError(f"Could not read '{soul_file}': {exc}") from exc

    policy = parse_soul(text)
    logger.info("loaded behaviour policy: %s", policy.describe())
    return policy
