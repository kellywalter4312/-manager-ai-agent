"""Governed, deterministic voice autonomy - the decision path itself.

This is the piece that was missing before this checkpoint: a real backend
function through which an agent can look at what a provider genuinely has
available and choose one, rather than either asking a person to pick from
a UI or silently hashing a name. It is deliberately *not* a Brain call.
Matching a requested language/accent against a small set of discovered
``VoiceOption``s is a constraint-satisfaction problem with one correct,
checkable answer - exactly the kind of decision this project's own
capability handlers already resolve deterministically wherever possible
(see ``create_diagram``'s explicit-description path), reserving genuine
reasoning for requests a fixed rule cannot parse. Nothing here calls a
Brain; nothing here needs to.

**Governance order this module is one link in, never the whole chain**
(the rest lives in the ``select_voice`` capability and
``voice_preference.effective_voice_for``):

    system safety/governance   ->  request.retain's own MemoryPolicy gate
    explicit user override     ->  voice_preference_for (checked BEFORE
                                    this module is ever consulted)
    applicable preference      ->  autonomous_voice_choice_for, once made
    agent autonomous choice    ->  select_voice() below
    execution                  ->  effective_voice_for at speak time
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.services.identity.voice_options import VoiceOption, languages_match, normalize_language_token

# A small, open set of accent-describing adjectives recognised in an
# ordinary request - not an exhaustive catalog of every accent a provider
# might ever report (those are read live from VoiceOption.accent, never
# hardcoded), only enough to recognise that a request is asking for one
# at all, e.g. "an Italian voice with an Italian accent". Extending this
# never requires touching selection logic - it only widens what a request
# can be recognised as asking for.
_ACCENT_HINT = re.compile(r"\baccent\b", re.IGNORECASE)


@dataclass(frozen=True)
class VoiceRequest:
    """What was actually asked for, parsed once, deterministically -
    never re-interpreted differently by different callers. ``language``
    and ``accent`` are independent and both optional, exactly as
    ``VoiceOption`` keeps them; ``raw_text`` is kept only so a refusal
    message can quote what was actually asked."""

    raw_text: str
    language: "str | None" = None
    accent: "str | None" = None
    locale: "str | None" = None

    def is_empty(self) -> bool:
        return self.language is None and self.accent is None and self.locale is None


def _find_language_occurrences(text: str) -> "list[tuple[int, str]]":
    """Every whole-word language *name* (never a bare code) found in
    ``text``, as ``(position, canonical_name)`` pairs in order of
    appearance. Not deduplicated - a name repeated (as in "an Italian
    voice with an Italian accent") keeps both occurrences, which is what
    lets the caller tell a repeated word from two genuinely different
    ones.

    Codes are deliberately excluded from *this* scan, even though
    ``LANGUAGE_NAMES``/``languages_match`` both support them for
    comparing two already-structured facts (a provider's own reported
    code against a canonical name). Free-form prose is a different
    situation: several real ISO codes are also extremely common English
    words - "it" (Italian), "hi" (Hindi), "he" (Hebrew), "no"
    (Norwegian) - and matching those as bare tokens produced real,
    observed false positives ("...it is impossible" -> Italian; "hi
    Alfred..." -> Hindi). A name like "Italian" carries no such
    ambiguity, so parsing a request uses names only.
    """
    from src.services.identity.voice_options import LANGUAGE_NAMES  # noqa: PLC0415

    lowered = text.lower()
    words = {name: name for name in LANGUAGE_NAMES.values()}
    found: list[tuple[int, str]] = []
    for token, canonical in words.items():
        for match in re.finditer(rf"\b{re.escape(token)}\b", lowered):
            found.append((match.start(), canonical))
    return sorted(found)


_ACCENT_ADJECTIVE = re.compile(r"\b([a-z][a-z-]*)\s+accent\b", re.IGNORECASE)


def parse_voice_request(text: str) -> VoiceRequest:
    """Deterministic, Brain-free extraction of a language/accent request
    from ordinary text.

    The accent, when asked for, is read directly - whatever adjective
    sits immediately before the word "accent" ("a *British* accent", "an
    *Italian* accent", "a *Nigerian* accent") - never limited to the
    small language-name table, since a regional accent descriptor
    ("british", "nigerian") is not itself a language name and providers
    genuinely report accents this project has no reason to enumerate in
    advance (see ``VoiceOption.accent``, always a free-form provider
    string). The language, separately, is read from
    ``voice_options.LANGUAGE_NAMES`` - matching the two contrasting
    examples this checkpoint's brief gives: "an Italian voice with an
    Italian accent" (the one language word doubles as the accent, since
    there is no other word to contrast it against) versus "speak English
    with a Spanish accent" (two distinct language-table words - the one
    immediately preceding "accent" is the accent; the other, if any, is
    the language - "Spanish" is consumed as the accent, leaving
    "English" as the language). Never guesses a language or accent that
    was not actually named.
    """
    accent_match = _ACCENT_ADJECTIVE.search(text)
    accent = accent_match.group(1).lower() if accent_match else None

    occurrences = _find_language_occurrences(text)
    distinct = list(dict.fromkeys(name for _, name in occurrences))

    if accent is None:
        return VoiceRequest(raw_text=text, language=distinct[0] if distinct else None, accent=None)

    others = [name for name in distinct if name != accent]
    if others:
        return VoiceRequest(raw_text=text, language=others[0], accent=accent)
    # No other distinct language word: if the accent word is itself a
    # known language name, it describes both facts (requirement 7's own
    # "Italian voice with an Italian accent" example); otherwise
    # ("British accent" alone - "british" is not a language name) the
    # language genuinely was not stated, and stays honestly None.
    from src.services.identity.voice_options import LANGUAGE_NAMES  # noqa: PLC0415

    language = accent if (accent in LANGUAGE_NAMES.values() or accent in LANGUAGE_NAMES) else None
    return VoiceRequest(raw_text=text, language=language, accent=accent)


@dataclass(frozen=True)
class VoiceSelection:
    """The whole, honest answer: what was chosen (if anything), and
    precisely which parts of the request that choice actually satisfies -
    never a single boolean collapsing "found something" with "found what
    was asked for"."""

    option: "VoiceOption | None"
    language_satisfied: bool
    accent_satisfied: bool
    reason: str


def select_voice(request: VoiceRequest, available: "tuple[VoiceOption, ...]") -> VoiceSelection:
    """Deterministic constraint matching over real, currently-discovered
    options - never a Brain call, never a fabricated option.

    Order: try to satisfy language AND accent together; if nothing does,
    fall back to language alone (still an honest, reported degradation -
    ``accent_satisfied`` says so); if language alone cannot be satisfied
    either, refuse rather than silently returning something unrelated.
    A request naming no language/accent at all is refused with a reason
    naming that, rather than picking arbitrarily.
    """
    if request.is_empty():
        return VoiceSelection(
            None, False, False,
            "no language or accent was recognised in that request",
        )
    if not available:
        return VoiceSelection(
            None, False, False,
            "no voice options are currently available from any configured provider",
        )

    def _matches(opt: VoiceOption, *, need_accent: bool) -> bool:
        if request.language is not None and not languages_match(opt.language, request.language):
            return False
        if need_accent and request.accent is not None:
            if opt.accent is None or normalize_language_token(opt.accent) != normalize_language_token(request.accent):
                return False
        return True

    both = [o for o in available if _matches(o, need_accent=True)]
    if both and request.language is not None:
        accent_satisfied = request.accent is not None
        reason = "language and accent both satisfied" if accent_satisfied else "language satisfied"
        return VoiceSelection(_pick(both), True, accent_satisfied, reason)

    language_only = [o for o in available if _matches(o, need_accent=False)] if request.language else []
    if language_only:
        reason = (
            "language satisfied; no currently available option also matched the requested accent"
            if request.accent is not None
            else "language satisfied"
        )
        return VoiceSelection(_pick(language_only), True, False, reason)

    if request.language is not None:
        return VoiceSelection(
            None, False, False,
            f"no currently available provider option is reported to support '{request.language}'",
        )

    # Accent requested with no language constraint (e.g. "speak with a
    # Spanish accent" naming no base language): match on accent alone,
    # honestly, never assuming a language.
    if request.accent is not None:
        accent_only = [
            o for o in available
            if o.accent is not None and normalize_language_token(o.accent) == normalize_language_token(request.accent)
        ]
        if accent_only:
            return VoiceSelection(_pick(accent_only), False, True, "accent satisfied; no language was requested")
        return VoiceSelection(
            None, False, False,
            f"no currently available provider option is reported with a '{request.accent}' accent",
        )

    return VoiceSelection(None, False, False, "the request matched no currently available option")


def _pick(options: "list[VoiceOption]") -> VoiceOption:
    """A stable, deterministic choice among several equally-valid
    matches - sorted by provider then voice_id, never by insertion
    order or randomness, so the same real catalog always yields the
    same choice."""
    return sorted(options, key=lambda o: (o.provider, o.voice_id))[0]
