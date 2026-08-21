"""What a specialist is allowed to remember about its own domain.

A specialist keeps different things from the orchestrator, so it needs its own
vocabulary rather than a share of the Manager's:

    Manager     USER_CONTEXT, DECISION, DELEGATION_OUTCOME, LESSON
    Specialist  DOMAIN_LESSON, DOMAIN_PATTERN, DOMAIN_PREFERENCE

The two never mix. A specialist writing DELEGATION_OUTCOME would be recording
an orchestration event it has no view of, and the Manager writing DOMAIN_PATTERN
would be claiming domain expertise it does not have.

**There is deliberately no DOMAIN_OUTCOME.** It is the obvious fourth category
and it is a trap twice over: it duplicates what the Manager already records
about every delegation, and it is exactly the routine execution history that
turns a memory into a log. "Examined four messages, found two" changes no future
decision. A specialist that recorded one of those per run would, within a week,
have a store where nothing useful could be found.

That is the principle the whole module encodes: **prefer durable knowledge over
history.** Three checks do the work, and each refuses a different way of
accidentally storing a diary entry:

    category     only the three above; anything else is not specialist memory
    confidence   a tentative observation is not yet knowledge
    routine      content that merely narrates a run is refused outright

Deliberately not model-backed. The policy is plain rules, so it is fully
deterministic and testable without a provider - the brain may *propose*
something worth keeping, but whether it is admitted must be answerable without
asking a model a second time.

This implements the existing MemoryPolicy interface unchanged, and judges the
existing MemoryRecord. No parallel candidate model is introduced: a record
already carries content, a category (``kind``), tags and context, which is
everything an admission decision needs.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from src.services.memory.memory import MemoryRecord
from src.services.memory.policy import MemoryPolicy, PolicyVerdict
from src.services.memory.secrets import looks_like_a_secret


class SpecialistMemoryCategory(str, Enum):
    """The only kinds of thing a specialist retains about its own work."""

    DOMAIN_LESSON = "domain_lesson"
    DOMAIN_PATTERN = "domain_pattern"
    DOMAIN_PREFERENCE = "domain_preference"

    @property
    def label(self) -> str:
        return {
            "domain_lesson": "DOMAIN LESSON",
            "domain_pattern": "DOMAIN PATTERN",
            "domain_preference": "DOMAIN PREFERENCE",
        }[self.value]

    @property
    def meaning(self) -> str:
        return {
            "domain_lesson": (
                "Something learned while doing this work that should change how "
                "similar work is approached next time."
            ),
            "domain_pattern": (
                "A regularity in the material itself - something that keeps being "
                "true of the things this specialist handles."
            ),
            "domain_preference": (
                "A preference stated within this specialist's domain that should "
                "shape how future work in it is judged."
            ),
        }[self.value]

    @classmethod
    def of(cls, record: MemoryRecord) -> "SpecialistMemoryCategory | None":
        try:
            return cls(record.kind)
        except ValueError:
            return None


# Categories that belong to the orchestrator. Named so a specialist writing one
# is refused with an explanation rather than a bare "unknown category" - the
# mistake is worth being clear about, because it means two components have
# started disagreeing about who owns a fact.
MANAGER_CATEGORIES: dict[str, str] = {
    "user_context": "user context belongs to the manager, not to a specialist",
    "decision": "orchestration decisions belong to the manager",
    "delegation_outcome": "delegation is the manager's concern; a specialist does "
    "not see it",
    "lesson": "use domain_lesson; a specialist's lessons are about its own domain",
}

# Phrasings that describe a run rather than something learned from it. A
# specialist narrating its own execution is writing a log entry, and a log
# entry in a memory store is worse than no entry at all - it displaces the
# things worth finding.
_ROUTINE_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bexamined\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bprocessed\s+\d+\b", re.IGNORECASE),
    re.compile(r"\banalys?zed\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bfound\s+\d+\s+(match|result|item|opportunit)", re.IGNORECASE),
    re.compile(r"\b\d+\s+message\(?s?\)?\s+(were\s+)?(read|scanned|fetched)\b", re.IGNORECASE),
    re.compile(r"^\s*(processed|handled|completed|ran)\b", re.IGNORECASE),
    re.compile(r"\btask\s+[\w-]+\s+(completed|succeeded|failed)\b", re.IGNORECASE),
)

# Below this, an observation is a hunch. Storing hunches as knowledge is how a
# memory starts asserting things nobody actually established.
MIN_CONFIDENCE = 0.5

# Distilled knowledge is short. Anything far longer is usually a dump of
# something else wearing a plausible category.
MAX_CONTENT_CHARS = 500


class SpecialistMemoryPolicy(MemoryPolicy):
    """A specialist's admission rules, as an object rather than a convention.

    Stateless and deterministic: the same record always gets the same verdict.
    Safe to share between specialists, unlike a Memory, which must not be.
    """

    def __init__(
        self,
        min_confidence: float = MIN_CONFIDENCE,
        max_content_chars: int = MAX_CONTENT_CHARS,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if max_content_chars <= 0:
            raise ValueError("max_content_chars must be positive")

        self.min_confidence = float(min_confidence)
        self.max_content_chars = max_content_chars

    def __repr__(self) -> str:
        return f"<SpecialistMemoryPolicy categories={len(self.categories)}>"

    @property
    def categories(self) -> frozenset[SpecialistMemoryCategory]:
        return frozenset(SpecialistMemoryCategory)

    @property
    def category_names(self) -> tuple[str, ...]:
        return tuple(sorted(c.value for c in SpecialistMemoryCategory))

    def describe(self) -> str:
        """The policy in a form that can be shown to a user."""
        lines = ["A specialist remembers only:"]
        for category in sorted(SpecialistMemoryCategory, key=lambda c: c.value):
            lines.append(f"  {category.label} - {category.meaning}")
        lines.append(
            "Routine execution history, the manager's categories, tentative "
            "observations and anything resembling a credential are refused."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # The decision
    # ------------------------------------------------------------------

    def evaluate(self, record: MemoryRecord) -> PolicyVerdict:
        """Judge one record against the specialist's rules."""
        if not isinstance(record, MemoryRecord):
            return PolicyVerdict(
                allowed=False,
                reason=f"only a MemoryRecord can be judged, got {type(record).__name__}",
            )

        kind = record.kind

        # 1. A manager category, named so the mistake is legible.
        if kind in MANAGER_CATEGORIES:
            return PolicyVerdict(
                allowed=False, reason=MANAGER_CATEGORIES[kind], category=kind
            )

        # 2. The closed allow-list, which does most of the work.
        category = SpecialistMemoryCategory.of(record)
        if category is None:
            allowed = ", ".join(self.category_names)
            return PolicyVerdict(
                allowed=False,
                reason=(
                    f"'{kind or '(none)'}' is not something a specialist remembers; "
                    f"allowed categories are: {allowed}"
                ),
                category=kind or None,
            )

        # 3. Credentials, refused under every category.
        secret = looks_like_a_secret(record.content, record.tags, record.context)
        if secret is not None:
            return PolicyVerdict(
                allowed=False,
                # The kind of secret, never the value.
                reason=f"refused: the record appears to contain {secret}",
                category=category.value,
            )

        # 4. Routine narration - a log entry, not knowledge.
        if self._is_routine(record.content):
            return PolicyVerdict(
                allowed=False,
                reason=(
                    "this describes what happened during a run rather than "
                    "something learned from it; routine history is not retained"
                ),
                category=category.value,
            )

        # 5. Confidence. A hunch stored as knowledge misleads later.
        confidence = self._confidence_of(record)
        if confidence < self.min_confidence:
            return PolicyVerdict(
                allowed=False,
                reason=(
                    f"confidence {confidence:.2f} is below the {self.min_confidence:.2f} "
                    f"needed to retain an observation as knowledge"
                ),
                category=category.value,
            )

        # 6. Length, which catches a dump under a plausible category.
        if len(record.content) > self.max_content_chars:
            return PolicyVerdict(
                allowed=False,
                reason=(
                    f"{len(record.content)} characters is too long to be distilled "
                    f"knowledge (limit {self.max_content_chars})"
                ),
                category=category.value,
            )

        return PolicyVerdict(
            allowed=True,
            reason=f"{category.label.lower()} is retained by this specialist",
            category=category.value,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_routine(content: str) -> bool:
        return any(marker.search(content) for marker in _ROUTINE_MARKERS)

    @staticmethod
    def _confidence_of(record: MemoryRecord) -> float:
        """How sure the specialist was, read from the record's context.

        Carried in context rather than as a new field on MemoryRecord: the
        storage contract has no opinion about confidence, and giving it one to
        serve a single policy would push a policy concern into a dumb store.

        Anything unreadable counts as no confidence at all. Treating a
        malformed value as certainty is how a bad record talks its way in.
        """
        raw: Any = (record.context or {}).get("confidence")
        if isinstance(raw, bool) or raw is None:
            return 0.0
        if isinstance(raw, (int, float)):
            return float(raw) if 0.0 <= float(raw) <= 1.0 else 0.0
        if isinstance(raw, str):
            try:
                value = float(raw.strip())
            except ValueError:
                return 0.0
            return value if 0.0 <= value <= 1.0 else 0.0
        return 0.0
