"""Governed, deterministic voice autonomy - the decision path itself.

This is the piece that was missing before the first voice-autonomy
checkpoint: a real backend function through which an agent can look at
what a provider genuinely has available and choose one, rather than
either asking a person to pick from a UI or silently hashing a name. It
is deliberately *not* a Brain call for the ordinary case. Matching a
requested language/accent/gender against a small set of discovered
``VoiceOption``s is a constraint-satisfaction problem with one correct,
checkable answer - exactly the kind of decision this project's own
capability handlers already resolve deterministically wherever possible
(see ``create_diagram``'s explicit-description path), reserving genuine
reasoning for requests a fixed rule cannot parse at all (see the
``select_voice`` capability's own Tier 2, which calls this module's
``parse_voice_request`` first and only reaches for the Brain when it
returns nothing).

**Governance order this module is one link in, never the whole chain**
(the rest lives in the ``select_voice`` capability and
``voice_preference.effective_voice_for``):

    system safety/governance   ->  capability enable/disable (routing
                                    layer, before this module is ever
                                    reached) and request.retain's own
                                    MemoryPolicy gate (after it)
    explicit user override     ->  voice_preference_for (checked BEFORE
                                    this module is ever consulted)
    applicable preference      ->  autonomous_voice_choice_for, once made
    agent autonomous choice    ->  select_voice() below (Tier 1,
                                    deterministic) or the capability's
                                    own Brain-assisted Tier 2
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
    never re-interpreted differently by different callers. ``language``,
    ``accent`` and ``gender`` are independent and each optional, exactly
    as ``VoiceOption`` keeps them; ``raw_text`` is kept only so a
    refusal message can quote what was actually asked."""

    raw_text: str
    language: "str | None" = None
    accent: "str | None" = None
    locale: "str | None" = None
    gender: "str | None" = None

    def is_empty(self) -> bool:
        return (
            self.language is None and self.accent is None
            and self.locale is None and self.gender is None
        )


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


def _find_gender(text: str) -> "str | None":
    """A real, commonly-reported voice trait, recognised the identical
    whole-word way language names are - "female"/"woman" -> female,
    "male"/"man" -> male. Small and open (``voice_options.GENDER_WORDS``),
    never guessed from anything else."""
    from src.services.identity.voice_options import GENDER_WORDS  # noqa: PLC0415

    lowered = text.lower()
    for word, canonical in GENDER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return canonical
    return None


_ACCENT_ADJECTIVE = re.compile(r"\b([a-z][a-z-]*)\s+accent\b", re.IGNORECASE)

# Words that can immediately precede a language name in ordinary English
# without describing a region at all - "a Spanish voice", "an English
# voice", "the French option". Excluded from the implicit
# "<adjective> <language>" reading below so "Use a Spanish voice" is
# never misread as accent="a". Deliberately small: only what ordinary
# grammar puts directly before a noun, never a stand-in for a real
# regional-adjective catalog (there is none - any other word is read as
# a genuine accent qualifier, honestly, even if it turns out not to
# match anything real later).
_NON_ACCENT_PRECEDING_WORDS = {"a", "an", "the", "this", "that", "some", "any", "one"}


