"""The knowledge-source-provider contract: what the *user* supplied.

The same abstraction move ``src/providers/base.py`` made for language
models and ``src/providers/live_info.py`` made for current information,
applied to a third and genuinely different kind of knowing. Alfred can
know something in four separable ways, and this project keeps them
separable on purpose because confusing any two of them is a lie:

    general model knowledge   what a reasoning provider already knows.
                              Settled, broad, undated, attributable to no
                              document - reached through the Brain
                              (``ManagerBrain.converse``), never here.
    user-supplied knowledge   documents, books, notes, course material a
                              person actually handed to Alfred. THIS
                              module. Attributable to a named document,
                              and to nothing else.
    live information          what the world looks like right now -
                              ``src/providers/live_info.py``, which is
                              the only thing in this project allowed to
                              imply currency.
    learning context          what the person is studying and how they
                              want it explained. That is a fact about the
                              *user*, so it lives in the governed Memory
                              every other user fact already lives in (see
                              ``src/services/memory/learning.py``), never
                              in a knowledge base of its own.

**Why this is not the live-information provider.** A ``LiveInfoResult``
answers "what does the world look like, according to what, and when"; a
``KnowledgeResult`` answers "what do the documents this person gave me
say about this, and which document said it". One is dated because its
value decays; the other is *located* because its value is in being
quotable. Collapsing them would mean either a book claiming to be current
or a news result claiming to be material the user owns.

**Why this is not a topic database.** Nothing here knows a subject. There
is no table of periods, people, countries, wars, sciences or titles, and
there must never be one: a query is a plain string that arrived with the
request, and what a provider finds for it is decided entirely by that
provider's own material. A provider added tomorrow for a different
document format is an additive change, exactly as swapping in a real
``LLMProvider`` already is.

**Why this is not the Brain.** A Brain reasons over what it is given and
has no method that reaches storage; this acquires and does not reason.
The governed capability boundary (``src/agents/tools/knowledge.py``)
stands between the two, the same separation ``ToolSet`` already enforces
for every other tool.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    """The default clock. Injectable so tests can control time."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class KnowledgeExcerpt:
    """One passage from one user-supplied document.

    ``document`` names the thing the passage came out of, and ``locator``
    is wherever inside it the passage sits - a chapter, a section, a page,
    a line range. Both are plain, opaque strings: this class never parses
    either, so a provider backed by a book, a set of lecture notes, an
    exported wiki or anything else describes itself in whatever terms are
    true for it, without a field being added per format.

    Provenance is required rather than optional, for the same reason
    ``LiveInfoResult`` carries a source and a moment: a passage that has
    lost the document it came from is indistinguishable from something a
    model invented, which is exactly the confusion this layer exists to
    prevent.
    """

    document: str
    content: str
    locator: str = ""

    def __post_init__(self) -> None:
        if not str(self.document).strip():
            raise ValueError("KnowledgeExcerpt.document must name its source document")
        object.__setattr__(self, "document", " ".join(str(self.document).split()))
        if not str(self.content).strip():
            raise ValueError("KnowledgeExcerpt.content must not be empty")
        object.__setattr__(self, "content", " ".join(str(self.content).split()))
        object.__setattr__(self, "locator", " ".join(str(self.locator or "").split()))

    def cite(self) -> str:
        """The passage with its own provenance attached, as one line.

        This is the form that crosses into a reasoning step, so the
        citation travels with the text rather than beside it - the same
        rule ``LiveInformationTool.run`` already follows for a source and
        a retrieval time.
        """
        where = f", {self.locator}" if self.locator else ""
        return f"[document: {self.document}{where}] {self.content}"


@dataclass(frozen=True)
class KnowledgeResult:
    """What a knowledge source found for one query.

    Minimal and provider-agnostic, the same discipline ``Completion`` and
    ``LiveInfoResult`` already apply: the request, what was found, whose
    material it is, and when it was consulted. Nothing here names a file
    format, a book, a subject or a vendor - a provider's own identity
    lives in ``source``, a plain string, never a new field per backend.
    """

    query: str
    excerpts: tuple[KnowledgeExcerpt, ...] = field(default_factory=tuple)
    source: str = ""
    consulted_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not str(self.query).strip():
            raise ValueError("KnowledgeResult.query must not be empty")
        object.__setattr__(self, "query", " ".join(str(self.query).split()))
        if not str(self.source).strip():
            raise ValueError("KnowledgeResult.source must not be empty")
        object.__setattr__(self, "source", " ".join(str(self.source).split()))
        object.__setattr__(self, "excerpts", tuple(self.excerpts or ()))
        for excerpt in self.excerpts:
            if not isinstance(excerpt, KnowledgeExcerpt):
                raise ValueError(
                    "KnowledgeResult.excerpts must all be KnowledgeExcerpt, got "
                    f"{type(excerpt).__name__}"
                )
        if not isinstance(self.consulted_at, datetime):
            raise ValueError("KnowledgeResult.consulted_at must be a datetime")

    @property
    def is_empty(self) -> bool:
        """True when the user's own material had nothing on this.

        The same honest signal ``LiveInfoResult.is_empty`` already gives:
        a source that looked and found nothing is not a source that
        failed, and the caller decides what that means. It is never an
        answer.
        """
        return not self.excerpts

    def citations(self) -> tuple[str, ...]:
        """Every excerpt rendered with its own provenance."""
        return tuple(excerpt.cite() for excerpt in self.excerpts)

    def describe(self) -> str:
        """A short line for logs. Excludes the excerpts themselves, for
        the same reason ``LiveInfoResult.describe()`` excludes content: a
        person's own documents must not be duplicated into a log file.
        """
        return (
            f"source={self.source} consulted_at={self.consulted_at.isoformat()} "
            f"excerpts={len(self.excerpts)}"
        )


class KnowledgeSourceProvider(ABC):
    """A body of knowledge the user themselves supplied.

    One method, plain text in, a small immutable result out - the same
    narrow shape ``LLMProvider`` and ``LiveInfoProvider`` already proved
    cheap to implement and cheap to fake. A null implementation, a
    scripted test double and a real document library all satisfy this
    identically.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and provenance, e.g. "null"."""

    @abstractmethod
    def lookup(self, query: str) -> KnowledgeResult:
        """Return whatever the user's own material says about ``query``.

        Implementations must raise ValueError on an empty query, the same
        rule ``LLMProvider.complete`` and ``LiveInfoProvider.fetch``
        already state. A genuine failure - an unreadable file, a backend
        outage - is also raised rather than returned as a special value,
        and the caller (``KnowledgeLookupTool``, then ``ToolSet.invoke``)
        turns it into an honest, structured failure.

        Implementations must be **deterministic**: the same material and
        the same query produce the same excerpts in the same order - the
        identical rule ``Memory`` already lives under, and for the same
        reason.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
