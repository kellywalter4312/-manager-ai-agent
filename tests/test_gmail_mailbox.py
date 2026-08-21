"""Tests for GmailMailbox (Milestone 7a).

Every test runs against a fake Gmail service. No credentials, no network, no
Google packages required.
"""

import logging
import socket
from datetime import timezone

import pytest
from gmail_fakes import FakeGmailService, b64, gmail_message

from src.services.email.gmail_mailbox import (
    GmailMailbox,
    decode_body_data,
    extract_body,
    header,
    html_to_text,
    to_datetime,
    to_email_message,
)
from src.services.email.mailbox import Mailbox, MailboxError, Outbox
from src.services.email.message import EmailMessage


def mailbox(**kwargs):
    return GmailMailbox(service=FakeGmailService(**kwargs))


class TestParsingHelpers:
    def test_decodes_base64url_without_padding(self):
        assert decode_body_data(b64("hello world")) == "hello world"

    def test_decodes_non_ascii(self):
        assert decode_body_data(b64("café — 日本語")) == "café — 日本語"

    def test_empty_data_yields_empty_string(self):
        assert decode_body_data("") == ""

    def test_malformed_data_degrades_instead_of_raising(self):
        """A mangled body must not lose the whole message."""
        assert decode_body_data("!!!not base64!!!") == ""

    def test_header_lookup_is_case_insensitive(self):
        payload = {"headers": [{"name": "FROM", "value": "a@example.com"}]}
        assert header(payload, "from") == "a@example.com"

    def test_missing_header_yields_empty_string(self):
        assert header({"headers": []}, "Subject") == ""

    def test_internal_date_becomes_aware_utc(self):
        moment = to_datetime("1755248400000")
        assert moment.tzinfo is not None
        assert moment.astimezone(timezone.utc).year == 2025

    def test_bad_internal_date_does_not_raise(self):
        assert to_datetime("not-a-number").tzinfo is not None


class TestHtmlFlattening:
    def test_strips_tags(self):
        assert html_to_text("<p>Senior <b>Engineer</b></p>") == "Senior Engineer"

    def test_drops_script_and_style_content(self):
        html = "<style>.a{color:red}</style><script>var x=1;</script><p>Real text</p>"
        text = html_to_text(html)
        assert "color:red" not in text and "var x" not in text
        assert "Real text" in text

    def test_unescapes_entities(self):
        assert "R&D" in html_to_text("<p>R&amp;D team</p>")

    def test_breaks_blocks_into_lines(self):
        text = html_to_text("<p>Role: Engineer</p><p>Salary: 60k</p>")
        assert "Role: Engineer" in text and "Salary: 60k" in text
        assert "\n" in text

    def test_handles_empty_html(self):
        assert html_to_text("") == ""


class TestBodyExtraction:
    def test_prefers_plain_text_over_html(self):
        payload = gmail_message(plain="PLAIN VERSION", html="<p>HTML VERSION</p>")["payload"]
        assert extract_body(payload) == "PLAIN VERSION"

    def test_falls_back_to_html_when_no_plain_part(self):
        """Wellfound-style emails are HTML-only; analyzers need readable text."""
        payload = gmail_message(plain=None, html="<p>We are <b>hiring</b></p>")["payload"]
        assert extract_body(payload) == "We are hiring"

    def test_walks_nested_multipart(self):
        payload = {
            "mimeType": "multipart/mixed",
            "headers": [],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [{"mimeType": "text/plain", "body": {"data": b64("nested body")}}],
                }
            ],
        }
        assert extract_body(payload) == "nested body"

    def test_missing_body_yields_empty_string(self):
        assert extract_body({"mimeType": "text/plain", "headers": []}) == ""


class TestMessageConversion:
    def test_produces_an_email_message(self):
        message = to_email_message(gmail_message())
        assert isinstance(message, EmailMessage)
        assert message.id == "m1"
        assert message.sender == "talent@northwind-labs.example"
        assert "Senior Python Engineer" in message.subject

    def test_lowercases_labels_via_the_existing_contract(self):
        message = to_email_message(gmail_message(label_ids=("INBOX", "Label_7")))
        assert message.labels == {"inbox", "label_7"}

    def test_missing_subject_is_allowed(self):
        assert to_email_message(gmail_message(subject="")).subject == ""

    def test_missing_sender_gets_a_placeholder(self):
        """EmailMessage requires a sender; fail readably rather than deep."""
        assert "unknown" in to_email_message(gmail_message(sender="")).sender

    def test_missing_id_is_an_error(self):
        with pytest.raises(MailboxError):
            to_email_message({"payload": {"headers": []}})


