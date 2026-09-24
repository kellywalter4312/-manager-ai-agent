"""Stage 13 - starting a collaboration against a real Manager, and the
governed entry point built on it.

Lives in ``src/manager`` rather than in ``src/conversation`` - that whole
package is bound by its own test to import nothing from ``src.manager`` or
``src.registry``, the purity ``conversation.py`` already holds itself to,
and every function below needs ``ManagerAgent``. It does not live in
``scripts/exchange.py`` either (where it was first written): the real,
live conversation path - ``observe()`` in ``src/control_center/trail.py``,
reached from every text and voice channel through ``Channel.ask`` - needs
to call ``answer_with_collaboration`` too, and ``src`` does not import
``scripts`` anywhere in this project. Putting the one implementation here
lets both callers share it: ``scripts/exchange.py`` re-exports these same
three names for ``start_collaboration``'s existing callers, and
``trail.py`` imports them directly.

Nothing here is a second collaboration API. There is exactly one
``start_collaboration``, one ``collaboration_candidates``, one
``answer_with_collaboration`` - this module - and every caller, terminal
or live, reaches the same functions.
"""

from __future__ import annotations

from typing import Sequence

from src.conversation.collaboration import Collaboration, Contribution
from src.conversation.conversation import Participant, ParticipantKind
from src.core.contracts import Task, TaskResult, tokenize
from src.manager.manager import ManagerAgent


def start_collaboration(
    manager: ManagerAgent,
    task: Task,
    *,
    with_agents: Sequence[str],
    results: dict[str, TaskResult] | None = None,
) -> Collaboration:
    """Stage 13 - User -> Alfred -> specialist(s) -> shared collaboration
    context -> Alfred.

    A thin orchestration helper, deliberately kept outside ManagerAgent
    itself so this stage adds nothing to that class's own surface - the
    Manager's existing DATA loop (decide/assemble/take_action/assess) is
    untouched, reused here exactly as it already exists.

    Each name in ``with_agents`` is resolved through the Manager's own
    registry - the same lookup ``decide``'s addressed-by-name branch
    already uses. An unregistered name is skipped, never invented; a
    registered agent that fails or returns nothing useful simply
    contributes nothing - collaboration fails honestly rather than
    fabricate a result, the same rule ``take_action``'s own fault
    isolation already gives every ordinary delegation. Nothing here
    reads an agent's Memory or Brain directly: a contribution is built
    only from the TaskResult that agent's own ``execute`` chose to
    return, the same object any other caller of it would see.

    ``results``, if given, is filled in with each resolved candidate's
    own ``TaskResult`` - the exact object ``take_action`` returned,
    keyed by agent name - as it is produced. Optional and additive:
    every existing caller that omits it sees this function behave
    exactly as before. ``answer_with_collaboration`` passes one so it
    can reuse an already-obtained result instead of re-invoking a
    specialist merely because Alfred's own synthesis afterward did not
    pan out - see its own docstring, and the Stage 13 final-correction
    entry in ARCHITECTURE.md, for why that matters.
    """
    collaboration = Collaboration(task=task.description)

    manager_name = manager.own_skills.name if manager.own_skills is not None else "manager"
    collaboration.join(Participant(manager_name, ParticipantKind.AGENT))

    for name in with_agents:
        agent = manager.registry.get(name)
        if agent is None:
            continue
        collaboration.join(Participant(agent.name, ParticipantKind.AGENT))

        result = manager.take_action(agent, task)
        if results is not None:
            results[agent.name] = result
        if result.succeeded and result.output:
            collaboration.contribute(
                Contribution(source=agent.name, content=result.output, kind="result")
            )

    return collaboration


