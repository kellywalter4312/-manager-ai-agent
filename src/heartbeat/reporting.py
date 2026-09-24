"""Turning a run into something worth your attention.

Reports are written to disk rather than emailed: sending requires a write
scope this project deliberately does not hold. A file is the honest
side-effect-free channel.

Quiet runs write nothing at all. That is the 3R principle applied literally -
an agent that files a report every hour regardless of whether anything happened
costs you the very time it was built to save.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_REPORTS_DIR = "data/reports"


def render_report(
    opportunities: Sequence[Any],
    ambiguous: Sequence[Any] = (),
    skipped: int = 0,
    considered: int = 0,
    when: datetime | None = None,
    max_reported: int = 5,
    include_ambiguous: bool = True,
) -> str:
    """Render one run as markdown."""
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
    shown = list(opportunities)[:max_reported]

    lines = [
        f"# Opportunity report - {stamp}",
        "",
        f"- messages considered: {considered}",
        f"- new opportunities: {len(opportunities)}",
        f"- already reported previously: {skipped}",
    ]
    if include_ambiguous and ambiguous:
        lines.append(f"- unclear, needs a look: {len(ambiguous)}")
    lines.append("")

    if shown:
        lines.append(f"## Top {len(shown)}")
        lines.append("")
        for index, opportunity in enumerate(shown, start=1):
            lines.append(f"### {index}. {_headline(opportunity)}")
            lines.append("")
            lines.append("```")
            lines.append(_explain(opportunity))
            lines.append("```")
            lines.append("")

    remaining = len(opportunities) - len(shown)
    if remaining > 0:
        lines.append(f"_{remaining} further new opportunity(ies) not shown._")
        lines.append("")

    if include_ambiguous and ambiguous:
        lines.append("## Needs a look - not enough information to rank")
        lines.append("")
        for opportunity in ambiguous:
            why = "; ".join(getattr(opportunity, "uncertainties", ())) or "insufficient detail"
            lines.append(f"- **{_headline(opportunity)}** - {why}")
        lines.append("")

    lines.append("---")
    lines.append(
        "_Analysis only. This agent has read-only access and takes no action "
        "on your behalf: it cannot send, reply, apply or contact anyone._"
    )
    return "\n".join(lines)


def _headline(opportunity: Any) -> str:
    headline = getattr(opportunity, "headline", None)
    return headline() if callable(headline) else str(opportunity)


def _explain(opportunity: Any) -> str:
    explain = getattr(opportunity, "explain", None)
    return explain() if callable(explain) else str(opportunity)


def write_report(
    text: str,
    when: datetime,
    directory: str | Path = DEFAULT_REPORTS_DIR,
    *,
    suffix: str = "opportunities",
) -> Path:
    """Write a report and return its path.

    ``suffix`` names what kind of report this is in the filename -
    defaulted to "opportunities" so every existing caller writes to
    exactly the same filename shape as before. Stage 12's proactive
    milestone report (``render_proactive_report`` below) is the one
    other kind this project writes, via ``suffix="proactive"`` - the
    same directory, the same atomic write, never a second reporting
    channel.
    """
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{when.strftime('%Y%m%d-%H%M%S')}-{suffix}.md"
    path.write_text(text, encoding="utf-8")
    logger.info("wrote %s report to '%s'", suffix, path)
    return path


def render_proactive_report(
    records: Sequence[Any],
    when: datetime | None = None,
    *,
    celebrations: "Mapping[str, Mapping[str, object]] | None" = None,
) -> str:
    """Render today's due proactive events as markdown.

    Plain and factual, deliberately not composed through a Brain: this
    file is written by the heartbeat, a per-tick process with no live
    conversation to compose warm, personal wording into - see
    ``src/manager/proactive_milestones.py``'s own docstring for where
    that boundary sits. How something recognised here is actually
    mentioned to the person - in Alfred's own voice - is a live
    conversation's job, not a report file's.

    Accepts either a plain ``MemoryRecord`` (the original Stage 12
    closure shape - ``.context``/``.content``/``.kind``) or a Stage 12
    closure "temporal awareness" ``ProactiveEvent`` (``.event_type``/
    ``.label``) duck-typed, so an existing caller passing records is
    unaffected. ``celebrations``, when given, is a genuine
    ``CelebrationReaction.to_dict()`` per event's own ``dedup_key`` -
    the presentation data reaching this report's own boundary, never
    re-selected here.
    """
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Proactive event report - {stamp}",
        "",
        f"- due today: {len(records)}",
        "",
    ]
    if records:
        lines.append("## Due today")
        lines.append("")
        for record in records:
            event_type = getattr(record, "event_type", None)
            if event_type is not None:
                label = event_type
                content = getattr(record, "label", event_type)
            else:
                context = getattr(record, "context", None) or {}
                label = context.get("fact_type") or context.get("milestone_type") or getattr(record, "kind", "fact")
                content = getattr(record, "content", str(record))
            lines.append(f"- **{label}**: {content}")

            if getattr(record, "source", None) == "reminder":
                # A reminder is a message the person asked for, never a
                # celebration (Pre-Stage-9 Campaign 6).
                lines.append(f"  - reminder: {content}")
                continue
            reaction = (celebrations or {}).get(getattr(record, "dedup_key", ""))
            if reaction and reaction.get("enabled"):
                lines.append(
                    f"  - celebration: {reaction.get('intensity')} "
                    f"avatar={reaction.get('avatar_action')} "
                    f"environment={reaction.get('environment_action')}"
                )
        lines.append("")
    lines.append("---")
    lines.append(
        "_Recognition only. Whether and how this is actually mentioned is "
        "decided the next time you talk to the Manager, never by this report._"
    )
    return "\n".join(lines)
