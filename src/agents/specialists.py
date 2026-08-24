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

from dataclasses import dataclass

from src.agents.brain_factory import domain_for
from src.services.identity.specialist import (
    DEFAULT_IDENTITIES_DIR,
    SpecialistIdentity,
    load_specialist_identity,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


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
