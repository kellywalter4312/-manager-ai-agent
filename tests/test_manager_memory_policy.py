"""Tests for the Manager's memory policy.

The policy answers one question - "may this be remembered?" - and the tests
that matter most are the refusals. An allow-list is only worth having if the
things it is meant to exclude are actually excluded, so each excluded category
from the brief gets its own test:

    TestGmailAndHeartbeatStateAreNotMemory
    TestLogsAreNotMemory
    TestModelReasoningIsNotMemory
    TestSecretsAreNeverRemembered

Nothing here stores anything: a policy judges records and returns verdicts.
No memory instance is required to test any rule.
"""

import socket

import pytest

from src.core.contracts import Task, TaskStatus
from src.manager.manager import ManagerAgent
from src.manager.memory_policy import (
    EXCLUDED_KINDS,
    MAX_CONTENT_CHARS,
    SECRET_CONTEXT_KEYS,
    ManagerMemoryPolicy,
    MemoryCategory,
)
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord, MemoryStoreError
from src.services.memory.policy import MemoryPolicy, PolicyVerdict


def code_of(module) -> str:
    """A module's executable lines, with docstrings and comments removed.

    The policy module discusses Gmail, logs and secrets at length while
    explaining why none of them are retained; grepping raw text would flag that
    prose as a violation, which is backwards.
    """
    source = open(module.__file__, encoding="utf-8").read()
    lines, in_docstring = [], False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.count('"""') == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith(("#", '"""')):
            continue
        lines.append(line.split("#")[0])
    return "\n".join(lines).lower()


POLICY = ManagerMemoryPolicy()


def record(content="the user prefers remote work", kind=MemoryCategory.USER_CONTEXT, **fields):
    if isinstance(kind, MemoryCategory):
        kind = kind.value
    return MemoryRecord(content=content, kind=kind, **fields)


def refused(**fields):
    verdict = POLICY.evaluate(record(**fields))
    assert not verdict.allowed, f"expected a refusal, got: {verdict.describe()}"
    return verdict


# ----------------------------------------------------------------------
# 1. The categories are explicit
# ----------------------------------------------------------------------


class TestTheCategoriesAreExplicit:
    def test_there_are_exactly_nine(self):
        """A small vocabulary on purpose. Adding a category is a decision -
        Stage 12 added two: RECORDING (an explicit "record this [for X]"
        capture, distinct from an ordinary durable fact) and MILESTONE (a
        structured, dated personal milestone). Stage 18 added a third,
        LEARNING (what the person is currently studying or reading), which
        passes the same admission test: it plainly changes what the Manager
        should do next time, because an explanation pitched at the work
        somebody is actually doing is a different explanation. Note what it
        is *not*: a place to put the study material itself, which belongs
        in the user's own documents where it can be quoted with
        provenance. Phase 5.1 added a fourth, TASK (an outstanding,
        actionable item with real state) - passes the identical test, and
        is deliberately not USER_CONTEXT: a task is something to do, not a
        stable fact about who the person is. The generalized user
        knowledge/personal-context checkpoint added a fifth,
        PERSONAL_CONTEXT (a self-declared, updatable fact about the person
        - a skill, a schedule, an organisational context) - passes the
        identical test, and is deliberately not LEARNING or USER_CONTEXT:
        its own facts genuinely supersede one another by topic, which
        neither of those two categories is built to represent."""
        assert len(MemoryCategory) == 9

    def test_the_categories_are_the_agreed_ones(self):
        assert POLICY.category_names == (
            "decision",
            "delegation_outcome",
            "learning",
            "lesson",
            "milestone",
            "personal_context",
            "recording",
            "user_context",
            "user_task",
        )

    def test_every_category_explains_what_belongs_in_it(self):
        for category in MemoryCategory:
            assert category.meaning.strip()
            assert category.label.strip()

    def test_the_allow_list_is_readable_as_data(self):
        assert POLICY.categories == frozenset(MemoryCategory)

    def test_the_policy_can_describe_itself(self):
        described = POLICY.describe()
        for category in MemoryCategory:
            assert category.label in described
        assert "refused" in described.lower()

    def test_a_category_is_stored_on_the_record_itself(self):
        """Reusing MemoryRecord.kind - no parallel field, no second contract."""
        assert MemoryCategory.of(record(kind=MemoryCategory.LESSON)) is MemoryCategory.LESSON

    def test_an_unknown_kind_maps_to_no_category(self):
        assert MemoryCategory.of(record(kind="whatever")) is None


