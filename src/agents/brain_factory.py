"""Turns a configured specialist-brain *name* into a brain *instance*.

The specialist mirror of src/manager/brain_factory.py, and deliberately the
same small-registry shape as the provider, router and Manager-brain factories.
Adding a second reasoning backend later means adding one entry to _BUILDERS,
not editing main.py and not editing any agent.

    Settings -> build_provider -> LLMProvider -> build_specialist_brain -> EmailAgent

Read that left to right and note where it stops. The agent is at the end,
receiving a finished SpecialistBrain. It never sees Settings, a provider, a
model name or a vendor - exactly as ManagerAgent never does.

What was already true before this module existed, and is worth stating because
it is unusual: CognitiveAgent has always accepted a ``brain``, and EmailAgent
has always *consulted* it, after the deterministic pipeline has finished. The
seam and its caller were both real. What was missing was any way to configure
one, so every specialist ever constructed in production received the null
brain. This module is that missing path - not a new capability, a reachable one.

Two boundaries this module does not cross:

- **It does not touch memory.** A specialist's Memory is a separate dependency
  with a separate lifecycle, and building a brain must not imply, replace or
  configure a store. Brain and Memory stay siblings.
- **It grants no authority.** A SpecialistBrain returns an Insight. It cannot
  execute, delegate, send or reach a mailbox, and nothing here changes that.
  The Manager remains the orchestrator.
"""

from __future__ import annotations

from typing import Callable

from src.agents.brain import NullSpecialistBrain, SpecialistBrain
from src.agents.llm_brain import LLMSpecialistBrain
from src.config.settings import Settings
from src.providers.base import LLMProvider
from src.services.identity.personality import AgentPersonality
from src.utils.logging import get_logger

logger = get_logger(__name__)

# One sentence per specialist, describing the area it reasons about. This is
# what makes a specialist brain *specialist* rather than a second Manager
# brain: the same LLMSpecialistBrain class becomes an email brain or a research
# brain purely by the domain it is given.
#
# Keyed by the agent's registered name so a future ResearchAgent or CodingAgent
# joins by adding a line here, inheriting the whole path without touching this
# function or any composition root.
SPECIALIST_DOMAINS: dict[str, str] = {
    "email_agent": (
        "email triage: reading a mailbox and judging which messages represent "
        "real opportunities or obligations for the user"
    ),
}


def domain_for(agent_name: str) -> str:
    """The domain sentence for a specialist, or "" if none is declared.

    An unknown agent gets an empty domain rather than an invented one, and
    LLMSpecialistBrain already treats that as "no domain stated". Making up a
    plausible-sounding speciality for an agent nobody described would be
    inventing a fact about it.
    """
    return SPECIALIST_DOMAINS.get(agent_name, "")


# Factory callables rather than instances, so a brain is only built when it is
# actually selected - the same reasoning as every other registry here.
_BUILDERS: dict[
    str, Callable[[Settings, LLMProvider, str, "AgentPersonality | None"], SpecialistBrain]
] = {
    "null": lambda settings, provider, domain, personality: NullSpecialistBrain(),
    "llm": lambda settings, provider, domain, personality: LLMSpecialistBrain(
        provider, domain=domain, personality=personality
    ),
}


def build_opportunity_interpreter(settings: Settings, provider: LLMProvider):
    """The email specialist's per-opportunity interpreter, or None.

    Gated on the same setting as the specialist brain rather than on a fourth
    switch of its own. Both are "the email specialist is allowed to use the
    model", and splitting them would mean a deployment could turn on one and be
    surprised by the cost of the other. It also means no new configuration
    surface for a capability that has no independent reason to vary.

    Returns None for the deterministic default, which is exactly what
    analyze_opportunities has always expected - the rules run, and no judgement
    is attached.

    Imported inside the function so the deterministic path never loads the LLM
    interpreter machinery, matching the lazy import JobOpportunityAnalyzer.
    analyze already uses for the same reason.
    """
    if settings.specialist_brain != "llm":
        return None

    from src.agents.email.llm_interpreter import OpportunityInterpreter

    interpreter = OpportunityInterpreter(provider)
    logger.info("opportunity interpreter ready: %s", provider.name)
    return interpreter


def available_specialist_brains() -> list[str]:
    """Every specialist-brain name that can currently be configured."""
    return sorted(_BUILDERS)


def build_specialist_brain(
    settings: Settings,
    provider: LLMProvider,
    domain: str = "",
    *,
    personality: AgentPersonality | None = None,
) -> SpecialistBrain:
    """Construct the specialist brain named by ``settings.specialist_brain``.

    The default is "null", matching every other reasoning default in this
    project: a specialist nobody deliberately equipped reasons about nothing.
    That is not a degraded state, it is the honest starting point - the
    deterministic analyzers do the domain work either way.

    Note that "llm" is not the dangerous option it looks like. The brain
    receives whatever build_provider returned, which is the null provider
    unless a provider was also configured deliberately. Turning on real
    specialist reasoning takes two settings and a credential, never one.

    ``personality`` (0.2 Final Surgical Pass) is optional and additive,
    reaching only ``LLMSpecialistBrain.converse``'s prompt - never
    ``consider()``'s domain-analysis one, see that class's own docstring
    for why. ``NullSpecialistBrain`` ignores it entirely, the same shape
    every other optional collaborator in this project already has.

    Fails fast on an unknown name, listing the valid options - a typo should
    stop the program with a readable message rather than surface later as a
    specialist that mysteriously never has a view.
    """
    try:
        builder = _BUILDERS[settings.specialist_brain]
    except KeyError:
        raise ValueError(
            f"Unknown specialist brain {settings.specialist_brain!r}. "
            f"Available specialist brains: {', '.join(available_specialist_brains())}"
        ) from None

    brain = builder(settings, provider, domain, personality)
    logger.info(
        "specialist brain ready: %s (%s)",
        settings.specialist_brain,
        type(brain).__name__,
    )
    return brain
