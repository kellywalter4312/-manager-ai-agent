"""The whole-ecosystem component inventory: what real, currently-installed
component exists, what category it belongs to, and what its real, honest
version signal is - the read side Full Alfred OS Ecosystem Evolution needs
before anything can be proposed, evaluated or applied against it.

**Reuses, never re-derives.** Every fact here is read from an architecture
this project already built and already trusts: the Universal Ecosystem
registry (``src/services/ecosystem/registry_factory.py``) for every
provider/connection category, ``CredentialStore`` for which account/model
authorises each, ``Runtime.specialists``/``AgentRegistry`` for which
specialists are installed, ``src/control_center/manifest.py``'s
``UpgradeManifest`` for SYSTEM_VERSION/schema/visual-environment versions,
and ``Memory.recall()`` for what has genuinely been retained. Nothing here
constructs a second inventory of any of these; it only reads them and
presents them side by side.

**Categories, kept genuinely distinct - the core invariant this checkpoint
was told to hold.** A ``ComponentSnapshot.category`` is one of:

    provider     one active backend per role (LLM/voice/video/media) -
                 switching it never deletes another connected account
                 (see ``src/services/credentials/account.py``).
    connection   an external application/service - additive, many can be
                 connected at once, none displaces another (Independent
                 Manageability, 52.27's own ``connections`` command).
    specialist   an installed specialist agent (Arthur, and any future
                 one) - independently versioned by its own identity
                 document's real content, never a fabricated number.
    brain        which reasoning mode is selected (Full/Half Brain) for
                 the Manager or for specialists by default - a different
                 axis from *which vendor* answers (that is a provider).
    memory       a *developmental* snapshot (retained knowledge by kind,
                 corrections, learning progress) - never a swappable
                 "version"; see ``memory_development_snapshot`` for why
                 more stored records is deliberately never reported as
                 "more evolved".
    system       SYSTEM_VERSION / memory & state SCHEMA_VERSION / visual
                 environment versions - read straight from the existing,
                 complete Stage 6-9 Upgrade Manifest, never duplicated.

**No fabrication.** Where this codebase genuinely has no version concept
for something (a tool class, a capability handler), no fake one is
invented here - see ``src/control_center/manifest.py``'s own, identical
stance, quoted in this module's tests. A provider/connection's "version"
is the real, honest tuple of (active backend, account id, model) it is
actually configured with today - a fact, not a fabricated semver.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import Settings
from src.control_center.manifest import UpgradeManifest, upgrade_manifest
from src.control_center.snapshot import SNAPSHOT_VERSION
from src.services.credentials.account import CredentialStore
from src.services.credentials.account_store_file import DEFAULT_ACCOUNTS_PATH, load_credential_store
from src.services.ecosystem.registry_factory import build_ecosystem_registry
from src.services.evolution.governed_config import DEFAULT_CONFIG_PATH, load_governed_config
from src.services.identity.specialist import identity_path

# Real, currently-hardcoded values this checkpoint made governable
# through GovernedConfig (see handlers.py) - defaults match this
# project's own pre-existing behaviour exactly, so an unconfigured
# installation reports (and behaves) identically to before.
CAPABILITY_GENERAL_CONVERSATION = "capability.general_conversation"
DEFAULT_CONVERSATION_HISTORY_LIMIT = "6"
TOOL_DOCUMENT_GENERATION = "tool.document_generation"
CONNECTION_GMAIL_ADAPTER = "connection.gmail"
DEFAULT_GMAIL_ADAPTER_VERSION = "1"

# Provider-axis categories: exactly one backend active per category, many
# accounts may be retained - the identical set Independent Manageability's
# own `_provider_axis_backend_names` (scripts/control_center.py) already
# names for the identical reason, reused here rather than re-derived.
_PROVIDER_CATEGORIES: tuple[str, ...] = (
    "llm_provider",
    "video_generation_provider",
    "video_understanding_provider",
    "speech_provider",
    "media",
    "avatar_provider",
    "environment_provider",
    "knowledge_provider",
    "live_info_provider",
    "celebration_provider",
    "calendar_event_provider",
    "document_rendering_provider",
)
# Additive, no single "active" slot across the axis - Gmail today, any
# future service tomorrow (see 52.27's own `connections` command, which
# this reads from the identical persisted CredentialStore).
_CONNECTION_CATEGORIES: tuple[str, ...] = ("connection", "communication_provider")


def _short_hash(data: bytes) -> str:
    """A short, stable, honest content fingerprint - never a fabricated
    semver. Twelve hex characters is enough to notice real drift and
    short enough to read at a terminal, the same tradeoff git's own
    abbreviated commit hashes make."""
    return hashlib.sha256(data).hexdigest()[:12]


def _file_version(path: Path) -> str:
    """A real component's version, when that component's only genuine
    source of truth is a document on disk: the document's own content
    hash. Changes if, and only if, the document's real content changes -
    never invented, never bumped by convention."""
    try:
        return _short_hash(path.read_bytes())
    except OSError:
        return "(missing)"


@dataclass(frozen=True)
class ComponentSnapshot:
    """One real, currently-installed ecosystem component, as it actually
    stands right now - never a live object, the same "describe, never
    hand over a capability" discipline ``ComponentDescriptor`` (Universal
    Ecosystem) already holds itself to.
    """

    component_id: str
    category: str
    label: str
    version: str
    detail: str = ""

    def describe(self) -> str:
        return f"[{self.category}] {self.component_id} = {self.version} - {self.label}"


def _provider_component(registry, category: str, store: CredentialStore) -> ComponentSnapshot:
    adapter = registry.adapter_for(category)
    active = adapter.active() if adapter is not None else None
    if active is None:
        return ComponentSnapshot(
            component_id=f"provider.{category}",
            category="provider",
            label=category,
            version="(none active)",
            detail="no backend currently selected for this category",
        )
    backend = active.key
    account = store.active(backend)
    if account is not None:
        model = account.model or "(default model)"
        version = f"{backend}:{account.account_id}:{model}"
    elif backend in ("null", "none") or not active.requires_credential:
        version = backend
    else:
        version = f"{backend} (no account on record - using environment credential, if any)"
    return ComponentSnapshot(
        component_id=f"provider.{category}",
        category="provider",
        label=category,
        version=version,
        detail=f"active backend={backend}",
    )


def _connection_component(registry, category: str, store: CredentialStore) -> ComponentSnapshot:
    """Gmail (``connection``) and the general messaging axis
    (``communication_provider``) are reported here too, alongside the
    additive, arbitrarily-named services 52.27's own ``connections``
    command already manages through the identical ``CredentialStore`` -
    kept as their own ecosystem-category entries because, unlike an
    arbitrary connected service, each already has a real, registered
    backend catalog (``EcosystemRegistry``) to report a genuine active
    selection against.
    """
    adapter = registry.adapter_for(category)
    active = adapter.active() if adapter is not None else None
    backend = active.key if active is not None else "(none)"
    account = store.active(backend) if active is not None else None
    version = backend if account is None else f"{backend}:{account.account_id}"
    return ComponentSnapshot(
        component_id=f"connection.{category}",
        category="connection",
        label=category,
        version=version,
    )


def additive_connections(store: CredentialStore, *, exclude: frozenset[str]) -> tuple[ComponentSnapshot, ...]:
    """Every additively-connected external service - reused directly from
    the persisted store 52.27's own ``connections`` command already
    writes to, never a second registry of "what is connected"."""
    services = sorted(p for p in store.providers() if p not in exclude and p != "null")
    result = []
    for service in services:
        accounts = store.accounts_for(service)
        active = store.active(service)
        version = f"{len(accounts)} account(s)" + (f", active={active.account_id}" if active else "")
        result.append(
            ComponentSnapshot(
                component_id=f"connection.{service}",
                category="connection",
                label=service,
                version=version,
            )
        )
    return tuple(result)


def _specialist_component(key: str, name: str, settings: Settings) -> ComponentSnapshot:
    path = identity_path(settings.identities_dir, key)
    return ComponentSnapshot(
        component_id=f"specialist.{key}",
        category="specialist",
        label=name,
        version=_file_version(path) if path.exists() else "(no identity document)",
        detail=str(path),
    )


def _surface_components(manager, config) -> tuple[ComponentSnapshot, ...]:
    """Every surface every agent offers - the manager's own and each
    registered specialist's - as a governable component whose version is its
    current state (implemented / planned), the governed config applied."""
    from src.control_center.interfaces import SURFACE_IMPLEMENTED, SURFACE_PLANNED, describe_interface, surface_component_id

    if manager is None:
        return ()
    agents = []
    if getattr(manager, "own_skills", None) is not None:
        agents.append(manager.own_skills)
    agents.extend(agent for agent in manager.registry.all_agents() if agent is not getattr(manager, "own_skills", None))
    result = []
    for agent in agents:
        descriptor = describe_interface(agent, governed=config.values)
        for kind in sorted(descriptor.features):
            result.append(
                ComponentSnapshot(
                    component_id=surface_component_id(descriptor.agent, kind),
                    category="surface",
                    label=f"{descriptor.display_name}: {kind}",
                    version=SURFACE_IMPLEMENTED if descriptor.can_render(kind) else SURFACE_PLANNED,
                )
            )
        # A withdrawn surface is genuinely gone from the descriptor - that is
        # what withdrawing one means - but it must still be *visible* here.
        # The inventory is the only place the owner can see a component in
        # order to propose against it, so a surface that vanished the moment
        # it was withdrawn could never be granted back: it was governable in
        # one direction only. Listed as withdrawn, "grant it back" is the same
        # propose/run it always was.
        result.extend(_withdrawn_surface_components(descriptor, config.values))
    return tuple(result)


def _withdrawn_surface_components(descriptor, values) -> "tuple[ComponentSnapshot, ...]":
    """Every surface this agent has had withdrawn through governance, still
    listed - so the owner can see one and grant it back."""
    from src.control_center.interfaces import (
        SURFACE_COMPONENT_PREFIX, SURFACE_WITHDRAWN, SurfaceKind, surface_agent_key,
    )

    prefix = f"{SURFACE_COMPONENT_PREFIX}{surface_agent_key(descriptor.agent)}."
    out = []
    for component_id, state in sorted((values or {}).items()):
        if not str(component_id).startswith(prefix) or state != SURFACE_WITHDRAWN:
            continue
        try:
            kind = SurfaceKind(str(component_id)[len(prefix):])
        except ValueError:
            continue
        out.append(ComponentSnapshot(
            component_id=str(component_id),
            category="surface",
            label=f"{descriptor.display_name}: {kind} (withdrawn - grant it back with 'ecosystem {component_id} propose implemented ...')",
            version=SURFACE_WITHDRAWN,
        ))
    return tuple(out)


def _brain_components(settings: Settings) -> tuple[ComponentSnapshot, ...]:
    """Which reasoning *mode* is selected - Full ("llm") or Half ("null") -
    a genuinely different axis from *which vendor* answers (that is
    ``provider.llm_provider``, above). Conflating the two would be exactly
    the kind of destructive coupling this checkpoint's own brief forbids."""
    return (
        ComponentSnapshot(
            component_id="brain.manager",
            category="brain",
            label="Alfred's own reasoning mode",
            version=settings.brain,
        ),
        ComponentSnapshot(
            component_id="brain.specialist_default",
            category="brain",
            label="default specialist reasoning mode",
            version=settings.specialist_brain,
        ),
    )