# ----------------------------------------------------------------------
# 2. Valid categories are accepted
# ----------------------------------------------------------------------


class TestValidMemoriesAreAccepted:
    @pytest.mark.parametrize("category", list(MemoryCategory))
    def test_every_category_is_accepted(self, category):
        verdict = POLICY.evaluate(record(kind=category))
        assert verdict.allowed
        assert verdict.category == category.value

    def test_user_context_is_accepted(self):
        assert POLICY.permits(
            record("the user is a CS student targeting junior roles",
                   kind=MemoryCategory.USER_CONTEXT)
        )

    def test_a_decision_is_accepted(self):
        assert POLICY.permits(
            record("delegated inbox scans to email_agent because it owns mail work",
                   kind=MemoryCategory.DECISION)
        )

    def test_a_delegation_outcome_is_accepted(self):
        assert POLICY.permits(
            record("the specialist returned three ranked matches and one unclear",
                   kind=MemoryCategory.DELEGATION_OUTCOME)
        )

    def test_a_lesson_is_accepted(self):
        assert POLICY.permits(
            record("digest emails need splitting before extraction, or roles are missed",
                   kind=MemoryCategory.LESSON)
        )

    def test_an_accepted_verdict_still_explains_itself(self):
        assert POLICY.evaluate(record()).reason.strip()

    def test_context_metadata_does_not_block_acceptance(self):
        assert POLICY.permits(record(context={"source": "conversation", "confidence": 0.8}))

    def test_tags_do_not_block_acceptance(self):
        assert POLICY.permits(record(tags=["remote", "preferences"]))

    def test_content_at_the_limit_is_accepted(self):
        assert POLICY.permits(record("x" * MAX_CONTENT_CHARS))


# ----------------------------------------------------------------------
# 3. Disallowed categories are refused
# ----------------------------------------------------------------------


class TestDisallowedCategoriesAreRefused:
    def test_an_unknown_category_is_refused(self):
        verdict = refused(kind="gossip")
        assert "gossip" in verdict.reason

    def test_a_refusal_lists_what_is_allowed(self):
        verdict = refused(kind="gossip")
        for name in POLICY.category_names:
            assert name in verdict.reason

    def test_a_record_with_no_category_is_refused(self):
        """Silence is not a category. Uncategorised knowledge is not knowledge."""
        assert not POLICY.permits(MemoryRecord(content="something"))

    def test_a_non_record_is_refused_rather_than_crashing(self):
        for bad in ("a string", 42, None, {"content": "x"}):
            verdict = POLICY.evaluate(bad)
            assert not verdict.allowed

    def test_evaluating_never_raises(self):
        for kind in ("", "log", "unknown", "decision"):
            assert isinstance(POLICY.evaluate(record(kind=kind)), PolicyVerdict)

    def test_content_beyond_the_limit_is_refused(self):
        """Catches a dump filed under a plausible category."""
        verdict = refused(content="x" * (MAX_CONTENT_CHARS + 1), kind=MemoryCategory.LESSON)
        assert "too long" in verdict.reason

    def test_the_limit_is_configurable_without_changing_the_rules(self):
        strict = ManagerMemoryPolicy(max_content_chars=10)
        assert not strict.permits(record("a" * 50))
        assert strict.permits(record("short"))

    def test_a_non_positive_limit_is_rejected(self):
        for bad in (0, -1):
            with pytest.raises(ValueError):
                ManagerMemoryPolicy(max_content_chars=bad)


# ----------------------------------------------------------------------
# 4. Gmail and heartbeat state are not Manager memory
# ----------------------------------------------------------------------


