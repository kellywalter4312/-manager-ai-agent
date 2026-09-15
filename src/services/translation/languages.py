"""The languages Alfred can name, translate between and speak - one table of
data, never a branch per language.

Whether a language can actually be *translated* is decided by what the offline
translator has installed; whether it can be *spoken* by the local voice is
decided by the voices that voice has installed. This table only says how a
person names a language, which locale a device voice is asked for, which
language the local neural voice pronounces it as, and a few very common words
used to guess the language of a typed phrase.

**Guessing is a guess, and says so.** ``guess_language`` counts common words
and looks at the script; it is right for ordinary sentences and can be wrong for
a two-word phrase. A person can always name the source language ("translate
hola from Spanish into English"), and spoken language is detected by speech
recognition instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    names: tuple[str, ...]
    locale: str
    # The language the local neural voice pronounces this as ("" when no
    # verified local voice speaks it - a device voice is used instead).
    voice_language: str = ""
    # Local voice ids for this language, preferred first.
    voices: tuple[str, ...] = ()
    common_words: frozenset[str] = field(default_factory=frozenset)


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


LANGUAGES: tuple[Language, ...] = (
    # The word lists also carry the everyday greetings and courtesies people
    # actually type first ("buenas noches", "bonsoir", "obrigado"). Found at
    # runtime: with only function words listed, a short real phrase scored
    # zero in every language, the guess fell back to English, and the phrase
    # was "translated" English-to-English and handed back unchanged.
    Language("en", "English", ("english", "inglés", "ingles", "anglais", "englisch", "inglese", "inglês"), "en-GB",
             "en-us", ("bf_emma", "af_heart"),
             _words("the is are was and to of where you i it what my in this that for with have do not we they "
                    "good morning evening afternoon night hello hi thanks thank please sorry yes no how much "
                    "can could would like buy ticket train station help name time")),
    Language("es", "Spanish", ("spanish", "español", "espanol", "castilian", "castellano"), "es-ES",
             "es", ("ef_dora", "em_alex"),
             _words("el la los las de que y en es está esta dónde donde por para un una mi yo no con se lo muy qué necesito "
                    "buenas buenos noches días dias tardes hola gracias señor señora perdón por favor sí cómo cuánto "
                    "comprar billete tren estación ayuda mañana nombre")),
    Language("fr", "French", ("french", "français", "francais"), "fr-FR",
             "fr-fr", ("ff_siwis",),
             _words("le la les des est et de un une je vous où ou pas que qui dans pour mon ma nous suis c'est "
                    "bonjour bonsoir salut merci s'il plaît oui non comment combien acheter billet train gare "
                    "aide matin soir nuit monsieur madame")),
    Language("de", "German", ("german", "deutsch", "alemán", "allemand"), "de-DE",
             "", (),
             _words("der die das und ist ich nicht ein eine wo zu mit sie es wir mein bin haben "
                    "guten morgen abend tag hallo danke bitte ja nein wie viel kaufen fahrkarte bahnhof hilfe nacht")),
    Language("it", "Italian", ("italian", "italiano"), "it-IT",
             "it", ("if_sara", "im_nicola"),
             _words("il lo gli di che è un una non per sono dove dov'è c'è mio mia ho della questo come "
                    "anche grazie ciao voglio posso devo vorrei del alla sei siamo "
                    "buongiorno buonasera buonanotte prego scusi sì quanto comprare biglietto treno stazione aiuto tutti")),
    Language("pt", "Portuguese", ("portuguese", "português", "portugues"), "pt-PT",
             "pt-br", ("pf_dora", "pm_alex"),
             _words("o a os as de que e é um uma não para onde fica meu minha com você estou "
                    "bom boa dia tarde noite olá obrigado obrigada desculpe sim como quanto comprar bilhete "
                    "comboio estação ajuda pela")),
    Language("zh", "Chinese", ("chinese", "mandarin", "中文", "普通话", "汉语"), "zh-CN",
             "cmn", ("zf_xiaoxiao", "zm_yunxi")),
    Language("hi", "Hindi", ("hindi", "हिन्दी", "हिंदी"), "hi-IN"),
    Language("ja", "Japanese", ("japanese", "日本語"), "ja-JP"),
)

_BY_CODE = {language.code: language for language in LANGUAGES}
_BY_NAME = {name: language for language in LANGUAGES for name in (language.code, *language.names)}

_HAN = re.compile(r"[一-鿿]")
_KANA = re.compile(r"[぀-ヿ]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[a-zà-ÿœç'’]+")
# Letters that point at one language - never one several languages share
# ("è" and "à" are as Italian as they are French).
_ACCENT_HINTS = {"es": "ñ¿¡", "fr": "çœêëîôû", "de": "ßäöü", "pt": "ãõ", "it": "ìòù"}


def language_by_code(code: object) -> "Language | None":
    return _BY_CODE.get(str(code or "").strip().lower())


def language_named(name: object) -> "Language | None":
    """A language from how a person names it - "Spanish", "español", "es"."""
    return _BY_NAME.get(" ".join(str(name or "").strip().lower().split()))


def guess_language(text: object, candidates: "tuple[str, ...] | None" = None) -> "str | None":
    """The most likely language code of ``text``, or None when nothing points
    anywhere. Script first (Han, kana, Devanagari), then common words and a few
    accents - a guess, never presented as detection."""
    words = str(text or "")
    allowed = set(candidates) if candidates else None
    if _KANA.search(words) and (allowed is None or "ja" in allowed):
        return "ja"
    if _HAN.search(words) and (allowed is None or "zh" in allowed):
        return "zh"
    if _DEVANAGARI.search(words) and (allowed is None or "hi" in allowed):
        return "hi"
    lowered = words.lower()
    tokens = _WORD.findall(lowered)
    best, best_score = None, 0.0
    for language in LANGUAGES:
        if allowed is not None and language.code not in allowed:
            continue
        score = float(sum(1 for token in tokens if token in language.common_words))
        score += 1.5 * sum(1 for ch in _ACCENT_HINTS.get(language.code, "") if ch in lowered)
        if score > best_score:
            best, best_score = language.code, score
    return best
