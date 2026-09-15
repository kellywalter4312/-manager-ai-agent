"""Local models, several websites, and the attributed local assistant.

No model is loaded and no network or browser is reached here: the local model
is exercised through an injected client, websites through their real
connection class with a scripted browser, and every system is built through
the production composition with isolated stores. The real model and the real
browser are verified at runtime (see ARCHITECTURE.md).
"""

from __future__ import annotations

import json

import pytest

import scripts.control_center as cc
from scripts.exchange import exchange
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.brain import BrainAction, BrainDecision
from src.manager.capabilities import (
    CapabilityRequest,
    named_connection,
    open_conversation,
    readable_connection_output,
)
from src.manager.cognition import CognitiveMode, assess_cognition
from src.providers.factory import available_providers, build_provider
from src.providers.local_assistant import ATTRIBUTION, LocalAssistant, build_local_assistant
from src.providers.local_llm_provider import LocalLLMProvider, LocalModelUnavailable


def _settings(tmp_path, **overrides):
    return Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **overrides,
    )


class FakeLlama:
    def __init__(self, text="A local answer."):
        self.text = text
        self.calls = []

    def create_chat_completion(self, messages, max_tokens, temperature):
        self.calls.append(messages)
        return {"choices": [{"message": {"content": self.text}}], "usage": {"prompt_tokens": 9, "completion_tokens": 3}}


class TestTheLocalModelProvider:
    def test_local_is_a_selectable_provider_that_needs_no_credential(self, tmp_path):
        assert "local" in available_providers()
        provider = build_provider(_settings(tmp_path, provider="local"))
        assert isinstance(provider, LocalLLMProvider) and provider.name == "local"

    def test_a_completion_comes_from_the_local_runtime(self):
        client = FakeLlama("Hello from the laptop.")
        completion = LocalLLMProvider(client=client).complete("Say hello", system="be brief")
        assert completion.text == "Hello from the laptop." and completion.provider == "local"
        assert client.calls[0][0] == {"role": "system", "content": "be brief"}

    def test_a_missing_model_file_is_an_honest_error(self, tmp_path):
        with pytest.raises(LocalModelUnavailable):
            LocalLLMProvider(model_path=str(tmp_path / "absent.gguf")).complete("hi")

    def test_vision_without_its_files_is_an_honest_error(self, tmp_path):
        provider = LocalLLMProvider(vision_model_path=str(tmp_path / "v.gguf"), vision_projector_path=str(tmp_path / "p.gguf"))
        with pytest.raises(LocalModelUnavailable):
            provider.analyze_image(b"png", "what is this?")

    def test_a_verified_local_model_is_labelled_local_never_full_brain(self):
        from src.providers.availability import Availability, AvailabilityState

        class Brain:
            name = "llm"
            provider_name = "local"
            availability = Availability(state=AvailabilityState.AVAILABLE, reason="a call succeeded")

        profile = assess_cognition(Brain())
        assert profile.mode is CognitiveMode.LOCAL_MODEL
        assert not profile.is_full_brain and profile.has_model_reasoning
        assert "not equivalent to a vendor-backed Full Brain" in profile.reason