def parse_voice_request(text: str) -> VoiceRequest:
    """Deterministic, Brain-free extraction of a language/accent/gender
    request from ordinary text.

    The accent, when asked for, is read two ways - whichever applies:

    1. Explicit: whatever adjective sits immediately before the word
       "accent" ("a *British* accent", "an *Italian* accent", "a
       *Nigerian* accent") - never limited to the small language-name
       table, since a regional accent descriptor ("british", "nigerian")
       is not itself a language name and providers genuinely report
       accents this project has no reason to enumerate in advance (see
       ``VoiceOption.accent``, always a free-form provider string).
    2. Implicit, the "<regional adjective> <language>" shape with no
       word "accent" at all - "a *Nigerian* English voice", "American
       Spanish" - recognised by a non-language-name word immediately
       preceding a recognised language name.

    The language, separately, is read from ``voice_options.LANGUAGE_NAMES``
    - matching the two contrasting examples this checkpoint's own brief
    gives: "an Italian voice with an Italian accent" (the one language
    word doubles as the accent, since there is no other word to
    contrast it against) versus "speak English with a Spanish accent"
    (two distinct language-table words - the one immediately preceding
    "accent" is the accent; the other is the language). Never guesses a
    language, accent or gender that was not actually named.
    """
    gender = _find_gender(text)

    accent_match = _ACCENT_ADJECTIVE.search(text)
    accent = accent_match.group(1).lower() if accent_match else None

    occurrences = _find_language_occurrences(text)
    distinct = list(dict.fromkeys(name for _, name in occurrences))

    if accent is None:
        # Implicit "<adjective> <language>" shape - a word immediately
        # preceding a recognised language occurrence, itself not a
        # language name, reads as a regional accent qualifier ("Nigerian
        # English" -> accent=nigerian, language=english).
        from src.services.identity.voice_options import GENDER_WORDS  # noqa: PLC0415

        for position, name in occurrences:
            preceding = text[:position].rstrip()
            match = re.search(r"([A-Za-z][A-Za-z-]*)\s*$", preceding)
            if not match:
                continue
            candidate = match.group(1).lower()
            if candidate in _NON_ACCENT_PRECEDING_WORDS or candidate in GENDER_WORDS:
                continue
            if candidate not in {n.lower() for n in distinct}:
                accent = candidate
                break
        if accent is None:
            return VoiceRequest(
                raw_text=text, language=distinct[0] if distinct else None, accent=None, gender=gender,
            )
        # The implicit shape names exactly one language word - it is the
        # language, and the adjective just found is the accent. Never
        # applies when the request already carries two or more distinct
        # language words (that shape is handled by the explicit-"accent"
        # branch below, and an unmarked second language word is
        # ambiguous enough to leave alone rather than guess at).
        if len(distinct) == 1:
            return VoiceRequest(raw_text=text, language=distinct[0], accent=accent, gender=gender)
        return VoiceRequest(raw_text=text, language=distinct[0] if distinct else None, accent=None, gender=gender)

    others = [name for name in distinct if name != accent]
    if others:
        return VoiceRequest(raw_text=text, language=others[0], accent=accent, gender=gender)
    # No other distinct language word: if the accent word is itself a
    # known language name, it describes both facts (requirement 7's own
    # "Italian voice with an Italian accent" example); otherwise
    # ("British accent" alone - "british" is not a language name) the
    # language genuinely was not stated, and stays honestly None.
    from src.services.identity.voice_options import LANGUAGE_NAMES  # noqa: PLC0415

    language = accent if (accent in LANGUAGE_NAMES.values() or accent in LANGUAGE_NAMES) else None
    return VoiceRequest(raw_text=text, language=language, accent=accent, gender=gender)


@dataclass(frozen=True)
class VoiceSelection:
    """The whole, honest answer: what was chosen (if anything), and
    precisely which parts of the request that choice actually satisfies -
    never a single boolean collapsing "found something" with "found what
    was asked for"."""

    option: "VoiceOption | None"
    language_satisfied: bool
    accent_satisfied: bool
    gender_satisfied: bool
    reason: str


def _pick(options: "list[VoiceOption]") -> VoiceOption:
    """A stable, deterministic choice among several equally-valid
    matches - sorted by provider then voice_id, never by insertion
    order or randomness, so the same real catalog always yields the
    same choice."""
    return sorted(options, key=lambda o: (o.provider, o.voice_id))[0]


