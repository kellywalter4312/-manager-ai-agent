"""Comparing an opportunity against the user's real profile.

The scoring deliberately combines four independent things rather than counting
keywords: skill coverage, seniority fit, alignment with what the user is
actually looking for, and how much of the posting could be read at all.

Two rules are absolute and enforced by tests:

1. A skill the user does not have is never reported as matched. LEARNING-tier
   skills are surfaced as "in progress", never folded into "you have".
2. Missing information is never invented. If the posting did not state its
   requirements, the result is AMBIGUOUS with the reason recorded - not a
   guess dressed up as a verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.agents.email.opportunity import MatchBand
from src.services.profile.profile import (
    SENIORITY_ORDER,
    SkillTier,
    UserProfile,
    normalize_seniority,
    normalize_skill,
)

# Banding thresholds, gathered here so they can be tuned in one place.
STRONG_COVERAGE = 0.7
POSSIBLE_COVERAGE = 0.4
MIN_CONFIDENCE = 0.4

# How far above the user's level still counts as a reasonable stretch.
STRETCH_LEVELS = 1

# Score weights (sum to 100).
WEIGHT_COVERAGE = 60.0
WEIGHT_SENIORITY = 25.0
WEIGHT_TARGET = 15.0

_RED_FLAG_RULES: tuple[tuple[str, str], ...] = (
    (r"\bunpaid\b|\bno pay\b|\bfor exposure\b", "advertised as unpaid"),
    (r"\bcommission[- ]only\b", "commission-only compensation"),
    (r"\bequity[- ]only\b", "equity-only compensation"),
    (r"\bpay (?:a )?fee\b|\bregistration fee\b|\bpay to apply\b", "asks for payment to apply"),
    (r"\burgent(?:ly)?\b.*\bapply\b|\bimmediate start\b", "urgency pressure"),
    (r"\bcrypto\b.*\binvest\b|\bguaranteed (?:income|returns)\b", "possible scam markers"),
)


@dataclass(frozen=True)
class MatchResult:
    """The outcome of comparing one opportunity to the profile."""

    band: MatchBand
    score: float
    confidence: float
    matched: tuple[str, ...] = ()
    partial: tuple[str, ...] = ()
    learning: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    supporting_projects: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()


def find_red_flags(text: str) -> tuple[str, ...]:
    """Rule-based warnings about the opportunity itself."""
    lowered = text.lower()
    flags = [note for pattern, note in _RED_FLAG_RULES if re.search(pattern, lowered)]
    return tuple(dict.fromkeys(flags))


def _bucket_skills(
    required: tuple[str, ...], profile: UserProfile
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Split required skills into matched / partial / learning / missing.

    The four buckets are disjoint and exhaustive: every required skill lands in
    exactly one, so nothing can be double-counted or quietly dropped.
    """
    matched, partial, learning, missing = [], [], [], []
    for skill in required:
        tier = profile.tier_of(skill)
        if tier is SkillTier.STRONG:
            matched.append(normalize_skill(skill))
        elif tier is SkillTier.WORKING:
            partial.append(normalize_skill(skill))
        elif tier is SkillTier.LEARNING:
            learning.append(normalize_skill(skill))
        else:
            missing.append(normalize_skill(skill))
    return tuple(matched), tuple(partial), tuple(learning), tuple(missing)


def _seniority_fit(required: str | None, profile: UserProfile) -> tuple[float, str | None, bool]:
    """Return (fit 0-1, explanation, is_disqualifying)."""
    level = normalize_seniority(required)
    user_rank = profile.seniority_rank()
    if level is None or user_rank is None:
        return 0.5, None, False

    gap = SENIORITY_ORDER.index(level) - user_rank
    if gap <= 0:
        return 1.0, f"seniority ({level}) is at or below your level", False
    if gap <= STRETCH_LEVELS:
        return 0.5, f"seniority ({level}) is one level above you - a stretch", False
    return 0.0, f"seniority ({level}) is well above your stated level", True


def _target_alignment(title: str | None, profile: UserProfile) -> tuple[float, str | None]:
    """How well the role title matches what the user is looking for."""
    if not title or not profile.targets:
        return 0.5, None
    words = set(re.findall(r"[a-z0-9.+#]+", title.lower()))
    for target in sorted(profile.targets):
        target_words = set(re.findall(r"[a-z0-9.+#]+", target))
        if target_words and len(target_words & words) >= max(1, len(target_words) - 1):
            return 1.0, f"matches your target '{target}'"
    return 0.2, "not obviously one of your target areas"


