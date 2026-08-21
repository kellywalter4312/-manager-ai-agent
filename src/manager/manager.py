"""The Manager (orchestrator) agent.

The DATA loop is written out as four explicit steps so the control flow is
readable at a glance:

    D - Diagnose    : work out which agent should handle this task  -> Router
    A - Assemble    : fetch that agent                              -> AgentRegistry
    T - Take Action : delegate and let the agent do the work        -> BaseAgent
    A - Assess      : check the returned result before reporting    -> Assessor

Every one of those four steps is performed by an injected collaborator. The
Manager itself contains no algorithm: it decides the *order* of the phases and
handles what happens between them. That is what "orchestration" means here, and
it is why swapping in a smarter Router or Assessor later requires no change to
this file.

A fifth collaborator is optional: a ManagerBrain, consulted for judgement the
four phases cannot express. It advises; the Manager still decides. Without one
the DATA loop behaves exactly as it always has.

A sixth is a Memory. Brain and Memory are separate dependencies on purpose:

    Manager
      |- Brain   - reasoning and decisions
      |- Memory  - retained knowledge

One reasons, the other retains. Keeping them apart means a reasoning upgrade
and a storage upgrade are independent changes, and it is what will let each
specialist own its own memory later without inheriting the Manager's.

What it records is deliberately narrow. One thing, at one point:

    T - Take Action   the specialist runs
    A - Assess        the result is checked
       useful?        a DELEGATION_OUTCOME is remembered, if the policy allows

Delegation outcomes are the first category to be recorded automatically because
they are the ones that change future orchestration: knowing that a specialist
returned something usable, or kept failing, is what a routing decision can act
on next time. USER_CONTEXT, DECISION and LESSON are not recorded automatically
- deciding when those are genuinely learned is a separate question.

Admission is not decided here. The Manager builds a record and asks its policy;
the policy is the authority on whether it may be kept.

Memory also flows the other way. Before the brain reasons, the Manager recalls
records relevant to the task and hands them over as context:

    memory -> recall_context(task) -> brain.deliberate(task, agents, notes)

That closes the loop - outcomes recorded after Assess become context for the
next diagnosis. It does not change routing: the Router still matches
capabilities mechanically, and nothing recalled here can move it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Sequence

from src.agents.base_agent import BaseAgent
from src.core.contracts import (
    FINDINGS_KEY,
    RoutingDecision,
    Task,
    TaskResult,
    TaskStatus,
)
from src.manager.assessor import Assessor, ContractAssessor
from src.manager.brain import BrainDecision, ManagerBrain, NullBrain
from src.manager.memory_policy import ManagerMemoryPolicy, MemoryCategory
from src.manager.router import CapabilityRouter, Router
from src.registry.agent_registry import AgentRegistry
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord
from src.services.memory.policy import MemoryPolicy
from src.services.memory.query import distinctive_terms
from src.utils.logging import get_logger, summarize, task_logger

# Module-level logger named after this module ("src.manager.manager"). Created
# at import time, but it emits nothing until the application configures logging.
logger = get_logger(__name__)


class MissionOutcome(str, Enum):
    """The Manager's verdict on a completed mission.

    Distinct from TaskStatus: that says what happened to one task, this says
    what the Manager intends to do about it. Deciding between "accept",
    "nothing to report" and "try again" is judgement, and judgement belongs to
    the orchestrator rather than to whatever loop invoked it.
    """

    ACCEPTED = "accepted"        # usable result with something worth reporting
    NOTHING_NEW = "nothing_new"  # ran fine, nothing worth the user's attention
    RETRY = "retry"              # transient failure; worth another attempt
    FAILED = "failed"            # will not succeed by repeating it
    REJECTED = "rejected"        # the agent broke its contract


# How many past records are offered to the brain as context. Bounded on
# purpose: a brain handed everything it ever recorded would drown the current
# task in history, and an unbounded context is the first step toward needing a
# summariser to manage it.
MEMORY_CONTEXT_LIMIT = 5

# Every recalled note carries this marker so the brain can always tell a
# remembered fact from an instruction its caller supplied. Provenance is never
# left implicit here - the same rule that stops a model claiming its own
# source in the LLM layers.
MEMORY_NOTE_PREFIX = "recalled"

# Which outcomes are worth remembering. Named as data rather than buried in a
# condition, so the rule can be read, tested and changed in one place - the
# same reasoning as EXCLUDED_KINDS in memory_policy.py.
#
# NOTHING_NEW is deliberately absent. An hourly heartbeat that finds nothing
# would otherwise write two dozen "found nothing" records a day, which is
# precisely the history-instead-of-knowledge failure the memory policy exists
# to prevent. A run that found nothing is a log line; it changes no future
# decision.
RECORDED_OUTCOMES: frozenset[MissionOutcome] = frozenset(
    {
        MissionOutcome.ACCEPTED,
        MissionOutcome.RETRY,
        MissionOutcome.FAILED,
        MissionOutcome.REJECTED,
    }
)


class ManagerAgent:
    """Receives a task, routes it to a capable agent, and reports the outcome."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        router: Router | None = None,
        assessor: Assessor | None = None,
        own_skills: BaseAgent | None = None,
        brain: ManagerBrain | None = None,
        memory: Memory | None = None,
        memory_policy: MemoryPolicy | None = None,
    ) -> None:
        # Every collaborator is injected but optional. Tests can substitute any
        # of them; ordinary callers get working defaults and need not care.
        # This is dependency injection with sensible defaults - flexibility
        # where it is wanted, no ceremony where it is not.
        self.registry = registry if registry is not None else AgentRegistry()
        self.router = router if router is not None else CapabilityRouter()
        self.assessor = assessor if assessor is not None else ContractAssessor()

        # The Manager's own capabilities are registered like any specialist, so
        # "handle it myself or delegate?" is answered by the same router that
        # picks between specialists. There is deliberately no separate
        # self-execution path: one mechanism, one set of rules, one thing to
        # test. The Manager wins the work when it is genuinely best suited -
        # not automatically, and not never.
        self.own_skills = own_skills
        if own_skills is not None:
            self.registry.register(own_skills)

        # Reasoning is optional and defaults to NullBrain, so "no brain" is an
        # ordinary configuration rather than a None to check for everywhere.
        # The type here is the abstraction: this file must never learn how
        # thinking is actually done, only that it can ask for it.
        self.brain = brain if brain is not None else NullBrain()

        # Retention is a separate dependency from reasoning, and the declared
        # type is the Memory abstraction: this file must never learn whether
        # records live in a list, a file or a database.
        #
        # The default is EphemeralMemory rather than a null object, and that is
        # a deliberate departure from the NullBrain pattern above. A null memory
        # would have to accept remember() and then silently discard it, so a
        # caller storing something and recalling nothing would get no signal at
        # all. A null brain can be honest because it *says* it has no view, in
        # its return value; a memory has no equivalent way to say so. An empty
        # in-process store is the honest alternative - it retains what it is
        # given, keeps nothing between runs, and needs no configuration.
        #
        # Constructed per Manager, never shared: two Managers get two memories,
        # and a specialist will get its own again. There is deliberately no
        # module-level default instance to fall back on.
        self.memory = memory if memory is not None else EphemeralMemory()

        # What may be remembered is a separate question from where it is kept,
        # and a separate object answers it. Stateless, so unlike a memory this
        # one is safe to share between Managers.
        self.memory_policy = (
            memory_policy if memory_policy is not None else ManagerMemoryPolicy()
        )

    def register_agent(self, agent: BaseAgent) -> None:
        """Convenience passthrough so callers can set up a Manager in one place."""
        self.registry.register(agent)

    # ------------------------------------------------------------------
    # The DATA loop
    # ------------------------------------------------------------------

    def handle_task(self, task: Task) -> TaskResult:
        """Run the full D -> A -> T -> A cycle for one task."""
        # One adapter for the whole cycle: every line below is automatically
        # stamped with this task's id, so the four phases can be read as a
        # single story even when many tasks are interleaved.
        log = task_logger(logger, task.id)
        log.info("received: %s", summarize(task.description))

        decision = self.diagnose(task)
        if decision is None:
            log.warning(
                "D: no match among %d registered agent(s)", len(self.registry)
            )
            return TaskResult.no_agent(
                task,
                error="No registered agent declares a capability matching this task.",
            )
        log.info(
            "D: selected '%s' (score=%d, %s)",
            decision.agent_name,
            decision.score,
            decision.reason,
        )

        agent = self.assemble(decision.agent_name)
        if agent is None:
            # Defensive: the router only sees agents that came from the
            # registry, so this should be unreachable. It is here because a
            # silent mismatch would be far harder to debug than an explicit
            # result.
            log.error("A: '%s' was selected but is not registered", decision.agent_name)
            return TaskResult.no_agent(
                task,
                error=f"Agent '{decision.agent_name}' was diagnosed but is not registered.",
            )
        log.debug("A: assembled '%s'", agent.name)

        result = self.take_action(agent, task)
        verdict = self.assess(task, agent.name, result)

        if verdict.status is TaskStatus.COMPLETED:
            log.info("A: accepted result from '%s'", agent.name)
        else:
            log.warning(
                "A: %s from '%s' - %s", verdict.status.value, agent.name, verdict.error
            )

        # The "useful outcome? -> MEMORY" step. It runs after assessment
        # because the assessment is what decides whether the outcome was
        # meaningful, and it cannot change what is returned below.
        self._remember_delegation(task, verdict)
        return verdict

    def diagnose(self, task: Task) -> RoutingDecision | None:
        """D - Ask the router which agent should handle this task."""
        return self.router.route(task, self.registry.all_agents())

    def assemble(self, agent_name: str) -> BaseAgent | None:
        """A - Fetch the concrete agent selected during diagnosis."""
        return self.registry.get(agent_name)

    def take_action(self, agent: BaseAgent, task: Task) -> TaskResult:
        """T - Delegate the task to the agent.

        The try/except is fault isolation: one badly behaved agent must not be
        able to bring down the orchestrator. Any exception it raises is caught
        at this boundary and converted into an ordinary failed TaskResult, so
        the caller always gets a result back in the expected shape.
        """
        log = task_logger(logger, task.id)
        log.debug("T: delegating to '%s'", agent.name)
        try:
            return agent.execute(task)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            # .exception() records the full traceback. Swallowing an error
            # without logging it is how bugs become invisible; we convert the
            # crash into a TaskResult *and* leave evidence behind.
            log.exception("T: '%s' raised %s", agent.name, type(exc).__name__)
            return TaskResult.failure(
                task,
                agent_name=agent.name,
                error=f"{type(exc).__name__}: {exc}",
            )

    def assess(self, task: Task, agent_name: str, result: object) -> TaskResult:
        """A - Ask the assessor to verify the agent honoured the contract."""
        return self.assessor.assess(task, agent_name, result)

    # ------------------------------------------------------------------
    # Reasoning
    # ------------------------------------------------------------------

    @property
    def can_reason(self) -> bool:
        """Whether a real reasoning capability was supplied."""
        return not isinstance(self.brain, NullBrain)

    def deliberate(self, task: Task, notes: Sequence[str] = ()) -> BrainDecision:
        """Ask the brain what it makes of this situation.

        Deliberately *not* wired into handle_task. The DATA loop is mechanical
        and predictable, and that is a feature: routing and assessment must not
        start depending on anything that reasons probabilistically. A caller
        that wants judgement asks for it explicitly, sees the answer, and
        decides what to do with it.

        The Manager supplies the roster from its own registry, so the brain can
        weigh delegation without holding a registry reference of its own.

        The try/except is the same fault isolation applied to delegated agents.
        A brain is injected and may be anything; a failure to think must
        degrade the Manager to "no view", never crash the orchestrator.
        """
        log = task_logger(logger, task.id)
        specs = tuple(agent.spec for agent in self.registry.all_agents())

        # Retrieval, then reasoning. The brain is handed what the Manager
        # already knows *before* it forms a view, which is the whole point of
        # having a memory rather than a log. Caller notes come first because
        # they describe the situation now; recalled notes are background.
        context = tuple(notes) + self.recall_context(task)

        try:
            decision = self.brain.deliberate(task, specs, context)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            log.exception("brain raised %s", type(exc).__name__)
            return BrainDecision.no_view(
                reason=f"reasoning failed: {type(exc).__name__}: {exc}",
                source=self._brain_name(),
            )

        if not isinstance(decision, BrainDecision):
            # A malformed answer is a broken contract, not advice. Treated the
            # way the Assessor treats a malformed result: rejected, not coerced.
            log.error("brain returned %s, not a decision", type(decision).__name__)
            return BrainDecision.no_view(
                reason=(
                    f"reasoning returned {type(decision).__name__}, "
                    f"which is not a decision"
                ),
                source=self._brain_name(),
            )

        log.info("brain: %s", decision.describe())
        return decision

    def recall_context(
        self, task: Task, limit: int = MEMORY_CONTEXT_LIMIT
    ) -> tuple[str, ...]:
        """Past records relevant to ``task``, rendered as notes for the brain.

        This is the retrieval half of the loop - the arrow that runs from
        memory back into diagnosis. It asks the store the same question the
        store already knows how to answer: ``recall(query)``. No second memory
        interface, no ranking of its own, no interpretation.

        Note the division of labour, which is the reason this method is three
        lines of real work:

            Memory  decides which records *match*  (literal token overlap)
            Manager decides how many to carry and how to label them
            Brain   decides what any of it *means*

        Relevance here is deliberately mechanical. A store that decided what
        was worth surfacing would be reasoning, and reasoning belongs to the
        brain - which is exactly the boundary this architecture is built on.

        Returns () rather than raising when anything goes wrong. Recall is a
        read on the way to forming an opinion; if it fails, the honest outcome
        is a brain that reasons without history, not a dead orchestrator.
        """
        log = task_logger(logger, task.id)

        if limit <= 0:
            return ()

        query = self._query_for(task)
        if not query:
            # Nothing specific to ask about. Recall with an empty query returns
            # the most recent records regardless of subject, which would hand
            # the brain unrelated history and call it context.
            log.debug("no distinctive terms in the task; recalling nothing")
            return ()

        try:
            records = self.memory.recall(query, limit=limit)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            log.exception("could not recall context: %s", type(exc).__name__)
            return ()

        try:
            notes = tuple(
                f"{MEMORY_NOTE_PREFIX}: {record.content}" for record in records
            )
        except Exception as exc:  # noqa: BLE001 - a store may return anything
            log.exception("recalled records were unreadable: %s", type(exc).__name__)
            return ()

        if notes:
            log.debug("recalled %d record(s) for context", len(notes))
        return notes

    @staticmethod
    def _query_for(task: Task) -> str:
        """The distinctive words of a task, as a recall query.

        Sorted so the same task always produces a byte-identical query - the
        same determinism rule the routing prompt follows.
        """
        return distinctive_terms(task.description)

    def _brain_name(self) -> str:
        """The brain's name, defensively - a broken brain may not have one."""
        try:
            return str(self.brain.name)
        except Exception:  # noqa: BLE001 - never fail while reporting a failure
            return "unknown"

    # ------------------------------------------------------------------
    # Mission-level judgement
    # ------------------------------------------------------------------

    def handled_it_myself(self, result: TaskResult) -> bool:
        """Whether this result came from the Manager's own skills."""
        return (
            self.own_skills is not None
            and result.agent_name == self.own_skills.name
        )

    def review_mission(self, task: Task, result: TaskResult) -> MissionOutcome:
        """Decide what to do about a completed mission.

        The Assessor answers "is this result well-formed?". This answers the
        larger question the CEO actually cares about: is there anything here
        worth acting on, should we try again, or has something broken? Keeping
        it on the Manager means the decision travels with the orchestrator
        rather than being reimplemented by every caller that drives one.
        """
        log = task_logger(logger, task.id)
        outcome = self._outcome_of(result)

        if outcome is MissionOutcome.REJECTED:
            log.warning("mission rejected: %s", result.error)
        elif outcome is MissionOutcome.FAILED:
            # Nobody can do this. Repeating it changes nothing.
            log.error("mission unroutable: %s", result.error)
        elif outcome is MissionOutcome.RETRY:
            # A specialist tried and could not finish - typically an upstream
            # dependency being unavailable. Worth another attempt later.
            log.warning("mission failed, will retry: %s", result.error)
        elif outcome is MissionOutcome.NOTHING_NEW:
            log.info("mission completed with nothing new to report")
        else:
            log.info("mission accepted from '%s'", result.agent_name)

        return outcome

    @staticmethod
    def _outcome_of(result: TaskResult) -> MissionOutcome:
        """The verdict logic, with no logging.

        Split out of review_mission so the memory hook can ask the same
        question during handle_task without emitting a second set of log lines
        for callers - the heartbeat among them - that call review_mission
        themselves. review_mission's behaviour is unchanged: it still logs
        exactly what it logged before, then returns this.
        """
        if result.status is TaskStatus.REJECTED:
            return MissionOutcome.REJECTED
        if result.status is TaskStatus.NO_AGENT_FOUND:
            return MissionOutcome.FAILED
        if result.status is TaskStatus.FAILED:
            return MissionOutcome.RETRY

        findings = ManagerAgent._findings_in(result)
        if findings is not None and not findings:
            return MissionOutcome.NOTHING_NEW

        return MissionOutcome.ACCEPTED

    @staticmethod
    def _findings_in(result: TaskResult) -> tuple | None:
        """Structured findings if the specialist supplied any, else None.

        Reads the domain-neutral ``findings`` key rather than anything specific
        to one specialist: the Manager must be able to judge "did this turn up
        anything?" for any agent, without knowing what that agent looks for.

        None means "this result carries no structured payload", which is not
        the same as "it found nothing" - so an agent that reports only text is
        never mistaken for one that searched and came back empty.
        """
        if not result.payload:
            return None
        findings = result.payload.get(FINDINGS_KEY)
        if findings is None:
            return None
        return tuple(findings)

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def remember_delegation(self, task: Task, verdict: TaskResult) -> None:
        """Record a delegation outcome that happened outside handle_task.

        handle_task calls this for itself. It is public so an alternative
        execution path - the cognitive loop - can record through exactly the
        same policy-gated, fault-isolated route rather than reaching for a
        private method or, worse, writing to the store directly. There is
        still only one place in this class that touches memory.
        """
        self._remember_delegation(task, verdict)

    def _remember_delegation(self, task: Task, verdict: TaskResult) -> None:
        """Record what a specialist returned, if it is worth keeping.

        Everything is wrapped, because **a memory failure is not a task
        failure**. The specialist has already run and the result has already
        been assessed by the time this is called; nothing that happens here can
        change either, and a store that raises must not turn a completed
        mission into a crash. The exception is logged rather than swallowed, so
        the failure stays visible.

        ``except Exception`` and not BaseException: a Ctrl-C during a memory
        write must still stop the program.
        """
        log = task_logger(logger, task.id)
        try:
            if self.handled_it_myself(verdict):
                # Work the Manager did itself is not a delegation. Recording it
                # here would quietly turn this into "remember every task".
                return

            if verdict.agent_name is None:
                return

            outcome = self._outcome_of(verdict)
            if outcome not in RECORDED_OUTCOMES:
                log.debug("outcome '%s' is not worth remembering", outcome.value)
                return

            record = self._delegation_record(task, verdict, outcome)

            # The policy is the authority, not this method. A record it refuses
            # is dropped with the reason it gave - the Manager does not get to
            # overrule its own admission rules.
            permitted = self.memory_policy.evaluate(record)
            if not permitted.allowed:
                log.info("not remembered: %s", permitted.reason)
                return

            self.memory.remember(record)
            log.debug("remembered %s", record.describe())

        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            log.exception(
                "could not record the delegation outcome: %s", type(exc).__name__
            )

    @staticmethod
    def _delegation_record(
        task: Task, verdict: TaskResult, outcome: MissionOutcome
    ) -> MemoryRecord:
        """Build the record for one delegation.

        Deliberately built from the *shape* of the result rather than its
        contents. ``verdict.output`` is never stored: for the mail specialist
        that string is a rendered report containing senders, subjects and
        posting text, and copying it here would move message content into a
        store that later feeds reasoning. How many findings came back is the
        part that informs a future routing decision; what was in them is not.

        Text that does reach the record - the task description and any error -
        is bounded by summarize(), the same helper that keeps log lines short.
        """
        findings = ManagerAgent._findings_in(verdict)

        detail = f", {len(findings)} finding(s)" if findings is not None else ""
        content = (
            f"delegated '{summarize(task.description)}' to "
            f"'{verdict.agent_name}': {outcome.value}{detail}"
        )
        if outcome is not MissionOutcome.ACCEPTED and verdict.error:
            content += f" - {summarize(verdict.error)}"

        context: dict[str, Any] = {
            "agent": verdict.agent_name,
            "task_id": task.id,
            "status": verdict.status.value,
            "outcome": outcome.value,
        }
        if findings is not None:
            context["findings"] = len(findings)

        return MemoryRecord(
            content=content,
            kind=MemoryCategory.DELEGATION_OUTCOME.value,
            tags=(verdict.agent_name or "", outcome.value),
            context=context,
        )
