"""What every agent knows about the person it serves - one canonical view.

**The gap this closes (Pre-Stage-9 Campaign 1, LRN-06).** Two documents
described the same person and never met:

    USER.md (UserProfile)        hand-written skills, projects, seniority,
                                 targets - what the email specialist ranked
                                 opportunities against
    personal context (Memory)    everything the person taught Alfred in
                                 conversation - "I've learned X", "I'm
                                 learning Y" - superseding as it changes

So "I've learned Rust" was remembered by Alfred and invisible to Arthur,
whose analyzer still read a profile that said nothing about it.

``UserKnowledge`` is the one read-only view both are built from. The base
document stays exactly what its owner wrote; the canonical personal context
is layered over it, read **live** on every call, so a fact taught a moment
ago is already part of the profile the next ranking uses - no restart, no
second store, no copy that could go stale.

**Honest tiers, never inflated.** The overlay follows the profile module's
own rule that claims must be earned:

    "I've learned X"   (category "skill")      -> WORKING at least - usable,
                                                   never promoted to STRONG
                                                   by saying so
    "I'm learning X"   (category "learning")   -> LEARNING - never counted
                                                   as a match
    discontinued                                -> an overlay skill is
                                                   dropped; the owner's own
                                                   document is never edited

A document tier above what conversation implies always wins, so telling
Alfred "I'm learning X" never demotes a skill USER.md already lists as
STRONG.

**A grant, not a reach.** A specialist receives this object from the
composition root the same way it receives its tools - it holds a bound,
read-only ``recall`` over the canonical store and can neither write to it
nor reach any other memory through it. No agent is named here.
"""

from __future__ import annotations

from typing import Callable, Iterable

from src.services.memory.memory import MemoryRecord
from src.services.memory.personal_context import (
    STATUS_CURRENT,
    PersonalContextRecord,
    all_current_personal_context,
)
from src.services.profile.profile import SkillTier, UserProfile, normalize_skill

_Recall = Callable[..., "Iterable[MemoryRecord]"]

# How many of the most recent canonical records are considered per read -
# the same "whole current state" bound the Control Center view uses.
_RESOLVE_LIMIT = 200

_TIER_RANK = {SkillTier.LEARNING: 0, SkillTier.WORKING: 1, SkillTier.STRONG: 2}

# Which open categories carry a skill claim at all. Categories are labels
# personal_context.py writes, never subjects - "a project called X" is
# context, not a skill, and is never folded into skills.
_TIER_FOR_CATEGORY = {"skill": SkillTier.WORKING, "learning": SkillTier.LEARNING}


def effective_profile(
    base: "UserProfile | None", items: "Iterable[PersonalContextRecord]",
) -> "UserProfile | None":
    """``base`` with the canonical personal context layered over it.

    Returns ``base`` itself (possibly None) when conversation added no skill
    claim at all, so a system with nothing taught behaves exactly as it did
    before this view existed."""
    skills: dict[str, SkillTier] = dict(base.skills) if base is not None else {}
    changed = False
    for item in items:
        tier = _TIER_FOR_CATEGORY.get(item.category)
        if tier is None:
            continue
        name = normalize_skill(item.topic)
        if not name:
            continue
        if item.status != STATUS_CURRENT:
            continue
        existing = skills.get(name)
        if existing is None or _TIER_RANK[tier] > _TIER_RANK[existing]:
            skills[name] = tier
            changed = True
    if not changed:
        return base
    if base is None:
        return UserProfile(skills=skills)
    return UserProfile(
        skills=skills, projects=base.projects, seniority=base.seniority,
        years=base.years, targets=base.targets, hard_filters=base.hard_filters,
    )


class UserKnowledge:
    """The one view of the person, shared by every agent that is granted it."""

    def __init__(self, *, base_profile: "UserProfile | None", recall: "_Recall | None") -> None:
        self._base = base_profile
        self._recall = recall

    @property
    def base_profile(self) -> "UserProfile | None":
        """USER.md as its owner wrote it - never modified by conversation."""
        return self._base

    def current(self) -> "tuple[PersonalContextRecord, ...]":
        """Every topic's current statement, read from the canonical store now."""
        if self._recall is None:
            return ()
        return all_current_personal_context(recall=self._recall, limit=_RESOLVE_LIMIT)

    def profile(self) -> "UserProfile | None":
        """The profile every matcher should use right now: the base document
        plus everything the person has since taught."""
        return effective_profile(self._base, self.current())

    def describe(self) -> str:
        """Counts only, safe for logs - the same rule UserProfile.describe keeps."""
        profile = self.profile()
        base = profile.describe() if profile is not None else "no profile"
        return f"{base}; {len(self.current())} taught topic(s)"