class TestGmailAndHeartbeatStateAreNotMemory:
    @pytest.mark.parametrize(
        "kind",
        ["raw_message", "message", "processed_state", "heartbeat_state",
         "dedup", "dedupe", "fingerprint"],
    )
    def test_it_is_refused(self, kind):
        assert not POLICY.permits(record(kind=kind))

    def test_a_raw_message_body_is_refused(self):
        verdict = refused(
            content="From: recruiter@example.com\nSubject: We are hiring\n\nHello...",
            kind="raw_message",
        )
        assert "raw source material" in verdict.reason

    def test_deduplication_state_is_refused_with_a_useful_reason(self):
        verdict = refused(content="18f2c3a91b4d0e77", kind="fingerprint")
        assert "belongs to the job that needs it" in verdict.reason

    def test_the_rule_does_not_depend_on_recognising_gmail(self):
        """A message-shaped record under an invented kind is refused anyway.

        The allow-list does the work; the named exclusions only improve the
        wording. That is why this module needs no Gmail knowledge.
        """
        verdict = refused(
            content="From: someone@example.com\nSubject: hello",
            kind="some_kind_nobody_thought_of",
        )
        assert "not something the Manager remembers" in verdict.reason

    def test_a_lesson_about_mail_handling_is_still_allowed(self):
        """The domain is not banned - only raw material and state are."""
        assert POLICY.permits(
            record("digest emails from job boards need splitting into separate roles",
                   kind=MemoryCategory.LESSON)
        )


# ----------------------------------------------------------------------
# 5. Logs are not Manager memory
# ----------------------------------------------------------------------


class TestLogsAreNotMemory:
    @pytest.mark.parametrize("kind", ["log", "debug", "trace", "stacktrace", "scratch"])
    def test_it_is_refused(self, kind):
        assert not POLICY.permits(record(kind=kind))

    def test_a_log_line_is_refused(self):
        verdict = refused(
            content="2026-08-18 15:40:27 INFO src.manager.manager | received: scan inbox",
            kind="log",
        )
        assert "logs record what happened" in verdict.reason

    def test_routine_task_history_is_refused(self):
        for kind in ("task", "task_history", "execution"):
            verdict = refused(content="handled task t-41 successfully", kind=kind)
            assert "history, not knowledge" in verdict.reason

    def test_a_lesson_drawn_from_repeated_failures_is_allowed(self):
        """The conclusion is memory even though the log entries are not."""
        assert POLICY.permits(
            record("the mailbox times out on fetches above 50 messages",
                   kind=MemoryCategory.LESSON)
        )


# ----------------------------------------------------------------------
# 6. Raw model reasoning is not Manager memory
# ----------------------------------------------------------------------


class TestModelReasoningIsNotMemory:
    @pytest.mark.parametrize("kind", ["reasoning", "thinking", "chain_of_thought"])
    def test_it_is_refused(self, kind):
        assert not POLICY.permits(record(kind=kind))

    def test_working_out_is_refused_with_a_reason(self):
        verdict = refused(
            content="Let me think. First I should consider... actually no, wait...",
            kind="chain_of_thought",
        )
        assert "working-out is not a conclusion" in verdict.reason

    def test_a_long_reasoning_dump_under_a_valid_category_is_still_refused(self):
        """The length bound catches what a relabelled category would hide."""
        assert not POLICY.permits(
            record("Let me think step by step. " * 100, kind=MemoryCategory.DECISION)
        )

    def test_a_decision_and_its_reason_are_allowed(self):
        """The conclusion is memory; the working-out that produced it is not."""
        assert POLICY.permits(
            record("chose to escalate rather than delegate: the request was ambiguous",
                   kind=MemoryCategory.DECISION)
        )


# ----------------------------------------------------------------------
# 7. Secrets are never remembered
# ----------------------------------------------------------------------


