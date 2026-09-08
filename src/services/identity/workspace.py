"""Workspace Session - the smallest fact a genuinely persistent, iterative
Workspace needs across turns: has this agent's Workspace ever been entered,
and when. Modeled on presence.py's own avatar/environment preference
functions, one governed record per agent, never a second Memory.

    Agent
    +-- ... (Brain, Memory, Identity, Personality, VoiceProfile,
    |        Capabilities, Connections, AgentPresence - unchanged)
    +-- InterfaceDescriptor (declares InterfaceFeature.WORKSPACE)
          +-- WorkspaceSession (this module) - when this agent's
                Workspace was first/last entered, and how many times

**Never a second database, never conversation Memory.** A Workspace entry is
a fact about *use*, not a remembered thing said - recorded under its own
fact_type/tag through the identical bound ``recall``/``remember``/``forget``
callables ``avatar_preference_for``/``set_avatar_preference`` already narrow
to, so it is never surfaced as something an agent "remembers being told" and
never lives in another agent's private conversation history.

**Agent-scoped by construction.** The tag is derived from ``agent_key`` the
same way ``_avatar_tag`` already derives one, and the record is written
through *that* agent's own ``remember``/``forget`` - whichever Memory the
caller resolved (see ``scripts/exchange.py``'s ``memory_for``). There is no
route by which entering Alfred's Workspace could touch Arthur's session, or
vice versa, because there is no shared store to touch - only one record per
agent, in that agent's own governed Memory.

The task board a Workspace renders is not this module's concern - it is the
*existing*, already-governed task capabilities
(src/manager/capabilities.py's create_task/list_tasks/complete_task,
MemoryCategory.TASK) reached the identical way Control Center's own
``tasks`` command already reaches them. This module owns exactly one thing:
whether, when, and how often the Workspace itself has been entered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from src.services.memory.memory import MemoryRecord

WORKSPACE_SESSION_FACT_TYPE = "workspace_session"

_Recall = Callable[..., "tuple[MemoryRecord, ...]"]
_Remember = Callable[[MemoryRecord], object]
_Forget = Callable[[str], bool]


def _workspace_tag(agent_key: str) -> str:
    return "workspace_session:" + "_".join(str(agent_key).lower().split())


@dataclass(frozen=True)
class WorkspaceSession:
    """One agent's own persisted Workspace-visit record - genuinely
    iterative state between turns, the exact property
    ``InterfaceFeature.WORKSPACE``'s own docstring names ("iterative work
    with state between turns"), never a conversation transcript.
    """

    agent_key: str
    visits: int = 0
    first_entered: "str | None" = None
    last_entered: "str | None" = None

    def __post_init__(self) -> None:
        if not str(self.agent_key).strip():
            raise ValueError("a WorkspaceSession must name the agent it describes")
        object.__setattr__(self, "agent_key", " ".join(str(self.agent_key).split()))
        object.__setattr__(self, "visits", max(0, int(self.visits)))

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_key": self.agent_key,
            "visits": self.visits,
            "first_entered": self.first_entered,
            "last_entered": self.last_entered,
        }


def workspace_session_for(agent_key: str, *, recall: "_Recall | None") -> WorkspaceSession:
    """This agent's current Workspace-session record, or an honest
    zero-visit session when nothing has ever been recorded (or no
    ``recall`` reaches this call at all) - the identical honest-absence
    shape ``avatar_preference_for`` already has.
    """
    if recall is None:
        return WorkspaceSession(agent_key=agent_key)
    for record in recall(kind="user_context", tags={_workspace_tag(agent_key)}, limit=1):
        context = record.context or {}
        return WorkspaceSession(
            agent_key=agent_key,
            visits=int(context.get("visits") or 0),
            first_entered=context.get("first_entered"),
            last_entered=context.get("last_entered"),
        )
    return WorkspaceSession(agent_key=agent_key)


def record_workspace_entry(
    agent_key: str,
    *,
    recall: "_Recall",
    remember: "_Remember",
    forget: "_Forget",
    now: "datetime | None" = None,
) -> WorkspaceSession:
    """Record one genuine entry into this agent's Workspace, returning the
    updated session - the same forget-then-remember composition
    ``set_avatar_preference`` already uses, so "the governed memory update
    path" stays singular across this module and presence.py.
    """
    when = (now or datetime.now(timezone.utc)).isoformat()
    existing = workspace_session_for(agent_key, recall=recall)
    updated = WorkspaceSession(
        agent_key=agent_key,
        visits=existing.visits + 1,
        first_entered=existing.first_entered or when,
        last_entered=when,
    )

    tag = _workspace_tag(agent_key)
    for record in recall(kind="user_context", tags={tag}, limit=5):
        forget(record.id)

    remember(
        MemoryRecord(
            content=f"Workspace entered for {agent_key} ({updated.visits} visit(s)).",
            kind="user_context",
            tags={WORKSPACE_SESSION_FACT_TYPE, tag},
            context={
                "fact_type": WORKSPACE_SESSION_FACT_TYPE,
                "agent_key": agent_key,
                "visits": updated.visits,
                "first_entered": updated.first_entered,
                "last_entered": updated.last_entered,
            },
        )
    )
    return updated
