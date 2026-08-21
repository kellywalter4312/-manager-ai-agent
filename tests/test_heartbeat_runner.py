"""Tests for the autonomous loop itself.

Everything is faked: mailbox, clock, state directory, profile. The real Gmail
boundary (GmailMailbox.fetch) is never crossed here - and several tests assert
that it could not have been.
"""

from datetime import datetime, timedelta, timezone

import pytest
from opportunity_fixtures import ALL_EMAILS, TEST_PROFILE, UNRELATED_EMAILS, email

from src.agents.email.analyzers import JobOpportunityAnalyzer
from src.agents.email_agent import EmailAgent
from src.heartbeat.runner import HeartbeatRunner, TickStatus
from src.heartbeat.state import StateStore
from src.manager.manager import ManagerAgent, MissionOutcome
from src.manager.manager_skills import ManagerSkills
from src.registry.agent_registry import AgentRegistry
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.mailbox import Mailbox, MailboxError
from src.services.identity.identity import AgentIdentity
from src.services.identity.soul import BehaviourPolicy
from src.services.profile.profile import ProfileError

NOW = datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)

IDENTITY = AgentIdentity(
    name="manager",
    description="Coordinates missions.",
    capabilities=frozenset({"capabilities", "status", "roster", "report"}),
)


class CountingMailbox(InMemoryMailbox):
    """An in-memory mailbox that records whether it was read."""

    def __init__(self, messages=()):
        super().__init__(messages)
        self.fetch_calls = 0
        self.last_limit = None

    def fetch(self, limit=20, label=None):
        self.fetch_calls += 1
        self.last_limit = limit
        return super().fetch(limit=limit, label=label)


class BrokenMailbox(Mailbox):
    def __init__(self):
        self.fetch_calls = 0

    def fetch(self, limit=20, label=None):
        self.fetch_calls += 1
        raise MailboxError("gmail unavailable")


def make_runner(tmp_path, mailbox=None, enabled=True, profile_loader=None,
                policy=None, clock=None, written=None):
    mailbox = mailbox if mailbox is not None else CountingMailbox(ALL_EMAILS)

    def build_manager(profile):
        registry = AgentRegistry()
        skills = ManagerSkills(IDENTITY, roster=registry.all_agents)
        manager = ManagerAgent(registry=registry, own_skills=skills)
        manager.register_agent(
            EmailAgent(mailbox, analyzers=[JobOpportunityAnalyzer(profile=profile)])
        )
        return manager

    def writer(text, when, directory):
        path = tmp_path / f"report-{when.strftime('%H%M%S')}.md"
        path.write_text(text, encoding="utf-8")
        if written is not None:
            written.append(text)
        return path

    return HeartbeatRunner(
        build_manager=build_manager,
        profile_loader=profile_loader or (lambda: TEST_PROFILE),
        state_store=StateStore(tmp_path / "state.json"),
        policy=policy or BehaviourPolicy(),
        enabled=enabled,
        fetch_limit=25,
        reports_dir=tmp_path,
        clock=clock or (lambda: NOW),
        report_writer=writer,
    ), mailbox


class TestDisabledHeartbeat:
    def test_disabled_tick_does_nothing(self, tmp_path):
        runner, mailbox = make_runner(tmp_path, enabled=False)
        result = runner.tick()
        assert result.status is TickStatus.DISABLED

    def test_disabled_tick_never_touches_the_mailbox(self, tmp_path):
        """Autonomy off must mean no Gmail access whatsoever."""
        runner, mailbox = make_runner(tmp_path, enabled=False)
        runner.tick()
        assert mailbox.fetch_calls == 0

    def test_disabled_tick_writes_no_state_or_report(self, tmp_path):
        runner, _ = make_runner(tmp_path, enabled=False)
        runner.tick()
        assert not (tmp_path / "state.json").exists()
        assert list(tmp_path.glob("report-*.md")) == []

    def test_enabled_is_reported(self, tmp_path):
        runner, _ = make_runner(tmp_path, enabled=False)
        assert runner.enabled is False


