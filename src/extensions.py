"""Stage 11.A - the extension/capability update system.

Governed infrastructure for adding a new feature to Alfred OS over time -
a specialist agent, a tool, a provider, an interface, an environment, a
connection, a multimodal capability, or a kind of feature that does not
exist yet - without rewriting or coupling the canonical architecture:

    Agent -> Identity -> Brain -> Memory -> Personality -> Voice
          -> Capabilities -> Connections

An extension never replaces any of those. It is a small, versioned,
typed declaration of what a feature *is* and what it needs, governed
through the same shape this project's Stage 6-9 Upgrade Manifest / Planner
/ Executor already established for schema migrations:

    DISCOVER    ExtensionRegistry.discover()  - what could be activated
         |
         v
    VALIDATE    validate_extension()          - is the declaration well-formed
         |
         v
    PLAN        plan_extension_activation()   - is it reachable from here
         |
         v
    APPROVE     an explicit, external decision - never made here
         |
         v
    APPLY       apply_extension()             - record it, only if approved
         |
         v
    VERIFY      verify_extension_activation()  - independently re-check
         |
         v
    ACTIVATE    run_extension_lifecycle()'s own final step

**This is the infrastructure, not the feature.** 11.A answers "can this
declared extension be safely marked active" - it does not import, execute,
download, or wire in a single line of the extension's own implementation.
Actually connecting a new specialist agent's class or a new provider's
module into the running system remains exactly what it is today: a
reviewed, explicit change to a registration point like
``src.agents.specialists.SPECIALISTS`` or
``src.providers.speech_factory._BUILDERS``. What this module adds is a
governed ledger to validate, approve and record that decision against -
never a mechanism that runs arbitrary code on an extension's behalf. That
boundary is deliberate: 11.B, a separate and later milestone, is where a
governed self-improvement layer would be built *on top of* this ledger;
11.A does not reach for it.

**A generic contract, not five hardcoded categories.** ``Extension.kind``
is a plain string, never a closed enum - the same reason ``AgentSpec.
capabilities`` and ``VoiceProfile.style`` are open rather than fixed.
Nothing in this module's validate/plan/apply/verify functions branches on
``kind`` at all; a "specialist_agent" extension and a "provider" extension
already share the exact same governance path, which is what lets a
genuinely new category be represented tomorrow without editing this file.
``metadata`` is the same idea applied to *implementation-specific* facts -
opaque here, carried and never interpreted, exactly the role
``VoiceProfile.style`` already plays for a provider-specific voice id.

**Reuses, never re-derives.** Compatibility is checked against
``src.core.versioning.SYSTEM_VERSION`` - the one real, existing product
version - not a second version concept invented for extensions. Nothing
here duplicates ``UpgradeManifest``/``UpgradePlan``/``execute_upgrade``:
those govern persisted *schema* migrations for stores that already exist;
this governs whether a *new* feature declaration may be marked active.
Different questions, deliberately kept as two small modules rather than
one that answers both badly.

**Touches nothing it governs.** This module imports nothing from
``src.manager``, ``src.services.memory``, ``src.services.identity``,
``src.channels``, or ``src.providers`` - not even to name one. An
extension's Brain, Memory, VoiceProfile and every other agent live
entirely outside this ledger, so adding, replacing or removing an
extension here cannot touch any of them, structurally, not by care.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from src.core.versioning import SYSTEM_VERSION


class ExtensionStatus(str, Enum):
    """Six outcomes, chosen to mean something - the same discipline
    UpgradeStatus already applies to a migration plan."""

    INVALID = "invalid"
    INCOMPATIBLE = "incompatible"
    MISSING_DEPENDENCY = "missing_dependency"
    ALREADY_ACTIVE = "already_active"
    READY = "ready"
    REPLACEMENT_READY = "replacement_ready"


# The only two statuses that describe real, approvable work - mirrors
# upgrade_lifecycle.py's own _REQUIRES_APPROVAL exactly, for extensions.
_EXECUTABLE = (ExtensionStatus.READY, ExtensionStatus.REPLACEMENT_READY)


@dataclass(frozen=True)
class Extension:
    """One declared feature, not yet active. A description, never an
    instruction to run anything - the same stance a VoiceProfile takes
    toward the voice it describes.

    ``key`` is stable and technical (never a display name); ``kind`` is an
    open vocabulary describing what sort of thing this is - "capability",
    "specialist_agent", "provider", "interface", "environment",
    "connection", "multimodal_feature" are today's examples, never an
    exhaustive or validated list. ``provides`` names what this extension
    offers once active (capability words, a provider name, anything a
    consumer would look for). ``requires`` names other extensions' *keys*
    that must already be active - not a package name, not a version
    string parsed out of a sentence. ``min_system_version``/
    ``max_system_version`` bound which Alfred OS versions this extension
    is compatible with; either or both may be None, meaning no bound in
    that direction. ``metadata`` is opaque, kind-specific detail this
    module never reads - where an actual implementation pointer (a module
    path, an identity document location, an env var name) lives without
    this contract having to know what a provider or a specialist is.
    """

    key: str
    name: str
    kind: str
    version: str
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    min_system_version: str | None = None
    max_system_version: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provides", tuple(self.provides))
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "provides": list(self.provides),
            "requires": list(self.requires),
            "min_system_version": self.min_system_version,
            "max_system_version": self.max_system_version,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ActiveExtension:
    """What the ledger actually records about one active extension - just
    enough to answer "is this key active, and at what version", never a
    copy of the full declaration."""

    key: str
    version: str
    kind: str


@dataclass(frozen=True)
class ExtensionLedger:
    """What is currently active. This system's own, narrow analogue of
    ``UpgradeManifest``: a plain record of fact, immutable, never a second
    agent registry and never persisted by this module - a caller that
    needs this to survive a restart owns that decision and that store,
    exactly as every other persisted fact in this project is owned by one
    explicit store rather than by the module that computes it.
    """

    active: tuple[ActiveExtension, ...] = ()

    def get(self, key: str) -> ActiveExtension | None:
        for entry in self.active:
            if entry.key == key:
                return entry
        return None

    def with_active(self, entry: ActiveExtension) -> "ExtensionLedger":
        """A new ledger with ``entry`` active - replacing any existing
        entry of the same key (this is what makes a version replacement
        and a first activation the same operation)."""
        remaining = tuple(e for e in self.active if e.key != entry.key)
        return ExtensionLedger(active=remaining + (entry,))

    def without(self, key: str) -> "ExtensionLedger":
        """A new ledger with ``key`` no longer active - removal. Nothing
        else about the ledger, and nothing outside it, is touched."""
        return ExtensionLedger(active=tuple(e for e in self.active if e.key != key))

    def to_dict(self) -> dict:
        return {
            "active": [
                {"key": e.key, "version": e.version, "kind": e.kind} for e in self.active
            ]
        }


class ExtensionRegistry:
    """DISCOVER: a plain, explicit catalog of candidate extensions.

    Populated by whoever assembles a deployment - a composition root, a
    test - never by scanning a filesystem, importing a plugin directory,
    or otherwise running code nobody explicitly registered. That absence
    is deliberate: an auto-discovering loader is exactly the "unrestricted
    self-modifying" shape this stage was told not to build. Adding a
    candidate extension is a one-line ``register`` call, the same shape
    ``SPECIALISTS``/``_BUILDERS`` already use elsewhere in this project.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, Extension] = {}

    def register(self, extension: Extension) -> None:
        self._by_key[extension.key] = extension

    def discover(self) -> tuple[Extension, ...]:
        """Every candidate extension currently registered - discovery,
        not activation. Nothing about calling this changes what is
        active."""
        return tuple(self._by_key.values())

    def get(self, key: str) -> Extension | None:
        return self._by_key.get(key)


