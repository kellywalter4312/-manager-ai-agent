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

MANAGER is a stable technical key, not the Manager's display name -
IDENTITY.md may call him Alfred, and that must never be what selects his
memory file. adopt_legacy_manager_memory() is what carries any history
already sitting under an old, display-name-derived file forward to this
stable owner the first time a composition runs after that fix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.config.settings import Settings
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryStoreError
from src.services.memory.persistent import PersistentMemory, adopt_legacy_store
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Owners used by the composition roots. Named here so a typo is a missing file
# rather than a silently separate store that never recalls anything.
MANAGER = "manager"
EMAIL_AGENT = "email_agent"


def normalize_owner(owner: str) -> str:
    """The exact transform build_memory() applies to an owner before
    selecting a file.

    Exposed so a caller that needs to predict, or check for, a store's path
    without constructing one - adopt_legacy_manager_memory() below is the
    one that does - uses the same rule build_memory() uses, not a second
    copy of it that could drift.
    """
    return " ".join(str(owner).split()).lower().replace(" ", "_")


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
    cleaned = normalize_owner(owner)
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


def adopt_legacy_manager_memory(settings: Settings, declared_name: str) -> bool:
    """Bring the Manager's persisted memory forward from an old,
    display-name-keyed file to the stable MANAGER-keyed one.

    The fix for the exact continuity gap ARCHITECTURE.md's stable-identity
    entry documents: the Manager's memory owner is now always ``MANAGER``,
    never ``declared_name`` (see src/composition.py) - but a real deployment
    may already hold history under whatever name IDENTITY.md declared
    before this fix existed, most likely "alfred". This call is what brings
    that history forward instead of silently starting empty under the new
    name.

    A thin, Manager-specific wrapper around the agent-agnostic
    adopt_legacy_store(): this function is the one place that knows the
    Manager's stable owner is MANAGER and that ``declared_name`` is where
    its memory used to live; the file mechanics (checkpoint, atomic write,
    idempotency) all live in persistent.py and are not duplicated here.

    A no-op whenever there is nothing to adopt: no durable backend
    configured (nothing survives a restart to adopt), the declared name
    already normalises to "manager" (nothing has ever diverged), the stable
    file already exists (already adopted, or a real store already lives
    there - either way, hands off), or no legacy file exists under the
    declared name. Safe, and inexpensive, to call on every composition -
    every heartbeat tick, every live-server request - for exactly that
    reason; see build_manager() in src/composition.py, the one caller.
    """
    if settings.memory != "persistent":
        return False
    return adopt_legacy_store(
        settings.memory_dir,
        stable_owner=MANAGER,
        legacy_owner=normalize_owner(declared_name),
    )