class TestProfileFailureStopsBeforeGmail:
    def test_profile_error_is_reported(self, tmp_path):
        def boom():
            raise ProfileError("USER.md missing")

        runner, _ = make_runner(tmp_path, profile_loader=boom)
        assert runner.tick().status is TickStatus.PROFILE_ERROR

    def test_profile_error_prevents_any_mailbox_access(self, tmp_path):
        """No profile means nothing to match against; do not read the inbox."""
        def boom():
            raise ProfileError("USER.md missing")

        runner, mailbox = make_runner(tmp_path, profile_loader=boom)
        runner.tick()
        assert mailbox.fetch_calls == 0

    def test_profile_error_is_flagged_as_not_touching_gmail(self, tmp_path):
        def boom():
            raise ProfileError("bad")

        runner, _ = make_runner(tmp_path, profile_loader=boom)
        assert runner.tick().touched_gmail is False


class TestSuccessfulRun:
    def test_reports_new_opportunities(self, tmp_path):
        runner, _ = make_runner(tmp_path)
        result = runner.tick()
        assert result.status is TickStatus.REPORTED
        assert result.new_opportunities > 0

    def test_writes_a_report_file(self, tmp_path):
        runner, _ = make_runner(tmp_path)
        result = runner.tick()
        assert result.report_path.exists()

    def test_report_names_real_opportunities(self, tmp_path):
        written = []
        runner, _ = make_runner(tmp_path, written=written)
        runner.tick()
        assert "Aurora" in written[0]

    def test_ignores_unrelated_mail(self, tmp_path):
        runner, _ = make_runner(tmp_path, mailbox=CountingMailbox(UNRELATED_EMAILS))
        assert runner.tick().status is TickStatus.NOTHING_NEW

    def test_passes_the_fetch_limit_through(self, tmp_path):
        runner, mailbox = make_runner(tmp_path)
        runner.tick()
        assert mailbox.last_limit == 25

    def test_records_the_mission_outcome(self, tmp_path):
        runner, _ = make_runner(tmp_path)
        assert runner.tick().outcome is MissionOutcome.ACCEPTED


class TestCrossRunDeduplication:
    def test_second_run_reports_nothing_new(self, tmp_path):
        runner, _ = make_runner(tmp_path)
        first = runner.tick()
        second = runner.tick()
        assert first.status is TickStatus.REPORTED
        assert second.status is TickStatus.NOTHING_NEW

    def test_second_run_writes_no_report(self, tmp_path):
        runner, _ = make_runner(tmp_path)
        runner.tick()
        before = len(list(tmp_path.glob("report-*.md")))
        runner.tick()
        assert len(list(tmp_path.glob("report-*.md"))) == before

    def test_processed_messages_are_excluded_from_re_analysis(self, tmp_path):
        """The cheap level: a known message is filtered out before analysis."""
        runner, mailbox = make_runner(tmp_path)
        runner.tick()
        second = runner.tick()
        assert mailbox.fetch_calls == 2       # it did look again
        assert second.considered == 0          # but analysed nothing

    def test_the_same_role_in_a_NEW_email_is_still_recognised(self, tmp_path):
        """The second level: fingerprints catch a repost the message id cannot.

        A board re-advertising the same job next week sends a different email,
        so message-level exclusion cannot help - only the role/company
        fingerprint prevents reporting it twice.
        """
        posting = (
            "Junior Python Developer at Aurora Systems\n"
            "Skills: Python, APIs, testing\n"
            "Seniority: Junior\n"
            "Salary: 32000\n"
        )
        week_one = email("wk1", "jobs@board.example", "This week's jobs", posting)
        week_two = email("wk2", "jobs@board.example", "This week's jobs", posting, minutes=60)

        first, _ = make_runner(tmp_path, mailbox=CountingMailbox([week_one]))
        assert first.tick().status is TickStatus.REPORTED

        second, _ = make_runner(tmp_path, mailbox=CountingMailbox([week_one, week_two]))
        result = second.tick()
        assert result.status is TickStatus.NOTHING_NEW
        assert result.already_seen > 0        # recognised, not merely skipped

    def test_state_persists_between_runners(self, tmp_path):
        """A fresh process must inherit the previous run's memory."""
        first, _ = make_runner(tmp_path)
        first.tick()
        second, _ = make_runner(tmp_path)
        assert second.tick().status is TickStatus.NOTHING_NEW

    def test_a_new_opportunity_is_still_reported_later(self, tmp_path):
        mailbox = CountingMailbox(UNRELATED_EMAILS)
        runner, _ = make_runner(tmp_path, mailbox=mailbox)
        assert runner.tick().status is TickStatus.NOTHING_NEW

        # New mail arrives before the next wake.
        later, _ = make_runner(tmp_path, mailbox=CountingMailbox(ALL_EMAILS))
        assert later.tick().status is TickStatus.REPORTED