class TestFetch:
    def test_is_a_mailbox(self):
        assert isinstance(mailbox(), Mailbox)

    def test_returns_email_messages(self):
        box = mailbox(messages=[gmail_message("m1"), gmail_message("m2")])
        assert all(isinstance(m, EmailMessage) for m in box.fetch())

    def test_returns_newest_first(self):
        older = gmail_message("old", internal_date=1_000_000_000_000)
        newer = gmail_message("new", internal_date=2_000_000_000_000)
        assert [m.id for m in mailbox(messages=[older, newer]).fetch()] == ["new", "old"]

    def test_respects_the_limit(self):
        messages = [gmail_message(f"m{i}", internal_date=1_000_000_000_000 + i) for i in range(5)]
        assert len(mailbox(messages=messages).fetch(limit=2)) == 2

    def test_passes_the_limit_to_the_api(self):
        service = FakeGmailService(messages=[gmail_message()])
        GmailMailbox(service=service).fetch(limit=7)
        assert service.calls[0][2] == 7  # maxResults

    def test_rejects_a_non_positive_limit(self):
        with pytest.raises(MailboxError):
            mailbox().fetch(limit=0)

    def test_rejects_a_blank_label(self):
        with pytest.raises(MailboxError):
            mailbox().fetch(label="   ")

    def test_empty_mailbox_returns_empty_list(self):
        assert mailbox().fetch() == []


class TestLabelResolution:
    def test_system_labels_pass_through(self):
        service = FakeGmailService(messages=[gmail_message(label_ids=("INBOX",))])
        GmailMailbox(service=service).fetch(label="inbox")
        assert "labels" not in service.call_names  # no lookup needed

    def test_user_labels_are_resolved_by_name(self):
        service = FakeGmailService(
            messages=[gmail_message("m1", label_ids=("Label_7",))],
            labels=[{"id": "Label_7", "name": "Jobs"}],
        )
        assert [m.id for m in GmailMailbox(service=service).fetch(label="Jobs")] == ["m1"]

    def test_label_lookup_is_case_insensitive(self):
        service = FakeGmailService(
            messages=[gmail_message("m1", label_ids=("Label_7",))],
            labels=[{"id": "Label_7", "name": "Jobs"}],
        )
        assert GmailMailbox(service=service).fetch(label="jObS")

    def test_unknown_label_is_an_error(self):
        with pytest.raises(MailboxError):
            mailbox().fetch(label="NoSuchLabel")

    def test_label_ids_are_cached(self):
        service = FakeGmailService(
            messages=[gmail_message("m1", label_ids=("Label_7",))],
            labels=[{"id": "Label_7", "name": "Jobs"}],
        )
        box = GmailMailbox(service=service)
        box.fetch(label="Jobs")
        box.fetch(label="Jobs")
        assert service.call_names.count("labels") == 1


class TestErrorMapping:
    def test_list_failure_becomes_mailbox_error(self):
        with pytest.raises(MailboxError) as exc:
            mailbox(list_error=RuntimeError("HttpError 500")).fetch()
        assert "Gmail request failed" in str(exc.value)

    def test_get_failure_becomes_mailbox_error(self):
        box = mailbox(messages=[gmail_message()], get_error=RuntimeError("boom"))
        with pytest.raises(MailboxError):
            box.fetch()

    def test_label_failure_becomes_mailbox_error(self):
        box = mailbox(messages=[gmail_message()], labels_error=RuntimeError("nope"))
        with pytest.raises(MailboxError):
            box.fetch(label="Jobs")

    def test_no_vendor_exception_escapes(self):
        """Callers depend on the Mailbox contract, not on googleapiclient."""
        with pytest.raises(MailboxError):
            mailbox(list_error=KeyError("vendor detail")).fetch()


class TestReadOnlyGuarantees:
    def test_exposes_no_write_methods(self):
        box = mailbox()
        for forbidden in ("send", "delete", "modify", "trash", "archive", "reply"):
            assert not hasattr(box, forbidden), forbidden

    def test_is_not_an_outbox(self):
        assert not isinstance(mailbox(), Outbox)

    def test_only_read_operations_reach_the_api(self):
        service = FakeGmailService(messages=[gmail_message()])
        GmailMailbox(service=service).fetch()
        assert set(service.call_names) <= {"list", "get", "labels"}

    def test_requests_only_the_readonly_scope(self):
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES

        assert SCOPES == (GMAIL_READONLY_SCOPE,)
        assert GMAIL_READONLY_SCOPE.endswith("gmail.readonly")

    def test_no_network_with_an_injected_service(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert mailbox(messages=[gmail_message()]).fetch()


class TestConstruction:
    def test_construction_requires_no_credentials(self):
        """Building a mailbox must not demand a token - only using one does."""
        assert GmailMailbox() is not None

    def test_repr_reports_connection_state_without_secrets(self):
        assert "not connected" in repr(GmailMailbox())
        assert "connected" in repr(mailbox())

    def test_logs_never_contain_message_bodies(self, caplog):
        box = mailbox(messages=[gmail_message(plain="SENSITIVE BODY TEXT")])
        with caplog.at_level(logging.DEBUG, logger="src"):
            box.fetch()
        assert "SENSITIVE BODY TEXT" not in caplog.text
