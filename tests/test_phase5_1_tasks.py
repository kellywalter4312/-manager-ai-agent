"""Phase 5.1 - Tasks.

Proves the Definition of Done behaviourally, through the real production
path (a real ``ManagerAgent`` built by ``src/composition.py``, real
``handle_task``/``exchange()`` calls) rather than by constructing
``src/services/memory/tasks.py``'s own functions in isolation and calling
that proof. A handful of unit-level tests for the pure extraction
functions exist too, but the decisive tests in this file all route a
real sentence through a real Manager.

What this file is deliberately NOT testing: due dates, reminders,
recurrence, scheduling, multi-user sharing, or a Control Center CRUD
form - none of that exists, on purpose (see this phase's own Why/DoD).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.contracts import Task
from src.manager.capabilities import (
    COMPLETE_TASK,
    CREATE_TASK,
    LIST_TASKS,
    MANAGER_CAPABILITIES,
    UPDATE_TASK,
    select_capability,
)
from src.manager.memory_policy import MemoryCategory
from src.services.memory.memory import MemoryRecord
from src.services.memory.tasks import (
    build_task_record,
    complete_task_intent,
    create_task_intent,
    extract_task_update,
    match_tasks_by_topic,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class TestTaskCreation:
    def test_creating_a_task_through_the_real_manager(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="remind me to submit my assignment"))
        assert result.succeeded
        assert "submit my assignment" in result.output

    def test_the_created_task_is_genuinely_persisted_in_memory(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        found = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        assert len(found) == 1
        assert found[0].content == "buy milk"
        assert found[0].context["status"] == "pending"

    def test_an_empty_creation_request_fails_honestly_without_creating_anything(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="add a task"))
        assert not result.succeeded
        assert manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10) == ()

    def test_two_tasks_get_two_distinct_stable_ids(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        manager.handle_task(Task(description="add a task: walk the dog"))
        found = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        assert len({r.id for r in found}) == 2


# ---------------------------------------------------------------------------
# Listing / retrieval
# ---------------------------------------------------------------------------


class TestTaskListingAndRetrieval:
    def test_listing_with_no_tasks_is_honest_not_a_crash(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="what are my tasks"))
        assert result.succeeded
        assert "no pending tasks" in result.output.lower()

    def test_listing_shows_every_pending_task(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        manager.handle_task(Task(description="add a task: walk the dog"))
        result = manager.handle_task(Task(description="what are my tasks"))
        assert "buy milk" in result.output
        assert "walk the dog" in result.output

    def test_a_completed_task_no_longer_appears_in_the_bare_listing(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        manager.handle_task(Task(description="mark the task buy milk as done"))
        result = manager.handle_task(Task(description="what are my tasks"))
        assert "buy milk" not in result.output

    def test_querying_a_specific_task_that_exists(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        result = manager.handle_task(Task(description="do i have a task about milk"))
        assert result.succeeded
        assert "buy milk" in result.output

    def test_querying_a_specific_task_that_does_not_exist_is_honest(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="do i have a task about spaceships"))
        assert result.succeeded
        assert "spaceships" in result.output
        assert "buy" not in result.output


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


class TestTaskCompletion:
    def test_completing_an_existing_task(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        result = manager.handle_task(Task(description="mark the task buy milk as done"))
        assert result.succeeded
        assert "Completed" in result.output

    def test_completion_actually_changes_the_stored_status(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        manager.handle_task(Task(description="mark the task buy milk as done"))
        found = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        assert len(found) == 1
        assert found[0].context["status"] == "complete"

    def test_completion_preserves_the_tasks_own_id(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        before = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)[0].id
        manager.handle_task(Task(description="mark the task buy milk as done"))
        after = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)[0].id
        assert before == after

    def test_completing_a_nonexistent_task_fails_honestly(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="complete the task nothing-like-this-exists"))
        assert not result.succeeded

    def test_completing_an_already_complete_task_fails_honestly(self):
        """A completed task is no longer tagged task:pending, so a second
        completion attempt must not silently succeed or double-apply."""
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        manager.handle_task(Task(description="mark the task buy milk as done"))
        result = manager.handle_task(Task(description="mark the task buy milk as done"))
        assert not result.succeeded

    def test_regression_a_shared_stopword_does_not_cause_a_false_ambiguity(self):
        """Pinned regression: 'water the plants' and 'write the quarterly
        report' share only the token 'the'. Memory.recall's own literal
        token-overlap would (and, before this was found and fixed,
        genuinely did) match both for a query naming just one of them -
        match_tasks_by_topic's exact/substring matching must not."""
        manager = real_manager()
        manager.handle_task(Task(description="add a task: water the plants"))
        manager.handle_task(Task(description="remind me to write the quarterly report"))
        result = manager.handle_task(Task(description="mark the task water the plants as done"))
        assert result.succeeded, result.error
        assert "water the plants" in result.output

        found = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        statuses = {r.content: r.context["status"] for r in found}
        assert statuses["water the plants"] == "complete"
        assert statuses["write the quarterly report"] == "pending"

    def test_a_genuinely_ambiguous_completion_is_refused_not_guessed(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: call the dentist"))
        manager.handle_task(Task(description="add a task: call the plumber"))
        result = manager.handle_task(Task(description="complete the task call the"))
        assert not result.succeeded
        assert "specific" in result.error.lower()
        # Neither candidate was touched by the refused, ambiguous request.
        found = manager.memory.recall(kind=MemoryCategory.TASK.value, tags={"task:pending"}, limit=10)
        assert len(found) == 2


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestTaskUpdate:
    def test_updating_an_existing_task_renames_it(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        result = manager.handle_task(
            Task(description="change the task buy milk to buy oat milk")
        )
        assert result.succeeded
        found = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        assert found[0].content == "buy oat milk"

    def test_update_preserves_the_tasks_own_id_and_status(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        before = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)[0]
        manager.handle_task(Task(description="change the task buy milk to buy oat milk"))
        after = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)[0]
        assert after.id == before.id
        assert after.context["status"] == "pending"

    def test_updating_a_nonexistent_task_fails_honestly(self):
        manager = real_manager()
        result = manager.handle_task(
            Task(description="change the task nothing-here to something else")
        )
        assert not result.succeeded

    def test_a_malformed_update_missing_the_separator_fails_honestly(self):
        manager = real_manager()
        manager.handle_task(Task(description="add a task: buy milk"))
        result = manager.handle_task(Task(description="update the task buy milk"))
        assert not result.succeeded
        # The original task is completely untouched by the refused request.
        found = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        assert found[0].content == "buy milk"


# ---------------------------------------------------------------------------
# Persistence across a real restart
# ---------------------------------------------------------------------------


class TestPersistenceAcrossRestart:
    def _make_manager(self, tmp_path):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="persistent", memory_dir=str(tmp_path)
        )
        runtime = build_runtime(settings)
        return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

    def test_a_created_task_survives_a_full_manager_reconstruction(self, tmp_path):
        first = self._make_manager(tmp_path)
        result = first.handle_task(Task(description="add a task: buy milk"))
        assert result.succeeded

        second = self._make_manager(tmp_path)  # a brand new instance - simulated restart
        assert second is not first
        assert second.memory is not first.memory
        found = second.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        assert len(found) == 1
        assert found[0].content == "buy milk"
        assert found[0].context["status"] == "pending"

    def test_a_completion_survives_a_full_manager_reconstruction(self, tmp_path):
        first = self._make_manager(tmp_path)
        first.handle_task(Task(description="add a task: buy milk"))
        first.handle_task(Task(description="mark the task buy milk as done"))

        second = self._make_manager(tmp_path)
        found = second.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        assert len(found) == 1
        assert found[0].context["status"] == "complete"

        third = self._make_manager(tmp_path)
        result = third.handle_task(Task(description="what are my tasks"))
        assert "no pending tasks" in result.output.lower()


