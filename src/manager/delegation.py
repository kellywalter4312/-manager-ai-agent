"""Explicit delegation: a person asking the orchestrator to hand a piece of
work to a particular participant.

    "ask <agent> to check my inbox"
    "have <agent> draft the summary"
    "<orchestrator>, tell <agent> to compare the two options"

The Manager already delegates on its own judgement - diagnose, assemble, take
action, assess. This recognises the case where the person has made that
judgement for it and named who should do the work, so the work goes to that
participant *as work*: the named agent receives the piece of work itself, not
the whole sentence that asked for it, and the result comes back through the
same assemble -> take action -> assess loop every delegation already uses.

**Who can be named is the registry's answer, never a list here.** A name
counts only when it is a registered agent's own name, the orchestrator's name
never counts (asking the orchestrator to hand work to itself is not a
delegation), and a name nobody is registered under is simply not a
delegation - the request carries on as ordinary work, and nothing is invented.

**Only the orchestrator delegates.** This runs inside the Manager's own DATA
loop. A specialist has no path to it: a request addressed to a specialist is
that specialist's work, and nothing a specialist does reaches the Manager's
decision. Granting one agent authority over another's work is not something a
sentence can do.

Deterministic, so it behaves identically with or without a language model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from src.core.contracts import tokenize

# Keys the delegated task carries, so the delegate - and anything that later
# reads the result - can see who handed the work over and from which request.
DELEGATED_BY_KEY = "delegated_by"
DELEGATION_OF_KEY = "delegation_of"

# "ask/tell/get X to <work>" need the "to": "tell X about Y" is talking about
# somebody, and "ask X about Y" is a question, not a hand-over. "have X <work>"
# and "let X <work>" are complete without it.
_NEEDS_TO = frozenset({"ask", "tell", "get"})
_BARE = frozenset({"have", "let"})
_VERB = re.compile(r"^(?P<verb>ask|tell|get|have|let)\s+", re.IGNORECASE)
_POLITE = re.compile(
    r"^(?:(?:please|kindly)\s+)?(?:(?:could|can|would|will)\s+you\s+)?(?:please\s+)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DelegatedWork:
    """Who the person named, as registered, and the work to hand them."""

    target: str
    work: str


def delegation_request(
    description: str, names: Iterable[str], *, orchestrator: str | None = None
) -> DelegatedWork | None:
    """The delegation this request asks for, or None.

    ``names`` are the registered agents' names; ``orchestrator`` is the
    Manager's own, which may open the request as an address ("<name>, ask ...")
    and is never a target. Longest name wins, so a two-word name beats a
    one-word name that begins it."""
    text = str(description or "").strip()
    if orchestrator:
        opening = re.match(rf"^\s*(?:hey\s+|ok(?:ay)?\s+)?{re.escape(orchestrator)}\s*[,:]\s*", text, re.IGNORECASE)
        if opening:
            text = text[opening.end():]
    text = _POLITE.sub("", text, count=1)
    verb_match = _VERB.match(text)
    if verb_match is None:
        return None
    verb = verb_match.group("verb").lower()
    rest = text[verb_match.end():]

    own = (orchestrator or "").strip().lower()
    candidates = sorted(
        {" ".join(str(n).split()) for n in names if str(n).strip() and str(n).strip().lower() != own},
        key=len, reverse=True,
    )
    for name in candidates:
        if not rest.lower().startswith(name.lower()):
            continue
        after = rest[len(name):]
        if after and after[0].isalnum():
            continue  # "Ali" inside "Alison"
        after = after.lstrip(" ,")
        if not after or not after[0].isalpha():
            return None  # "get <agent>'s opinion" is not a hand-over
        if after.lower().startswith("to "):
            work = after[3:]
        elif verb in _BARE:
            work = after
        else:
            return None
        work = re.sub(r"[\s,]*please[.!?]*$", "", work.strip(), flags=re.IGNORECASE).strip()
        if not tokenize(work):
            return None
        return DelegatedWork(target=name, work=work)
    return None
