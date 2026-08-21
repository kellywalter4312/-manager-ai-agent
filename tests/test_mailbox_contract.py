"""One contract suite, run against every Mailbox implementation.

If InMemoryMailbox and GmailMailbox ever disagree about what the interface
means, these tests fail - which is the point. An abstraction is only real if
its implementations are actually interchangeable, and asserting that in one
parametrized place is stronger than asserting it twice in two files.

It also protects the EmailAgent: the agent is written against this contract,
so anything guaranteed here is something the agent can rely on.
"""

from datetime import datetime, timedelta, timezone

import pytest
from gmail_fakes import FakeGmailService, gmail_message

from src.services.email.gmail_mailbox import GmailMailbox
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.mailbox import Mailbox, MailboxError, Outbox
from src.services.email.message import EmailMessage

BASE = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
BASE_MS = int(BASE.timestamp() * 1000)


def _in_memory(count, label=None):
    messages = [
        EmailMessage(
            id=f"m{i}",
            sender=f"sender{i}@example.com",
            subject=f"Subject {i}",
            body=f"Body {i}",
            received_at=BASE + timedelta(minutes=i),
            labels=frozenset({label} if label else set()),
        )
        for i in range(count)
    ]
    return InMemoryMailbox(messages)


def _gmail(count, label=None):
    messages = [
        gmail_message(
            id_=f"m{i}",
            sender=f"sender{i}@example.com",
            subject=f"Subject {i}",
            plain=f"Body {i}",
            internal_date=BASE_MS + i * 60_000,
            label_ids=(label,) if label else (),
        )
        for i in range(count)
    ]
    return GmailMailbox(
        service=FakeGmailService(
            messages=messages,
            labels=[{"id": "Label_7", "name": "Jobs"}],
        )
    )


# Each factory takes (count, label) and returns a populated Mailbox.
BUILDERS = {"in_memory": _in_memory, "gmail": _gmail}


@pytest.fixture(params=sorted(BUILDERS), ids=sorted(BUILDERS))
def build(request):
    return BUILDERS[request.param]


class TestEveryMailboxHonoursTheContract:
    def test_is_a_mailbox(self, build):
        assert isinstance(build(0), Mailbox)

    def test_returns_email_messages(self, build):
        assert all(isinstance(m, EmailMessage) for m in build(3).fetch())

    def test_empty_source_returns_an_empty_list(self, build):
        assert build(0).fetch() == []

    def test_returns_newest_first(self, build):
        received = [m.received_at for m in build(4).fetch()]
        assert received == sorted(received, reverse=True)

    def test_honours_the_limit(self, build):
        assert len(build(5).fetch(limit=2)) == 2

    def test_limit_larger_than_available_is_fine(self, build):
        assert len(build(2).fetch(limit=50)) == 2

    def test_default_fetch_returns_everything_available(self, build):
        assert len(build(3).fetch()) == 3

    def test_rejects_a_non_positive_limit(self, build):
        with pytest.raises(MailboxError):
            build(1).fetch(limit=0)

    def test_rejects_a_negative_limit(self, build):
        with pytest.raises(MailboxError):
            build(1).fetch(limit=-5)

    def test_rejects_a_blank_label(self, build):
        with pytest.raises(MailboxError):
            build(1).fetch(label="   ")

    def test_filters_by_label(self, build):
        # "Label_7" is the Gmail id whose display name is "Jobs"; the in-memory
        # box stores labels verbatim. Both must filter on their own vocabulary.
        box = build(3, label="Label_7")
        assert len(box.fetch(label="Label_7")) == 3

    def test_results_are_repeatable(self, build):
        box = build(4)
        assert [m.id for m in box.fetch()] == [m.id for m in box.fetch()]

    def test_no_mailbox_can_send(self, build):
        box = build(1)
        assert not isinstance(box, Outbox)
        assert not hasattr(box, "send")
