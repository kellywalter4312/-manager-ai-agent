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
from typing import Any, Sequence

from src.agents.brain_factory import (
    build_opportunity_interpreter,
    build_specialist_brain,
)
from src.agents.specialists import SpecialistWiring, wiring_for
from src.agents.base_agent import BaseAgent
from src.agents.email.analyzers import JobOpportunityAnalyzer
from src.agents.email_agent import EmailAgent
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
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The *technical key* of the specialist this project currently ships. Not its
# name: the name is whatever identities/email_agent.md declares, and may be
# changed without touching a line of code. This key is what selects that
# document and the built-in reasoning-domain default, and it is stable.
#
# Keeping the two apart is what lets a specialist be renamed safely. Keeping
# the *derivations* together - name, memory owner, brain domain, all from one
# SpecialistWiring - is what stops them disagreeing. Before that existed the
# three came from three unrelated constants and nothing compared them.
EMAIL_AGENT_KEY = EMAIL_AGENT


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
    specialist_brain: SpecialistBrain
    interpreter: Any | None
    # Who the email specialist is. An identity document describes an *agent*,
    # not the user, so it has an agent's lifetime: read once, here, alongside
    # the Manager's own identity. Nothing about it changes between heartbeat
    # ticks, which is precisely why it belongs in Runtime and the user's
    # profile does not.
    email_specialist: SpecialistWiring

    def describe(self) -> str:
        """A short line for a startup banner. Never prints a credential."""
        return (
            f"provider={self.provider.name} "
            f"router={type(self.router).__name__} "
            f"brain={type(self.manager_brain).__name__} "
            f"specialist_brain={type(self.specialist_brain).__name__} "
            f"interpreter={'yes' if self.interpreter is not None else 'no'} "
            f"specialist={self.email_specialist.name} "
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

    # Resolved before the specialist brain is built, because the brain's
    # reasoning domain now comes from it. One identity in, three agreeing
    # facts out - the name the agent registers under, the store it owns, and
    # the area it reasons about.
    email_specialist = wiring_for(
        EMAIL_AGENT_KEY,
        fallback_name=EMAIL_AGENT_KEY,
        identities_dir=settings.identities_dir,
    )

    runtime = Runtime(
        settings=settings,
        calendar=calendar,
        identity=identity,
        provider=provider,
        router=build_router(settings, provider),
        manager_brain=build_brain(settings, provider),
        specialist_brain=build_specialist_brain(
            settings, provider, email_specialist.brain_domain
        ),
        interpreter=build_opportunity_interpreter(settings, provider),
        email_specialist=email_specialist,
    )
    logger.info("runtime ready: %s", runtime.describe())
    return runtime


def build_manager(
    runtime: Runtime,
    *,
    mailbox: Mailbox,
    profile: UserProfile | None,
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

    The Manager and the specialist receive **separate** memories, selected by
    owner, and the **same** calendar. Same temporal rules, different stores.
    """
    registry = AgentRegistry()
    manager = ManagerAgent(
        registry=registry,
        router=runtime.router,
        brain=runtime.manager_brain,
        own_skills=ManagerSkills(runtime.identity, roster=registry.all_agents),
        # The Manager's store is selected by the Manager's own declared name,
        # for the same reason the specialist's is selected by his. An agent
        # that registers under one name and remembers under another is the
        # divergence this milestone exists to make impossible, and the Manager
        # is not exempt from it.
        memory=build_memory(runtime.settings, runtime.identity.name),
        calendar=runtime.calendar,
    )

    email_agent_options: dict[str, Any] = {}
    if fetch_limit is not None:
        # Only passed when a caller asks for it, so the agent's own default
        # stays in force for anyone who does not care.
        email_agent_options["fetch_limit"] = fetch_limit

    # One wiring, read three times. The agent is named by it, its store is
    # selected by it, and the brain it was given was built from it upstream -
    # so there is no second place where a name could be typed differently.
    specialist = runtime.email_specialist
    manager.register_agent(
        EmailAgent(
            mailbox,
            analyzers=[
                JobOpportunityAnalyzer(profile=profile, interpreter=runtime.interpreter)
            ],
            brain=runtime.specialist_brain,
            memory=build_memory(runtime.settings, specialist.memory_owner),
            calendar=runtime.calendar,
            identity=specialist.identity,
            **email_agent_options,
        )
    )

    for agent in extra_agents:
        manager.register_agent(agent)

    return manager
