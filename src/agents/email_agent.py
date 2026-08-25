"""The first real specialized agent: a general email specialist.

It implements the same BaseAgent contract as EchoAgent, and the Manager,
Router, Assessor and Registry are unchanged because of it. That is the claim
every previous milestone has been making; this is the first agent that tests it
against real work rather than a canned string.

What it is: a *general* email agent that reads a mailbox and dispatches to
analyzers. Job/opportunity analysis is the first analyzer registered, not the
agent's identity - adding a second one widens what this agent can do without
touching this file's logic or anything above it.

What it is not, yet: a sender. Composition has a defined seam (an injected
Outbox) but no implementation, and a task asking this agent to send mail gets
an explicit refusal rather than silence. Sending mail is irreversible and
outward-facing; it stays out of scope until the safeguards around it exist.
"""

from __future__ import annotations

from typing import Sequence

from src.agents.brain import SpecialistBrain
from src.agents.cognitive_agent import CognitiveAgent
from src.services.memory.day import Calendar
from src.agents.memory_policy import SpecialistMemoryCategory
from src.agents.tools.toolset import ToolSet
from src.agents.email.analyzers import Analyzer, JobOpportunityAnalyzer
from src.core.contracts import FINDINGS_KEY, AgentSpec, Task, TaskResult, tokenize
from src.services.email.mailbox import Mailbox, MailboxError, Outbox
from src.services.memory.memory import Memory, MemoryRecord
from src.services.identity.specialist import SpecialistIdentity
from src.services.memory.policy import MemoryPolicy
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)

# The name this agent answers to when no identity document names it. It is
# also the key its identity document is filed under, and the owner of its
# memory store by default - see src/agents/specialists.py, which is the one
# place those three are derived together.
AGENT_NAME = "email_agent"

# What the agent says it does when no document says it better. Named rather
# than inlined so the fallback is visible next to the name it accompanies.
DEFAULT_DESCRIPTION = (
    "Reads and analyzes email. Can scan an inbox and identify job "
    "and opportunity messages. Cannot send or compose mail."
)

# Capabilities the agent has regardless of which analyzers are registered.
BASE_CAPABILITIES = frozenset({"email", "emails", "inbox", "mail", "gmail", "messages"})

# Words that indicate the task wants mail *sent* rather than read.
SEND_WORDS = frozenset({"send", "compose", "reply", "draft", "forward"})

DEFAULT_FETCH_LIMIT = 20


