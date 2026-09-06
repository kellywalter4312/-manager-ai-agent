"""Memory that lives in the process and dies with it.

Named for its lifecycle rather than its storage, because the lifecycle is the
thing a caller has to know. "InMemoryMemory" would have described how it works
and hidden what actually matters: **nothing survives a restart.** Every record
here is gone when the program exits, and that is the intended behaviour, not a
limitation to work around.

Why ship this first, before any durable backend:

  - It makes "no memory configured" a working state rather than a crash, the
    same role NullProvider and NullBrain play in their layers.
  - It writes nothing to disk, opens no connection and requires no
    configuration, so every consumer of Memory is testable with no setup at
    all.
  - It defines the behaviour a persistent backend must reproduce. When SQLite
    or a file store arrives, the tests written against this contract are the
    ones it has to pass - which is what makes the swap safe rather than hopeful.

Replacing it later is a one-line change at the composition root, because
nothing depends on this class. Consumers depend on Memory.

Deliberately absent, and each of these is a later decision rather than an
oversight: persistence, a database, embeddings, semantic search, summarisation,
expiry, and any notion of which memories matter more than others.
"""

from __future__ import annotations

import math
from typing import Iterable

from src.core.contracts import tokenize
from src.services.memory.memory import (
    Memory,
    MemoryRecord,
    MemoryStoreError,
    normalise_tags,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _stem(token: str) -> str:
    """A small, deterministic suffix strip - not a real Porter stemmer, just
    enough to close the most common English inflection gap that literal
    token overlap missed outright: "colors"/"color", "preferred"/"prefer",
    "running"/"run". A fixed table of endings, applied the same way every
    time - no dictionary, no model, no network, and nothing that reasons
    about what a word *means* (see test_relevance_is_literal_not_inferred:
    "telecommuting" must never match "remote work", and it still will not -
    stemming only strips endings from the *same* word, it does not relate
    different words to each other).
    """
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ing") and len(token) > 5:
        return token[:-3]
    if token.endswith("ed") and len(token) > 4:
        return token[:-2]
    if token.endswith("es") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _stemmed(tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(_stem(token) for token in tokens)


class EphemeralMemory(Memory):
    """An in-process store backed by a plain list.

    A list rather than a dict keyed by id because insertion order *is* the
    recency order, and recency is the tiebreak that makes recall deterministic.
    Lookups by id are rare and the volume is small; when that stops being true,
    the answer is a real backend, not a cleverer data structure here.

    Not thread-safe, and deliberately not made so. Nothing in this project runs
    agents concurrently yet, and adding locking now would be guessing at a
    concurrency model that does not exist.
    """

    def __init__(self, records: Iterable[MemoryRecord] = ()) -> None:
        """Optionally seed with existing records.

        Seeding exists for tests and for a future backend that loads records
        from storage and hands them over. It copies into a private list, so a
        caller cannot mutate this store by holding on to the sequence it passed.
        """
        self._records: list[MemoryRecord] = []
        for record in records:
            self.remember(record)

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return f"<EphemeralMemory records={len(self._records)}>"

    # ------------------------------------------------------------------
    # The contract
    # ------------------------------------------------------------------

    def remember(self, record: MemoryRecord) -> MemoryRecord:
        if not isinstance(record, MemoryRecord):
            # Validating at the boundary means bad input is rejected here,
            # once, rather than causing a confusing failure during a recall
            # much later. Same reasoning as AgentRegistry.register.
            raise MemoryStoreError(
                f"Expected a MemoryRecord, got {type(record).__name__}"
            )

        # An id must identify one record. Storing a duplicate would make
        # forget() ambiguous and recall() return the same thing twice.
        if any(existing.id == record.id for existing in self._records):
            raise MemoryStoreError(f"a record with id '{record.id}' is already stored")

        self._records.append(record)
        logger.debug("remembered %s", record.describe())
        return record

    def recall(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        tags: Iterable[str] = (),
        limit: int = 10,
    ) -> tuple[MemoryRecord, ...]:
        if limit <= 0:
            raise MemoryStoreError(f"limit must be positive, got {limit}")

        wanted_kind = " ".join(str(kind).split()).lower() if kind is not None else None
        wanted_tags = normalise_tags(tags)
        query_tokens = _stemmed(tokenize(query or ""))

        # Enumerate first: the index is the insertion sequence, which is what
        # makes "most recent" well-defined without trusting timestamps. Two
        # records created in the same microsecond would otherwise tie, and the
        # ordering would depend on the sort's stability rather than on intent.
        pool: list[tuple[int, MemoryRecord]] = []
        for position, record in enumerate(self._records):
            if wanted_kind is not None and record.kind != wanted_kind:
                continue
            if wanted_tags and not wanted_tags <= record.tags:
                continue
            pool.append((position, record))

        if not query_tokens:
            candidates = [(0, position, record) for position, record in pool]
        else:
            # Foundation Repair #2 (reconciliation audit, 2026-09-05):
            # relevance beyond literal token overlap. Still purely
            # lexical and local - no embedding, no model, no network (see
            # test_no_llm_or_embedding_machinery_exists) - but a shared
            # word that appears in nearly every record here says nothing
            # about *this* record in particular, while a rare, shared word
            # says a great deal; raw overlap counted both the same.
            #
            # Document frequency is computed over this query's own pool -
            # after kind/tag filtering, not the whole store - because
            # "common" only means something relative to what this query
            # could actually return. Stemming (see _stem above) closes the
            # plain-inflection gap ("colors"/"color") that literal
            # overlap missed outright; it relates a word only to its own
            # endings, never to a different word, so
            # test_relevance_is_literal_not_inferred still holds exactly
            # as before.
            stems_by_position = {position: _stemmed(record.tokens) for position, record in pool}
            document_frequency: dict[str, int] = {}
            for stems in stems_by_position.values():
                for stem in stems:
                    document_frequency[stem] = document_frequency.get(stem, 0) + 1

            total = len(pool)
            candidates = []
            for position, record in pool:
                matched = stems_by_position[position] & query_tokens
                if not matched:
                    # A query was asked and this record has nothing to do
                    # with it. Returning it anyway would be worse than
                    # returning nothing.
                    continue
                # +1 smoothing throughout: with very few records (the
                # common case for a personal memory store, not an edge
                # case) a term's "rarity" is barely meaningful, and
                # unsmoothed IDF can reach zero for a store of one. The
                # smoothing keeps every genuine match worth at least as
                # much as literal overlap already counted it for, while
                # still rewarding a rarer, more distinctive shared word
                # over a common one once the store has enough records for
                # that distinction to exist.
                score = sum(
                    math.log((total + 1) / (document_frequency[stem] + 1)) + 1.0
                    for stem in matched
                )
                candidates.append((score, position, record))

        # Strongest match first, then most recent. Fully determined by the
        # data, so the same store and query always produce the same order.
        candidates.sort(key=lambda item: (-item[0], -item[1]))
        return tuple(record for _, _, record in candidates[:limit])

    def forget(self, record_id: str) -> bool:
        wanted = str(record_id).strip()
        if not wanted:
            raise MemoryStoreError("record_id must not be empty")

        for index, record in enumerate(self._records):
            if record.id == wanted:
                del self._records[index]
                logger.debug("forgot record %s", wanted)
                return True
        return False

    def clear(self) -> int:
        removed = len(self._records)
        self._records.clear()
        if removed:
            logger.debug("cleared %d record(s)", removed)
        return removed
