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

    def test_gender_is_recognised_independently_of_language_and_accent(self):
        r = parse_voice_request("find a female voice that sounds appropriate for this")
        assert r.gender == "female"
        assert r.language is None
        assert r.accent is None

    def test_gender_combines_with_language(self):
        r = parse_voice_request("find me a female Italian voice")
        assert r.gender == "female"
        assert r.language == "italian"
        assert r.accent is None

    def test_gender_combines_with_language_and_accent(self):
        r = parse_voice_request("find me a male English voice with a British accent")
        assert r.gender == "male"
        assert r.language == "english"
        assert r.accent == "british"

    def test_articles_before_a_language_name_are_never_mistaken_for_an_accent(self):
        """Real, observed false positive, fixed and pinned: 'Use a
        Spanish voice' was parsing accent='a'."""
        for text in ("Use a Spanish voice", "Give me an English voice", "the French option"):
            r = parse_voice_request(text)
            assert r.accent is None, (text, r.accent)

    def test_the_reverse_accent_predicate_shape_is_recognised(self):
        """Real, observed false positive, fixed and pinned: 'your accent
        should be Australian' was parsing accent='your' (the
        adjective-before-'accent' pattern naively captured the
        possessive). Fixed by rejecting a stopword capture and falling
        back to the 'accent (is/should be/to be/:) X' shape."""
        for text in (
            "your accent should be Australian", "make the accent French",
            "the accent is British", "accent: Scottish",
        ):
            r = parse_voice_request(text)
            assert r.accent is not None and r.accent != "your", (text, r.accent)

    def test_negated_language_is_never_extracted_as_a_positive_request(self):
        """Real, observed false positive, fixed and pinned: 'choose a
        voice, not Italian' was extracting language='italian' - the
        opposite of what was asked. True negative-preference semantics
        stays out of scope; the fix is an honest 'nothing usable was
        stated' rather than the inverted request."""
        for text in ("choose a voice, not Italian", "never Spanish", "no French accent"):
            r = parse_voice_request(text)
            assert r.is_empty(), (text, r)

    def test_implicit_regional_adjective_before_a_language_name_is_read_as_accent(self):
        """'Nigerian English voice' names no word 'accent' at all, but
        still clearly asks for a regional variant - requirement 3's own
        example ('Use a Nigerian English voice if one is available')."""
        r = parse_voice_request("Use a Nigerian English voice if one is available")
        assert r.language == "english"
        assert r.accent == "nigerian"

    def test_a_second_distinct_language_word_is_not_swept_into_the_implicit_accent_reading(self):
        """Two distinct, unmarked language words with no word 'accent' is
        genuinely ambiguous - left alone rather than guessed at."""
        r = parse_voice_request("Spanish or English, whichever voice you have")
        # Whichever is picked (or neither), it must never silently
        # invent an accent from the other language word.
        assert r.accent is None

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


class TestSelectVoiceWithGender:
    FEMALE_ITALIAN = VoiceOption("g1", "Female Italian", "elevenlabs", language="italian", gender="female")
    MALE_ITALIAN = VoiceOption("g2", "Male Italian", "elevenlabs", language="italian", gender="male")
    FEMALE_ENGLISH = VoiceOption("g3", "Female English", "windows", language="english", gender="female")
    NO_GENDER_ENGLISH = VoiceOption("g4", "Plain English", "windows", language="english")
    OPTIONS = (FEMALE_ITALIAN, MALE_ITALIAN, FEMALE_ENGLISH, NO_GENDER_ENGLISH)

    def test_gender_alone_is_matched_honestly(self):
        result = select_voice(parse_voice_request("find a female voice"), self.OPTIONS)
        assert result.option is not None
        assert result.option.gender == "female"
        assert result.gender_satisfied is True
        assert result.language_satisfied is False

    def test_language_and_gender_together_are_both_satisfied_when_possible(self):
        result = select_voice(parse_voice_request("find me a female Italian voice"), self.OPTIONS)
        assert result.option == self.FEMALE_ITALIAN
        assert result.language_satisfied and result.gender_satisfied

    def test_language_satisfied_but_gender_unavailable_degrades_honestly(self):
        options = (VoiceOption("x1", "Some Italian", "elevenlabs", language="italian"),)
        result = select_voice(parse_voice_request("find me a female Italian voice"), options)
        assert result.option is not None
        assert result.language_satisfied is True
        assert result.gender_satisfied is False
        assert "gender" in result.reason

    def test_a_gender_no_option_reports_is_refused_never_substituted(self):
        options = (self.MALE_ITALIAN,)
        result = select_voice(parse_voice_request("find a female voice"), options)
        assert result.option is None
        assert "female" in result.reason

    def test_options_with_no_reported_gender_never_satisfy_a_gender_request(self):
        result = select_voice(parse_voice_request("find a female voice"), (self.NO_GENDER_ENGLISH,))
        assert result.option is None
