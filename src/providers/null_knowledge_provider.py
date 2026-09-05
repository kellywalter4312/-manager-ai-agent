"""A knowledge source that holds nothing, because nobody supplied anything.

The same role ``NullProvider`` plays for language models and
``NullLiveInfoProvider`` plays for current information: not a placeholder
to be replaced later, but how "the user has given Alfred no documents"
stays a valid, testable state rather than a special case.

Deliberately empty rather than a placeholder sentence.
``KnowledgeResult.is_empty`` is the signal ``KnowledgeLookupTool``
already checks, so a null provider degrading through that exact path -
rather than through a branch of its own - is what keeps "no knowledge
source is configured" and "the configured source found nothing"
indistinguishable in the one place that matters: neither is ever treated
as an answer, and neither is ever filled in from somewhere else.
"""

from __future__ import annotations

from src.providers.knowledge import (
    KnowledgeResult,
    KnowledgeSourceProvider,
    utc_now,
)


class NullKnowledgeProvider(KnowledgeSourceProvider):
    """Returns an honestly empty result instead of inventing material."""

    @property
    def name(self) -> str:
        return "null"

    def lookup(self, query: str) -> KnowledgeResult:
        if not str(query).strip():
            raise ValueError("query must not be empty")
        return KnowledgeResult(query=query, excerpts=(), source=self.name, consulted_at=utc_now())
