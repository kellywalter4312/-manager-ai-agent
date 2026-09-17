"""Whole-ecosystem repair campaign (2026-09-17) - every defect found at runtime
and every capability built, pinned through the real served system
(``scripts.serve_ui.build_live_system`` - the object the page talks to) over
isolated temp directories. Half Brain throughout unless a test binds a scripted
Brain the way this project's own tests always have; never a vendor call.

    Control Center   a backend switched is live in the running session; a
                     governed capability value is read every turn; a new
                     workspace type is defined with no code; the turn's Brain
                     mode is the one its own reasoning just proved
    Memory           abbreviations and decimals survive; a named wrong value
                     is swapped, not the whole fact; history is kept; "remember
                     that" notes are recalled and forgotten; goals can be added
    Agents           "Arthur, ..." reaches Arthur; a specialist answers from
                     the one canonical view of the person
    Documents        "what does this PDF say" / "summarise it" / "translate
                     this document" are answered in Half Brain; an edited copy
                     becomes "this document"
    Media            spoken time ranges clip; the local assistant never echoes
    Organisation     "I finished renewing ..." completes "renew ..."; "yoga
                     every Wednesday at 7am" is a weekly calendar item
    Coach            weekly non-negotiables, logged progress, a graded review
                     naming the two lowest areas, a recurring review reminder,
                     continuity across restart
    Guidance         situation kept and recalled, week planned across sources,
                     first priority, goals linked, a safety reply, and the
                     Brain given every source in Full Brain
    Channel          a reply the console cannot encode never fails the turn
"""

from __future__ import annotations

import base64
import io
import json
import sys

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.services.memory.personal_context import all_current_personal_context, personal_context_history


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, leaf in (("DEFAULT_ACCOUNTS_PATH", "accounts.json"), ("DEFAULT_CONFIG_PATH", "config.json"),
                       ("DEFAULT_LEDGER_PATH", "state.json"), ("DEFAULT_STAGING_DIR", "staging"),
                       ("DEFAULT_BACKUPS_DIR", "backups")):
        monkeypatch.setattr(cc, name, tmp_path / leaf)
    return tmp_path


def _system(tmp_path, **overrides):
    base = dict(
        env={}, memory="persistent", memory_dir=str(tmp_path / "memory"), profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    )
    base.update(overrides)
    return su.build_live_system(Settings.from_env(**base))


def _say(system, text, to=None):
    return exchange(system.channel, system.conversation, text, addressed=to)


def _command(system, text):
    status, body, ctype = su.route_command(text, None, system)
    if ctype.startswith("application/json"):
        data = json.loads(body)
        if data.get("requires_confirmation"):
            status, body, ctype = su.route_command(text, data["confirm_token"], system)
            return json.loads(body)["result"]
        return data.get("result", data.get("error"))
    return body.decode("utf-8")


def _upload(system, name, data, mime=""):
    status, body, _ = su.route_upload(
        system.manager.own_skills.name, name, mime, base64.b64encode(data).decode(), "conversation", system,
    )
    assert status == 200, body
    return json.loads(body)


class _Scripted(LLMProvider):
    def __init__(self, answer="SCRIPTED"):
        self.prompts = []
        self._answer = answer

    @property
    def name(self):
        return "scripted"

    def complete(self, prompt, *, system=None):
        self.prompts.append(prompt)
        return Completion(text=self._answer, provider="scripted")


def _with_brain(system, provider):
    system.manager.brain = LLMManagerBrain(provider, personality=system.runtime.identity.personality)
    system.manager.own_skills._converse = system.manager.brain.converse


# ---------------------------------------------------------------------------
# Control Center changes reach the running system
# ---------------------------------------------------------------------------