# ---------------------------------------------------------------------------
# Governance / content-admission policy
# ---------------------------------------------------------------------------


class TestGovernanceAndPolicy:
    def test_the_task_category_is_a_real_allowed_category(self):
        assert MemoryCategory.TASK.value == "user_task"
        assert MemoryCategory.TASK in MemoryCategory

    def test_the_literal_string_task_remains_excluded_a_different_concept(self):
        """Regression pin: EXCLUDED_KINDS already reserves the literal
        string "task" for internal Task/TaskResult execution bookkeeping -
        this is exactly why MemoryCategory.TASK's own value is
        "user_task", not "task". If this ever collided, every task would
        be silently refused by the pre-existing exclusion."""
        from src.manager.memory_policy import EXCLUDED_KINDS, ManagerMemoryPolicy

        assert "task" in EXCLUDED_KINDS
        policy = ManagerMemoryPolicy()
        refused = policy.evaluate(MemoryRecord(content="anything", kind="task"))
        assert not refused.allowed
        admitted = policy.evaluate(
            MemoryRecord(content="buy milk", kind=MemoryCategory.TASK.value)
        )
        assert admitted.allowed

    def test_a_task_that_looks_like_a_secret_is_refused(self):
        manager = real_manager()
        result = manager.handle_task(
            Task(description="add a task: api_key=sk-live-abc123xyz")
        )
        assert not result.succeeded

    def test_task_creation_goes_through_the_real_governed_retain_path(self):
        """Not a second write path: manager.retain is the one governed
        gate every durable write already crosses (see ManagerAgent.retain's
        own docstring) - proven by disabling it and observing the refusal,
        rather than merely reading the source."""
        from src.manager.capabilities import CapabilityRequest, CREATE_TASK
        from src.services.identity.identity import AgentIdentity

        request = CapabilityRequest(
            task=Task(description="add a task: buy milk"),
            identity=AgentIdentity(name="Alfred", capabilities=frozenset({"roster"})),
            roster=lambda: (),
            own_name="Alfred",
            own_capabilities=(),
            retain=None,
        )
        result = CREATE_TASK.handle(request)
        assert not result.succeeded
        assert "way to track tasks" in result.error


