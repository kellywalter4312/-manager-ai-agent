"""The profile-aware pipeline: detect, split, extract, match, rank.

Kept in its own module so the detection-only path in analyzers.py stays as
small and cheap as it was in Milestone 6, and so this pipeline can be tested
directly.

    messages
      -> detect opportunity signals        (analyzers._SIGNAL_GROUPS)
      -> split digests into postings       (extractors.split_opportunity_blocks)
      -> extract fields per posting        (extractors.*)
      -> match against the profile         (matching.match_opportunity)
      -> rank, then split confident vs ambiguous
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from src.agents.email import extractors
from src.agents.email.llm_analysis import LLMJudgement
from src.agents.email.matching import find_red_flags, match_opportunity
from src.agents.email.opportunity import (
    EXTRACTED_FIELDS,
    MatchBand,
    Opportunity,
    OpportunityReport,
)
from src.services.email.message import EmailMessage
from src.services.profile.profile import UserProfile
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Band ordering for ranking: strongest first.
_BAND_RANK = {MatchBand.STRONG: 0, MatchBand.POSSIBLE: 1, MatchBand.POOR: 2}


def analyze_opportunities(
    messages: Sequence[EmailMessage],
    profile: UserProfile,
    analyzer_name: str = "job_opportunity",
    min_score: int = 2,
    top_n: int = 5,
    interpreter: Any | None = None,
) -> OpportunityReport:
    """Run the full pipeline over a set of messages.

    ``interpreter`` is optional and injected. Supplied, each opportunity is
    additionally offered to a language model for a second opinion; omitted,
    this behaves exactly as it did before - the deterministic pipeline neither
    knows nor cares whether one exists.

    The interpreter is asked only *after* the rules have finished, and its
    answer is attached alongside their conclusions rather than replacing any
    of them.
    """
    from src.agents.email.analyzers import JobOpportunityAnalyzer

    detector = JobOpportunityAnalyzer(min_score=min_score)

    confident: list[Opportunity] = []
    ambiguous: list[Opportunity] = []
    ignored = 0

    for message in messages:
        text = extractors.readable_text(message)
        signals = JobOpportunityAnalyzer._matched_groups(text)
        if len(signals) < min_score:
            ignored += 1
            continue

        source = extractors.detect_source(message)
        for block in extractors.split_opportunity_blocks(text):
            opportunity = _build_opportunity(message, block, signals, source, profile)
            if interpreter is not None:
                opportunity = _with_judgement(opportunity, interpreter, profile)
            if opportunity.band is MatchBand.AMBIGUOUS:
                ambiguous.append(opportunity)
            else:
                confident.append(opportunity)

    confident.sort(key=_rank_key)
    ambiguous.sort(key=lambda o: (o.message_id, o.headline()))

    logger.info(
        "analysed %d message(s): %d ranked, %d unclear, %d ignored",
        len(messages),
        len(confident),
        len(ambiguous),
        ignored,
    )

    return OpportunityReport(
        analyzer=analyzer_name,
        considered=len(messages),
        findings=tuple(confident),
        top_n=top_n,
        ambiguous=tuple(ambiguous),
        ignored=ignored,
    )


def _with_judgement(
    opportunity: Opportunity, interpreter: Any, profile: UserProfile
) -> Opportunity:
    """Attach a model's judgement, leaving every rule-derived field alone.

    ``replace`` returns a new frozen Opportunity carrying the same deterministic
    conclusions plus the judgement - the model cannot edit what the rules
    decided, because it never touches the original object.

    OpportunityInterpreter already promises never to raise, but the interpreter
    is injected and a caller may supply anything. A failure here must cost this
    opportunity its second opinion, never the analysis run, so the boundary is
    guarded the same way the Manager guards a delegated agent.
    """
    try:
        judgement = interpreter.interpret(opportunity, profile)
    except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
        logger.exception(
            "interpreter failed on %s: %s", opportunity.message_id, type(exc).__name__
        )
        judgement = LLMJudgement.unavailable(
            message_id=opportunity.message_id,
            provider=str(getattr(interpreter, "provider_name", "unknown")),
            reason=f"the interpreter failed: {type(exc).__name__}: {exc}",
        )
    return replace(opportunity, judgement=judgement)


def _rank_key(opportunity: Opportunity):
    """Band first, then score, then confidence, then id for determinism."""
    return (
        _BAND_RANK.get(opportunity.band, 3),
        -opportunity.match_score,
        -opportunity.confidence,
        opportunity.message_id,
        opportunity.headline(),
    )


def _build_opportunity(
    message: EmailMessage,
    block: str,
    signals: tuple[str, ...],
    source: str | None,
    profile: UserProfile,
) -> Opportunity:
    """Extract one posting and match it against the profile."""
    title, company = extractors.extract_title_and_company(block, message.subject, source)
    required, preferred = extractors.extract_skills(block)

    fields = {
        "title": title,
        "company": company,
        "source": source,
        "seniority": extractors.extract_seniority(block),
        "required_skills": required,
        "preferred_skills": preferred,
        "experience_required": extractors.extract_experience(block),
        "salary": extractors.extract_salary(block),
        "deadline": extractors.extract_deadline(block),
        "location": extractors.extract_location(block),
        "employment_type": extractors.extract_employment_type(block),
        "apply_link": extractors.extract_apply_link(block),
    }
    # Anything that could not be read is named, never filled in.
    missing_fields = tuple(name for name in EXTRACTED_FIELDS if not fields.get(name))

    result = match_opportunity(
        profile=profile,
        required_skills=required,
        preferred_skills=preferred,
        title=title,
        seniority=fields["seniority"],
        missing_fields=missing_fields,
        text=block,
    )

    return Opportunity(
        message_id=message.id,
        sender=message.sender,
        subject=message.subject,
        score=len(signals),
        signals=signals,
        role=title,
        company=company,
        source=source,
        seniority=fields["seniority"],
        required_skills=required,
        preferred_skills=preferred,
        experience_required=fields["experience_required"],
        salary=fields["salary"],
        deadline=fields["deadline"],
        location=fields["location"],
        employment_type=fields["employment_type"],
        apply_link=fields["apply_link"],
        description=extractors.extract_description(block),
        missing_fields=missing_fields,
        band=result.band,
        match_score=result.score,
        confidence=result.confidence,
        matched_skills=result.matched,
        partial_skills=result.partial,
        learning_skills=result.learning,
        missing_skills=result.missing,
        supporting_projects=result.supporting_projects,
        red_flags=find_red_flags(block),
        reasons=result.reasons,
        uncertainties=result.uncertainties,
    )
