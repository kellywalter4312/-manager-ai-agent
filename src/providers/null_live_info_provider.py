"""A live-information provider that needs no key, no search engine and no
network.

The same role ``NullProvider`` plays for language models: not a
placeholder to be replaced later, but how the system stays runnable and
testable with zero external dependencies, and the honest default for a
project that has not configured a real current-information source.
"""

from __future__ import annotations

from src.providers.live_info import LiveInfoProvider, LiveInfoResult, utc_now


class NullLiveInfoProvider(LiveInfoProvider):
    """Returns an honestly empty result instead of reaching the network.

    Deliberately empty rather than a placeholder sentence: ``LiveInfoResult
    .is_empty`` is the signal the live-information Tool already checks for
    "nothing usable came back", so a Null provider degrading through that
    exact path - rather than a special case - is what keeps "no current-
    information source is configured" and "the configured source found
    nothing" indistinguishable in the one place that already matters:
    neither is ever treated as an answer.
    """

    @property
    def name(self) -> str:
        return "null"

    def fetch(self, query: str) -> LiveInfoResult:
        if not query.strip():
            raise ValueError("query must not be empty")
        return LiveInfoResult(query=query, content="", source=self.name, retrieved_at=utc_now())