def _system_components(manifest: UpgradeManifest) -> tuple[ComponentSnapshot, ...]:
    result = [
        ComponentSnapshot(
            component_id="system.core",
            category="system",
            label="Alfred OS (assembled product)",
            version=manifest.system_version,
        ),
        ComponentSnapshot(
            component_id="system.snapshot",
            category="system",
            label="Control Center export shape",
            version=str(manifest.snapshot_version),
        ),
        ComponentSnapshot(
            component_id="system.memory_schema",
            category="system",
            label="PersistentMemory on-disk shape",
            version=str(manifest.memory_schema_version),
        ),
        ComponentSnapshot(
            component_id="system.state_schema",
            category="system",
            label="Heartbeat StateStore on-disk shape",
            version=str(manifest.state_schema_version),
        ),
    ]
    for entry in manifest.visual_environments:
        result.append(
            ComponentSnapshot(
                component_id=f"system.visual_environment.{entry.surface}",
                category="system",
                label=f"visual environment ({entry.surface})",
                version=f"{entry.id}:{entry.version}",
            )
        )
    return tuple(result)


def _capability_config_components(config) -> tuple[ComponentSnapshot, ...]:
    """Real, currently-hardcoded capability configuration made
    governable (``handlers.CapabilityConfigHandler``) - never a
    fabricated "capability version" for capabilities whose actual
    *implementation* is fixed Python code and genuinely has no safe,
    real way to change without editing source."""
    return (
        ComponentSnapshot(
            component_id=CAPABILITY_GENERAL_CONVERSATION,
            category="capability",
            label="conversation-history context turns",
            version=config.get(CAPABILITY_GENERAL_CONVERSATION, DEFAULT_CONVERSATION_HISTORY_LIMIT),
            detail="how many recent turns open_conversation/knowledge_inquiry/live_information see as context",
        ),
    )


