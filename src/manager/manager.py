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

import re
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
from src.events.events import EventLog, delegation
from src.manager.assessor import Assessor, ContractAssessor
from src.manager.brain import BrainDecision, ManagerBrain, NullBrain
from src.manager.memory_policy import ManagerMemoryPolicy, MemoryCategory
from src.manager.router import CapabilityRouter, Router
from src.registry.agent_registry import AgentRegistry
from src.services.memory.day import Calendar, DayView
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord
from src.services.memory.policy import MemoryPolicy, PolicyVerdict
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
        calendar: Calendar | None = None,
        events: EventLog | None = None,
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

        # How this Manager reckons days. Defaults to UTC rather than to the
        # configured zone, because a Manager built without one has been given
        # no configuration at all - the composition roots pass the real
        # calendar. Retention and reasoning are untouched by it: it decides
        # only which records count as *today* when context is recalled.
        self.calendar = calendar if calendar is not None else Calendar.utc()

        # What observably happened, as distinct from what was worth retaining.
        #
        # A collaborator on the same terms as the others: injected, optional,
        # with a working default. The default is a real bounded log rather than
        # a null object, for the reason the memory comment above gives -
        # something that accepted a record and silently discarded it would make
        # "nothing happened" and "nothing was recorded" indistinguishable.
        #
        # Deliberately not a second memory. An EventLog is bounded, in-process
        # and dies with this Manager; memory is admitted by policy and may
        # persist. They answer different questions - "what happened?" and "what
        # should change a future decision?" - and an event that turned out to be
        # knowledge would still have to go through the policy to be remembered.
        self.events = events if events is not None else EventLog()

        # Campaign 2 workspaces: bound by the composition root once the
        # governed config and tools exist; absent, nothing is scoped.
        self._workspace_scope = None
        self._workspace_navigation = None

    def bind_workspaces(self, *, scope, navigation) -> None:
        """Workspaces for every agent this Manager delegates to, not only its
        own skills: ``scope(agent, names)`` says why work does not belong in
        the workspace an agent is in (or None), and ``navigation(agent, task)``
        answers a request to move between workspaces for that agent (or None).
        A specialist registered later is covered with no change."""
        self._workspace_scope = scope
        self._workspace_navigation = navigation
        if self.own_skills is not None and hasattr(self.own_skills, "bind_workspace_scope"):
            self.own_skills.bind_workspace_scope(scope)

    def register_agent(self, agent: BaseAgent) -> None:
        """Convenience passthrough so callers can set up a Manager in one place."""
        self.registry.register(agent)

    # ------------------------------------------------------------------
    # The DATA loop
    # ------------------------------------------------------------------

    def handle_task(self, task: Task, addressed: str | None = None) -> TaskResult:
        """Run the full D -> A -> T -> A cycle for one task.

        ``addressed`` is who the person was speaking to, when they were
        speaking to somebody in particular. It is *context about the request*,
        not a second way into the system: everything below it is unchanged, so
        an addressed task is assembled, executed, assessed, remembered and
        announced exactly like any other. Only the D step differs, and only in
        where the answer comes from.

        Deciding what an address means is orchestration, which is why it is
        decided here rather than in whatever collected the words. A channel or
        a room that could pick an agent would be a second router.
        """
        # One adapter for the whole cycle: every line below is automatically
        # stamped with this task's id, so the four phases can be read as a
        # single story even when many tasks are interleaved.
        log = task_logger(logger, task.id)
        log.info("received: %s", summarize(task.description))

        decision = self.decide(task, addressed)
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

        # Two records of the same moment, answering different questions.
        #
        #   event   what observably happened - every real delegation
        #   memory  what should change a future decision - if policy admits it
        #
        # Both run after assessment, because until a result is assessed there
        # is nothing truthful to say about it, and neither can change what is
        # returned below.
        self._record_delegation_event(decision, verdict)
        self._remember_delegation(task, verdict)
        return verdict

    def _record_delegation_event(
        self, decision: RoutingDecision, verdict: TaskResult
    ) -> None:
        """Note that work went to somebody else, and on what reasoning.

        Raised here because this is where it happened. A delegation is an
        orchestration act - the Manager decided this work was better done by
        another agent - and only the Manager knows it occurred. Somewhere
        downstream noticing that a result carries another agent's name would be
        an inference about the decision rather than a report of it, and the
        first time the two disagreed nobody could say which was real.

        Only a genuine handover. Work the Manager took on itself is not a
        delegation, and raising one for it would make the record of "times
        Alfred handed something over" quietly wrong - the same distinction
        _remember_delegation already draws through handled_it_myself.

        Nothing about any particular specialist is known here. The name comes
        from the result and the reasoning from the router's own decision, so
        this method cannot tell one specialist from another - and must not be
        able to.

        Never raises. Recording that something happened must not change whether
        it happened, so an observability failure is contained the same way
        take_action contains a failing agent.
        """
        if self.handled_it_myself(verdict) or not verdict.agent_name:
            return
        try:
            self.events.record(
                delegation(
                    manager=self.own_skills.name if self.own_skills else "manager",
                    specialist=verdict.agent_name,
                    reason=decision.reason,
                )
            )
        except Exception:  # noqa: BLE001 - observing must not break the work
            logger.debug("could not record a delegation event", exc_info=True)

    def decide(self, task: Task, addressed: str | None = None) -> RoutingDecision | None:
        """D - Who should handle this task, taking any address into account.

        Four ways to arrive at one decision, and they are not four routers:

            addressed   the person named somebody. That is a better answer to
                        "who should handle this" than a word-overlap score, so
                        it is used - and it is used *as a decision*, feeding
                        the same assemble/act/assess loop as any other.

            claimed     one of the Manager's own capabilities recognised this
                        task's *shape* (own_skills.claims(task) - see that
                        method's own docstring). A specialist's declared
                        capability words are a coarse, incidental signal
                        compared to Alfred's own capability explicitly
                        recognising its own request, so this is checked
                        before the router's word-overlap score - the same
                        priority ``addressed`` already has, for the same
                        reason.

            scored      the router scores declared capabilities, exactly as
                        before.

            unclaimed   no specialist's declared capabilities matched anything
                        in the task. The Manager is the default owner of
                        unclaimed work - a property of the orchestrator role,
                        never a name - so own_skills takes it when one is
                        registered, rather than the request dying here with
                        nothing to show for it.

        An address is honoured only when it names a *registered* agent. A name
        the registry does not know is not turned into an agent - nothing here
        creates a participant, and inventing one to satisfy a sentence would be
        the system agreeing that somebody exists because they were mentioned.
        When that happens the address is dropped, the router decides, and the
        reason below says so, so a person can see that their address did not
        land rather than wondering why somebody else replied.

        Note the Manager still decides. Being addressed does not let a caller
        reach an agent directly; it lets a caller tell the Manager who they
        were talking to.
        """
        if addressed:
            agent = self.registry.get(addressed)
            own_name = self.own_skills.name if self.own_skills is not None else None
            if agent is not None and agent.name == own_name:
                # Pre-Stage-9 Campaign 5 (ART-01): addressing the orchestrator
                # is asking the orchestrator - which includes asking it to hand
                # work to whoever should do it. Found at runtime: on the Manager's
                # own private line a request a registered specialist claims was
                # answered by the Manager itself, never delegated, while the
                # identical request unaddressed was delegated correctly. So
                # an address to the Manager falls through to claims, the router
                # and unclaimed work below - which still returns the Manager for
                # anything no specialist should take.
                logger.debug("the Manager was addressed; deciding who handles it as usual")
            elif agent is not None:
                return RoutingDecision(
                    agent_name=agent.name,
                    score=1,
                    reason="addressed directly by name",
                )
            else:
                logger.info(
                    "'%s' was addressed but is not registered; routing normally",
                    addressed,
                )
        if self.own_skills is not None:
            claims = getattr(self.own_skills, "claims", None)
            if claims is not None and claims(task):
                return RoutingDecision(
                    agent_name=self.own_skills.name,
                    score=1,
                    reason="claimed by one of the Manager's own capabilities",
                )
        decision = self.diagnose(task)
        if decision is not None:
            return decision
        return self._unclaimed_work_decision()

    def _unclaimed_work_decision(self) -> RoutingDecision | None:
        """Who takes work no specialist's declared capabilities matched.

        None when there is no own_skills to fall back to - a bare
        ManagerAgent with no Manager capabilities registered at all, which
        preserves diagnose()'s exact prior meaning for that case (see
        test_manager.py's own fixture, which has none). Score is 1, the
        same sentinel the addressed-by-name branch above already uses for
        a decision that is not a word-overlap strength: this one is not
        a capability match either, it is "nobody more specific claimed
        this."
        """
        if self.own_skills is None:
            return None
        return RoutingDecision(
            agent_name=self.own_skills.name,
            score=1,
            reason="no specialist's declared capabilities matched; the Manager takes unclaimed work by default",
        )

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
            # Post-Pre-Stage-9: every agent's Workspace has its own task board.
            # An agent other than the Manager that offers one answers board
            # requests from it before its domain work - so a specialist added
            # later has a working board with no change here. The Manager's own
            # board is its task capabilities, reached through execute().
            if agent is not self.own_skills:
                if self._workspace_navigation is not None:
                    moved = self._workspace_navigation(agent.name, task)
                    if isinstance(moved, TaskResult):
                        return moved
                if self._workspace_scope is not None:
                    asked = set(re.findall(r"[a-z0-9_]+", str(task.description).lower()))
                    named = tuple(sorted(asked & {str(c).lower() for c in agent.spec.capabilities}))
                    refusal = self._workspace_scope(agent.name, named) if named else None
                    if refusal:
                        return TaskResult.failure(task, agent_name=agent.name, error=refusal)
                board = getattr(agent, "workspace_task_reply", None)
                if callable(board):
                    answered = board(task)
                    if isinstance(answered, TaskResult):
                        return answered
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

    def retain(self, record: MemoryRecord) -> PolicyVerdict:
        """Offer one record to this Manager's memory. The policy decides.

        **The one governed write path.** Every route into this Manager's store
        goes through here, so "what may Alfred remember?" has a single answer
        and a single place to read it. A second path that wrote directly would
        make the policy advisory, which is the same as not having one.

        The policy is the authority, not this method: a refused record is
        returned with the reason it gave, and the Manager does not get to
        overrule his own admission rules. The verdict comes back rather than
        being swallowed so a caller can say *why* something was not kept.

        Writes into **this agent's** store and no other. There is no owner
        argument and no way to name a different memory, which is what stops a
        continuity path becoming a way to write into somebody else's.
        """
        verdict = self.memory_policy.evaluate(record)
        if verdict.allowed:
            self.memory.remember(record)
        return verdict

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
            # Today only. Yesterday's records are still stored and still
            # retrievable, but they do not drift into today's reasoning
            # unasked - see DayView.history for the deliberate request.
            records = DayView(self.memory, self.calendar).today(query, limit=limit)
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

            permitted = self.retain(record)
            if not permitted.allowed:
                log.info("not remembered: %s", permitted.reason)
                return
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
