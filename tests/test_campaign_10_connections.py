"""Pre-Stage-9 Campaign 10 - connections and outward-action integrity.

Real isolated systems through the production composition. Nothing is ever
sent: no communication connection is configured, so a confirmed send can only
fail honestly. The Gmail connection is exercised through its real class with an
in-memory mailbox - never the network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import scripts.control_center as cc
from scripts.exchange import exchange
from src.config.settings import Settings
from src.manager.capabilities import _communication_shape


def _build(tmp_path):
    return cc.build(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


class TestWhoAndWhatAMessageIs:
    @pytest.mark.parametrize("text, expected", [
        ("send a message to Mum saying I'll be late", ("send", "Mum", "I'll be late")),
        ("text a note to Sam: running late", ("send", "Sam", "running late")),
        ("message Kai to say hello", ("send", "Kai", "hello")),
        ("send a text to Dr. Okafor saying the results are in", ("send", "Dr. Okafor", "the results are in")),
        ("text Sam that dinner is at 8", ("send", "Sam", "dinner is at 8")),
        ("send a message to Rema", ("send", "Rema", "")),
        ("send the report to Kai", ("send", "Kai", "the report")),
        ("call Kai", ("call", "Kai", "")),
    ])
    def test_the_party_and_the_words_are_what_was_said(self, text, expected):
        assert _communication_shape(text) == expected


class TestConsentIsAskedHonouredAndCredited:
    def test_a_declined_send_says_back_exactly_what_was_not_sent(self, tmp_path):
        _runtime, _manager, channel, conversation = _build(tmp_path)
        asked = exchange(channel, conversation, "send a message to Mum saying I'll be late")
        assert asked.capability == "communication" and asked.outcome == 'Send "I\'ll be late" to Mum? (yes/no)'
        declined = exchange(channel, conversation, "no")
        assert declined.outcome == 'Okay, I won\'t send "I\'ll be late" to Mum.'

    def test_each_answer_is_credited_to_the_capability_that_asked(self, tmp_path):
        _runtime, _manager, channel, conversation = _build(tmp_path)
        for text, answer in (("call Kai", "no"), ("text Sam that dinner is at 8", "nope")):
            exchange(channel, conversation, text)
            turn = exchange(channel, conversation, answer)
            assert turn.capability == "communication", (text, turn.capability)
            assert turn.outcome.startswith("Okay, I won't")

    def test_a_message_with_no_words_asks_what_to_send(self, tmp_path):
        _runtime, _manager, channel, conversation = _build(tmp_path)
        turn = exchange(channel, conversation, "send a message to Rema")
        assert turn.outcome == "What would you like to send to Rema?"

    def test_a_confirmed_send_with_no_connection_fails_honestly_and_sends_nothing(self, tmp_path):
        _runtime, _manager, channel, conversation = _build(tmp_path)
        exchange(channel, conversation, "text Sam that dinner is at 8")
        turn = exchange(channel, conversation, "yes")
        assert not turn.succeeded and "no authorized communication connection" in turn.outcome
        assert turn.capability == "communication"


class TestGmailStaysReadOnly:
    def _connection(self, tmp_path, messages=(), fail=None):
        from src.providers.gmail_connection import GmailConnection
        from src.services.email.mailbox import MailboxError

        class Mailbox:
            def fetch(self, limit=20, label=None):
                if fail:
                    raise MailboxError(fail)
                return list(messages)[:limit]

        token = tmp_path / "token.json"
        token.write_text("{}", encoding="utf-8")
        return GmailConnection(Mailbox(), token_path=token)

    def test_a_read_reports_only_a_count(self, tmp_path):
        connection = self._connection(tmp_path, messages=("m1", "m2"))
        result = connection.execute("read_email", limit=3)
        assert result.status.name == "SUCCEEDED" and result.detail == "2 message(s)"
        assert connection.availability.state.name == "AVAILABLE"

    def test_no_write_operation_exists(self, tmp_path):
        connection = self._connection(tmp_path)
        assert connection.supported_operations == ("read_email",)
        assert connection.execute("send_email").status.name == "UNSUPPORTED"

    def test_a_failed_read_is_honest(self, tmp_path):
        connection = self._connection(tmp_path, fail="token expired")
        result = connection.execute("read_email")
        assert result.status.name == "FAILED" and "token expired" in result.detail
        assert connection.availability.state.name == "UNAVAILABLE"