@dataclass(frozen=True)
class ValidationResult:
    """Whether one Extension's own declaration is well-formed - a fact
    about the extension alone, never about the environment it might run
    in. Compatibility and dependencies are a different question, answered
    by plan_extension_activation below."""

    valid: bool
    reason: str


def validate_extension(extension: Extension) -> ValidationResult:
    """VALIDATE: the one place a declaration is checked for well-
    formedness, before any compatibility question is asked of it - the
    same role ``upgrade_planner._malformed`` plays for an UpgradeRequest.
    """
    if not extension.key.strip():
        return ValidationResult(False, "an extension must have a non-blank key")
    if not extension.name.strip():
        return ValidationResult(False, "an extension must have a non-blank name")
    if not extension.kind.strip():
        return ValidationResult(False, "an extension must declare a kind")
    if not extension.version.strip():
        return ValidationResult(False, "an extension must declare a version")
    if extension.key in extension.requires:
        return ValidationResult(False, "an extension cannot require itself")
    for bound_name, bound in (
        ("min_system_version", extension.min_system_version),
        ("max_system_version", extension.max_system_version),
    ):
        if bound is not None and not bound.strip():
            return ValidationResult(False, f"{bound_name}, if given, must not be blank")
    return ValidationResult(True, "well-formed")