class TestControlCenterChangesAreLive:
    def test_a_backend_switched_in_the_control_center_is_used_by_the_very_next_turn(self, isolated):
        system = _system(isolated)
        before = _say(system, "Translate 'good night' into Spanish.")
        assert "not enabled" in before.outcome
        result = _command(system, "provider translation_provider use argos")
        assert "Applied live" in result, result
        after = _say(system, "Translate 'good night' into Spanish.")
        if not system.runtime.translation.translates:
            pytest.skip("argos language packs are not installed on this machine")
        assert "buenas noches" in after.outcome.lower()
        assert system.runtime.settings.translation == "argos"

    def test_a_failed_backend_switch_changes_nothing_and_says_so(self, isolated, monkeypatch):
        system = _system(isolated)
        import src.composition as composition

        def refuse(*_a, **_k):
            raise RuntimeError("construction refused")

        monkeypatch.setattr(composition, "build_speech", refuse)
        before = system.runtime
        result = _command(system, "provider translation_provider use argos")
        assert "not applied to this running session" in result
        assert system.runtime is before

    def test_a_governed_capability_value_is_live_without_a_restart(self, isolated):
        system = _system(isolated)
        proposal = _command(system, "ecosystem capability.general_conversation propose 2 low shorter|lighter").split("'")[1]
        applied = _command(system, f"ecosystem capability.general_conversation run {proposal} yes")
        assert "Live now" in applied
        for line in ("one", "two", "three", "four"):
            _say(system, f"Thanks {line}.")
        assert len(system.manager.own_skills._history()) == 2

    def test_a_newly_registered_provider_is_chosen_and_answers_live_in_the_same_conversation(self, isolated, monkeypatch):
        import src.providers.factory as factory

        system = _system(isolated)
        _say(system, "My sister's name is Amaka.")
        conversation = system.conversation
        monkeypatch.setitem(factory._BUILDERS, "_campaign_vendor", lambda settings, env, account=None: _Scripted("Because of Rayleigh scattering."))
        with pytest.raises(ValueError):
            factory.register_provider("null", lambda settings, env, account=None: _Scripted())
        assert "Refused" not in _command(system, "provider llm_provider use _campaign_vendor")
        proposal = _command(system, "ecosystem brain.manager propose llm low owner|reasoning").split("'")[1]
        assert "Applied live" in _command(system, f"ecosystem brain.manager run {proposal} yes")
        answered = _say(system, "Why is the sky blue?")
        assert answered.outcome == "Because of Rayleigh scattering."
        assert "Amaka" in _say(system, "What is my sister's name?").outcome
        assert system.conversation is conversation
        _command(system, f"ecosystem brain.manager rollback {proposal} yes")
        assert "no reasoning capability" in _say(system, "Why is the sky blue?").outcome

    def test_an_installed_translation_pack_names_its_own_languages(self, tmp_path):
        from src.providers.translation.offline import OfflineTranslator
        from src.services.translation.languages import all_languages, language_named

        pack = tmp_path / "en_xq"
        pack.mkdir()
        (pack / "metadata.json").write_text(json.dumps({"from_code": "en", "to_code": "xq", "to_name": "Campaignish"}))
        (pack / "sentencepiece.model").write_bytes(b"")
        translator = OfflineTranslator([pack])
        assert ("en", "xq") in translator.pairs()
        assert language_named("campaignish").code == "xq"
        assert any(language.code == "xq" for language in all_languages())

    def test_an_owner_defined_workspace_type_needs_no_code(self, isolated):
        system = _system(isolated)
        result = _command(system, "workspace type research_lab Research lab|tasks,artifacts:document")
        assert result.startswith("Defined the workspace type 'research_lab'")
        snapshot = json.loads(su.route("/snapshot.json", system)[1])
        alfred = next(agent for agent in snapshot["agents"] if agent["name"] == "Alfred")
        assert "research_lab" in json.dumps(alfred["workspace_types"])
        assert _command(system, "workspace type broken Broken|nonsense").startswith("FAILED")
        assert "Removed" in _command(system, "workspace type research_lab remove")

    def test_the_turn_reports_the_brain_its_own_reasoning_just_proved(self, isolated):
        system = _system(isolated)
        from src.manager.cognition import CognitiveMode

        class Verified(_Scripted):
            pass

        provider = Verified("Rayleigh scattering.")
        _with_brain(system, provider)
        turn = _say(system, "In one sentence, why is the sky blue?")
        from src.manager.cognition import assess_manager

        assert turn.brain_mode == assess_manager(system.manager).mode.label


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class TestMemoryRepairs:
    def test_an_abbreviation_is_not_the_end_of_the_fact(self, isolated):
        system = _system(isolated)
        _say(system, "I work as a nurse at St. Mary's hospital.")
        assert "St. Mary's hospital" in _say(system, "Where do I work?").outcome
        _say(system, "My GPA is 3.8.")
        assert "3.8" in _say(system, "What is my GPA?").outcome

    def test_a_named_wrong_value_is_swapped_and_the_rest_kept_with_history(self, isolated):
        system = _system(isolated)
        _say(system, "I'm learning Spanish on Tuesdays.")
        turn = _say(system, "Actually, it's on Thursdays, not Tuesdays.")
        assert "Spanish on Thursdays" in turn.outcome
        current = all_current_personal_context(recall=system.manager.memory.recall)
        spanish = [item for item in current if "spanish" in item.content.lower()]
        assert len(spanish) == 1 and "Thursdays" in spanish[0].content
        history = personal_context_history(spanish[0].topic, recall=system.manager.memory.recall)
        assert any("Tuesdays" in item.content for item in history)

    def test_a_remembered_note_is_recalled_and_forgotten_in_natural_words(self, isolated):
        system = _system(isolated)
        _say(system, "Remember that my passport expires in March 2028.")
        assert "2028" in _say(system, "When does my passport expire?").outcome
        forgotten = _say(system, "Forget my passport expiry.")
        assert forgotten.succeeded and "Forgotten" in forgotten.outcome
        assert "2028" not in _say(system, "When does my passport expire?").outcome

    def test_forget_never_takes_ordinary_speech_as_a_request(self, isolated):
        system = _system(isolated)
        _say(system, "Remember that my passport expires in March 2028.")
        _say(system, "I always forget my keys.")
        assert "2028" in _say(system, "When does my passport expire?").outcome

    def test_a_second_goal_is_added_and_a_restated_one_says_what_it_replaced(self, isolated):
        system = _system(isolated)
        _say(system, "My goal this year is to run a half marathon.")
        _say(system, "My other goal this year is to save 5000 dollars.")
        both = _say(system, "What is my goal this year?").outcome
        assert "marathon" in both and "5000" in both
        replaced = _say(system, "My favourite colour is blue.")
        replaced = _say(system, "My favourite colour is green.")
        assert "replaces what you told me before (blue)" in replaced.outcome

    def test_learning_survives_a_genuine_rebuild(self, isolated):
        _say(_system(isolated), "I work as a nurse at St. Mary's hospital.")
        assert "St. Mary's" in _say(_system(isolated), "Where do I work?").outcome


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class TestAgentsAnswerForThemselves:
    def test_naming_a_specialist_who_is_not_in_the_room_yet_reaches_him(self, isolated):
        system = _system(isolated)
        turn = _say(system, "Arthur, who are you?")
        assert turn.handled_by == "Arthur" and "Arthur" in turn.outcome
        assert system.conversation.has("Arthur")

    def test_a_specialist_answers_about_the_person_from_the_one_canonical_view(self, isolated):
        system = _system(isolated)
        _say(system, "I am a senior Python developer looking for remote work.")
        turn = _say(system, "Arthur, what do you know about me?")
        assert turn.handled_by == "Arthur" and "Python developer" in turn.outcome
        arthur = system.manager.registry.get("Arthur")
        assert not [r for r in arthur.memory.recall(limit=100) if "python" in (r.content or "").lower()]

    def test_the_manager_named_is_still_the_manager(self, isolated):
        system = _system(isolated)
        assert _say(system, "Alfred, who are you?").handled_by == "Alfred"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _pdf(body):
    from src.providers.documents.pdf import SimplePDFRenderer
    from src.services.artifacts.models import DocumentContent, DocumentSection

    return SimplePDFRenderer().render(DocumentContent("Memo", (DocumentSection("Notes", body),)))