class TestSeveralWebsitesByName:
    @pytest.mark.parametrize("text, expected", [
        ("read the page on bella salon", "bellasalon"),
        ("what does the BellaSalon site say", "bellasalon"),
        ("check city-library for my loans", "citylibrary"),
        ("tell me a joke", None),
        ("compare bellasalon and citylibrary", None),
    ])
    def test_a_request_names_one_equipped_connection(self, text, expected):
        assert named_connection(text, ("bellasalon", "citylibrary", "null"), "null") == expected

    def test_fields_and_buttons_are_named_by_their_visible_label(self):
        from src.providers.website_connection import WebsiteConnection

        connection = WebsiteConnection.__new__(WebsiteConnection)
        connection._remember_elements(json.dumps({"elements": [
            {"locator": "full_name", "tag": "input", "label": "Full name"},
            {"locator": "button-2", "tag": "button", "type": "submit", "label": "Book appointment"},
        ]}))
        assert connection.values_from_request("click", "click Book appointment on bellasalon") == {"locator": "button-2"}
        assert connection.values_from_request("fill_field", "fill Full name with Kelly Abudu on bellasalon") == {
            "locator": "full_name", "value": "Kelly Abudu",
        }
        assert connection.values_from_request("click", "click something that is not there") == {}

    def test_a_page_reads_back_as_plain_words(self):
        page = json.dumps({"url": "http://site/", "title": "Bella Salon - bookings", "text": "Open slots: Tuesday 10:00",
                           "elements": [{"locator": "full_name", "tag": "input", "label": "Full name"},
                                        {"locator": "b", "tag": "button", "type": "submit", "label": "Book appointment"},
                                        {"locator": "a-0", "tag": "a", "label": "Renew loans"}]})
        text = readable_connection_output("bellasalon", page)
        assert text.startswith("bellasalon: Bella Salon - bookings")
        assert "Fields: Full name" in text and "Buttons: Book appointment" in text and "Links: Renew loans" in text
        assert readable_connection_output("x", "3 message(s)") == "3 message(s)"


class TestTheAttributedLocalAssistant:
    def test_off_by_default_and_absent_without_a_model(self, tmp_path):
        assert build_local_assistant(_settings(tmp_path)) is None
        assert build_local_assistant(_settings(tmp_path, local_assist="local", local_model_path=str(tmp_path / "none.gguf"))) is None

    def test_an_open_question_is_answered_and_attributed_when_no_brain_answers(self, tmp_path):
        runtime, _manager, _channel, _conversation = cc.build(_settings(tmp_path))
        assistant = LocalAssistant(LocalLLMProvider(client=FakeLlama("Chlorophyll fades, so other pigments show.")))
        request = CapabilityRequest(
            task=Task(description="why do leaves change colour?"), identity=runtime.identity,
            roster=lambda: (), own_name="Alfred", own_capabilities=(), assist=assistant,
            converse=lambda task, notes: BrainDecision.no_view(reason="no reasoning capability is configured", source="null"),
        )
        result = open_conversation(request)
        assert result.succeeded and "Chlorophyll fades" in result.output and ATTRIBUTION in result.output
        assert result.payload["brain"] == "local_assist"

    def test_without_an_assistant_half_brain_still_refuses_honestly(self, tmp_path):
        runtime, _manager, channel, conversation = cc.build(_settings(tmp_path))
        turn = exchange(channel, conversation, "why do leaves change colour?")
        assert not turn.succeeded and "rather say so than guess" in turn.outcome

    def test_deterministic_answers_never_go_to_the_assistant(self, tmp_path):
        runtime, manager, channel, conversation = cc.build(_settings(tmp_path))
        manager.own_skills._assist = LocalAssistant(LocalLLMProvider(client=FakeLlama("SHOULD NOT APPEAR")))
        assert "teal" in exchange(channel, conversation, "My favourite colour is teal").outcome
        answer = exchange(channel, conversation, "what's my favourite colour?")
        assert "teal" in answer.outcome and "SHOULD NOT APPEAR" not in answer.outcome

    def test_the_control_center_lists_and_saves_the_choice(self, tmp_path, monkeypatch):
        for name, leaf in (("DEFAULT_ACCOUNTS_PATH", "accounts.json"), ("DEFAULT_CONFIG_PATH", "config.json")):
            monkeypatch.setattr(cc, name, tmp_path / leaf)
        runtime, manager, channel, conversation = cc.build(_settings(tmp_path))
        centre = cc.ControlCenter(runtime, manager)
        listing = cc.run_command(centre, "provider local_assist_provider", 100, channel, conversation)
        assert "local" in listing and "null" in listing
        saved = cc.run_command(centre, "provider local_assist_provider use local", 100, channel, conversation)
        assert saved.startswith("Saved: 'local_assist_provider' now uses 'local'")
        restarted, *_ = cc.build(_settings(tmp_path))
        assert restarted.settings.local_assist == "local"
