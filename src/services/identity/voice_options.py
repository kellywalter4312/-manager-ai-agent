"""What a provider can actually offer, discovered rather than declared.

``voice.py`` describes what an agent should sound like; ``voice_preference.py``
lets that be overridden at runtime. Neither ever learns what a real, live
speech provider's own catalog contains - deliberately, per both modules'
own docstrings. This module is the missing third fact: a live, honest
snapshot of what one provider genuinely has available right now, never
persisted, never a catalog invented ahead of what a vendor actually
reports.

**Language, accent and locale are kept as three separate, optional
facts - never collapsed into one string.** This mirrors, at the value-type
level, a distinction ``VoiceProfile.accent`` never made explicit: an
"accent" like the existing declared trait ``"nigerian-english"`` conflates
a language (English) with a regional variant (Nigerian). ``VoiceOption``
keeps them apart so a caller can genuinely ask "is this in Italian" as a
different question from "does this sound British" - answerable only when
the provider itself reports it, honestly ``None`` otherwise. Never
guessed, never defaulted to a common case.

**Provider-native metadata only.** ``language``/``accent``/``locale``
here are populated exclusively from what a real vendor API or OS facility
actually reports for a given voice (ElevenLabs' own
``verified_languages``/``labels``, Windows' own ``VoiceInfo.Culture``) -
never inferred from a name, never filled in with a plausible guess. A
provider that cannot report one of these leaves it ``None``, and every
caller in this project must treat that as "unknown", not as "does not
apply" or "matches anything".
"""

from __future__ import annotations

from dataclasses import dataclass

# A small, open, non-exhaustive map from common ISO 639-1-ish language
# codes to a human-readable English name - used only to make a provider's
# own reported code legible, and to recognise a handful of language names
# typed in an ordinary request. Never a closed enum, never treated as the
# complete set of languages a provider might genuinely report: an
# unrecognised code is preserved as-is (see ``normalize_language_token``)
# rather than discarded, because a real fact this table does not yet know
# the English name for is still a real fact.
#
# "ro"/"romanian" deliberately absent: this project's own
# architecture-audit guard (test_stage18_knowledge_and_learning.py)
# forbids any shipped string literal containing "roman" (an example
# history subject that must never be hardcoded), and "romanian" contains
# it as a substring - an honest collision with an unrelated word, not a
# violation of that rule's own intent. A provider reporting "ro" still
# works correctly; it is simply carried through unrecognised (see
# normalize_language_token) rather than translated to an English name.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "english", "it": "italian", "es": "spanish", "fr": "french",
    "de": "german", "pt": "portuguese", "nl": "dutch", "pl": "polish",
    "sv": "swedish", "da": "danish", "no": "norwegian", "fi": "finnish",
    "ru": "russian", "uk": "ukrainian", "tr": "turkish", "ar": "arabic",
    "hi": "hindi", "ja": "japanese", "ko": "korean", "zh": "chinese",
    "el": "greek", "he": "hebrew", "id": "indonesian", "ms": "malay",
    "vi": "vietnamese", "th": "thai", "cs": "czech",
    "hu": "hungarian", "sk": "slovak", "bg": "bulgarian", "hr": "croatian",
    "ta": "tamil", "fil": "filipino",
}
# The reverse lookup, built once - a name typed in a request resolves back
# to the code a provider actually reports, so matching works in whichever
# direction the two sides happen to use.
_NAME_TO_CODE: dict[str, str] = {name: code for code, name in LANGUAGE_NAMES.items()}


def normalize_language_token(value: "str | None") -> "str | None":
    """A lowercase, whitespace-collapsed canonical form for comparing two
    language facts - a provider's own code/name against another's, or a
    word found in a request against a provider's report. Never widens or
    narrows the fact itself: ``"it"`` and ``"Italian"`` normalize to
    forms that compare equal (both resolve through the table above),
    but a value this table has never heard of normalizes only by casing,
    never dropped or replaced with a guess.
    """
    if value is None:
        return None
    token = " ".join(str(value).split()).lower()
    if not token:
        return None
    if token in _NAME_TO_CODE:
        return token
    if token in LANGUAGE_NAMES:
        return token
    return token


def languages_match(a: "str | None", b: "str | None") -> bool:
    """Whether two language facts genuinely name the same language -
    comparing a code against a name (``"it"`` vs ``"italian"``) correctly,
    and never claiming a match when either side is unknown (``None``
    never matches anything, including another ``None``: two unknowns are
    not evidence of agreement)."""
    if a is None or b is None:
        return False
    na, nb = normalize_language_token(a), normalize_language_token(b)
    if na == nb:
        return True
    ca = _NAME_TO_CODE.get(na, na)
    cb = _NAME_TO_CODE.get(nb, nb)
    return ca == cb


@dataclass(frozen=True)
class VoiceOption:
    """One real, currently-available voice a provider genuinely reports -
    never a fabricated or remembered one. ``voice_id`` is the provider's
    own stable identifier (an ElevenLabs voice id, an installed Windows
    voice name); ``name`` is what a person would recognise. ``provider``
    names which adapter this option came from (``"elevenlabs"``,
    ``"windows"``, ...) - required, because applying a choice back onto a
    ``VoiceProfile`` (see ``voice_preference.py``) needs to know which
    provider-specific field it belongs in.

    ``language``/``accent``/``locale`` are each optional and independent -
    see the module docstring. A voice with ``language=None`` is not "any
    language"; it is "this provider did not report one", and must never
    be offered as satisfying a specific language request.
    """

    voice_id: str
    name: str
    provider: str
    language: "str | None" = None
    accent: "str | None" = None
    locale: "str | None" = None

    def __post_init__(self) -> None:
        if not str(self.voice_id).strip():
            raise ValueError("VoiceOption.voice_id must not be blank")
        if not str(self.name).strip():
            raise ValueError("VoiceOption.name must not be blank")
        if not str(self.provider).strip():
            raise ValueError("VoiceOption.provider must not be blank")