MEMO = (
    "The final submission deadline is October 14th. The budget is 4200 dollars. The review panel meets in "
    "room 4. Every submission needs a signed cover sheet. Late submissions are not accepted. The budget covers "
    "travel and printing. Questions go to the programme office."
)


class TestDocumentsInHalfBrain:
    def test_what_a_document_says_is_read_not_refused(self, isolated):
        system = _system(isolated)
        _upload(system, "memo.pdf", _pdf(MEMO), "application/pdf")
        turn = _say(system, "What does this file say?")
        assert turn.succeeded and "October 14th" in turn.outcome and "here are its own words" in turn.outcome

    def test_a_summary_request_gets_an_honestly_labelled_extract(self, isolated):
        system = _system(isolated)
        _upload(system, "memo.pdf", _pdf(MEMO), "application/pdf")
        turn = _say(system, "Summarize this document.")
        assert turn.succeeded and "an extract" in turn.outcome and "not a summary written by a model" in turn.outcome
        assert "budget" in turn.outcome.lower()

    def test_translate_this_document_reaches_translation(self, isolated):
        system = _system(isolated)
        _upload(system, "memo.pdf", _pdf(MEMO), "application/pdf")
        turn = _say(system, "Translate this document into French.")
        assert turn.capability == "translate", turn.capability

    def test_an_edited_copy_becomes_this_document(self, isolated):
        system = _system(isolated)
        _upload(system, "memo.pdf", _pdf(MEMO), "application/pdf")
        assert _say(system, "In this document, replace October 14th with November 2nd.").succeeded
        assert "November 2nd" in _say(system, "What does this file say?").outcome