class TestSecretsAreNeverRemembered:
    @pytest.mark.parametrize(
        "content",
        [
            "sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456",
            "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
            "AIzaSyD-abcdefghijklmnopqrstuvwxyz12345",
            "-----BEGIN RSA PRIVATE KEY-----",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "password: hunter2",
            "api_key=abc123def456",
            "CLIENT_SECRET = s3cr3tv4lu3",
            "refresh_token: 1//0gabcdefghijklmnop",
        ],
    )
    def test_secret_shaped_content_is_refused(self, content):
        assert not POLICY.permits(record(content, kind=MemoryCategory.USER_CONTEXT))

    def test_a_secret_is_refused_under_every_category(self):
        for category in MemoryCategory:
            assert not POLICY.permits(record("password: hunter2", kind=category))

    def test_a_secret_in_context_metadata_is_refused(self):
        for key in ("api_key", "password", "refresh_token", "client_secret"):
            assert not POLICY.permits(record(context={key: "whatever"}))

    def test_every_named_secret_key_is_caught(self):
        for key in SECRET_CONTEXT_KEYS:
            assert not POLICY.permits(record(context={key: "value"})), key

    def test_a_secret_tag_is_refused(self):
        assert not POLICY.permits(record(tags=["api_key"]))

    def test_the_refusal_never_quotes_the_secret(self):
        """A rule that refused a credential must not then log it."""
        verdict = POLICY.evaluate(record("password: hunter2"))
        assert "hunter2" not in verdict.reason
        assert "hunter2" not in verdict.describe()

    def test_the_refusal_says_what_kind_of_secret(self):
        assert "credential" in POLICY.evaluate(record("password: hunter2")).reason

    def test_talking_about_credentials_is_not_a_secret(self):
        """Erring toward refusal must not make ordinary sentences unstorable."""
        assert POLICY.permits(
            record("the user stores credentials in environment variables, never in code",
                   kind=MemoryCategory.USER_CONTEXT)
        )

    def test_an_innocent_context_key_is_not_a_secret(self):
        assert POLICY.permits(record(context={"keywords": "python", "author": "someone"}))

    def test_credential_kinds_are_refused_outright(self):
        for kind in ("credential", "secret"):
            verdict = refused(kind=kind)
            assert "secrets are never remembered" in verdict.reason


# ----------------------------------------------------------------------
# The excluded list is real data, not decoration
# ----------------------------------------------------------------------


class TestTheExclusionsAreExplicit:
    def test_every_exclusion_gives_a_reason(self):
        for kind, reason in EXCLUDED_KINDS.items():
            assert reason.strip(), kind

    def test_no_exclusion_is_also_an_allowed_category(self):
        """A contradiction here would make the policy ambiguous."""
        assert not set(EXCLUDED_KINDS) & set(POLICY.category_names)

    def test_every_exclusion_is_actually_refused(self):
        for kind in EXCLUDED_KINDS:
            assert not POLICY.permits(record(kind=kind)), kind

    def test_the_exclusions_are_advisory_not_load_bearing(self):
        """Removing one would not admit it - the allow-list still refuses."""
        assert not POLICY.permits(record(kind="a_kind_that_is_not_listed_anywhere"))


# ----------------------------------------------------------------------
# The verdict contract
# ----------------------------------------------------------------------


class TestTheVerdict:
    def test_a_verdict_is_frozen(self):
        verdict = POLICY.evaluate(record())
        with pytest.raises(Exception):
            verdict.allowed = False

    def test_a_verdict_must_explain_itself(self):
        with pytest.raises(ValueError):
            PolicyVerdict(allowed=True, reason="   ")

    def test_a_verdict_is_truthy_when_allowed(self):
        assert POLICY.evaluate(record())
        assert not POLICY.evaluate(record(kind="log"))

    def test_describe_is_a_single_safe_line(self):
        described = POLICY.evaluate(record()).describe()
        assert "\n" not in described and "allowed" in described


class TestThePolicyContract:
    def test_it_implements_the_abstraction(self):
        assert isinstance(POLICY, MemoryPolicy)

    def test_the_abstraction_declares_one_method(self):
        assert set(MemoryPolicy.__abstractmethods__) == {"evaluate"}

    def test_the_abstraction_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            MemoryPolicy()

    def test_a_policy_never_stores_anything(self):
        public = {n for n in dir(MemoryPolicy) if not n.startswith("_")}
        for storing in ("remember", "store", "save", "record", "write"):
            assert storing not in public, storing

    def test_admissible_filters_without_storing(self):
        records = [record(), record(kind="log"), record(kind=MemoryCategory.LESSON)]
        kept = POLICY.admissible(records)
        assert len(kept) == 2
        assert all(isinstance(r, MemoryRecord) for r in kept)

    def test_the_policy_is_deterministic(self):
        item = record()
        assert POLICY.evaluate(item).reason == POLICY.evaluate(item).reason

    def test_the_policy_holds_no_state(self):
        policy = ManagerMemoryPolicy()
        before = dict(vars(policy))
        for _ in range(10):
            policy.evaluate(record())
        assert vars(policy) == before

    def test_a_policy_can_be_shared_unlike_a_memory(self):
        """Stateless, so two Managers sharing one policy is safe."""
        assert ManagerMemoryPolicy().evaluate(record()).allowed
        assert POLICY.evaluate(record()).allowed