def _tool_config_components(config, settings: Settings) -> tuple[ComponentSnapshot, ...]:
    """Real, currently-hardcoded tool configuration made governable
    (``handlers.ToolConfigHandler``)."""
    return (
        ComponentSnapshot(
            component_id=TOOL_DOCUMENT_GENERATION,
            category="tool",
            label="document generation output directory",
            version=config.get(TOOL_DOCUMENT_GENERATION, settings.documents_dir),
        ),
    )


def _connection_adapter_components(config) -> tuple[ComponentSnapshot, ...]:
    """A connection's own real adapter *implementation* version -
    distinct from, and never touching, ``connection.connection``
    (which backend/account 52.27's own single-active axis has
    selected) or the additive per-service account state
    ``additive_connections`` already reports."""
    return (
        ComponentSnapshot(
            component_id=CONNECTION_GMAIL_ADAPTER,
            category="connection",
            label="Gmail adapter implementation version",
            version=config.get(CONNECTION_GMAIL_ADAPTER, DEFAULT_GMAIL_ADAPTER_VERSION),
            detail="v1: limit-only reads; v2: also honours a label filter",
        ),
    )


# Pre-Stage-9 Campaign 5 (HC-01): the orchestrator's identity component is
# keyed by its role, never by whichever name its identity document currently
# declares. "alfred" was the id before this and is still accepted everywhere a
# component id is resolved (LEGACY_MANAGER_COMPONENT_IDS), so a ledger written
# under it keeps working.
MANAGER_COMPONENT_ID = "manager"
LEGACY_MANAGER_COMPONENT_IDS = ("alfred",)


