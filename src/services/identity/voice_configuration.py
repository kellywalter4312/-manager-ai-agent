"""How an agent sounds, configured against what the active speech provider can
actually do - voice, spoken language, accent and pace kept as separate facts.

    voice      who is heard: one of the provider's real, installed voices
    language   what is spoken: "speak Italian" changes this
    accent     how the voice sounds: the language/variety the chosen voice was
               trained on - "English with an Italian accent" is an Italian
               voice speaking English, never Italian
    variant    which regional variety of the spoken language itself -
               "Brazilian Portuguese", "British English": a native voice of
               that language whose reported locale is that region (a dialect
               or variant is the language's own, an accent is a voice's)
    style      a manner of speaking ("calm", "cheerful") - only when the
               provider reports speaking styles; none of the local ones do, so
               a style request is refused honestly rather than ignored
    pace       how fast: a word the provider maps to its own speed or rate

No language, accent, dialect or provider is special here. A request is planned
against the provider's own facts (``VoiceFacts``, read live from the adapter):
its voices with their reported language/accent/locale/gender, the languages it
can speak, and whether a voice can speak a language other than its own. A
combination the provider cannot produce is refused with what it *can* do -
never approximated silently.

Settings are the agent's own governed Memory records, beside the existing voice
preference (``voice_preference.py``) - one mechanism, never a second store - and
they reach the ``VoiceProfile`` a channel speaks with through
``effective_voice_for``. Changing any of them changes nothing about the agent's
identity, personality, memory, conversation or capabilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

from src.services.identity.voice import VoiceProfile
from src.services.identity.voice_options import LANGUAGE_NAMES, VoiceOption
from src.services.memory.memory import MemoryRecord

VOICE_SETTING_FACT_TYPE = "voice_setting"
SETTINGS = ("language", "variant", "accent", "pace", "style")

# ISO country codes a provider reports in a locale ("en-GB"), and the everyday
# word for someone from there - world data used only to compare a requested
# accent with a reported locale, never a list of supported accents. Partial by
# nature: a locale this does not name falls back to the voice's own language,
# so a provider is never limited to the regions listed here.
_REGION_WORDS = {
    "us": "american", "gb": "british", "uk": "british", "au": "australian", "nz": "new zealand",
    "ie": "irish", "ca": "canadian", "in": "indian", "za": "south african", "ng": "nigerian",
    "ke": "kenyan", "jm": "jamaican", "sg": "singaporean", "ph": "filipino",
    "br": "brazilian", "pt": "portuguese", "mx": "mexican", "es": "spanish", "ar": "argentinian",
    "co": "colombian", "fr": "french", "be": "belgian", "ch": "swiss", "de": "german",
    "at": "austrian", "it": "italian", "cn": "mandarin", "tw": "taiwanese", "hk": "cantonese",
    "jp": "japanese", "kr": "korean", "ru": "russian", "nl": "dutch", "se": "swedish",
}
_PACE_REQUESTS = (
    (re.compile(r"\b(?:speak|talk|go)\s+(?:a\s+(?:bit|little)\s+)?(?:slower|more\s+slowly)\b|\bslow\s+down\b", re.I), "slow"),
    (re.compile(r"\b(?:speak|talk|go)\s+(?:a\s+(?:bit|little)\s+)?(?:faster|more\s+quickly|quicker)\b|\bspeed\s+up\b", re.I), "fast"),
    (re.compile(r"\b(?:normal|usual|regular)\s+(?:speed|pace)\b", re.I), "normal"),
)


def _tag(agent_key: str, setting: str) -> str:
    return f"voice_setting:{setting}:" + "_".join(str(agent_key).lower().split())


def voice_setting_for(agent_key: str, setting: str, *, recall: "Callable[..., Any] | None") -> "str | None":
    if recall is None:
        return None
    for record in recall(kind="user_context", tags={_tag(agent_key, setting)}, limit=1):
        value = (record.context or {}).get("value")
        if value:
            return str(value)
    return None


def set_voice_setting(
    agent_key: str, setting: str, value: str, *,
    recall: "Callable[..., Any]", remember: "Callable[[MemoryRecord], Any]", forget: "Callable[[str], Any]",
) -> None:
    """Store one voice setting for this agent, replacing the previous value; a
    blank value clears it (the agent's declared default applies again)."""
    if setting not in SETTINGS:
        raise ValueError(f"unknown voice setting {setting!r}")
    tag = _tag(agent_key, setting)
    for existing in recall(kind="user_context", tags={tag}, limit=5):
        forget(existing.id)
    value = " ".join(str(value or "").split())
    if value:
        remember(MemoryRecord(
            content=f"Voice {setting} for {agent_key} is {value}.", kind="user_context",
            tags={VOICE_SETTING_FACT_TYPE, tag},
            context={"fact_type": VOICE_SETTING_FACT_TYPE, "agent_key": agent_key, "setting": setting, "value": value},
        ))


def voice_for_settings(
    options: "Iterable[VoiceOption]", agent_key: str, *, recall: "Callable[..., Any] | None",
) -> "VoiceOption | None":
    """A voice on the active provider that satisfies this agent's stored accent
    (and spoken language, when the provider's voices speak only their own) -
    how a request made under one provider carries to another whose voices have
    different names. None when no accent was asked for or none matches."""
    options = tuple(options)
    language = voice_setting_for(agent_key, "language", recall=recall)
    variant = voice_setting_for(agent_key, "variant", recall=recall)
    if variant:
        # The variety of a language travels too: a native voice of it from
        # that region, on whichever provider is speaking now.
        native = [v for v in options if variant_of(v) == variant
                  and (not language or canonical_language(v.language) == language)]
        if native:
            return sorted(native, key=lambda v: v.voice_id)[0]
    accent = voice_setting_for(agent_key, "accent", recall=recall)
    if not accent:
        return None
    with_accent = [v for v in options if _accent_matches(v, accent)]
    native = [v for v in with_accent if language and canonical_language(v.language) == language]
    return (sorted(native or with_accent, key=lambda v: v.voice_id) or [None])[0]


def apply_voice_settings(profile: VoiceProfile, agent_key: str, *, recall: "Callable[..., Any] | None") -> VoiceProfile:
    language = voice_setting_for(agent_key, "language", recall=recall)
    pace = voice_setting_for(agent_key, "pace", recall=recall)
    changes = {}
    if language:
        changes["language"] = language
    if pace:
        changes["pace"] = pace
    return replace(profile, **changes) if changes else profile


@dataclass(frozen=True)
class VoiceFacts:
    """What one speech provider reports it can do, read live."""

    provider: str
    voices: "tuple[VoiceOption, ...]"
    spoken_languages: "tuple[str, ...]"
    across_languages: bool
    paces: "tuple[str, ...]"
    can_synthesize: bool
    # Speaking styles the provider reports ("speaking_styles()"), if any.
    styles: "tuple[str, ...]" = ()

    def accents(self) -> "tuple[str, ...]":
        return tuple(sorted({a for a in (accent_of(v) for v in self.voices) if a}))

    def variants(self, language: str) -> "tuple[str, ...]":
        """The regional varieties this provider has native voices for in ``language``."""
        return tuple(sorted({r for r in (variant_of(v) for v in self.voices
                                          if canonical_language(v.language) == language) if r}))


def voice_facts(adapter: Any) -> VoiceFacts:
    def ask(name: str, default: Any) -> Any:
        method = getattr(adapter, name, None)
        if not callable(method):
            return default
        try:
            return method()
        except Exception:  # noqa: BLE001 - a provider that cannot answer reports nothing
            return default

    voices = tuple(ask("available_voices", ()))
    spoken = tuple(ask("spoken_languages", ())) or tuple(sorted({v.language for v in voices if v.language}))
    return VoiceFacts(
        provider=str(getattr(adapter, "name", "none")),
        voices=voices,
        spoken_languages=tuple(canonical_language(x) or x for x in spoken),
        across_languages=bool(getattr(adapter, "speaks_across_languages", False)),
        paces=tuple(ask("pace_words", ())),
        can_synthesize=callable(getattr(adapter, "synthesize", None)),
        styles=tuple(str(s).lower() for s in ask("speaking_styles", ())),
    )


def variant_of(option: VoiceOption) -> "str | None":
    """The regional variety a voice speaks its own language in - the region of
    its reported locale ("en-GB" -> "british"), or None when it reports none."""
    locale = str(option.locale or "")
    if "-" not in locale:
        return None
    return _REGION_WORDS.get(locale.split("-", 1)[1].lower())


def region_word(value: object) -> "str | None":
    """``value`` when it names a region's people/variety ("Brazilian",
    "british"), else None - world data, never a list of what is supported."""
    word = " ".join(str(value or "").split()).lower()
    return word if word in set(_REGION_WORDS.values()) else None


def canonical_language(value: object) -> "str | None":
    """A language named any common way ("it", "Italian", "italiano") as its
    lower-case English name, or None when it is not a language."""
    token = " ".join(str(value or "").split()).lower()
    if not token:
        return None
    if token in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[token]
    if token in LANGUAGE_NAMES.values():
        return token
    try:
        from src.services.translation.languages import language_named

        found = language_named(token)
    except Exception:  # noqa: BLE001 - translation languages are optional here
        found = None
    return found.name.lower() if found is not None else None


def accent_of(option: VoiceOption) -> "str | None":
    """The accent a voice has: the provider's reported accent, else the region
    of its reported locale, else its own language."""
    if option.accent:
        return option.accent.lower()
    locale = str(option.locale or "")
    if "-" in locale:
        region = _REGION_WORDS.get(locale.split("-", 1)[1].lower())
        if region:
            return region
    return canonical_language(option.language) if option.language else None


def _accent_matches(option: VoiceOption, wanted: str) -> bool:
    wanted = wanted.lower()
    return wanted in {x for x in (accent_of(option), canonical_language(option.language)) if x}


@dataclass(frozen=True)
class VoicePlan:
    ok: bool
    message: str
    voice: "VoiceOption | None" = None
    language: "str | None" = None
    pace: "str | None" = None
    variant: "str | None" = None
    style: "str | None" = None


def _keep_gender(options: "list[VoiceOption]", current: "VoiceOption | None") -> "list[VoiceOption]":
    """``options`` narrowed to the current voice's gender when any match - a
    change of language, accent or variant is not a request for a different
    kind of voice."""
    if current is None or not current.gender:
        return options
    return [v for v in options if (v.gender or "").lower() == current.gender.lower()] or options


def plan_voice(
    facts: VoiceFacts, *, current: "VoiceOption | None", language: "str | None" = None,
    accent: "str | None" = None, gender: "str | None" = None, pace: "str | None" = None,
    current_language: str = "english", variant: "str | None" = None, style: "str | None" = None,
) -> VoicePlan:
    """What to set to satisfy a request on this provider - or an honest refusal
    naming what the provider offers instead."""
    provider = facts.provider
    if provider == "none" or not facts.voices:
        return VoicePlan(False, f"No voice provider with voices is active ('{provider}'). Choose one with: provider speech_provider use <backend>.")
    spoken = canonical_language(language) if language else None
    if language and spoken is None:
        return VoicePlan(False, f"'{language}' is not a language I recognise.")
    if spoken and spoken not in facts.spoken_languages:
        return VoicePlan(False, f"The {provider} voice provider can't speak {spoken.title()}. It speaks: {', '.join(x.title() for x in facts.spoken_languages)}.")
    if pace and facts.paces and pace not in facts.paces:
        return VoicePlan(False, f"The {provider} voice provider has no '{pace}' pace. It offers: {', '.join(facts.paces)}.")
    if style:
        wanted_style = style.lower()
        if not facts.styles:
            return VoicePlan(False, f"The {provider} voice provider has no speaking styles to choose from - what it can change is "
                                    "the voice, the language, the accent and the pace.")
        if wanted_style not in facts.styles:
            return VoicePlan(False, f"The {provider} voice provider has no '{style}' style. Its styles: {', '.join(facts.styles)}.")
    target_language = spoken or current_language
    candidates = list(facts.voices)
    if gender:
        candidates = [v for v in candidates if (v.gender or "").lower() == gender.lower()] or []
        if not candidates:
            return VoicePlan(False, f"The {provider} voice provider reports no {gender} voice.")
    chosen = current
    if variant:
        native = [v for v in candidates if canonical_language(v.language) == target_language and variant_of(v) == variant]
        if not native:
            offered = facts.variants(target_language)
            return VoicePlan(
                False,
                f"The {provider} voice provider has no {variant.title()} {target_language.title()} voice. "
                + (f"Its {target_language.title()} voices are: {', '.join(o.title() for o in offered)}." if offered
                   else f"It reports no regional varieties of {target_language.title()}."),
            )
        keep = [v for v in native if current is not None and v.voice_id == current.voice_id]
        chosen = (keep or sorted(_keep_gender(native, current), key=lambda v: v.voice_id))[0]
    elif accent:
        with_accent = [v for v in candidates if _accent_matches(v, accent)]
        if not with_accent:
            return VoicePlan(
                False,
                f"The {provider} voice provider has no voice with {'an' if accent[:1].lower() in 'aeiou' else 'a'} {accent.title()} accent. The accents it has: "
                f"{', '.join(a.title() for a in facts.accents()) or 'none reported'}.",
            )
        speakable = [v for v in with_accent if facts.across_languages or canonical_language(v.language) == target_language]
        if not speakable:
            return VoicePlan(
                False,
                f"The {provider} voice provider's voices only speak their own language, so {'an' if accent[:1].lower() in 'aeiou' else 'a'} {accent.title()} accent "
                f"in {target_language.title()} isn't something it can do.",
            )
        preferred = [v for v in speakable if current is not None and v.voice_id == current.voice_id]
        chosen = (preferred or sorted(_keep_gender(speakable, current), key=lambda v: v.voice_id))[0]
    elif spoken:
        native = [v for v in candidates if canonical_language(v.language) == spoken]
        keep_current = current is not None and canonical_language(current.language) == spoken
        if keep_current:
            chosen = current
        elif native:
            same_gender = [v for v in native if current is not None and v.gender and v.gender == current.gender]
            chosen = sorted(same_gender or native, key=lambda v: v.voice_id)[0]
        elif facts.across_languages and current is not None:
            chosen = current
        else:
            return VoicePlan(False, f"The {provider} voice provider has no voice that speaks {spoken.title()}.")
    elif gender:
        chosen = sorted(candidates, key=lambda v: v.voice_id)[0]
    parts = []
    if chosen is not None and (current is None or chosen.voice_id != current.voice_id):
        parts.append(f"voice {chosen.name}")
    if variant:
        parts.append(f"speaking {variant.title()} {target_language.title()}")
    elif spoken:
        parts.append(f"speaking {spoken.title()}")
    if chosen is not None and not variant:
        sound = accent_of(chosen)
        native = canonical_language(chosen.language)
        if sound and (accent or (native and native != target_language)):
            article = "an" if sound[:1] in "aeiou" else "a"
            parts.append(f"with {article} {sound.title()} accent")
    if style:
        parts.append(f"in a {style} style")
    if pace:
        parts.append(f"at a {pace} pace")
    return VoicePlan(True, ", ".join(parts) or "no change needed", voice=chosen, language=spoken, pace=pace,
                     variant=variant, style=style.lower() if style else None)


@dataclass(frozen=True)
class VoiceConfigurationRequest:
    language: "str | None" = None
    accent: "str | None" = None
    pace: "str | None" = None
    question: bool = False
    variant: "str | None" = None
    style: "str | None" = None

    def is_empty(self) -> bool:
        return not (self.language or self.accent or self.pace or self.variant or self.style)


_SPEAK_IN = re.compile(
    r"\b(?:speak|talk|reply|answer|respond|say\s+(?:things|everything))(?:\s+to\s+me)?\s+(?:in\s+|only\s+in\s+)?(?P<word>[A-Za-z]+)\b",
    re.IGNORECASE,
)
_ACCENT_WORD = re.compile(
    r"\bwith\s+(?:an?\s+)?(?P<word>[A-Za-z]+(?:\s+[A-Za-z]+)?)\s+accent\b|\b(?:use|give\s+me|put\s+on|do)\s+(?:an?\s+)?(?P<word2>[A-Za-z]+(?:\s+[A-Za-z]+)?)\s+accent\b"
    r"|\bsound\s+(?:like\s+)?(?:you(?:'re|\s+are)|someone|a\s+person)\s+from\s+(?P<place>[A-Za-z]+(?:\s+[A-Za-z]+)?)",
    re.IGNORECASE,
)


# "Speak Brazilian Portuguese" names the variety by speaking it; "use a British
# English dialect" has to say so - "use a Nigerian English voice if one is
# available" asks for a voice, a softer request select_voice already answers.
_VARIANT = re.compile(
    r"\b(?:speak|talk|reply|answer|respond)\s+(?:to\s+me\s+)?(?:in\s+)?(?:an?\s+)?(?P<region>[A-Za-z]+)\s+(?P<lang>[A-Za-z]+)\b"
    r"|\b(?:use|in|with)\s+(?:an?\s+|the\s+)?(?P<region2>[A-Za-z]+)\s+(?P<lang2>[A-Za-z]+)\s+(?:dialect|variant|variety)\b",
    re.IGNORECASE,
)
_STYLE = re.compile(
    r"\b(?:in|with)\s+an?\s+(?P<style>[A-Za-z]+(?:,?\s+(?:and\s+)?[A-Za-z]+)?)\s+(?:speaking\s+)?(?:style|tone|manner)\b",
    re.IGNORECASE,
)


def parse_voice_configuration(text: str) -> VoiceConfigurationRequest:
    """The spoken language, accent and pace a request names - each only when
    actually named. "Speak Italian" names a language; "speak English with an
    Italian accent" names both; "sound like someone from Liverpool" names an
    accent (a place) and no language."""
    working = str(text or "")
    language = None
    for match in _SPEAK_IN.finditer(working):
        found = canonical_language(match.group("word"))
        if found:
            language = found
            break
    accent = None
    match = _ACCENT_WORD.search(working)
    if match is not None:
        raw = match.group("word") or match.group("word2") or match.group("place") or ""
        words = [w for w in raw.lower().split() if w not in ("a", "an", "the", "my", "your", "no")]
        accent = " ".join(words) or None
    # "Speak Brazilian Portuguese", "use a British English dialect": a region's
    # word before a language names that language's own regional variety.
    variant = None
    for match in _VARIANT.finditer(working):
        region = region_word(match.group("region") or match.group("region2"))
        spoken = canonical_language(match.group("lang") or match.group("lang2"))
        if region and spoken:
            variant, language = region, spoken
            break
    style = None
    found_style = _STYLE.search(working)
    if found_style is not None:
        style = " ".join(found_style.group("style").replace(",", " ").split()).lower() or None
    pace = next((value for pattern, value in _PACE_REQUESTS if pattern.search(working)), None)
    question = bool(re.match(r"^\s*(?:can|could|do|would)\s+you\b", working, re.IGNORECASE)) and working.rstrip().endswith("?")
    return VoiceConfigurationRequest(language=language, accent=accent, pace=pace, question=question,
                                     variant=variant, style=style)


def find_option(facts: VoiceFacts, name: "str | None") -> "VoiceOption | None":
    wanted = str(name or "").strip().lower()
    return next((v for v in facts.voices if v.name.lower() == wanted or v.voice_id.lower() == wanted), None)


def current_option(facts: VoiceFacts, profile: "VoiceProfile | None") -> "VoiceOption | None":
    """The provider option a resolved profile currently speaks with, if it is one."""
    if profile is None:
        return None
    ids: Iterable[str] = [str(v) for k, v in profile.style.items() if str(k).endswith("_voice_id")]
    for candidate in (*ids, profile.preferred_voice):
        found = find_option(facts, candidate)
        if found is not None:
            return found
    return None
