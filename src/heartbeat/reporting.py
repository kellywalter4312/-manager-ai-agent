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
from typing import Any, Sequence

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


def write_report(text: str, when: datetime, directory: str | Path = DEFAULT_REPORTS_DIR) -> Path:
    """Write a report and return its path."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{when.strftime('%Y%m%d-%H%M%S')}-opportunities.md"
    path.write_text(text, encoding="utf-8")
    logger.info("wrote opportunity report to '%s'", path)
    return path