def is_manager_component(component_id: str) -> bool:
    return component_id == MANAGER_COMPONENT_ID or component_id in LEGACY_MANAGER_COMPONENT_IDS


def manager_component(settings: Settings) -> ComponentSnapshot:
    path = Path(settings.identity_path)
    name = "the Manager"
    if path.exists():
        try:
            from src.services.identity.identity import load_identity

            name = load_identity(path).name or name
        except Exception:  # noqa: BLE001 - an unreadable document still has a component
            pass
    return ComponentSnapshot(
        component_id=MANAGER_COMPONENT_ID,
        category="specialist",
        label=f"{name} (the Manager)",
        version=_file_version(path) if path.exists() else "(no identity document)",
        detail=str(path),
    )


# Kept for any caller written against the old name.
alfred_component = manager_component


def _image_technique_components(config) -> tuple[ComponentSnapshot, ...]:
    """Every image technique available now - built in, registered in code, or
    composed through governance - and every one the owner withdrew, as
    governable components (``image_technique.<id>``)."""
    from src.providers.media.image_techniques import (
        DISABLED, ENABLED, TECHNIQUE_COMPONENT_PREFIX, image_techniques,
    )

    values = dict(config.values)
    listed: list[ComponentSnapshot] = []
    for technique in image_techniques(values):
        component_id = TECHNIQUE_COMPONENT_PREFIX + technique.technique_id
        listed.append(ComponentSnapshot(
            component_id=component_id, category="image_technique", label=technique.label,
            version=values.get(component_id, ENABLED), detail=f"{technique.purpose}: {', '.join(technique.words[:4])}",
        ))
    shown = {component.component_id for component in listed}
    for component_id, value in values.items():
        if component_id.startswith(TECHNIQUE_COMPONENT_PREFIX) and component_id not in shown and value == DISABLED:
            listed.append(ComponentSnapshot(
                component_id=component_id, category="image_technique",
                label=component_id[len(TECHNIQUE_COMPONENT_PREFIX):] + " (withdrawn)", version=DISABLED,
            ))
    return tuple(listed)