def _version_tuple(value: str) -> tuple[int, ...]:
    """A small, dependency-free version comparator - "0.1.0" ->
    (0, 1, 0). No external package (no ``packaging``) for the same reason
    this whole project avoids a dependency for something a few lines of
    stdlib already do. A non-numeric segment sorts as 0, so a version
    string this parser does not fully understand still compares rather
    than crashing - the same fail-honest-not-fail-loud stance
    ``Availability`` already takes.
    """
    parts = []
    for segment in value.strip().split("."):
        digits = "".join(ch for ch in segment if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _system_version_compatible(extension: Extension, system_version: str) -> bool:
    current = _version_tuple(system_version)
    if extension.min_system_version is not None and current < _version_tuple(extension.min_system_version):
        return False
    if extension.max_system_version is not None and current > _version_tuple(extension.max_system_version):
        return False
    return True


@dataclass(frozen=True)
class ExtensionPlan:
    """PLAN's whole answer: a status, why, and the extension it is about."""

    status: ExtensionStatus
    reason: str
    extension: Extension

    def to_dict(self) -> dict:
        return {"status": self.status.value, "reason": self.reason, "extension": self.extension.to_dict()}


def plan_extension_activation(
    extension: Extension,
    *,
    ledger: ExtensionLedger,
    system_version: str = SYSTEM_VERSION,
) -> ExtensionPlan:
    """PLAN: is ``extension`` reachable from ``ledger`` as it stands -
    without activating, applying, or touching anything.

    Deterministic and read-only, the same guarantee ``plan_upgrade``
    already makes for a migration plan: the same extension, ledger and
    system version always produce the same plan.
    """
    validity = validate_extension(extension)
    if not validity.valid:
        return ExtensionPlan(ExtensionStatus.INVALID, validity.reason, extension)

    if not _system_version_compatible(extension, system_version):
        return ExtensionPlan(
            ExtensionStatus.INCOMPATIBLE,
            f"requires system_version between {extension.min_system_version!r} and "
            f"{extension.max_system_version!r}, this installation is {system_version!r}",
            extension,
        )

    missing = [key for key in extension.requires if ledger.get(key) is None]
    if missing:
        return ExtensionPlan(
            ExtensionStatus.MISSING_DEPENDENCY,
            f"requires extension(s) not currently active: {', '.join(missing)}",
            extension,
        )

    existing = ledger.get(extension.key)
    if existing is not None:
        if existing.version == extension.version:
            return ExtensionPlan(
                ExtensionStatus.ALREADY_ACTIVE,
                f"'{extension.key}' is already active at version {extension.version}",
                extension,
            )
        return ExtensionPlan(
            ExtensionStatus.REPLACEMENT_READY,
            f"'{extension.key}' is active at {existing.version}; {extension.version} would replace it",
            extension,
        )

    return ExtensionPlan(
        ExtensionStatus.READY,
        f"'{extension.key}' is compatible and its dependencies are satisfied",
        extension,
    )


@dataclass(frozen=True)
class ExtensionApplyResult:
    """APPLY's whole answer: whether the ledger update happened, and the
    resulting ledger either way (unchanged on failure)."""

    success: bool
    failure_reason: str | None
    ledger: ExtensionLedger

    def to_dict(self) -> dict:
        return {"success": self.success, "failure_reason": self.failure_reason, "ledger": self.ledger.to_dict()}


def apply_extension(
    plan: ExtensionPlan, *, approved: bool, ledger: ExtensionLedger
) -> ExtensionApplyResult:
    """APPLY: record ``plan.extension`` as active - only for a plan that
    describes real work (READY or REPLACEMENT_READY), and only when
    ``approved`` is True.

    ``approved`` has no default, exactly as ``execute_upgrade``'s does
    not: a caller must decide and say so, every call. A plan that is
    INVALID, INCOMPATIBLE, MISSING_DEPENDENCY or ALREADY_ACTIVE is refused
    outright regardless of ``approved`` - there is no executable work for
    approval to authorise, the same reasoning ``execute_upgrade`` already
    applies to INVALID/UNSUPPORTED plans.
    """
    if plan.status not in _EXECUTABLE:
        return ExtensionApplyResult(
            False, f"plan status {plan.status.value!r} describes no activatable work", ledger
        )
    if not approved:
        return ExtensionApplyResult(False, "not approved", ledger)

    entry = ActiveExtension(key=plan.extension.key, version=plan.extension.version, kind=plan.extension.kind)
    return ExtensionApplyResult(True, None, ledger.with_active(entry))


def verify_extension_activation(
    extension: Extension, ledger: ExtensionLedger
) -> tuple[bool, str | None]:
    """VERIFY: independently confirm the extension actually landed in the
    ledger at the intended version - never a re-statement of
    ``ExtensionApplyResult.success`` alone, the same "the executor
    returned success" vs "the system is correct" distinction
    ``upgrade_lifecycle.py`` already draws for a migration.
    """
    found = ledger.get(extension.key)
    if found is None:
        return False, f"'{extension.key}' is not present in the ledger after apply"
    if found.version != extension.version:
        return False, (
            f"'{extension.key}' is active at {found.version!r}, expected {extension.version!r}"
        )
    return True, None


@dataclass(frozen=True)
class ExtensionActivationResult:
    """One governed activation's whole answer, from plan to verified (or
    refused) outcome - mirrors ``UpgradeLifecycleResult``'s shape and its
    three-state ``verified`` for the same reason: "not attempted" and
    "attempted and failed" are different facts.
    """

    status: ExtensionStatus
    success: bool
    approved: bool
    applied: bool
    verified: bool | None
    activated: bool
    reason: str
    plan: ExtensionPlan
    before_ledger: ExtensionLedger
    after_ledger: ExtensionLedger

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "success": self.success,
            "approved": self.approved,
            "applied": self.applied,
            "verified": self.verified,
            "activated": self.activated,
            "reason": self.reason,
            "plan": self.plan.to_dict(),
            "before_ledger": self.before_ledger.to_dict(),
            "after_ledger": self.after_ledger.to_dict(),
        }


