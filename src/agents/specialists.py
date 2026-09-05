"""One identity per specialist, and one place that derives everything from it.

A specialist is known by two different things, and confusing them is the bug
this module exists to prevent:

    key     stable and technical - "email_agent". Chosen by the code, never by
            the user. It selects the identity document and the built-in
            defaults, and it is what a builder is registered under.

    name    human and configurable - whatever the user writes in the identity
            document. It is what the agent registers as, what selects its
            memory file, and what appears in a report.

The key never changes, so code that refers to a specialist keeps working. The
name may change freely, so a specialist can be given a real name without
anything being found and edited.

**Why the derivations live here and nowhere else.** Three facts about a
specialist have to agree, and before this module they came from three
independent places:

    registered name   AGENT_NAME in email_agent.py
    memory owner      EMAIL_AGENT in services/memory/factory.py
    brain domain      a key in SPECIALIST_DOMAINS in agents/brain_factory.py

Nothing checked them against each other. If they had ever diverged, the agent
would have registered under one name, remembered under a second, and reasoned
with a domain looked up by a third - and every test would still have passed,
because each is correct in isolation. SpecialistWiring computes all three from
a single identity, so disagreeing is no longer something that can be typed.

Renaming a specialist therefore moves its memory with it: the store is selected
by name, so a new name is a new file. That is the honest consequence of making
the name authoritative, and it is visible rather than hidden - the previous
records are still on disk under the old name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.agents.base_agent import BaseAgent
from src.agents.brain import SpecialistBrain
from src.agents.brain_factory import domain_for
from src.agents.tools.toolset import ToolSet
from src.agents.email.analyzers import JobOpportunityAnalyzer
from src.agents.email_agent import EmailAgent
from src.config.settings import Settings
from src.providers.base import LLMProvider
from src.providers.celebration import CelebrationProvider
from src.services.email.mailbox import Mailbox
from src.services.identity.specialist import (
    DEFAULT_IDENTITIES_DIR,
    SpecialistIdentity,
    load_specialist_identity,
)
from src.services.identity.personality import AgentPersonality, default_personality
from src.services.identity.voice import (
    VoiceActivation,
    VoiceProfile,
    default_activation,
    default_voice,
)
from src.services.memory.day import Calendar
from src.services.memory.factory import EMAIL_AGENT, build_memory
from src.services.memory.memory import Memory
from src.services.profile.profile import UserProfile
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The stable technical key of the specialist this project ships. Not its name:
# the name is whatever identities/email_agent.md declares. This key selects
# that document, the built-in reasoning-domain default, and the builder below.
EMAIL_AGENT_KEY = EMAIL_AGENT


@dataclass(frozen=True)
class SpecialistWiring:
    """One identity, and the three facts derived from it.

    Frozen, and holding no collaborator: this decides *what a specialist is
    called* and nothing about how it is built. Construction stays in the
    composition root.
    """

    key: str
    identity: SpecialistIdentity | None
    fallback_name: str
    fallback_domain: str

    @property
    def name(self) -> str:
        """The name the specialist registers under.

        The document when there is one, the module's own constant when there
        is not - so a project with no identity documents behaves exactly as it
        did before they existed.
        """
        return self.identity.name if self.identity is not None else self.fallback_name

    @property
    def memory_owner(self) -> str:
        """Which store this specialist reads and writes.

        The name, deliberately and without translation. build_memory
        normalises it into a filename, so a human name works as well as a
        technical one.
        """
        return self.name

    @property
    def brain_domain(self) -> str:
        """The area this specialist's brain reasons about.

        Taken from the identity document's area of responsibility when it
        states one, and from the built-in table otherwise. A domain sentence
        shapes what an LLM brain is asked about; it grants no reach, because a
        SpecialistBrain can only return an Insight.
        """
        if self.identity is not None and self.identity.area_of_responsibility:
            return self.identity.area_of_responsibility
        return self.fallback_domain

    @property
    def voice(self) -> "VoiceProfile":
        """How this specialist sounds. The fourth thing one identity decides.

        Name, memory owner and brain domain already come from here for the
        reason this class exists: three facts that must agree, derived once so
        disagreeing is not something that can be typed. A voice is a fourth,
        and it belongs for exactly the same reason - a specialist whose voice
        was configured somewhere else could drift from its own identity, and
        nobody would notice until two agents sounded alike.

        Falls back to a voice derived from the specialist's *own name* rather
        than to a shared default. Two specialists that both declare nothing
        must still be distinguishable, and a shared default object would give
        them one voice - the failure this whole layer exists to prevent.
        """
        declared = self.identity.voice if self.identity is not None else None
        return declared if declared is not None else default_voice(self.name)

    @property
    def activation(self) -> "VoiceActivation":
        """How this specialist is addressed by voice. A fifth thing this one
        identity decides, for the same reason voice above is the fourth: an
        activation identity configured somewhere else could drift from this
        specialist's own name and document, and this class exists precisely
        so that cannot happen.

        Falls back to a phrase list derived from the specialist's *own
        name*, never to a shared default - two specialists that both
        declare nothing must still be addressable as themselves, not as
        each other.
        """
        declared = self.identity.activation if self.identity is not None else None
        return declared if declared is not None else default_activation(self.name)

    @property
    def personality(self) -> "AgentPersonality":
        """How this specialist tends to communicate. A sixth thing this
        one identity decides, for the same reason voice/activation above
        are: a personality configured somewhere else could drift from this
        specialist's own document.

        Falls back to an unconfigured, name-keyed personality - two
        specialists that both declare nothing must still be distinguishable
        by key, not given one shared communication style.
        """
        declared = self.identity.personality if self.identity is not None else None
        return declared if declared is not None else default_personality(self.name)

    @property
    def has_identity_document(self) -> bool:
        return self.identity is not None

    def describe(self) -> str:
        source = "document" if self.has_identity_document else "built-in default"
        return f"{self.key} -> '{self.name}' (from {source})"


def wiring_for(
    key: str,
    fallback_name: str,
    identities_dir: str = DEFAULT_IDENTITIES_DIR,
) -> SpecialistWiring:
    """Resolve one specialist's identity into its name, store and domain.

    Reads a document if one exists and falls back to the supplied constant if
    not. Raises only when a document exists and cannot be understood, because
    a document that is present is meant to take effect.
    """
    identity = load_specialist_identity(identities_dir, key)
    wiring = SpecialistWiring(
        key=key,
        identity=identity,
        fallback_name=fallback_name,
        fallback_domain=domain_for(key),
    )
    logger.info("specialist wiring: %s", wiring.describe())
    return wiring


# ---------------------------------------------------------------------------
# The registry: which specialists exist, and how each one is built
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecialistContext:
    """Everything a specialist builder is given to work with.

    Frozen, and assembled fresh for each build - so a builder can read it and
    cannot use it to reach back into the system that made it.

    ``mailbox``, ``fetch_limit`` and ``interpreter`` are the resources the
    entry points happen to supply for the one specialist this project ships.
    They are named honestly rather than hidden behind a generic bag of
    options, and this shape is expected to be revisited when a second
    specialist needs something different. Guessing now at what that specialist
    might want would build a plugin framework for an audience of one.

    Note what a builder does *not* receive: the Manager, the registry, the
    router, or any other agent. A specialist is constructed with its own
    equipment and handed nothing it could use to reach a peer.
    """

    settings: Settings
    calendar: Calendar
    provider: LLMProvider
    # Who this specialist is, and therefore what it registers, remembers and
    # reasons as. Resolved once by build_runtime; see SpecialistWiring above.
    wiring: SpecialistWiring
    # This specialist's own brain, built from its own domain. Never shared
    # with the Manager and never shared with another specialist.
    brain: SpecialistBrain
    # What this specialist is equipped to reach in the world.
    #
    # **Access is a grant, never an identity.** An identity document says who a
    # specialist is; it has no field in which a tool, a credential or an
    # endpoint could be written, and that is deliberate. What an agent may
    # *reach* is decided here, in composition, by somebody wiring the system
    # together - the same separation that keeps a capability word from granting
    # a capability.
    #
    # Empty by default, and per specialist. An integration handed to every
    # agent because one needed it would make "who can read that mailbox" a
    # question nobody could answer, so a specialist nobody equipped can do
    # nothing - the same rule the Manager's own ToolSet already lives under.
    tools: ToolSet = field(default_factory=ToolSet)
    profile: UserProfile | None = None
    mailbox: Mailbox | None = None
    fetch_limit: int | None = None
    interpreter: Any | None = None
    # Stage 12 closure: the same, single, shared CelebrationProvider every
    # other agent's own presentation reaction ultimately comes from -
    # selected once by build_celebration_provider, never a second instance
    # per specialist. None (the honest "nothing was supplied" default) lets
    # a builder fall back to its own agent's constructor default, exactly
    # as every builder already does for a caller that does not care.
    celebration: CelebrationProvider | None = None

    def memory(self) -> Memory:
        """This specialist's own store, selected by its own name.

        A method rather than a field, because a memory has a per-run lifetime:
        calling it is what re-reads a persistent file, and that has to happen
        per tick rather than once when the context was defined.
        """
        return build_memory(self.settings, self.wiring.memory_owner)


# A builder turns a context into a registered-ready agent. Callables rather
# than classes so a specialist can be assembled however it needs to be - the
# same reasoning as every other small registry in this project.
SpecialistBuilder = Callable[[SpecialistContext], BaseAgent]


def build_email_specialist(context: SpecialistContext) -> BaseAgent:
    """Assemble the email specialist - Arthur, unless his document says otherwise.

    Moved here from the composition root so that adding a second specialist is
    a new entry in SPECIALISTS rather than another hardcoded construction in
    build_manager. The composition root still decides *when* this runs and
    what it is given; it no longer needs to know how an EmailAgent goes
    together.

    Nothing about the Gmail boundary is decided here. No Outbox is passed
    because none exists to pass, so the agent that comes out of this function
    has no object with a send method on it - the same structural refusal it
    has always had.
    """
    if context.mailbox is None:
        raise ValueError(
            "the email specialist needs a mailbox; the composition root "
            "chooses which one, because --dry-run substitutes a sample inbox"
        )

    options: dict[str, Any] = {}
    if context.fetch_limit is not None:
        # Only forwarded when a caller asks, so the agent's own default stays
        # in force for anyone who does not care.
        options["fetch_limit"] = context.fetch_limit

    return EmailAgent(
        context.mailbox,
        analyzers=[
            JobOpportunityAnalyzer(
                # The profile arrives per run, freshly loaded. A skill the
                # user learned since the last tick is already in it.
                profile=context.profile,
                interpreter=context.interpreter,
            )
        ],
        brain=context.brain,
        memory=context.memory(),
        # Whatever this specialist was equipped with, and nothing more. An
        # empty ToolSet is the honest default: the agent then names what it
        # cannot do rather than guessing.
        tools=context.tools,
        calendar=context.calendar,
        identity=context.wiring.identity,
        # Stage 12 closure: the shared, Settings-selected provider when the
        # composition root supplied one; None falls back to EmailAgent's
        # own constructor default (DefaultCelebrationProvider()) exactly
        # as before, for any caller - a test, an older composition path -
        # that does not supply one.
        celebration=context.celebration,
        **options,
    )


# Every specialist this project ships, keyed by its stable technical key.
#
# **This is the extension point.** A future specialist joins by adding one
# entry here and shipping identities/<key>.md - and nothing in the Manager,
# the router, the assessor, main.py or scripts/heartbeat.py is touched. The
# Manager learns a specialist exists the way it always has: because something
# registered one, and the router scores its declared capabilities like any
# other's.
#
# A dict of callables rather than a conditional, for the same reason the
# provider, router, brain and memory factories are: a switch statement grows a
# branch per specialist in a file that should not care how many there are.
#
# Deliberately not auto-discovered from the identities/ directory. Dropping a
# markdown file into a folder must not be able to register an agent - that
# would let a document grant something again, one level up from the capability
# rule it already cannot break. Registration stays explicit and in code.
SPECIALISTS: dict[str, SpecialistBuilder] = {
    EMAIL_AGENT_KEY: build_email_specialist,
}


def available_specialists() -> list[str]:
    """Every specialist key that can currently be built."""
    return sorted(SPECIALISTS)