def discover_components(
    runtime, manager, *, accounts_path=DEFAULT_ACCOUNTS_PATH, config_path=DEFAULT_CONFIG_PATH
) -> tuple[ComponentSnapshot, ...]:
    """The whole, real ecosystem inventory, right now - the one function
    every ``ecosystem`` Control Center action reads its facts from.

    ``accounts_path``/``config_path`` default to the real, canonical
    persisted files but are always explicit rather than re-imported
    fresh by name - the identical reason
    ``run_provider_command``/``run_connections_command``
    (scripts/control_center.py) thread their own module-level defaults
    through by parameter instead of relying on every module that reads
    them staying in sync by accident. A caller that monkeypatches its
    own copy of a constant (a test, or Control Center's own patched
    module during a session) must pass it here explicitly to have any
    effect.
    """
    settings = runtime.settings
    registry = build_ecosystem_registry(settings)
    store = load_credential_store(accounts_path)
    config = load_governed_config(config_path)

    components: list[ComponentSnapshot] = [manager_component(settings)]

    for key, specialist in runtime.specialists.items():
        components.append(_specialist_component(key, specialist.name, settings))

    for category in _PROVIDER_CATEGORIES:
        if registry.adapter_for(category) is not None:
            components.append(_provider_component(registry, category, store))

    for category in _CONNECTION_CATEGORIES:
        if registry.adapter_for(category) is not None:
            components.append(_connection_component(registry, category, store))

    provider_backend_names = {
        key
        for category in _PROVIDER_CATEGORIES
        if (adapter := registry.adapter_for(category)) is not None
        for key in (d.key for d in adapter.available())
    }
    components.extend(additive_connections(store, exclude=frozenset(provider_backend_names)))
    components.extend(_connection_adapter_components(config))

    components.extend(_brain_components(settings))
    components.extend(_surface_components(manager, config))

    components.extend(_capability_config_components(config))
    components.extend(_tool_config_components(config, settings))
    components.extend(_image_technique_components(config))

    manifest = upgrade_manifest(snapshot_version=SNAPSHOT_VERSION)
    components.extend(_system_components(manifest))

    # Pre-Stage-9 Campaign 12: components a registered handler declares that
    # nothing above discovered - a category added through register_handler.
    from src.services.evolution.handlers import registered_handlers

    known = {component.component_id for component in components}
    for handler in registered_handlers():
        for declared in handler.governed_components(config=config):
            if declared.component_id not in known:
                components.append(declared)
                known.add(declared.component_id)

    return tuple(components)