def collaboration_candidates(manager: ManagerAgent, task: Task) -> tuple[str, ...]:
    """Which *other* registered agents' own declared capabilities overlap
    this task's words - the generic, governed signal
    ``answer_with_collaboration`` decides on.

    The identical signal ``CapabilityRouter`` already uses to pick an
    agent for an ordinary delegation (``agent.spec.capabilities &
    tokens``), asked here about every agent besides Alfred rather than
    about "who should get the whole task" - Alfred is not delegating, he
    is about to answer and is checking whether anyone else's declared
    expertise is relevant first. No agent name and no subject/topic word
    is compared anywhere in this function; only each agent's own,
    already-declared capability words, exactly as IDENTITY.md and every
    specialist identity document already state them.

    **Empty, on purpose, when Alfred's own capability system already
    claims this task's shape** (``own_skills.claims`` - see
    ``ManagerAgent.decide``'s own docstring for the identical check and
    why it exists: Stage 15's routing audit found that a specialist's
    incidental word overlap - Arthur's own, legitimate "messages"
    capability word colliding with a communication request about phone
    messages - was inviting that specialist into collaboration and
    handing its contribution onward as evidence for a request it was
    never actually relevant to). Nothing here is disabled for a specific
    word, name or specialist; a request Alfred's own capability system
    was never confident about still reaches this exactly as before.
    """
    if manager.own_skills is None:
        return ()
    # Phase 10: the person already said who should do this ("ask <agent> to
    # ..."). Collaboration is the orchestrator deciding whom to consult; an explicit
    # hand-over is that decision already made, so nobody else is drafted in.
    if manager.delegation_for(task) is not None:
        return ()
    claims = getattr(manager.own_skills, "claims", None)
    if claims is not None and claims(task):
        return ()
    tokens = tokenize(task.description)
    return tuple(
        agent.name
        for agent in manager.registry.all_agents()
        if agent.name != manager.own_skills.name and agent.spec.capabilities & tokens
    )


def answer_with_collaboration(manager: ManagerAgent, task: Task) -> TaskResult:
    """Stage 13 - the governed collaboration entry point.

        User request
          -> Alfred decides whether specialist collaboration is needed
          -> collaboration (start_collaboration, above)
          -> specialist contribution (Contribution, via the Shared Context Graph)
          -> Alfred's final response (manager.brain.converse, with the
             contributions as context)

    This is what ``observe()`` calls for every unaddressed request on the
    real conversation path (text and voice both funnel through
    ``Channel.ask`` -> ``observe``), and what ``scripts/exchange.py``
    re-exports for anything still calling it by its old import path.
    There is no second copy of this decision anywhere else.

    **The decision is generic, never a router.** When no other registered
    agent's declared capabilities overlap this task, this function does
    nothing collaboration-specific at all - it hands the task to
    ``manager.handle_task``, Alfred's ordinary, unmodified path, unchanged.
    Only when ``collaboration_candidates`` finds somebody does a
    Collaboration get created at all - "handle it myself" remains the
    default.

    **Honest, never fabricated, in every direction.** A candidate that
    fails or produces nothing contributes nothing (``start_collaboration``'s
    own guarantee); a Brain that cannot reason (Half Brain, or a failed
    call) returns ``no_view`` from ``converse`` exactly as it already
    does, and this function falls back to Alfred's ordinary handling
    rather than inventing a synthesis from an empty collaboration.

    **Never re-executes a specialist merely because synthesis failed
    afterward (Stage 13 final correction).** A specialist that already
    succeeded during collaboration produced a real, honest TaskResult -
    re-running it would not make that result any more true, and would
    risk a duplicate side effect the moment a future specialist is not
    read-only, the way today's Email Agent is. So when the Brain has
    nothing usable to say and the collaboration produced exactly one
    successful contribution, that contribution's own original TaskResult
    is returned directly rather than asking ``manager.handle_task`` to
    redo the same work. A fresh ``manager.handle_task(task)`` only runs
    when the collaboration produced nothing usable to reuse - which is
    genuinely a first attempt, not a repeat.

    Does not call ``ManagerAgent.remember_delegation`` - the result is
    attributed to Alfred himself (``handled_it_myself`` would already be
    True), which that method already treats as a no-op, so nothing here
    duplicates it.
    """
    candidates = collaboration_candidates(manager, task)
    if not candidates:
        return manager.handle_task(task)

    results: dict[str, TaskResult] = {}
    collaboration = start_collaboration(
        manager, task, with_agents=candidates, results=results
    )
    notes = tuple(
        f"{contribution.source} contributed: {contribution.content}"
        for contribution in collaboration.contributions
    )
    contributed_by = {contribution.source for contribution in collaboration.contributions}
    collaboration.close()

    decision = manager.brain.converse(task, notes)
    if decision.is_decided and decision.reason.strip():
        return TaskResult.success(task, manager.own_skills.name, decision.reason)

    # No usable Brain, or nothing worth answering from. A specialist
    # that already produced a genuine contribution must not be asked to
    # do the same work again just because Alfred could not compose a
    # synthesis from it - its own already-honest result is the honest
    # fallback. Only reused when it is the single such result: with more
    # than one, nothing here can honestly pick which one "is" the
    # answer, so the ordinary path decides that exactly as it always has.
    reusable = [result for name, result in results.items() if name in contributed_by]
    if len(reusable) == 1:
        return reusable[0]

    return manager.handle_task(task)