# ---------------------------------------------------------------------------
# Media, organisation, channel
# ---------------------------------------------------------------------------


class TestSmallerRepairs:
    @pytest.mark.parametrize("text, expected", [
        ("From second 1 to second 4, make a clip of this video.", (1.0, 4.0)),
        ("Give me the part of the video between 2 and 6 seconds.", (2.0, 6.0)),
        ("cut 1 minute 20 to 1 minute 45 of the video", (80.0, 105.0)),
    ])
    def test_spoken_time_ranges_become_clips(self, text, expected):
        from src.agents.tools.media_production import parse_clip_specs
        from src.core.contracts import Task
        from src.manager.capabilities import MANAGER_CAPABILITIES, select_capability

        specs = parse_clip_specs(text)
        assert [(s.start_seconds, s.end_seconds) for s in specs] == [expected]
        assert select_capability(Task(description=text), MANAGER_CAPABILITIES).name == "clip_video"

    def test_the_local_assistant_never_presents_an_echo_as_an_answer(self):
        from src.providers.local_assistant import LocalAssistant

        class Echo:
            def complete(self, prompt, *, system=None):
                return Completion(text="From second 1 to second 4, make a clip of this video.", provider="local")

        assert LocalAssistant(Echo()).answer("From second 1 to second 4, make a clip of this video.") is None

    def test_finishing_a_task_in_another_form_of_its_words_completes_it(self, isolated):
        system = _system(isolated)
        _say(system, "Add a task to renew my car insurance.")
        assert _say(system, "I finished renewing my car insurance.").succeeded
        assert "insurance" not in _say(system, "What are my tasks?").outcome

    def test_a_weekly_item_named_first_is_a_recurring_calendar_item(self, isolated):
        system = _system(isolated)
        _say(system, "I have yoga every Wednesday at 7am.")
        listed = _say(system, "What's on my calendar?").outcome
        assert "yoga" in listed.lower() and "wednesday" in listed.lower()

    def test_a_reply_the_console_cannot_encode_never_fails_the_turn(self, isolated, monkeypatch):
        system = _system(isolated)
        narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        monkeypatch.setattr(sys, "stdout", narrow)
        from src.channels.channel import _show

        _show("我喜欢学习语言")


# ---------------------------------------------------------------------------
# Coach
# ---------------------------------------------------------------------------