class TestAmbiguousItemsCountAsAlreadySeen:
    """Regression: ambiguous items were deduplicated but never counted.

    Found on a real Gmail run - a cycle that correctly suppressed a repeated
    ambiguous item reported "already seen: 0", which reads as though it had
    found nothing at all.
    """

    # Detected as an opportunity (role + hiring + structure signals) but
    # AMBIGUOUS, because it never lists required skills. Title and company do
    # extract, so its fingerprint is role-based and therefore stable across
    # different message ids - which is what lets the second copy be recognised.
    POSTING = (
        "Junior Developer at Acme Systems\n"
        "We are hiring for this opening.\n"
        "Seniority: Junior\n"
    )

    def _mailboxes(self):
        first = email("amb-1", "jobs@acme.example", "A role for you", self.POSTING)
        second = email("amb-2", "jobs@acme.example", "A role for you", self.POSTING, minutes=60)
        return first, second

    def test_the_item_is_ambiguous_to_begin_with(self, tmp_path):
        first, _ = self._mailboxes()
        runner, _ = make_runner(tmp_path, mailbox=CountingMailbox([first]))
        result = runner.tick()
        assert result.status is TickStatus.REPORTED
        assert result.new_opportunities == 0        # ranked none; ambiguous only

    def test_repeat_in_a_new_message_counts_as_already_seen(self, tmp_path):
        first, second = self._mailboxes()

        seeding, _ = make_runner(tmp_path, mailbox=CountingMailbox([first]))
        seeding.tick()

        # A different message carrying the same posting: message-level dedup
        # cannot catch it, so only the fingerprint can.
        repeat, _ = make_runner(tmp_path, mailbox=CountingMailbox([first, second]))
        result = repeat.tick()

        assert result.already_seen == 1             # the bug reported 0
        assert result.status is TickStatus.NOTHING_NEW

    # A posting that ranks rather than staying ambiguous, because it lists
    # its required skills.
    RANKED_POSTING = (
        "Junior Python Developer at Aurora Systems\n"
        "Skills: Python, APIs, testing\n"
        "Seniority: Junior\n"
        "Salary: 32000\n"
    )

    def test_ranked_and_ambiguous_both_contribute(self, tmp_path):
        """A run seeing a repeat of each kind counts two, not one.

        Both repeats must arrive in NEW messages: message-level dedup filters
        a re-sent id before the fingerprint stage ever sees it.
        """
        ambiguous_first, ambiguous_second = self._mailboxes()
        ranked_first = email("rank-1", "jobs@aurora.example", "Role", self.RANKED_POSTING)
        ranked_second = email(
            "rank-2", "jobs@aurora.example", "Role", self.RANKED_POSTING, minutes=60
        )

        seeding, _ = make_runner(
            tmp_path, mailbox=CountingMailbox([ambiguous_first, ranked_first])
        )
        seeded = seeding.tick()
        assert seeded.new_opportunities > 0          # the ranked one was reported

        repeat, _ = make_runner(
            tmp_path,
            mailbox=CountingMailbox(
                [ambiguous_first, ranked_first, ambiguous_second, ranked_second]
            ),
        )
        result = repeat.tick()
        assert result.considered == 2                # only the two new messages
        assert result.already_seen == 2              # one ranked + one ambiguous

    def test_report_agrees_with_the_tick_count(self, tmp_path):
        """The report's 'already reported previously' is the same number."""
        written = []
        first, second = self._mailboxes()
        other = email("new-1", "careers@zenith.example", "Python Developer at Zenith",
                      "Required skills: python, testing\nSeniority: Junior\n", minutes=90)

        seeding, _ = make_runner(tmp_path, mailbox=CountingMailbox([first]))
        seeding.tick()

        repeat, _ = make_runner(
            tmp_path, mailbox=CountingMailbox([first, second, other]), written=written
        )
        result = repeat.tick()
        assert result.status is TickStatus.REPORTED
        assert f"already reported previously: {result.already_seen}" in written[-1]


