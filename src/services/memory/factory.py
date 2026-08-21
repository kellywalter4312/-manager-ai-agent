"""Turns a configured memory *name* into a memory *instance*.

The same small-registry shape as the provider, router, brain and specialist
brain factories. Adding a durable backend later - SQLite, or something remote -
means adding one entry here, not editing main.py and not editing any agent.

    Settings -> build_memory(settings, owner) -> Memory -> ManagerAgent
                                                        -> EmailAgent

The ``owner`` argument is what keeps the architecture's separation intact. It
is not a label: it selects the file, so the Manager and each specialist get
their own store by construction. Two callers cannot accidentally share one
unless they pass the same owner, and nothing in the codebase does.

The default is "ephemeral", and that is deliberate. Turning on a store that
outlives the process is a decision about where a person's data lives, and it
should be made deliberately in configuration rather than inherited by upgrading.
Every existing caller that constructs an agent without a memory keeps the
behaviour it has today.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.config.settings import Settings
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryStoreError
from src.services.memory.persistent import PersistentMemory
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Owners used by the composition roots. Named here so a typo is a missing file
# rather than a silently separate store that never recalls anything.
MANAGER = "manager"
EMAIL_AGENT = "email_agent"


def _persistent(settings: Settings, owner: str) -> Memory:
    return PersistentMemory(Path(settings.memory_dir) / f"{owner}.json")


_BUILDERS: dict[str, Callable[[Settings, str], Memory]] = {
    "ephemeral": lambda settings, owner: EphemeralMemory(),
    "persistent": _persistent,
}


def available_memories() -> list[str]:
    """Every memory name that can currently be configured."""
    return sorted(_BUILDERS)


def build_memory(settings: Settings, owner: str) -> Memory:
    """Construct the memory named by ``settings.memory`` for one owner.

    ``owner`` must be non-empty: an empty owner would put every agent in the
    same file, quietly merging the Manager's records with a specialist's - the
    one thing this architecture is most careful to keep apart.

    Fails fast on an unknown name, listing the valid options, rather than
    surfacing later as an agent that mysteriously remembers nothing.
    """
    cleaned = " ".join(str(owner).split()).lower().replace(" ", "_")
    if not cleaned:
        raise MemoryStoreError("build_memory requires a non-empty owner")

    try:
        builder = _BUILDERS[settings.memory]
    except KeyError:
        raise ValueError(
            f"Unknown memory {settings.memory!r}. "
            f"Available memories: {', '.join(available_memories())}"
        ) from None

    memory = builder(settings, cleaned)
    logger.info("memory ready for '%s': %s (%s)", cleaned, settings.memory, type(memory).__name__)
    return memory
