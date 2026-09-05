"""Assembling the evidence Alfred has for a knowledge question - and being
exact about which kind of knowing each piece of it is.

This module is the seam that keeps four things apart that a single
"knowledge engine" would quietly merge. It answers one question, for one
query - *what do I actually have to reason from, and where did each piece
come from* - and it answers it without reasoning, without storing
anything, and without knowing a single subject.

    source                     where it comes from            what it means
    ------------------------------------------------------------------------
    USER_DOCUMENTS             the knowledge_lookup tool      quotable, the
                               (a KnowledgeSourceProvider)    person's own
    LEARNING_CONTEXT           the agent's governed Memory    who is asking
                                                              and why
    MODEL_KNOWLEDGE            the Brain, one layer up        settled, broad
    (live information)         deliberately NOT here          the present

**Live information is deliberately absent, and its absence is the point.**
``LIVE_INFORMATION`` in ``capabilities.py`` already owns "what is true
right now", is claimed by its own request shape, and is the only path in
this project permitted to imply currency. A knowledge answer that quietly
folded in a live lookup would make "settled" and "current" one
indistinguishable thing, which is the exact confusion
``src/providers/live_info.py`` was written to prevent. So this module
gathers only settled evidence, and says so in the note it appends.

**It never invents a source.** Every note returned here came from a tool
that actually ran or a record that was actually stored. When nothing is
installed and nothing is remembered, the result is honestly empty and
``unavailable`` names what was missing, by name - so a caller can report
the gap rather than fill it in.

**It receives no CapabilityRequest.** The two narrow collaborators it
needs are passed in directly, so this module imports nothing from
``capabilities.py`` and can be exercised with a bare ToolSet and a plain
function. A handler passes what it has; there is nothing else in scope to
reach.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from src.agents.tools.toolset import ToolSet
from src.manager.memory_policy import MemoryCategory
from src.services.memory.memory import MemoryRecord
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The one tool name this module knows. Matching the capability that grants
# it (``KNOWLEDGE``) and the tool that implements it
# (``src/agents/tools/knowledge.py``) - one name across all three, never a
# provider's or a format's own name.
KNOWLEDGE_TOOL = "knowledge_lookup"

# What each kind of evidence is called when a result reports which sources
# it actually used. Plain strings rather than an enum on purpose: they
# travel in a TaskResult payload, which is a mapping of ordinary values.
USER_DOCUMENTS = "user_documents"
LEARNING_CONTEXT = "learning_context"
MODEL_KNOWLEDGE = "model_knowledge"

# How many stored learning-context records are worth carrying into one
# answer. Small: this is context for pitching an explanation, not a
# transcript of everything the person has ever studied.
LEARNING_RECALL_LIMIT = 3

# The inert line that stops settled evidence being read as current. Phrased
# as a statement *about the evidence* rather than as an instruction, because
# notes are evidence and never instructions - the rule
# ``build_deliberation_prompt`` already states for its CONTEXT section.
SETTLED_KNOWLEDGE_NOTE = (
    "The evidence above is settled knowledge and the user's own documents. "
    "None of it is live or current information, and none of it was checked "
    "against the present moment."
)


@dataclass(frozen=True)
class KnowledgeEvidence:
    """What was found for one query, and what was not.

    Frozen, and carrying both halves on purpose. ``notes`` is what a Brain
    may reason over; ``sources`` is what may honestly be claimed
    afterwards; ``unavailable`` is what a refusal may name. A result that
    reported only the first would leave a caller unable to tell "the
    documents said nothing" from "there are no documents", which is the
    same conflation ``ToolStatus`` already refuses between rejected and
    failed.
    """

    notes: tuple[str, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = field(default_factory=tuple)
    unavailable: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """True when nothing was found from any consulted source."""
        return not self.notes

    def with_settlement_note(self) -> tuple[str, ...]:
        """The notes a Brain should see, closed with the settled-knowledge
        statement. Appended only when there is evidence to qualify - a lone
        disclaimer with nothing above it would be noise."""
        if not self.notes:
            return ()
        return self.notes + (SETTLED_KNOWLEDGE_NOTE,)


def gather_knowledge_evidence(
    query: str,
    *,
    tools: ToolSet | None = None,
    recall: Callable[..., Sequence[MemoryRecord]] | None = None,
) -> KnowledgeEvidence:
    """Everything Alfred genuinely holds for ``query``, with provenance.

    Both collaborators are optional and independently so, because their
    availability is genuinely independent: an installation may have the
    user's documents and no memory, memory and no documents, both or
    neither - and all four states must be describable rather than guessed
    at. Nothing here raises; a tool that fails is recorded as unavailable
    and the rest of the evidence still stands, the same fault isolation
    ``ToolSet.invoke`` already provides one layer down.
    """
    text = str(query or "").strip()
    if not text:
        return KnowledgeEvidence()

    notes: list[str] = []
    sources: list[str] = []
    unavailable: list[str] = []

    granted = tools if tools is not None else ToolSet()
    if not granted.permits(KNOWLEDGE_TOOL):
        unavailable.append(
            f"{USER_DOCUMENTS}: no '{KNOWLEDGE_TOOL}' capability is installed, "
            "so no user-supplied document was consulted"
        )
    else:
        outcome = granted.invoke(KNOWLEDGE_TOOL, {"query": text})
        if outcome.ok and outcome.output.strip():
            # The tool's own output already carries each document name and
            # locator (see KnowledgeExcerpt.cite), so the citation travels
            # with the text rather than being reattached here.
            for line in outcome.output.splitlines():
                if line.strip():
                    notes.append(f"From your own documents: {line.strip()}")
            sources.append(USER_DOCUMENTS)
        else:
            unavailable.append(f"{USER_DOCUMENTS}: {outcome.error or 'nothing was found'}")

    if recall is None:
        unavailable.append(
            f"{LEARNING_CONTEXT}: no memory reaches this request, so nothing is "
            "known about what the user is studying"
        )
    else:
        try:
            records = tuple(
                recall(text, kind=MemoryCategory.LEARNING.value, limit=LEARNING_RECALL_LIMIT)
            )
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            # A memory that cannot be read must not take an answer down with
            # it; the rest of the evidence is still true.
            logger.warning("could not recall learning context: %s", exc)
            unavailable.append(f"{LEARNING_CONTEXT}: memory could not be read")
        else:
            if records:
                for record in records:
                    notes.append(f"Learning context: {record.content}")
                sources.append(LEARNING_CONTEXT)
            else:
                unavailable.append(
                    f"{LEARNING_CONTEXT}: nothing is stored about what the user is studying"
                )

    return KnowledgeEvidence(
        notes=tuple(notes),
        sources=tuple(sources),
        unavailable=tuple(unavailable),
    )