# ---------------------------------------------------------------------------
# Routing / capability boundaries
# ---------------------------------------------------------------------------


class TestRoutingAndCapabilityBoundaries:
    @pytest.mark.parametrize(
        "phrase,expected",
        [
            ("remind me to submit my assignment", CREATE_TASK),
            ("add a task: buy milk", CREATE_TASK),
            ("what are my tasks", LIST_TASKS),
            ("do i have a task about milk", LIST_TASKS),
            ("mark the task buy milk as done", COMPLETE_TASK),
            ("i finished submitting my assignment", COMPLETE_TASK),
            ("change the task buy milk to buy oat milk", UPDATE_TASK),
        ],
    )
    def test_each_phrasing_routes_to_the_right_capability(self, phrase, expected):
        chosen = select_capability(Task(description=phrase), MANAGER_CAPABILITIES)
        assert chosen is expected

    def test_task_capabilities_are_not_scoreable(self):
        """Word-overlap alone must never route an ordinary remark that
        merely contains 'task' to one of these - the same discipline
        record_this/forget already hold to."""
        for capability in (CREATE_TASK, LIST_TASKS, COMPLETE_TASK, UPDATE_TASK):
            assert capability.scoreable is False

    def test_an_incidental_mention_of_the_word_task_does_not_create_one(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="that was quite a task to finish"))
        assert manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10) == ()

    def test_forget_and_record_this_remain_unaffected(self):
        """No collision introduced with the existing marker-based
        capabilities this phase deliberately mirrors."""
        manager = real_manager()
        manager.retain(MemoryRecord(content="we prefer blue", kind=MemoryCategory.USER_CONTEXT.value))
        result = manager.handle_task(Task(description="forget that we prefer blue"))
        assert result.succeeded
        assert manager.memory.recall("blue") == ()