def run_extension_lifecycle(
    extension: Extension,
    *,
    approved: bool,
    ledger: ExtensionLedger,
    system_version: str = SYSTEM_VERSION,
) -> ExtensionActivationResult:
    """The whole governed lifecycle in one call: VALIDATE (inside PLAN) ->
    PLAN -> the APPROVE gate -> APPLY -> VERIFY -> ACTIVATE.

    ``approved`` has no default, exactly as ``run_upgrade``'s does not -
    there is no environment variable, no status-based shortcut, and no
    path here that supplies consent on a caller's behalf. Activation
    (``activated=True``) is only ever set after ``apply_extension``
    succeeded *and* ``verify_extension_activation`` independently
    confirmed it - never from ``apply``'s own report alone.
    """
    plan = plan_extension_activation(extension, ledger=ledger, system_version=system_version)

    if plan.status not in _EXECUTABLE:
        success = plan.status is ExtensionStatus.ALREADY_ACTIVE
        return ExtensionActivationResult(
            status=plan.status,
            success=success,
            approved=approved,
            applied=False,
            verified=None,
            activated=success,
            reason=plan.reason,
            plan=plan,
            before_ledger=ledger,
            after_ledger=ledger,
        )

    result = apply_extension(plan, approved=approved, ledger=ledger)

    if not result.success:
        return ExtensionActivationResult(
            status=plan.status,
            success=False,
            approved=approved,
            applied=False,
            verified=None,
            activated=False,
            reason=result.failure_reason or "apply failed",
            plan=plan,
            before_ledger=ledger,
            after_ledger=result.ledger,
        )

    verified, verify_reason = verify_extension_activation(extension, result.ledger)
    return ExtensionActivationResult(
        status=plan.status,
        success=verified,
        approved=approved,
        applied=True,
        verified=verified,
        activated=verified,
        reason="activated and independently verified" if verified else (
            verify_reason or "post-apply verification failed"
        ),
        plan=plan,
        before_ledger=ledger,
        after_ledger=result.ledger,
    )


def deactivate_extension(key: str, *, ledger: ExtensionLedger) -> ExtensionLedger:
    """Removal - pure ledger bookkeeping. Touches nothing outside the
    ledger, so it cannot corrupt a Brain, a Memory, a VoiceProfile, or any
    other agent by construction, not by care: this module never held a
    reference to any of them in the first place.
    """
    return ledger.without(key)
