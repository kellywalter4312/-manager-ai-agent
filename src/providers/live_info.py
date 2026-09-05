"""The live-information-provider contract.

The same abstraction move `src/providers/base.py` already made for language
models, applied to current, external information: anything that wants
current information depends on this interface, never on a vendor SDK, a
search engine's API shape, or a particular website. A real backend - a
search API, a news API, anything else - becomes an additive change rather
than a rewrite, exactly as swapping in a real LLMProvider already is.

**Why this is not the Brain, and why the Brain never touches it.** A Brain
reasons over information it is given; it has no method that reaches the
network, and nothing under `src/manager/` imports this module. This module
*acquires* information - the governed capability boundary
(`src/agents/tools/live_information.py`) is what stands between the two,
the same separation `ToolSet` already enforces for every other tool a
specialist might be granted.

**Why a result carries provenance, not just text.** A `Completion` answers
"what did the model say"; a `LiveInfoResult` answers a different, harder
question - "what did the world look like, according to what, and when" -
because current information that has lost its source and its moment
becomes indistinguishable from Alfred's own remembered or trained
knowledge, which is exactly the confusion this whole stage exists to
prevent (see `ToolResult.observed_at`'s own docstring for the identical
argument, made first).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now() -> datetime:
    """The default clock. Injectable so tests can control time."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LiveInfoResult:
    """What a live-information provider found for one query.

    Minimal and provider-agnostic on purpose - the same discipline
    `Completion` already applies to model output: the request, what was
    retrieved, where it came from, and when. Nothing here names a search
    engine, a news outlet or a website; a real provider's own identity
    lives in ``source``, a plain string, never a new field per vendor.
    """

    query: str
    content: str
    source: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if not str(self.query).strip():
            raise ValueError("LiveInfoResult.query must not be empty")
        if not str(self.source).strip():
            raise ValueError("LiveInfoResult.source must not be empty")
        object.__setattr__(self, "query", " ".join(str(self.query).split()))
        object.__setattr__(self, "source", " ".join(str(self.source).split()))
        if not isinstance(self.retrieved_at, datetime):
            raise ValueError("LiveInfoResult.retrieved_at must be a datetime")

    @property
    def is_empty(self) -> bool:
        """True when nothing usable came back - the same honest signal
        ``Completion.is_empty`` already gives for a model with nothing to
        say. A provider that found nothing is not a provider that failed;
        it is a provider that looked and came back empty, and the caller
        decides what that means."""
        return not self.content.strip()

    def describe(self) -> str:
        """A short line for logs. Excludes ``content`` for the same
        reason ``Completion.describe()`` excludes ``text``: current
        information may restate task content that should not be
        duplicated in a log file."""
        return (
            f"source={self.source} retrieved_at={self.retrieved_at.isoformat()} "
            f"chars={len(self.content)}"
        )


class LiveInfoProvider(ABC):
    """A source of current, external information.

    One method, plain text in, a small immutable result out - the same
    narrow shape ``LLMProvider`` already proved cheap to implement and
    cheap to fake. A null implementation, a scripted test double, and a
    real backend all satisfy this identically.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and provenance, e.g. "null"."""

    @abstractmethod
    def fetch(self, query: str) -> LiveInfoResult:
        """Return current information for ``query``.

        Implementations must raise ValueError on an empty query, the
        same rule ``LLMProvider.complete`` already states. A genuine
        retrieval failure - a network error, a timeout, a provider
        outage - is also raised rather than returned as a special
        value: the identical convention ``complete()`` uses, and the
        caller (the live-information Tool, see
        ``src/agents/tools/live_information.py``) is responsible for
        catching it, exactly as ``ToolSet.invoke`` already catches
        every other tool's exceptions at its own boundary.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