def find_component(
    runtime, manager, component_id: str, *, accounts_path=DEFAULT_ACCOUNTS_PATH, config_path=DEFAULT_CONFIG_PATH
) -> ComponentSnapshot | None:
    for component in discover_components(runtime, manager, accounts_path=accounts_path, config_path=config_path):
        if component.component_id == component_id:
            return component
    return None


@dataclass(frozen=True)
class MemoryDevelopmentSnapshot:
    """A *developmental* reading of one agent's memory - never a version,
    never a claim that more records means more capable. Reports real,
    qualitative facts already tracked by this project's own memory
    architecture (``MemoryCategory``, Phase 4's own confirmation-resolved
    corrections) rather than inventing a new "learning score".
    """

    agent_name: str
    total_records: int
    by_kind: dict
    corrections: int
    learning_sessions: int
    lessons: int

    def describe(self) -> str:
        lines = [
            f"memory development - {self.agent_name}:",
            f"  total retained records : {self.total_records}",
        ]
        for kind, count in sorted(self.by_kind.items()):
            lines.append(f"    {kind}: {count}")
        lines.append(f"  corrections resolved    : {self.corrections}")
        lines.append(f"  guided-learning sessions: {self.learning_sessions}")
        lines.append(f"  explicit lessons learned: {self.lessons}")
        return "\n".join(lines)


def memory_development_snapshot(agent_name: str, memory) -> MemoryDevelopmentSnapshot:
    """Read-only, real, and bounded - the same ``recall(limit=...)`` any
    other caller already uses, never a second store or a background
    process. ``limit`` is generous rather than unbounded so this remains
    a real, single, fast call regardless of how much has accumulated.
    """
    records = memory.recall(limit=2000)
    by_kind: dict[str, int] = {}
    corrections = 0
    learning_sessions = 0
    lessons = 0
    for record in records:
        by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
        if "resolved_ambiguity" in record.tags:
            corrections += 1
        if "learning_progress" in record.tags:
            learning_sessions += 1
        if record.kind == "lesson":
            lessons += 1
    return MemoryDevelopmentSnapshot(
        agent_name=agent_name,
        total_records=len(records),
        by_kind=by_kind,
        corrections=corrections,
        learning_sessions=learning_sessions,
        lessons=lessons,
    )


def brain_development_snapshot(agent_name: str, memory, *, reasoning_mode: str) -> str:
    """Brain development, honestly distinguished from provider
    replacement: which *vendor* answers is ``provider.llm_provider``'s
    own axis, entirely untouched by this. What genuinely develops in
    this architecture is what feeds Alfred's reasoning over time - the
    same corrections/lessons ``memory_development_snapshot`` already
    reports, reused here rather than duplicated, framed explicitly as
    Brain development alongside the current reasoning mode (Full/Half).

    Deliberately a read-only string, never a propose/apply target: no
    safe, real mechanism exists in this codebase to version Alfred's
    own reasoning *code* without editing source, and inventing one to
    look complete would be exactly the fabrication this checkpoint's
    own brief forbids.
    """
    snapshot = memory_development_snapshot(agent_name, memory)
    return (
        f"BRAIN DEVELOPMENT - {agent_name}\n"
        f"  reasoning mode           : {reasoning_mode} ('llm' = Full Brain, 'null' = Half Brain)\n"
        f"  corrections learned      : {snapshot.corrections}\n"
        f"  explicit lessons learned : {snapshot.lessons}\n"
        f"  guided-learning sessions : {snapshot.learning_sessions}\n"
        "\n"
        "  Read-only by design: which vendor answers is a provider (see "
        "'ecosystem provider.llm_provider'), never Brain development; and "
        "no safe, real way exists yet to version Alfred's own reasoning "
        "code without editing source, so this is reported, never proposed "
        "against."
    )
