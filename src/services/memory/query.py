"""Turning a piece of text into a recall query.

Memory matches on literal token overlap. That is deliberate - a store that
interpreted a question would be reasoning, and reasoning belongs to a brain -
but it has one sharp edge: almost any two English sentences share "the" or "a".
Ask about "research the freelance market" and a record about "the calendar sync
failed last Tuesday" matches on "the". Everything becomes relevant, which is
the same as nothing being relevant.

So whoever asks has to ask well. This is query construction, not ranking:

    caller  decides what to ask about       <- here
    Memory  decides what matches            <- unchanged
    brain   decides what any of it means

Lives beside Memory rather than inside it because it is the *caller's* concern,
and because both the orchestrator and every specialist need it. Duplicating a
sixty-word list in two packages would have been worse than a shared module, and
a specialist importing it from src/manager/ would have been worse still.
"""

from __future__ import annotations

from src.core.contracts import STOPWORDS, tokenize

# Words too common to make a record relevant. Declared beside ``tokenize`` in
# src/core/contracts.py (see the note there for why it moved) and re-exported
# here under the name this module has always published it as.
__all__ = ["STOPWORDS", "distinctive_terms"]


def distinctive_terms(text: str) -> str:
    """The words in ``text`` that could make a record genuinely relevant.

    Sorted, so the same text always produces a byte-identical query. Returns
    an empty string when nothing distinctive remains - callers should treat
    that as "nothing specific to ask about" rather than passing it on, since
    an empty query matches everything.
    """
    meaningful = sorted(token for token in tokenize(text or "") if token not in STOPWORDS)
    return " ".join(meaningful)
