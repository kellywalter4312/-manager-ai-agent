"""The Manager's own capabilities, expressed as an ordinary agent.

This is what makes "can I handle this myself?" a real question rather than a
hardcoded branch. ManagerSkills implements BaseAgent and is registered in the
same registry as every specialist, so the existing capability router decides
self-vs-delegate by the same scoring it already uses. No parallel mechanism,
no hidden self-execution path, and no change to Router, Registry or Assessor.

Its area of responsibility is coordination: knowing what it and its specialists
can do, and reporting on that. It does not attempt a specialist's work - and it
is not obliged to delegate merely because a specialist exists.

**What it offers is a declaration and a handler, together.** IDENTITY.md says
what the author intends the Manager to take on; src/manager/capabilities.py
holds the handlers that can actually answer. The spec below offers only their
intersection, which cuts both ways: a word added to the document with nothing
behind it grants nothing, and a handler registered in code does not widen the
Manager until the document declares it too.

So editing IDENTITY.md still changes what the Manager takes on - within what
the code can do. That last clause is the part this class exists to enforce,
and it is the same rule specialist identity documents already live under.

Note what stays out of ManagerAgent: everything. Choosing a handler and running
it happens here, in an ordinary registered agent. The orchestrator still holds
no algorithm and grows no branch when a capability is added.
"""

from __future__ import annotations

from typing import Callable, Sequence

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult
from src.manager.capabilities import (
    MANAGER_CAPABILITIES,
    CapabilityRequest,
    ManagerCapability,
    offered_capabilities,
    select_capability,
)
from src.manager.recognition import Recognition, recognise
from src.services.identity.identity import AgentIdentity
from src.services.profile.profile import UserProfile
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)


class ManagerSkills(BaseAgent):
    """Work the Manager performs directly."""

    def __init__(
        self,
        identity: AgentIdentity,
        roster: Callable[[], Sequence[BaseAgent]] | None = None,
        capabilities: dict[str, ManagerCapability] | None = None,
        user: UserProfile | None = None,
    ) -> None:
        """``roster`` supplies the currently registered agents.

        A callable rather than a registry reference: the Manager registers this
        object *into* the registry, so holding the registry directly would be a
        cycle. A late-bound callable keeps the dependency one-way and lets the
        roster answer reflect agents registered after construction.

        ``capabilities`` is the handler registry, defaulting to the one every
        deployment ships. Injectable for the same reason every other
        collaborator in this project is: so a test can register a capability
        without mutating global state that another test would then inherit.

        ``user`` is who the Manager is serving. Until now the profile passed
        straight through the composition root into the email specialist's
        analyzer and the Manager never saw it - so the orchestrator accountable
        for a mission had no idea who the mission was for. It arrives here at
        construction, which is per run and per heartbeat tick, so an edit to
        USER.md is picked up on the next tick exactly as it always has been.

        It is context, never authority. A UserProfile has no field that could
        be read as a permission, a tool, or a right to reach a specialist. What
        the user can do is a fact about the user; what the software can do is
        decided by handlers and wiring.
        """
        self._identity = identity
        self._roster = roster or (lambda: ())
        self._capabilities = (
            MANAGER_CAPABILITIES if capabilities is None else dict(capabilities)
        )
        self._user = user

    @property
    def spec(self) -> AgentSpec:
        """Declared and handled, never merely declared.

        AgentSpec is what the router scores against, so this is where the
        intersection has to be applied - offering a capability nothing can
        answer would route work here that would then be answered by the
        fallback, which is the silent-wrong-behaviour outcome this project
        refuses everywhere else.
        """
        return AgentSpec(
            name=self._identity.name,
            description=(
                self._identity.description
                or self._identity.role
                or f"{self._identity.name} agent"
            ),
            capabilities=offered_capabilities(
                self._identity.capabilities, self._capabilities
            ),
        )

    @property
    def capabilities(self) -> dict[str, ManagerCapability]:
        """The handler registry this instance answers from."""
        return dict(self._capabilities)

    @property
    def identity(self) -> AgentIdentity:
        return self._identity

    def recognise(self, task: Task) -> Recognition:
        """Which of the user's declared abilities this work names.

        Alfred's own faculty, not a specialist's and not a second brain. It
        reasons about nothing: it reads the profile he was built with and
        reports what the task named, deterministically and identically in
        Full Brain and Half Brain.

        The result is evidence. ``Recognition.notes()`` renders it as inert
        strings for the ``deliberate(task, notes=...)`` channel Alfred's Brain
        has always accepted, so nothing about how he reasons changes - he
        simply now has something true to reason from.
        """
        return recognise(task, self._user)

    @property
    def user(self) -> UserProfile | None:
        """The profile this Manager was built with, or None if there is none.

        Deliberately not merged into ``identity``: one says who the agent is,
        the other says who it serves, and they have different lifetimes -
        identity is read once with the runtime, the profile is re-read every
        run because the person keeps changing.
        """
        return self._user

    def execute(self, task: Task) -> TaskResult:
        """Pick the handler this task is asking for, and run it.

        Selection is the same scoring the router used one level up: match the
        task's words against what each handler declares, strongest wins. With
        a single handler registered - which is what this project ships today -
        the answer is always that handler, and the behaviour is exactly what
        it was before a registry existed.

        No branch here grows when a capability is added. That is the point:
        this method reads the same with one handler or with twenty.
        """
        log = task_logger(logger, task.id)
        capability = select_capability(task, self._capabilities)
        log.debug(
            "manager handling '%s' itself via '%s'",
            task.description,
            capability.name,
        )

        return capability.handle(
            CapabilityRequest(
                task=task,
                identity=self._identity,
                roster=self._roster,
                own_name=self.name,
                # What this Manager actually offers - the intersection, not the
                # raw declaration - so a handler reporting "what can you do"
                # cannot advertise a word nothing is able to answer.
                own_capabilities=tuple(sorted(self.spec.capabilities)),
                # Handed to every handler as read-only context. A handler that
                # has no use for it simply does not look.
                user=self._user,
                # Computed once per execution, before any handler runs, so two
                # handlers cannot disagree about what this task named.
                recognition=self.recognise(task),
            )
        )
