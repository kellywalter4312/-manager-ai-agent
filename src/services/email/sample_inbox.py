"""A realistic sample inbox for the demo and for tests.

Every address uses the reserved ``.example`` domain and every company is
invented, so nothing here can reach or imitate a real person or organisation.

The mix is deliberate: genuine job emails, a near-miss that mentions hiring
only in passing, and ordinary mail that should not match at all. A sample where
everything matches would prove nothing about the analyzer.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.message import EmailMessage

# A fixed base time keeps the sample deterministic - the demo and its tests
# produce the same ordering on every run.
BASE_TIME = datetime(2026, 8, 15, 9, 0, 0)


def _at(minutes: int) -> datetime:
    return BASE_TIME + timedelta(minutes=minutes)


SAMPLE_MESSAGES: tuple[EmailMessage, ...] = (
    EmailMessage(
        id="msg-001",
        sender="talent@northwind-labs.example",
        subject="Senior Python Engineer at Northwind Labs",
        body=(
            "Hi, we are hiring for a senior position on our platform team.\n"
            "Required skills: Python, APIs, testing\n"
            "Seniority: Senior\n"
            "Experience: 5+ years experience\n"
            "Salary: competitive, includes equity\n"
            "Location: Remote\n"
            "If you are interested, apply by 30 August."
        ),
        received_at=_at(0),
        labels=frozenset({"inbox", "jobs"}),
    ),
    EmailMessage(
        id="msg-002",
        sender="no-reply@grocerybox.example",
        subject="Your weekly delivery is confirmed",
        body="Your order arrives Thursday between 9am and 11am.",
        received_at=_at(30),
        labels=frozenset({"inbox", "receipts"}),
    ),
    EmailMessage(
        id="msg-003",
        sender="recruiting@harborview.example",
        subject="Backend Developer role with Harborview",
        body=(
            "We reviewed your CV and would like to schedule an interview.\n"
            "The opening is for a backend developer working on payments.\n"
            "Required skills: Python, APIs\n"
            "Seniority: Junior\n"
            "Location: Remote\n"
            "Type: Full-time"
        ),
        received_at=_at(60),
        labels=frozenset({"inbox", "jobs"}),
    ),
    EmailMessage(
        id="msg-004",
        sender="digest@devweekly.example",
        subject="This week in software",
        body=(
            "Three articles on database design, plus a note on how one startup "
            "is hiring across Europe."
        ),
        received_at=_at(90),
        labels=frozenset({"inbox", "newsletters"}),
    ),
    EmailMessage(
        id="msg-005",
        sender="sam@friends.example",
        subject="Coffee on Saturday?",
        body="Are you free around 11? There's a new place near the station.",
        received_at=_at(120),
        labels=frozenset({"inbox", "personal"}),
    ),
    EmailMessage(
        id="msg-006",
        sender="careers@brightpath.example",
        subject="Opportunity: Data Analyst at Brightpath",
        body=(
            "A new vacancy has opened for a data analyst.\n"
            "Required skills: SQL, Excel, Power BI\n"
            "Seniority: Mid\n"
            "Compensation: 55-65k depending on experience\n"
            "Location: Hybrid\n"
            "The closing date is 5 September."
        ),
        received_at=_at(150),
        labels=frozenset({"inbox", "jobs"}),
    ),
)


def sample_inbox() -> InMemoryMailbox:
    """A mailbox pre-loaded with the sample messages."""
    return InMemoryMailbox(SAMPLE_MESSAGES)