class TestQuietRuns:
    def test_nothing_new_writes_no_report_by_default(self, tmp_path):
        runner, _ = make_runner(tmp_path, mailbox=CountingMailbox(UNRELATED_EMAILS))
        assert runner.tick().report_path is None

    def test_policy_can_request_a_report_anyway(self, tmp_path):
        policy = BehaviourPolicy(report_when_nothing_new=True)
        runner, _ = make_runner(
            tmp_path, mailbox=CountingMailbox(UNRELATED_EMAILS), policy=policy
        )
        assert runner.tick().report_path is not None

    def test_policy_caps_how_many_are_shown(self, tmp_path):
        written = []
        policy = BehaviourPolicy(max_opportunities_reported=1)
        runner, _ = make_runner(tmp_path, policy=policy, written=written)
        runner.tick()
        assert "## Top 1" in written[0]


class TestFailureHandling:
    def test_mailbox_failure_becomes_a_failed_tick(self, tmp_path):
        runner, _ = make_runner(tmp_path, mailbox=BrokenMailbox())
        result = runner.tick()
        assert result.status is TickStatus.FAILED

    def test_mailbox_failure_is_retryable(self, tmp_path):
        runner, _ = make_runner(tmp_path, mailbox=BrokenMailbox())
        assert runner.tick().outcome is MissionOutcome.RETRY

    def test_mailbox_failure_does_not_raise(self, tmp_path):
        """A heartbeat that crashes stops beating."""
        runner, _ = make_runner(tmp_path, mailbox=BrokenMailbox())
        assert runner.tick() is not None

    def test_failure_does_not_poison_the_next_run(self, tmp_path):
        broken, _ = make_runner(tmp_path, mailbox=BrokenMailbox())
        broken.tick()
        healthy, _ = make_runner(tmp_path)
        assert healthy.tick().status is TickStatus.REPORTED


class TestSafetyBoundaries:
    def test_the_agent_used_by_the_heartbeat_cannot_send(self, tmp_path):
        runner, mailbox = make_runner(tmp_path)
        manager = runner._build_manager(TEST_PROFILE)
        agent = manager.registry.get("email_agent")
        assert agent.can_send is False
        assert not hasattr(agent, "send")

    def test_tick_result_describes_itself_safely(self, tmp_path):
        runner, _ = make_runner(tmp_path)
        assert "opportunit" in runner.tick().describe()

    def test_clock_is_injected_not_read_from_the_system(self, tmp_path):
        stamps = []
        runner, _ = make_runner(tmp_path, clock=lambda: (stamps.append(1) or NOW))
        runner.tick()
        assert stamps  # the injected clock was used