# ----------------------------------------------------------------------
# 8. No forbidden imports, no I/O
# ----------------------------------------------------------------------


class TestThePolicyIsIndependent:
    def _sources(self):
        import src.manager.memory_policy as manager_policy
        import src.services.memory.policy as contract

        return {"policy.py": code_of(contract), "memory_policy.py": code_of(manager_policy)}

    def test_no_vendor_is_imported_or_named(self):
        for name, code in self._sources().items():
            for word in ("anthropic", "openai", "gmail", "googleapiclient"):
                assert word not in code, f"{word} in {name}"

    def test_no_provider_is_imported(self):
        for name, code in self._sources().items():
            assert "from src.providers" not in code, name

    def test_no_specialist_is_imported(self):
        for name, code in self._sources().items():
            assert "from src.agents" not in code, name
            assert "from src.services.email" not in code, name

    def test_no_heartbeat_state_is_imported(self):
        for name, code in self._sources().items():
            assert "from src.heartbeat" not in code, name

    def test_the_generic_contract_does_not_import_the_manager(self):
        """So a specialist can implement it without dragging in the CEO."""
        import src.services.memory.policy as contract

        assert "from src.manager" not in code_of(contract)

    def test_no_storage_or_database_appears(self):
        for name, code in self._sources().items():
            for word in ("sqlite", "redis", "open(", "json.dump", "path("):
                assert word not in code, f"{word} in {name}"

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert POLICY.permits(record())

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        POLICY.evaluate(record())
        POLICY.evaluate(record(kind="log"))
        assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------
# 9-10. Nothing existing changed
# ----------------------------------------------------------------------


class TestExistingBehaviourIsUnchanged:
    def test_the_memory_contract_is_untouched(self):
        assert set(Memory.__abstractmethods__) == {"remember", "recall", "forget", "clear"}

    def test_memory_still_stores_without_consulting_any_policy(self):
        """Storage and admission stay separate - the store has no opinion."""
        store = EphemeralMemory()
        store.remember(record(kind="log"))   # the policy would refuse this
        assert len(store) == 1

    def test_the_memory_module_does_not_import_the_policy(self):
        import src.services.memory.memory as contract

        assert "policy" not in code_of(contract)

    def test_memory_still_rejects_invalid_records(self):
        with pytest.raises(MemoryStoreError):
            EphemeralMemory().remember("not a record")

    def test_the_manager_holds_a_policy(self):
        """Retargeted: the Manager now consults a policy before remembering."""
        manager = ManagerAgent()
        assert isinstance(manager.memory_policy, MemoryPolicy)

    def test_the_policy_can_be_injected(self):
        strict = ManagerMemoryPolicy(max_content_chars=5)
        assert ManagerAgent(memory_policy=strict).memory_policy is strict

    def test_the_manager_records_only_what_the_policy_allows(self):
        """A policy that refuses everything leaves memory empty."""
        from src.agents.echo_agent import EchoAgent

        class RefuseEverything(MemoryPolicy):
            def evaluate(self, record):
                return PolicyVerdict(allowed=False, reason="refused for this test")

        manager = ManagerAgent(memory_policy=RefuseEverything())
        manager.register_agent(EchoAgent(name="alpha_agent", capabilities={"email"}))
        result = manager.handle_task(Task(description="check my email"))

        assert result.status is TaskStatus.COMPLETED   # the task is unaffected
        assert len(manager.memory) == 0                # nothing was admitted

    def test_the_policy_remains_the_authority(self):
        """The Manager cannot overrule its own admission rules."""
        import src.manager.manager as module

        code = code_of(module)
        assert "self.memory_policy.evaluate(" in code