def _matches(opt: VoiceOption, request: VoiceRequest, *, check_language: bool, check_accent: bool, check_gender: bool) -> bool:
    """Whether ``opt`` satisfies whichever of the three checked
    constraints ``request`` actually asked for - a constraint the
    request never named is always a no-op, never a filter (so passing
    ``check_accent=True`` for a request with no accent asked for is
    harmless), which is what lets ``select_voice`` below try several
    constraint combinations without special-casing which were actually
    requested."""
    if check_language and request.language is not None and not languages_match(opt.language, request.language):
        return False
    if check_accent and request.accent is not None:
        if opt.accent is None or normalize_language_token(opt.accent) != normalize_language_token(request.accent):
            return False
    if check_gender and request.gender is not None:
        if opt.gender is None or normalize_language_token(opt.gender) != normalize_language_token(request.gender):
            return False
    return True


def _describe(has_lang: bool, has_accent: bool, has_gender: bool, lang_ok: bool, accent_ok: bool, gender_ok: bool) -> str:
    parts = []
    for asked, satisfied, label in (
        (has_lang, lang_ok, "language"), (has_accent, accent_ok, "accent"), (has_gender, gender_ok, "gender"),
    ):
        if asked:
            parts.append(f"{label} satisfied" if satisfied else f"no currently available option also matched the requested {label}")
    return "; ".join(parts) if parts else "matched"


def select_voice(request: VoiceRequest, available: "tuple[VoiceOption, ...]") -> VoiceSelection:
    """Deterministic constraint matching over real, currently-discovered
    options - never a Brain call, never a fabricated option.

    Language, when requested, is the one constraint that is never
    dropped: every attempt below that includes a language request also
    checks it, and a language that matches nothing available is refused
    outright rather than answering with an unrelated language. Accent
    and gender degrade independently and honestly - tried together
    first, then each alone, so a real partial match is preferred over no
    match, and ``VoiceSelection`` always reports precisely which of the
    three were actually satisfied. A request naming none of the three is
    refused with a reason naming that, rather than picking arbitrarily.
    """
    if request.is_empty():
        return VoiceSelection(
            None, False, False, False,
            "no language, accent or gender was recognised in that request",
        )
    if not available:
        return VoiceSelection(
            None, False, False, False,
            "no voice options are currently available from any configured provider",
        )

    has_lang = request.language is not None
    has_accent = request.accent is not None
    has_gender = request.gender is not None

    # Every attempt checks only constraints that were actually
    # requested, and every attempt checks at least one - an attempt
    # that "drops" the one and only constraint asked for would silently
    # match anything, which is exactly the bug this explicit
    # enumeration (rather than a generic drop-one-at-a-time loop) exists
    # to rule out structurally. Ordered strictest to loosest; language,
    # once requested, is checked in every attempt but the very last.
    if has_lang:
        if has_accent and has_gender:
            attempts: "list[tuple[bool, bool, bool]]" = [
                (True, True, True), (True, True, False), (True, False, True), (True, False, False),
            ]
        elif has_accent:
            attempts = [(True, True, False), (True, False, False)]
        elif has_gender:
            attempts = [(True, False, True), (True, False, False)]
        else:
            attempts = [(True, False, False)]
    else:
        if has_accent and has_gender:
            attempts = [(False, True, True), (False, True, False), (False, False, True)]
        elif has_accent:
            attempts = [(False, True, False)]
        else:
            attempts = [(False, False, True)]

    for check_language, check_accent, check_gender in attempts:
        candidates = [
            o for o in available
            if _matches(o, request, check_language=check_language, check_accent=check_accent, check_gender=check_gender)
        ]
        if candidates:
            lang_ok = check_language and has_lang
            accent_ok = check_accent and has_accent
            gender_ok = check_gender and has_gender
            return VoiceSelection(
                _pick(candidates), lang_ok, accent_ok, gender_ok,
                _describe(has_lang, has_accent, has_gender, lang_ok, accent_ok, gender_ok),
            )

    if has_lang:
        return VoiceSelection(
            None, False, False, False,
            f"no currently available provider option is reported to support '{request.language}'",
        )
    if has_accent:
        return VoiceSelection(
            None, False, False, False,
            f"no currently available provider option is reported with a '{request.accent}' accent",
        )
    return VoiceSelection(
        None, False, False, False,
        f"no currently available provider option is reported as '{request.gender}'",
    )