def _confidence(required: tuple[str, ...], title: str | None, missing_fields: tuple[str, ...]) -> float:
    """How much of the posting could actually be read.

    Low confidence is not a poor match - it is an unknown one, and the two are
    reported differently.
    """
    score = 0.0
    if required:
        score += 0.5
    if title:
        score += 0.3
    score += max(0.0, 0.2 - 0.02 * len(missing_fields))
    return round(min(score, 1.0), 2)


def _violates_hard_filter(text: str, profile: UserProfile) -> str | None:
    """Check the posting against the user's non-negotiables."""
    lowered = text.lower()
    for rule in profile.hard_filters:
        keywords = [w for w in re.findall(r"[a-z]+", rule.lower()) if len(w) > 3 and w != "work"]
        if keywords and all(word in lowered for word in keywords):
            return rule
    return None


def match_opportunity(
    profile: UserProfile,
    required_skills: tuple[str, ...],
    preferred_skills: tuple[str, ...] = (),
    title: str | None = None,
    seniority: str | None = None,
    missing_fields: tuple[str, ...] = (),
    text: str = "",
) -> MatchResult:
    """Compare one opportunity to the profile and classify the fit."""
    matched, partial, learning, missing = _bucket_skills(required_skills, profile)
    confidence = _confidence(required_skills, title, missing_fields)

    reasons: list[str] = []
    uncertainties: list[str] = []

    if not required_skills:
        uncertainties.append("the posting does not list its required skills")
    for field_name in missing_fields:
        if field_name in ("salary", "deadline", "seniority", "company"):
            uncertainties.append(f"no {field_name} stated")

    # Coverage counts STRONG fully and WORKING at half. LEARNING contributes
    # nothing - being partway through learning a skill is not having it.
    coverage: float | None = None
    if required_skills:
        earned = len(matched) + SkillTier.WORKING.weight * len(partial)
        coverage = earned / len(required_skills)

    seniority_fit, seniority_note, disqualifying = _seniority_fit(seniority, profile)
    if seniority_note:
        reasons.append(seniority_note)

    target_fit, target_note = _target_alignment(title, profile)
    if target_note:
        reasons.append(target_note)

    score = round(
        WEIGHT_COVERAGE * (coverage if coverage is not None else 0.0)
        + WEIGHT_SENIORITY * seniority_fit
        + WEIGHT_TARGET * target_fit,
        1,
    )

    if matched or partial:
        reasons.append(
            f"you demonstrate {len(matched) + len(partial)} of "
            f"{len(required_skills)} required skill(s)"
        )
    if missing:
        reasons.append(f"you have not demonstrated: {', '.join(missing)}")
    if learning:
        reasons.append(f"currently learning: {', '.join(learning)} - not counted as a match")

    violated = _violates_hard_filter(text, profile)
    band = _classify(
        coverage=coverage,
        confidence=confidence,
        disqualifying_seniority=disqualifying,
        hard_filter=violated,
    )
    if violated:
        reasons.insert(0, f"breaks your hard filter: '{violated}'")

    supporting = profile.projects_evidencing(matched + partial)

    return MatchResult(
        band=band,
        score=score,
        confidence=confidence,
        matched=matched,
        partial=partial,
        learning=learning,
        missing=missing,
        supporting_projects=supporting,
        reasons=tuple(reasons),
        uncertainties=tuple(dict.fromkeys(uncertainties)),
    )


def _classify(
    coverage: float | None,
    confidence: float,
    disqualifying_seniority: bool,
    hard_filter: str | None,
) -> MatchBand:
    """Turn the measurements into a band, in a fixed, explainable order."""
    # A non-negotiable is a veto, whatever the skills look like.
    if hard_filter:
        return MatchBand.POOR
    # Too little information to judge is its own answer, not a poor match.
    if coverage is None or confidence < MIN_CONFIDENCE:
        return MatchBand.AMBIGUOUS
    if disqualifying_seniority:
        return MatchBand.POOR
    if coverage >= STRONG_COVERAGE:
        return MatchBand.STRONG
    if coverage >= POSSIBLE_COVERAGE:
        return MatchBand.POSSIBLE
    return MatchBand.POOR
