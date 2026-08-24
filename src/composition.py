"""One place where the system is assembled.

Two entry points already wire this agent - main.py and scripts/heartbeat.py -
and they were building the same fourteen collaborators independently. A third
consumer would have meant a third copy, and copies drift: the specialist brain,
persistent memory and the opportunity interpreter each reached one entry point
before the other, and each gap was found by inspection rather than by a test.

So this module owns the construction, and the entry points own the decisions.

**Two functions, because there are genuinely two lifetimes.** This is the whole
design, and collapsing it would be a bug:

    build_runtime   built ONCE - a provider, a router, brains, a calendar.
                    None holds per-run state, so sharing them is free.

    build_manager   built PER RUN - and for the heartbeat, per tick.

The Manager and its memories must be rebuilt each tick, and not as an
efficiency oversight. A PersistentMemory re-reads its file at construction, so
a fresh Manager is what lets tick 2 see what tick 1 wrote; and the profile is
re-read each tick so an edit to USER.md takes effect without a restart. A
single long-lived Manager - the obvious "System" object - would freeze both,
quietly breaking the temporal memory design in a way no existing test covers.

What this module deliberately does **not** decide: which mailbox to use, how to
handle a missing profile, which extra agents to register, how to report an
error, or what to do next. Those differ between the two entry points on
purpose - the heartbeat swaps in a sample inbox for --dry-run and treats a
missing profile as a stop condition, while main.py degrades to detection-only -
and moving them here would turn product decisions into shared machinery.

Nothing here has a side effect worth fearing: no file is written, no network is
touched, and no credential is read. build_provider checks that a key *exists*
without importing a vendor SDK, and GmailMailbox connects lazily on first use,
so a Control Center may construct all of this simply to look at it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.agents.brain_factory import (
    build_opportunity_interpreter,
    build_specialist_brain,
)
from src.agents.specialists import (
    SPECIALISTS,
    SpecialistContext,
    SpecialistWiring,
    wiring_for,
)
from src.agents.base_agent import BaseAgent
from src.agents.brain import SpecialistBrain
from src.config.settings import Settings
from src.manager.brain import ManagerBrain
from src.manager.brain_factory import build_brain
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.manager.router import Router
from src.manager.router_factory import build_router
from src.providers.base import LLMProvider
from src.providers.factory import build_provider
from src.registry.agent_registry import AgentRegistry
from src.services.email.mailbox import Mailbox
from src.services.identity.identity import AgentIdentity, load_identity
from src.services.memory.day import Calendar
from src.services.memory.factory import EMAIL_AGENT, build_memory
from src.services.profile.profile import UserProfile
from src.services.schedule.schedule import UserSchedule
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The technical key of the specialist this project ships. Defined beside the
# registry that uses it and re-exported here, because callers already reach
# for it from the composition root.
EMAIL_AGENT_KEY = EMAIL_AGENT


@dataclass(frozen=True)
class SpecialistRuntime:
    """One specialist's long-lived half, resolved once at startup.

    Everything here is either identity or a stateless collaborator, which is
    what makes sharing it across heartbeat ticks correct. Note what is absent:
    the agent itself and its memory. Those are per-tick and are produced by
    the builder, not stored here - a specialist held in Runtime would be a
    singleton whose persistent store never re-read its file.
    """

    key: str
    wiring: SpecialistWiring
    brain: SpecialistBrain
    build: Any

    @property
    def name(self) -> str:
        return self.wiring.name


@dataclass(frozen=True)
class Runtime:
    """The collaborators that are safe to build once and share.

    Frozen, and holding nothing that changes during a run. Everything here is
    either configuration, a stateless strategy, or a client that connects
    lazily - which is what makes sharing them across heartbeat ticks correct
    rather than merely convenient.

    Note what is absent: no Manager, no memory, no mailbox, no profile. Those
    have a per-run lifetime and belong to build_manager.
    """

    settings: Settings
    calendar: Calendar
    identity: AgentIdentity
    provider: LLMProvider
    router: Router
    manager_brain: ManagerBrain
    interpreter: Any | None
    # Every specialist this project ships, keyed by its technical key, each
    # with its own identity and its own brain. Plural because there is no
    # architectural reason for there to be one - the fact that this project
    # currently ships a single specialist is a fact about the project, not a
    # shape the composition root should be built around.
    #
    # An identity document describes an *agent*, not the user, so it has an
    # agent's lifetime: read once, here. Nothing in it changes between
    # heartbeat ticks, which is precisely why it belongs in Runtime and the
    # user's profile does not.
    specialists: Mapping[str, SpecialistRuntime]

    # ------------------------------------------------------------------
    # Named accessors for the one specialist this project ships.
    #
    # The mapping above is the source of truth; these are conveniences for
    # callers that already know they mean the email specialist - a startup
    # banner, a test. They read from the mapping rather than holding a second
    # copy, so there is nothing here that could fall out of step with it.
    # ------------------------------------------------------------------

    @property
    def email_specialist(self) -> SpecialistWiring:
        return self.specialists[EMAIL_AGENT_KEY].wiring

    @property
    def specialist_brain(self) -> SpecialistBrain:
        return self.specialists[EMAIL_AGENT_KEY].brain

    def describe(self) -> str:
        """A short line for a startup banner. Never prints a credential."""
        return (
            f"provider={self.provider.name} "
            f"router={type(self.router).__name__} "
            f"brain={type(self.manager_brain).__name__} "
            f"specialists={','.join(s.name for s in self.specialists.values())} "
            f"interpreter={'yes' if self.interpreter is not None else 'no'} "
            f"timezone={self.settings.timezone}"
        )


def build_runtime(settings: Settings) -> Runtime:
    """Build everything that outlives a single run.

    Raises rather than degrades. A timezone that cannot be resolved or a
    provider that cannot be configured is a misconfiguration, and the caller -
    which knows whether it is a CLI that should exit or an interface that
    should show a message - decides how to report it. That is why this function
    does no printing and calls no sys.exit.

    check_configuration is deliberately *not* called here. It is a startup
    policy about whether a combination of settings makes sense, and the entry
    points already run it before anything is built; calling it again here would
    make constructing a system for inspection illegal.
    """
    calendar = Calendar.configured(settings.timezone)
    identity = load_identity(settings.identity_path)
    provider = build_provider(settings)

    # One pass over the registry: every specialist gets its identity resolved
    # and its own brain built from its own domain. The loop is what makes a
    # second specialist a data change rather than a code change - nothing
    # below counts them, names them, or is written for exactly one.
    specialists = {
        key: SpecialistRuntime(
            key=key,
            wiring=(wiring := wiring_for(
                key, fallback_name=key, identities_dir=settings.identities_dir
            )),
            # Its own brain, from its own domain. Two specialists get two
            # brains; neither gets the Manager's.
            brain=build_specialist_brain(settings, provider, wiring.brain_domain),
            build=build,
        )
        for key, build in SPECIALISTS.items()
    }

    runtime = Runtime(
        settings=settings,
        calendar=calendar,
        identity=identity,
        provider=provider,
        router=build_router(settings, provider),
        manager_brain=build_brain(settings, provider),
        interpreter=build_opportunity_interpreter(settings, provider),
        specialists=specialists,
    )
    logger.info("runtime ready: %s", runtime.describe())
    return runtime


def build_manager(
    runtime: Runtime,
    *,
    mailbox: Mailbox,
    profile: UserProfile | None,
    schedule: UserSchedule | None = None,
    fetch_limit: int | None = None,
    extra_agents: Sequence[BaseAgent] = (),
) -> ManagerAgent:
    """Assemble one Manager, with the email specialist registered.

    Called once per process by main.py and **once per tick** by the heartbeat.
    Each call produces a fresh Manager with fresh memories, which is what makes
    a persistent store re-read its file and see what the previous tick wrote.

    ``mailbox`` and ``profile`` are parameters rather than decisions taken here:
    the heartbeat substitutes a sample inbox for --dry-run, and the two entry
    points disagree about what a missing profile means. ``extra_agents`` exists
    for the same reason - main.py registers a calendar stand-in that the
    autonomous path deliberately does not.

    The Manager and every specialist receive **separate** memories, selected
    by owner, and the **same** calendar. Same temporal rules, different
    stores. A specialist's memory is built inside its builder, per call, which
    is what lets a persistent store re-read its file each tick.
    """
    registry = AgentRegistry()
    manager = ManagerAgent(
        registry=registry,
        router=runtime.router,
        brain=runtime.manager_brain,
        # Alfred is given who he serves as well as who he is. The profile was
        # already loaded per run for the specialist's analyzer and passed
        # straight through this function; the orchestrator accountable for the
        # mission simply never saw it. It arrives per call, like the memory
        # beside it, so an edit to USER.md takes effect on the next tick.
        own_skills=ManagerSkills(
            runtime.identity,
            roster=registry.all_agents,
            user=profile,
            # A second per-run document about the same person. Passed here and
            # never held in Runtime: a schedule read once at startup would
            # leave every later heartbeat tick reasoning about a week the user
            # has since changed, which is the same trap the profile avoids.
            schedule=schedule,
        ),
        # The Manager's store is selected by the Manager's own declared name,
        # for the same reason the specialist's is selected by his. An agent
        # that registers under one name and remembers under another is the
        # divergence this milestone exists to make impossible, and the Manager
        # is not exempt from it.
        memory=build_memory(runtime.settings, runtime.identity.name),
        calendar=runtime.calendar,
    )

    # Every registered specialist, built and registered the same way. This
    # loop is the whole of Step 3: before it, adding a specialist meant
    # writing another construction here; now it means adding an entry to
    # SPECIALISTS and shipping an identity document.
    #
    # Iterating the *resolved* mapping rather than the registry directly is
    # deliberate - the registry was consulted once at startup, so a specialist
    # cannot appear here without a brain and an identity having been built for
    # it, and there is no lookup that could miss.
    for entry in runtime.specialists.values():
        manager.register_agent(
            entry.build(
                SpecialistContext(
                    settings=runtime.settings,
                    calendar=runtime.calendar,
                    provider=runtime.provider,
                    wiring=entry.wiring,
                    brain=entry.brain,
                    # Per-run, every run. A profile freshly loaded this tick
                    # is what lets a skill learned since the last one count.
                    profile=profile,
                    mailbox=mailbox,
                    fetch_limit=fetch_limit,
                    interpreter=runtime.interpreter,
                )
            )
        )

    for agent in extra_agents:
        manager.register_agent(agent)

    return manager
