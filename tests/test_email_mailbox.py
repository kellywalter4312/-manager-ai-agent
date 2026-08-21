"""Tests for the email service layer (Milestone 6a).

No agent, no Manager, no credentials, no network - this layer is testable
entirely on its own, which is the point of separating it.
"""

from datetime import datetime, timedelta

import pytest

from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.mailbox import Mailbox, MailboxError, Outbox
from src.services.email.message import EmailMessage, sort_newest_first

BASE = datetime(2026, 8, 15, 9, 0, 0)


def msg(id_="m1", sender="recruiter@example.com", subject="Hello", body="Body text",
        minutes=0, labels=()):
    return EmailMessage(
        id=id_,
        sender=sender,
        subject=subject,
        body=body,
        received_at=BASE + timedelta(minutes=minutes),
        labels=frozenset(labels),
    )


class TestEmailMessage:
    def test_carries_the_core_fields(self):
        m = msg(subject="Interview invite", body="Are you available Thursday?")
        assert m.subject == "Interview invite"
        assert m.body == "Are you available Thursday?"

    def test_is_immutable(self):
        with pytest.raises(Exception):
            msg().subject = "changed"

    def test_requires_an_id(self):
        with pytest.raises(ValueError):
            msg(id_="   ")

    def test_requires_a_sender(self):
        with pytest.raises(ValueError):
            msg(sender="  ")

    def test_normalizes_labels(self):
        m = msg(labels=[" INBOX ", "Jobs", ""])
        assert m.labels == {"inbox", "jobs"}

    def test_has_label_is_case_insensitive(self):
        m = msg(labels=["Jobs"])
        assert m.has_label("JOBS") and not m.has_label("spam")

    def test_text_joins_subject_and_body(self):
        m = msg(subject="Role", body="Details")
        assert "Role" in m.text and "Details" in m.text

    def test_preview_excludes_the_body(self):
        """Previews reach logs; message bodies carry personal data."""
        m = msg(body="SENSITIVE PERSONAL CONTENT")
        assert "SENSITIVE" not in m.preview()

    def test_preview_truncates_a_long_subject(self):
        assert len(msg(subject="x" * 200).preview(limit=20)) < 100


class TestSortNewestFirst:
    def test_orders_by_arrival_descending(self):
        older, newer = msg("a", minutes=0), msg("b", minutes=30)
        assert sort_newest_first([older, newer]) == [newer, older]

    def test_is_deterministic_for_identical_timestamps(self):
        first, second = msg("a", minutes=5), msg("b", minutes=5)
        assert sort_newest_first([first, second]) == sort_newest_first([second, first])


class TestInMemoryMailbox:
    def test_is_a_mailbox(self):
        assert isinstance(InMemoryMailbox(), Mailbox)

    def test_empty_by_default(self):
        assert InMemoryMailbox().fetch() == []

    def test_returns_messages_newest_first(self):
        older, newer = msg("a", minutes=0), msg("b", minutes=30)
        assert InMemoryMailbox([older, newer]).fetch() == [newer, older]

    def test_respects_the_limit(self):
        box = InMemoryMailbox([msg(str(i), minutes=i) for i in range(5)])
        assert len(box.fetch(limit=2)) == 2

    def test_filters_by_label(self):
        jobs, other = msg("a", labels=["jobs"]), msg("b", labels=["newsletters"])
        assert InMemoryMailbox([jobs, other]).fetch(label="jobs") == [jobs]

    def test_label_filter_is_case_insensitive(self):
        jobs = msg("a", labels=["Jobs"])
        assert InMemoryMailbox([jobs]).fetch(label="JOBS") == [jobs]

    def test_unmatched_label_returns_empty(self):
        assert InMemoryMailbox([msg(labels=["jobs"])]).fetch(label="spam") == []

    def test_rejects_a_non_positive_limit(self):
        with pytest.raises(MailboxError):
            InMemoryMailbox().fetch(limit=0)

    def test_rejects_a_blank_label(self):
        with pytest.raises(MailboxError):
            InMemoryMailbox().fetch(label="   ")

    def test_rejects_non_message_objects(self):
        with pytest.raises(TypeError):
            InMemoryMailbox(["not a message"])

    def test_reports_its_size(self):
        assert len(InMemoryMailbox([msg("a"), msg("b")])) == 2


class TestOutboxSeam:
    """Sending is declared but unimplemented - verify the seam, not behaviour."""

    def test_no_implementation_ships_in_this_milestone(self):
        with pytest.raises(TypeError):
            Outbox()  # abstract: send() has no implementation anywhere

    def test_a_fake_outbox_can_satisfy_the_interface(self):
        class FakeOutbox(Outbox):
            def __init__(self):
                self.sent = []

            def send(self, to, subject, body):
                self.sent.append((to, subject, body))
                return "sent-1"

        outbox = FakeOutbox()
        assert outbox.send("a@example.com", "Hi", "Body") == "sent-1"
        assert outbox.sent == [("a@example.com", "Hi", "Body")]

    def test_reading_and_sending_are_separate_interfaces(self):
        """A Mailbox cannot send: the capability is absent, not merely unused."""
        assert not hasattr(InMemoryMailbox(), "send")
        assert not issubclass(InMemoryMailbox, Outbox)