class TestTheWeeklyCoach:
    def test_the_whole_weekly_loop(self, isolated):
        system = _system(isolated)
        set_ = _say(system, "My weekly non-negotiables are: exercise 4 times, study Spanish 3 times, and no takeaway food.")
        assert set_.succeeded and "Sunday at 18:00" in set_.outcome
        reminders = _say(system, "List my reminders.").outcome
        assert "weekly review with your coach" in reminders
        logged = _say(system, "This week I exercised 2 times, studied Spanish 3 times, and had takeaway twice.")
        assert logged.succeeded and "2 still to do" in logged.outcome and "over your limit" in logged.outcome
        _say(system, "Be ruthless with me.")
        review = _say(system, "Do my weekly review.")
        assert review.succeeded
        assert "Your two lowest areas: takeaway food (3.0) and exercise (5.0)." in review.outcome
        assert "study Spanish: 10.0/10 (A)" in review.outcome
        assert "chose to skip" in review.outcome
        restarted = _system(isolated)
        again = _say(restarted, "Do my weekly review.")
        assert "takeaway food (3.0)" in again.outcome

    def test_silence_scores_nothing_and_a_statement_without_commitments_is_ordinary_learning(self, isolated):
        system = _system(isolated)
        learned = _say(system, "I went running 3 times this week.")
        assert learned.capability != "coach" or "Logged" not in learned.outcome
        _say(system, "My weekly non-negotiables are: read 3 times.")
        review = _say(system, "How did I do this week?")
        assert "0.0/10" in review.outcome and "nothing reported" in review.outcome


# ---------------------------------------------------------------------------
# Guidance
# ---------------------------------------------------------------------------


class TestPersonalGuidance:
    def test_a_situation_is_kept_and_recalled(self, isolated):
        system = _system(isolated)
        kept = _say(system, "Remember what I'm dealing with right now: my landlord is selling the flat and I must move by November.")
        assert kept.succeeded and kept.capability == "personal_guidance"
        recalled = _say(system, "What am I dealing with right now?")
        assert "you must move by November" in recalled.outcome

    def test_life_direction_and_talking_are_guided_in_half_brain(self, isolated):
        system = _system(isolated)
        _say(system, "My goal this year is to change careers.")
        life = _say(system, "I need help figuring out what I'm doing with my life.")
        assert life.succeeded and "change careers" in life.outcome and "?" in life.outcome
        talk = _say(system, "I need to talk about something bothering me.")
        assert talk.succeeded and "listening" in talk.outcome

    def test_the_week_is_planned_across_calendar_tasks_reminders_and_commitments(self, isolated):
        system = _system(isolated)
        _say(system, "I have yoga every Wednesday at 7am.")
        _say(system, "Add a task to finish the quarterly report.")
        _say(system, "My weekly non-negotiables are: exercise 2 times, no takeaway food.")
        plan = _say(system, "Help me organize my week.")
        assert plan.succeeded
        assert "yoga" in plan.outcome and "finish the quarterly report" in plan.outcome
        assert plan.outcome.count("exercise (non-negotiable)") == 2 and "no takeaway food" in plan.outcome
        first = _say(system, "Given my tasks and my calendar, what should I do first this week?")
        assert "Do first: finish the quarterly report" in first.outcome and "yoga" in first.outcome

    def test_a_safety_reply_comes_before_anything_else(self, isolated):
        system = _system(isolated)
        turn = _say(system, "Honestly I don't want to live anymore.")
        assert turn.capability == "personal_guidance" and "crisis line" in turn.outcome

    def test_full_brain_is_given_every_source(self, isolated):
        system = _system(isolated)
        _say(system, "Remember what I'm dealing with right now: I have to choose between two job offers.")
        _say(system, "My goal this year is to buy a house.")
        provider = _Scripted("Let's weigh the offers against the house goal.")
        _with_brain(system, provider)
        turn = _say(system, "Guide me through this.")
        assert turn.outcome == "Let's weigh the offers against the house goal."
        sent = "\n".join(provider.prompts)
        assert "two job offers" in sent and "buy a house" in sent