# ---------------------------------------------------------------------------
# Agent-agnosticism / specialist compatibility
# ---------------------------------------------------------------------------


class TestAgentAgnosticism:
    def test_build_task_record_carries_no_alfred_specific_assumption(self):
        record = build_task_record("remind me to prepare the client deck", agent_name="Arthur")
        assert record is not None
        assert record.context["created_by"] == "Arthur"
        assert record.kind == MemoryCategory.TASK.value

    def test_the_tasks_module_contains_no_agent_specific_comparison(self):
        """The module's own docstring names Alfred/Arthur as *examples of
        what it does not depend on* - real prose, not a hardcoded
        coupling - so this checks for the actual dangerous pattern (a
        literal comparison against one agent's name) rather than banning
        the words outright, which would also ban the sentence
        disclaiming them."""
        source = (REPO_ROOT / "src" / "services" / "memory" / "tasks.py").read_text(encoding="utf-8")
        for name in ("Alfred", "Arthur"):
            assert f'== "{name}"' not in source
            assert f"== '{name}'" not in source

    def test_match_and_extraction_are_pure_functions_of_text_alone(self):
        """The whole matching/extraction surface takes only text (and,
        for matching, a caller-supplied candidate pool) - no agent
        identity ever enters these functions, which is what makes them
        reusable by a future specialist's own request handling with zero
        change here."""
        pool = (
            MemoryRecord(content="buy milk", kind=MemoryCategory.TASK.value, id="a"),
            MemoryRecord(content="buy oat milk", kind=MemoryCategory.TASK.value, id="b"),
        )
        assert [r.id for r in match_tasks_by_topic(pool, "buy milk")] == ["a"]


# ---------------------------------------------------------------------------
# No hardcoded personal facts
# ---------------------------------------------------------------------------


class TestNoHardcodedPersonalFacts:
    """A capability that learns must never itself be the place personal
    facts get hardcoded - this project's own standing rule, checked here
    the same structural way test_stage17_2_digital_human.py's own
    TestCrossDeviceAndHonestyStillHold checks a different forbidden
    vocabulary."""

    SUSPICIOUS = (
        "birthday is", "born on", "favorite color", "my school",
        "my goal is", "i live at", "my family",
    )

    def test_the_new_tasks_module_contains_no_hardcoded_personal_facts(self):
        """Scoped to the one wholly-new module this phase adds - the
        existing, large files it also touches (capabilities.py,
        control_center.py) legitimately contain unrelated example prose
        (e.g. a "kelly: ...", "alfred: ..." attributed-transcript-line
        example elsewhere in capabilities.py) that a blind substring scan
        of the whole file cannot tell apart from a real hardcoded fact -
        the additions to those files are covered instead by their own
        behavioural proof (task content always comes from the marker
        text, never a constant) and by direct review."""
        lowered = (REPO_ROOT / "src" / "services" / "memory" / "tasks.py").read_text(
            encoding="utf-8"
        ).lower()
        for phrase in self.SUSPICIOUS:
            assert phrase not in lowered, f"tasks.py contains a hardcoded-looking personal fact: {phrase}"

    def test_task_creation_never_writes_to_user_context(self):
        """A task and a durable fact are different categories, on
        purpose - creating a task must never also, or instead, write a
        USER_CONTEXT record."""
        manager = real_manager()
        manager.handle_task(Task(description="remind me to submit my assignment"))
        facts = manager.memory.recall(kind=MemoryCategory.USER_CONTEXT.value, limit=10)
        assert facts == ()

    def test_task_creation_through_exchange_does_not_double_retain_as_a_fact(self):
        """The full conversational path (scripts/exchange.py), not just
        handle_task directly - retain_what_was_asked_for runs on every
        real turn and must not also capture task text as a durable fact
        under a different category."""
        from scripts.exchange import exchange
        from src.channels.channel import VoiceChannel
        from src.conversation.conversation import Conversation, Participant, ParticipantKind

        manager = real_manager()
        channel = VoiceChannel(manager)
        conversation = Conversation(participants=(Participant("you", ParticipantKind.PERSON),))

        exchange(channel, conversation, "remind me to submit my assignment")

        tasks = manager.memory.recall(kind=MemoryCategory.TASK.value, limit=10)
        facts = manager.memory.recall(kind=MemoryCategory.USER_CONTEXT.value, limit=10)
        recordings = manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=10)
        assert len(tasks) == 1
        assert facts == ()
        assert recordings == ()


