"""Prompt construction and answer parsing for LLM-driven routing.

Both functions here are **pure**: they take data and return data, touching no
provider, no network and no clock. That is the whole point of the split. The
hard parts of LLM routing - "does the prompt describe every agent?", "is a
hallucinated name rejected?" - become ordinary unit tests with no model
involved, leaving llm_router.py with almost nothing left to get wrong.

Security note: the model's answer is **untrusted input**. It is validated
against the set of agents that actually exist, exactly as you would validate a
value arriving from a web form. See parse_routing_answer.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from src.agents.base_agent import BaseAgent
from src.core.contracts import Task

# The routing identity - the "Give it an identity" step of the AGENT framework.
ROUTING_SYSTEM_PROMPT = (
    "You are the routing component of an agent orchestrator. "
    "You are given a task and a list of available agents. "
    "Choose the single agent best suited to the task. "
    "Reply with exactly one agent name from the list, and nothing else. "
    "If no listed agent is suitable, reply with the single word NONE."
)

# The answer meaning "none of these fit".
NO_AGENT_ANSWER = "none"


def build_routing_prompt(task: Task, agents: Sequence[BaseAgent]) -> str:
    """Describe the task and the available agents for a routing model.

    Agents are listed in sorted order so the same inputs always produce a
    byte-identical prompt. Determinism matters here beyond tidiness: it keeps
    tests stable and would make prompt caching possible later.

    Only ``task.description`` is included. ``task.payload`` is deliberately
    left out - it may carry credentials or personal data, and the routing
    decision does not need it. Same reasoning as keeping payloads out of logs.
    """
    lines = ["Task:", task.description, "", "Available agents:"]

    for agent in sorted(agents, key=lambda a: a.spec.name):
        spec = agent.spec
        capabilities = ", ".join(sorted(spec.capabilities))
        lines.append(f"- {spec.name}: {spec.description} (capabilities: {capabilities})")

    lines += [
        "",
        f"Reply with exactly one agent name from the list above, or {NO_AGENT_ANSWER.upper()}.",
    ]
    return "\n".join(lines)


def parse_routing_answer(text: str, valid_names: Iterable[str]) -> str | None:
    """Extract a valid agent name from a model's answer, or None.

    The contract is strict on purpose: only the first line is considered, and
    the result must match a **registered** agent name. A model that replies
    with an agent that does not exist, or with an explanation instead of a
    name, is treated as having failed to answer.

    Being strict here is what bounds the blast radius of a bad or manipulated
    answer: the worst outcome is that a real, registered agent is chosen, or
    that routing declines. A name can never be invented into existence.
    """
    if not text or not text.strip():
        return None

    candidate = text.strip().splitlines()[0]
    # Models often decorate answers: "alpha_agent." or `"alpha_agent"`.
    candidate = candidate.strip().strip("\"'`*.,:;! \t").lower()

    if not candidate or candidate == NO_AGENT_ANSWER:
        return None

    # Case-insensitive lookup that returns the *registered* spelling, never the
    # model's. Downstream code should only ever see names the registry knows.
    lookup = {name.lower(): name for name in valid_names}
    return lookup.get(candidate)
