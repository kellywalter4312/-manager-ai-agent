"""Agent-autonomous voice selection checkpoint (2026-09-08+).

Pure, deterministic unit coverage for the two new value/decision modules:
``src/services/identity/voice_options.py`` (VoiceOption, language
normalisation) and ``src/services/identity/voice_selection.py``
(VoiceRequest parsing, select_voice's constraint matching). Neither
module imports anything from ``src`` beyond each other and stdlib, so
none of this needs a built runtime - the same "pure value object, no
runtime needed to test it" discipline ``voice.py``'s own suite already
follows.
"""

from __future__ import annotations

import pytest

from src.services.identity.voice_options import VoiceOption, languages_match, normalize_language_token
from src.services.identity.voice_selection import (
    VoiceRequest,
    parse_voice_request,
    select_voice,
)


class TestVoiceOption:
    def test_requires_a_real_voice_id_name_and_provider(self):
        with pytest.raises(ValueError):
            VoiceOption(voice_id="", name="x", provider="elevenlabs")
        with pytest.raises(ValueError):
            VoiceOption(voice_id="x", name="", provider="elevenlabs")
        with pytest.raises(ValueError):
            VoiceOption(voice_id="x", name="y", provider="")

    def test_language_accent_locale_default_to_honest_unknown(self):
        option = VoiceOption(voice_id="v1", name="Voice", provider="elevenlabs")
        assert option.language is None
        assert option.accent is None
        assert option.locale is None


class TestLanguageNormalization:
    def test_a_code_and_its_name_compare_equal(self):
        assert languages_match("it", "italian")
        assert languages_match("Italian", "IT")

    def test_unrelated_languages_do_not_match(self):
        assert not languages_match("it", "spanish")

    def test_none_never_matches_anything_including_another_none(self):
        assert not languages_match(None, "italian")
        assert not languages_match("italian", None)
        assert not languages_match(None, None)

    def test_an_unrecognised_token_still_normalizes_by_casing_only(self):
        assert normalize_language_token("Klingon") == "klingon"


class TestParseVoiceRequest:
    def test_language_and_matching_accent_both_recognised(self):
        r = parse_voice_request("find me an Italian voice with an Italian accent")
        assert r.language == "italian"
        assert r.accent == "italian"

    def test_a_bare_language_request_has_no_accent(self):
        r = parse_voice_request("I want a Spanish voice")
        assert r.language == "spanish"
        assert r.accent is None

    def test_language_and_accent_are_not_conflated_when_distinct(self):
        r = parse_voice_request("speak English with a Spanish accent")
        assert r.language == "english"
        assert r.accent == "spanish"

    def test_a_regional_accent_that_is_not_a_language_name_is_still_recognised(self):
        r = parse_voice_request("give me a voice with a British accent")
        assert r.accent == "british"
        assert r.language is None  # "british" names no language on its own

    def test_a_request_naming_nothing_recognisable_is_honestly_empty(self):
        r = parse_voice_request("pilot the antigravity beam array to the third moon")
        assert r.is_empty()

    def test_ordinary_english_words_that_collide_with_iso_codes_are_never_mistaken_for_a_language(self):
        """Real, observed false positives, fixed and pinned: several ISO
        639-1 codes are also extremely common English words - "it"
        (Italian), "hi" (Hindi), "he" (Hebrew), "no" (Norwegian). A
        request must never be misread because of one of these, which is
        why _find_language_occurrences matches full names only, never
        bare codes, when scanning free text."""
        assert parse_voice_request("...it is impossible").is_empty()
        assert parse_voice_request("hi Alfred, how are you").is_empty()
        assert parse_voice_request("he said no to the plan").is_empty()
        assert parse_voice_request("no, I don't think so").is_empty()

    def test_a_real_language_word_still_works_alongside_a_colliding_pronoun(self):
        r = parse_voice_request("hi Alfred, find me an English voice")
        assert r.language == "english"
        r2 = parse_voice_request("no, I want a Spanish voice")
        assert r2.language == "spanish"

    def test_raw_text_is_preserved_for_honest_refusal_messages(self):
        r = parse_voice_request("some request")
        assert r.raw_text == "some request"


class TestSelectVoice:
    ITALIAN_WITH_ACCENT = VoiceOption("id1", "Italian Male", "elevenlabs", language="it", accent="italian")
    ITALIAN_NO_ACCENT = VoiceOption("id2", "Generic Italian", "elevenlabs", language="italian")
    SPANISH = VoiceOption("id3", "Spanish Female", "elevenlabs", language="es", accent="mexican")
    ENGLISH = VoiceOption("id4", "Some English", "windows", language="english")
    OPTIONS = (ITALIAN_WITH_ACCENT, ITALIAN_NO_ACCENT, SPANISH, ENGLISH)

    def test_language_and_accent_both_satisfied_when_a_real_option_matches_both(self):
        result = select_voice(parse_voice_request("an Italian voice with an Italian accent"), self.OPTIONS)
        assert result.option == self.ITALIAN_WITH_ACCENT
        assert result.language_satisfied and result.accent_satisfied

    def test_language_only_request_is_satisfied_without_requiring_an_accent(self):
        result = select_voice(parse_voice_request("I want a Spanish voice"), self.OPTIONS)
        assert result.option == self.SPANISH
        assert result.language_satisfied
        assert result.accent_satisfied is False  # no accent was asked for

    def test_an_unsupported_language_is_refused_honestly_never_substituted(self):
        result = select_voice(parse_voice_request("find me a German voice"), self.OPTIONS)
        assert result.option is None
        assert "german" in result.reason

    def test_language_satisfied_but_requested_accent_unavailable_degrades_honestly(self):
        result = select_voice(parse_voice_request("an Italian voice with a British accent"), self.OPTIONS)
        assert result.option is not None
        assert result.option.language == "italian" or result.option.language == "it"
        assert result.language_satisfied is True
        assert result.accent_satisfied is False
        assert "accent" in result.reason

    def test_an_empty_request_is_refused_never_picks_arbitrarily(self):
        result = select_voice(parse_voice_request("hello there"), ())
        assert result.option is None
        assert not result.language_satisfied and not result.accent_satisfied

    def test_no_available_options_at_all_is_refused_honestly(self):
        result = select_voice(parse_voice_request("a Spanish voice"), ())
        assert result.option is None
        assert "no voice options" in result.reason

    def test_accent_only_request_matches_on_accent_without_assuming_a_language(self):
        result = select_voice(parse_voice_request("give me a voice with a Mexican accent"), self.OPTIONS)
        assert result.option == self.SPANISH
        assert result.language_satisfied is False
        assert result.accent_satisfied is True

    def test_selection_is_deterministic_across_repeated_calls(self):
        request = parse_voice_request("an Italian voice")
        first = select_voice(request, self.OPTIONS)
        second = select_voice(request, self.OPTIONS)
        assert first.option == second.option