class EmailAgent(CognitiveAgent):
    """Reads a mailbox and reports on its contents."""

    def __init__(
        self,
        mailbox: Mailbox,
        analyzers: Sequence[Analyzer] | None = None,
        outbox: Outbox | None = None,
        fetch_limit: int = DEFAULT_FETCH_LIMIT,
        brain: SpecialistBrain | None = None,
        memory: Memory | None = None,
        memory_policy: MemoryPolicy | None = None,
        tools: ToolSet | None = None,
        calendar: Calendar | None = None,
        identity: SpecialistIdentity | None = None,
    ) -> None:
        """Every collaborator is injected - mailbox, analyzers, outbox.

        The agent therefore works identically against an in-memory mailbox and
        a future Gmail one, and its tests need no account, key or network.

        ``brain``, ``memory`` and ``memory_policy`` are this agent's own, and
        never the orchestrator's. They default to a null brain, an empty store
        and the standard specialist policy - so an agent constructed with none
        of them reasons about nothing and retains nothing, which is the honest
        behaviour for a specialist nobody has equipped.

        ``identity`` names this agent. It supplies the name and description
        only: capabilities are computed below from the analyzers actually
        registered, and a SpecialistIdentity has no capabilities field for a
        document to put a claim in. Renaming this agent therefore changes what
        it is called and nothing about what it can do.

        Optional, and defaulting to the module constants, so an EmailAgent
        constructed without one behaves exactly as it did before identity
        documents existed.
        """
        super().__init__(
            brain=brain,
            memory=memory,
            memory_policy=memory_policy,
            tools=tools,
            # Forwarded, not interpreted. This agent has no opinion about
            # calendars; it simply lets one be given to the specialist
            # machinery underneath, the same way brain and memory are.
            calendar=calendar,
        )

        if not isinstance(mailbox, Mailbox):
            raise TypeError(f"Expected a Mailbox, got {type(mailbox).__name__}")
        if fetch_limit <= 0:
            raise ValueError("fetch_limit must be positive")

        self._identity = identity
        # Resolved once. `spec` rebuilds an AgentSpec on every access and
        # execute() reports a name several times per task; recomputing the
        # fallback each time would be wasted work, and worse, would make the
        # name look like something that could change mid-task.
        self._name = identity.name if identity is not None else AGENT_NAME

        self._mailbox = mailbox
        self._outbox = outbox
        self._fetch_limit = fetch_limit
        self._analyzers = list(
            analyzers if analyzers is not None else [JobOpportunityAnalyzer()]
        )
        for analyzer in self._analyzers:
            if not isinstance(analyzer, Analyzer):
                raise TypeError(
                    f"Expected an Analyzer, got {type(analyzer).__name__}"
                )

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def spec(self) -> AgentSpec:
        """Identity plus every capability this agent currently offers.

        Capabilities are *computed* from the registered analyzers rather than
        hardcoded, so adding an analyzer automatically widens what the existing
        CapabilityRouter routes here. No routing code changes.

        Note where the identity document reaches and where it stops. It sets
        the name and may improve the description; the capability set below is
        assembled from wired collaborators and never consults it. A document
        claiming this agent can send does not make `send` routable here, and
        cannot, because there is no path from prose to this set.
        """
        capabilities = set(BASE_CAPABILITIES)
        for analyzer in self._analyzers:
            capabilities |= analyzer.capabilities

        description = DEFAULT_DESCRIPTION
        if self._identity is not None:
            description = (
                self._identity.description
                or self._identity.role
                or DEFAULT_DESCRIPTION
            )

        return AgentSpec(
            name=self._name,
            description=description,
            capabilities=frozenset(capabilities),
        )

    @property
    def identity(self) -> SpecialistIdentity | None:
        """The document this agent was named by, if it was named by one."""
        return self._identity

    @property
    def interface(self):
        """How this specialist would like to be interacted with.

        A description of shape, never a grant. Declaring a mailbox surface adds
        no permission: the Gmail scope is still readonly, no Outbox exists, and
        sending is still refused. All this changes is what a screen may offer.

        Chat and activity are marked implemented because the Control Center
        renders both today. The mailbox and opportunity surfaces are declared
        and not yet implemented, which is the honest state - this agent really
        does read a mailbox and really does produce ranked findings, and the
        interface for looking through them directly has not been built. Saying
        so beats either hiding the shape of the work or pretending a screen
        exists.
        """
        from src.control_center.interfaces import InterfaceDescriptor, InterfaceFeature

        return InterfaceDescriptor(
            agent=self._name,
            display_name=self._name,
            features=frozenset(
                {
                    InterfaceFeature.CHAT,
                    InterfaceFeature.ACTIVITY,
                    InterfaceFeature.MAILBOX,
                    InterfaceFeature.OPPORTUNITIES,
                }
            ),
            implemented=frozenset(
                {InterfaceFeature.CHAT, InterfaceFeature.ACTIVITY}
            ),
            summary="reads a mailbox and ranks what it finds against your profile",
        )

    @property
    def can_send(self) -> bool:
        """Whether a send channel is wired up. False in this milestone."""
        return self._outbox is not None

    @property
    def analyzers(self) -> list[Analyzer]:
        return list(self._analyzers)

    # ------------------------------------------------------------------
    # Work
    # ------------------------------------------------------------------

    def execute(self, task: Task) -> TaskResult:
        log = task_logger(logger, task.id)
        tokens = tokenize(task.description)

        if tokens & SEND_WORDS:
            return self._refuse_to_send(task, log)

        # A mission may narrow the work: skip messages already processed on an
        # earlier run, or cap how many to pull. Both ride in the Task payload
        # that has existed since Milestone 1 - no contract change needed, and
        # an agent given no payload behaves exactly as before.
        limit = self._fetch_limit
        requested = task.payload.get("limit")
        if isinstance(requested, int) and requested > 0:
            limit = requested
        exclude = {str(m) for m in task.payload.get("exclude_message_ids", ()) or ()}

        try:
            messages = self._mailbox.fetch(limit=limit)
        except MailboxError as exc:
            # A mailbox failure is an expected outcome, not a bug: report it as
            # a failed task rather than letting it escape to the Manager.
            log.warning("mailbox unavailable: %s", exc)
            return TaskResult.failure(task, self._name, f"Mailbox error: {exc}")

        fetched = len(messages)
        if exclude:
            messages = [m for m in messages if m.id not in exclude]
            log.debug(
                "fetched %d message(s), %d new after excluding %d already seen",
                fetched,
                len(messages),
                fetched - len(messages),
            )
        else:
            log.debug("fetched %d message(s)", fetched)

        analyzer = self._select_analyzer(tokens)
        if analyzer is None:
            return self._summarize_inbox(task, messages)

        try:
            report = analyzer.analyze(messages)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            # One faulty analyzer must not take down the agent, exactly as one
            # faulty agent must not take down the Manager.
            log.exception("analyzer '%s' failed", analyzer.name)
            return TaskResult.failure(
                task, self._name, f"Analyzer '{analyzer.name}' failed: {exc}"
            )

        log.info(
            "analyzer '%s' found %d match(es) in %d message(s)",
            analyzer.name,
            len(report.findings),
            report.considered,
        )
        # Domain work is finished and the rules have had their say. Only now is
        # the brain asked what it makes of the result - after the deterministic
        # pipeline, never instead of it. Nothing below can change a finding, a
        # score or a band; the judgement rides alongside them.
        insight = self.reason_about(task, notes=self._domain_notes(report, messages))

        # Retention comes last: after the mailbox, after the analyzer, after
        # reflection. Nothing is recorded on the send-refusal or mailbox-failure
        # paths above, because no domain work happened there to learn from.
        # Once per execution, and only if the policy admits what is proposed.
        self.remember_experience(task, insight, report)

        return TaskResult.success(
            task,
            self._name,
            report.summary(),
            payload=self._payload_for(report, messages, insight),
        )

    def propose_memory(self, task, insight, outcome=None):
        """Offer a lesson, but only when there is genuinely one to offer.

        The gate is the brain's own confidence. An insight the brain was unsure
        about is a hunch, and a hunch stored as knowledge misleads every later
        run that recalls it. With no brain configured - the default - there is
        no insight at all and nothing is ever proposed, so an ordinary run of
        this agent stores nothing.

        What is proposed is the reflection, not the run. The counts, the
        message ids and the rendered report all stay out: those describe what
        happened once, and the policy would refuse them as routine anyway.
        Categorised as a lesson because that is what a reflection on completed
        work is - patterns and preferences require noticing something across
        several runs, which needs history this agent does not yet consult.
        """
        if insight is None or not insight.is_useful:
            return None

        return MemoryRecord(
            content=insight.summary,
            kind=SpecialistMemoryCategory.DOMAIN_LESSON.value,
            tags=(self._name,),
            context={
                # The policy reads confidence from here; the storage contract
                # has no opinion about it.
                "confidence": insight.confidence,
                "source": insight.source,
                "unknowns": len(insight.unknowns),
            },
        )

    @staticmethod
    def _domain_notes(report, messages: Sequence) -> tuple[str, ...]:
        """What the rules established, stated for the brain.

        Counts and shapes, never message content. The brain is being asked to
        judge the *outcome* - "three clear matches and two the rules could not
        place" is the useful frame - and putting sender or posting text here
        would move message content into whatever a brain is later backed by.
        """
        ranked = tuple(getattr(report, "ranked", report.findings))
        ambiguous = tuple(getattr(report, "ambiguous", ()))

        notes = [
            f"analyzer '{report.analyzer}' examined {report.considered} message(s)",
            f"{len(ranked)} confident match(es), {len(ambiguous)} the rules could not place",
        ]
        if not messages:
            notes.append("the mailbox returned nothing to examine")
        if ambiguous:
            # Where the rules were least certain is where judgement is worth
            # most, so it is named explicitly rather than left to be inferred.
            notes.append(
                "the unplaced items are where the deterministic rules were least certain"
            )
        return tuple(notes)

    @staticmethod
    def _payload_for(report, messages: Sequence, insight=None) -> dict:
        """Structured mirror of the rendered report.

        The objects themselves are passed rather than serialised dictionaries:
        this crosses a function call, not a network, and the Manager can then
        reason about real Opportunity objects instead of re-parsing prose.
        """
        payload = {
            # FINDINGS_KEY is the domain-neutral contract the Manager reads;
            # the richer, email-specific detail sits alongside it for callers
            # that do care about opportunities specifically.
            FINDINGS_KEY: tuple(getattr(report, "ranked", report.findings)),
            "analyzer": report.analyzer,
            "considered": report.considered,
            "ambiguous": tuple(getattr(report, "ambiguous", ())),
            "message_ids": tuple(message.id for message in messages),
        }
        # Additive, and absent when no judgement was formed. A missing key
        # means "this specialist did not reason about it", which is a
        # different and honest thing from "it had no concerns".
        if insight is not None:
            payload["insight"] = insight
        return payload

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_analyzer(self, tokens: frozenset[str]) -> Analyzer | None:
        """Pick the analyzer whose declared capabilities best match the task.

        The same scoring idea as CapabilityRouter, one level down: the Manager
        routes a task to an agent, and the agent routes it to an analyzer.
        """
        best: Analyzer | None = None
        best_score = 0
        for analyzer in self._analyzers:
            score = len(analyzer.capabilities & tokens)
            if score > best_score or (
                score == best_score and score > 0 and best is not None
                and analyzer.name < best.name
            ):
                best, best_score = analyzer, score
        return best

    def _summarize_inbox(self, task: Task, messages: Sequence) -> TaskResult:
        """Fallback behaviour: describe the inbox when no analyzer applies.

        This is what makes the agent a general email specialist rather than a
        job-analysis agent that fails at everything else.
        """
        if not messages:
            return TaskResult.success(task, self._name, "Inbox is empty.")

        lines = [f"{len(messages)} message(s) in the inbox, newest first:"]
        # preview() never includes the body - summaries reach logs and consoles.
        lines.extend(f"  - {message.preview()}" for message in messages)
        return TaskResult.success(task, self._name, "\n".join(lines))

    def _refuse_to_send(self, task: Task, log) -> TaskResult:
        """Composition is out of scope; say so explicitly rather than guessing.

        Note the agent cannot send even if it wanted to: with no Outbox it
        holds no object with a send method. The refusal is a clear message
        about a structural fact, not a policy check that could be bypassed.
        """
        if self._outbox is None:
            error = (
                "Composing or sending email is not enabled: no outbox is "
                "configured for this agent."
            )
        else:
            error = (
                "An outbox is configured, but sending is not implemented yet."
            )
        log.info("declined a send request: %s", error)
        return TaskResult.failure(task, self._name, error)
