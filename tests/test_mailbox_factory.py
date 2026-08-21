"""Tests for config-driven mailbox construction, and the EmailAgent working
through the Gmail-backed mailbox via the shared interface.
"""

import sys

import pytest
from gmail_fakes import FakeGmailService, gmail_message

from src.agents.email_agent import EmailAgent
from src.config.settings import Settings
from src.core.contracts import Task, TaskStatus
from src.manager.manager import ManagerAgent
from src.services.email.gmail_mailbox import GmailMailbox
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.mailbox import Mailbox
from src.services.email.mailbox_factory import available_mailboxes, build_mailbox


class TestFactory:
    def test_memory_is_the_default(self):
        assert isinstance(build_mailbox(Settings()), InMemoryMailbox)

    def test_builds_a_gmail_mailbox_when_selected(self):
        assert isinstance(build_mailbox(Settings(mailbox="gmail")), GmailMailbox)

    def test_selecting_gmail_needs_no_credentials(self):
        """Construction must not read a token - only fetching does."""
        assert build_mailbox(Settings(mailbox="gmail")) is not None

    def test_every_built_mailbox_satisfies_the_interface(self):
        for name in available_mailboxes():
            assert isinstance(build_mailbox(Settings(mailbox=name)), Mailbox)

    def test_lists_available_mailboxes(self):
        assert set(available_mailboxes()) == {"memory", "gmail"}

    def test_rejects_an_unknown_mailbox_with_a_helpful_message(self):
        with pytest.raises(ValueError) as exc:
            build_mailbox(Settings(mailbox="carrier_pigeon"))
        assert "carrier_pigeon" in str(exc.value) and "memory" in str(exc.value)

    def test_passes_credential_paths_through(self):
        settings = Settings(
            mailbox="gmail",
            gmail_credentials_path="data/custom_credentials.json",
            gmail_token_path="data/custom_token.json",
        )
        assert isinstance(build_mailbox(settings), GmailMailbox)

    def test_google_packages_are_not_imported_by_default(self):
        """The default path must not load Gmail code or vendor SDKs."""
        build_mailbox(Settings())
        assert "googleapiclient" not in sys.modules
        assert "google_auth_oauthlib" not in sys.modules


class TestSettings:
    def test_mailbox_defaults_to_memory(self):
        assert Settings.from_env(env={}).mailbox == "memory"

    def test_mailbox_is_read_from_the_environment(self):
        assert Settings.from_env(env={"MANAGER_MAILBOX": "GMAIL"}).mailbox == "gmail"

    def test_credential_paths_default_under_data(self):
        settings = Settings()
        assert settings.gmail_credentials_path == "data/credentials.json"
        assert settings.gmail_token_path == "data/token.json"

    def test_credential_paths_are_configurable(self):
        settings = Settings.from_env(
            env={"MANAGER_GMAIL_TOKEN_PATH": "/secure/token.json"}
        )
        assert settings.gmail_token_path == "/secure/token.json"

    def test_describe_reports_the_mailbox_and_no_secrets(self):
        described = Settings(mailbox="gmail", api_key="sk-ant-SECRET").describe()
        assert "mailbox=gmail" in described and "SECRET" not in described

    def test_settings_store_paths_not_secrets(self):
        """Only file locations live in config; contents never do."""
        settings = Settings(mailbox="gmail")
        assert settings.gmail_token_path.endswith(".json")
        assert settings.api_key is None


class TestEmailAgentThroughGmail:
    """DoD 2: the EmailAgent consumes Gmail-style messages via the interface."""

    @staticmethod
    def _agent():
        service = FakeGmailService(
            messages=[
                gmail_message(
                    "g1",
                    sender="talent@northwind-labs.example",
                    subject="Senior Python Engineer at Northwind Labs",
                    plain="We are hiring. Salary is competitive. Apply by Friday.",
                    internal_date=1_755_248_400_000,
                ),
                gmail_message(
                    "g2",
                    sender="no-reply@grocerybox.example",
                    subject="Your delivery is confirmed",
                    plain="Arriving Thursday between 9 and 11.",
                    internal_date=1_755_248_500_000,
                ),
            ]
        )
        return EmailAgent(GmailMailbox(service=service))

    def test_summarizes_a_gmail_inbox(self):
        result = self._agent().execute(Task(description="read my inbox"))
        assert result.status is TaskStatus.COMPLETED
        assert "message(s) in the inbox" in result.output

    def test_finds_job_opportunities_in_gmail_messages(self):
        result = self._agent().execute(Task(description="find job opportunities"))
        assert result.status is TaskStatus.COMPLETED
        assert "Northwind" in result.output

    def test_ignores_non_job_gmail_messages(self):
        result = self._agent().execute(Task(description="find job opportunities"))
        assert "delivery is confirmed" not in result.output

    def test_still_refuses_to_send_when_backed_by_gmail(self):
        result = self._agent().execute(Task(description="send an email to my team"))
        assert result.status is TaskStatus.FAILED
        assert "not enabled" in result.error

    def test_a_gmail_failure_becomes_a_failed_task_not_a_crash(self):
        agent = EmailAgent(
            GmailMailbox(service=FakeGmailService(list_error=RuntimeError("quota")))
        )
        result = agent.execute(Task(description="read my inbox"))
        assert result.status is TaskStatus.FAILED
        assert "Gmail request failed" in result.error

    def test_the_manager_routes_to_it_unchanged(self):
        manager = ManagerAgent()
        manager.register_agent(self._agent())
        result = manager.handle_task(Task(description="scan my inbox for job opportunities"))
        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "email_agent"