# ---------------------------------------------------------------------------
# Control Center visibility (read-only, by design)
# ---------------------------------------------------------------------------


class TestControlCenterVisibility:
    def _system(self):
        import scripts.control_center as cc
        from src.control_center.observer import ControlCenter
        from src.config.settings import Settings

        runtime, manager, channel, conversation = cc.build(
            Settings.from_env(env={}, memory="ephemeral")
        )
        return cc, ControlCenter(runtime, manager), channel, conversation, manager

    def test_bare_tasks_verb_shows_alfreds_own_pending_tasks(self):
        cc, centre, channel, conversation, manager = self._system()
        manager.handle_task(Task(description="add a task: buy milk"))
        result = cc.run_command(centre, "tasks", 80, channel, conversation)
        assert "buy milk" in result

    def test_completed_tasks_are_hidden_unless_all_is_requested(self):
        cc, centre, channel, conversation, manager = self._system()
        manager.handle_task(Task(description="add a task: buy milk"))
        manager.handle_task(Task(description="mark the task buy milk as done"))
        bare = cc.run_command(centre, "tasks", 80, channel, conversation)
        assert "buy milk" not in bare
        full = cc.run_command(centre, "tasks Alfred all", 80, channel, conversation)
        assert "buy milk" in full

    def test_an_unknown_agent_name_is_refused_honestly(self):
        cc, centre, channel, conversation, manager = self._system()
        result = cc.run_command(centre, "tasks NoSuchAgent", 80, channel, conversation)
        assert "no agent called" in result

    def test_the_tasks_verb_is_read_only_no_administrative_write_classification(self):
        """Tasks are ordinary personal data, not administrative
        configuration - classify_administrative_write must never gate a
        'tasks' command the way it gates provider/connections/
        capabilities/ecosystem."""
        from scripts.control_center import classify_administrative_write

        requires_confirmation, _ = classify_administrative_write("tasks Alfred all")
        assert requires_confirmation is False

    def test_tasks_is_a_recognised_management_verb_over_http(self):
        from scripts.serve_ui import MANAGEMENT_VERBS

        assert "tasks" in MANAGEMENT_VERBS


# ---------------------------------------------------------------------------
# Honest failure reporting
# ---------------------------------------------------------------------------


class TestHonestFailureReporting:
    @pytest.mark.parametrize(
        "phrase",
        [
            "add a task",
            "complete the task nothing-like-this-exists-anywhere",
            "update the task nothing-here-either",
            "change the task buy milk",
        ],
    )
    def test_every_failure_path_reports_failed_not_success(self, phrase):
        manager = real_manager()
        result = manager.handle_task(Task(description=phrase))
        assert not result.succeeded
        assert result.error


# ---------------------------------------------------------------------------
# Pure extraction functions (unit level, alongside the behavioural tests above)
# ---------------------------------------------------------------------------


class TestExtractionFunctionsDirectly:
    def test_create_task_intent_recognises_its_own_markers(self):
        assert create_task_intent("remind me to call the bank")
        assert not create_task_intent("what do you think about tasks")

    def test_complete_task_intent_recognises_its_own_markers(self):
        assert complete_task_intent("i finished the report")
        assert not complete_task_intent("what are my tasks")

    def test_extract_task_update_requires_a_to_separator(self):
        assert extract_task_update("change the task buy milk to buy oat milk") == (
            "buy milk", "buy oat milk",
        )
        assert extract_task_update("change the task buy milk") is None
