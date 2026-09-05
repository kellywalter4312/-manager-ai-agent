"""Universal Ecosystem & Evolution Management Layer - the generic
contract every replaceable *category* of component in Alfred OS can be
described through, and the one small registry that indexes them all.

**What problem this solves.** By the time this module was written, this
project already had roughly a dozen independent axes - the LLM
provider, video-generation vendor, image/narration/composition trio,
speech engine, connection, avatar, knowledge source, live-info source,
celebration, calendar events - each following the *identical* shape:
an abstract contract, an honest null/default implementation, a small
``_BUILDERS`` dict keyed by name, an ``available_*()`` function, and a
``build_*()`` factory. That repetition is not an accident to clean up -
it is this project's own, already-proven extensibility mechanism,
independently reinvented once per axis because nothing tied the axes
together. This module is that missing tie: a generic way to *ask* "what
categories exist, what backends does each have, which is active, can I
validate a candidate, what depends on this" without a caller needing to
know a dozen different function names, and without forcing a future,
unimagined category to invent its own dozen-and-first copy of the
pattern from scratch.

**What this module deliberately does not do.** It does not replace any
existing factory, contract, or credential mechanism - ``LLMProvider``,
``VideoGenerationProvider``, ``AvatarProvider``, ``CredentialStore``
and every other axis keep exactly the shape they already have. This
module *wraps* them, generically, the same relationship
``src/composition.py`` already has to every individual factory: it
assembles, it does not reimplement. Nothing here imports ``src.manager``
or ``src.agents`` - a registry describing components has no need to
reason about Alfred, the same narrow-imports discipline
``ProviderAccount``/``CredentialStore`` already hold themselves to.

**Why a generic ``CategoryAdapter`` contract, not one bespoke class per
category.** The brief this module answers is explicit that forcing
every component into an identical artificial interface is wrong, and
that building a skeleton for every imaginable future category is also
wrong. The resolution is the same one every other axis in this project
already uses for *concrete* providers: one abstract contract
(``CategoryAdapter``), and as many or as few concrete adapters as there
are genuinely distinct categories - most of which, in practice, differ
only in which existing ``available_*``/``build_*``/credential functions
they close over, which is exactly what ``GenericCategoryAdapter`` below
exists to make cheap without writing a new class per axis.

**How this relates to Stage 11.A/11.B (``src/extensions.py``/
``src/evolution.py``) - found during CHECK, not duplicated.** Those two
modules already govern a *different* question: whether an abstract,
not-yet-built feature *declaration* (``Extension``, ``kind`` an open
string that already lists "provider"/"environment"/"connection" as
examples) or a proposed *change* to something existing
(``EvolutionProposal``) is approved to be marked active in an immutable
ledger - deliberately without ever importing a real provider, touching
a real credential, or constructing anything. This module answers the
complementary, concrete question: for an axis that already has a real,
working implementation, what backends exist right now, which is
active, and can a candidate genuinely be constructed - real
`build_*()` calls, real `CredentialStore` accounts, a real CLI. Stage
11.A's own docstring states this boundary explicitly for itself:
"Actually connecting a new... provider's module into the running
system remains... a reviewed, explicit change to a registration point"
- this module, and the real registration points it wraps
(``src/providers/*_factory.py``), are that reviewed, explicit,
already-existing mechanism. The two layers are neither merged nor
made to duplicate one another: a future, genuinely new provider is
still free to also be declared as a Stage 11.A ``Extension`` for
governance/audit purposes, entirely independently of whether it is
also registered here for real, operational use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Iterator


class LifecycleOperation(Enum):
    """One stage of the governed component lifecycle. Not every
    component supports every operation - ``CategoryAdapter.
    supported_operations()`` is what says which apply to a given
    category, never this enum itself."""

    DISCOVER = "discover"
    REGISTER = "register"
    CONFIGURE = "configure"
    AUTHENTICATE = "authenticate"
    VALIDATE = "validate"
    PREVIEW = "preview"
    SELECT = "select"
    ACTIVATE = "activate"
    USE = "use"
    MONITOR = "monitor"
    UPDATE = "update"
    REPLACE = "replace"
    DISABLE = "disable"
    REMOVE = "remove"
    RESTORE = "restore"
    VERIFY = "verify"


@dataclass(frozen=True)
class ComponentDescriptor:
    """One backend a category could be, or currently is, configured
    with - never a live object. The same "describe, never hand over a
    capability" discipline ``ToolSpec``/``ProviderAccount.describe()``
    already hold themselves to: a caller inspecting the ecosystem gets
    facts, never a constructed provider it could call through.
    """

    category: str
    key: str
    requires_credential: bool = False
    is_active: bool = False
    detail: str = ""

    def __post_init__(self) -> None:
        category = " ".join(str(self.category).split()).lower()
        if not category:
            raise ValueError("a ComponentDescriptor must name a category")
        object.__setattr__(self, "category", category)

        key = " ".join(str(self.key).split()).lower()
        if not key:
            raise ValueError("a ComponentDescriptor must name a key")
        object.__setattr__(self, "key", key)

    def describe(self) -> str:
        marker = "*" if self.is_active else " "
        cred = " (credential required)" if self.requires_credential else ""
        detail = f" - {self.detail}" if self.detail else ""
        return f"[{marker}] {self.key}{cred}{detail}"


@dataclass(frozen=True)
class ValidationOutcome:
    """The honest result of attempting to construct a real candidate -
    never a claim about a live external call. See this checkpoint's own
    reality standard: constructing a client is IMPLEMENTED/TEST-VERIFIED
    evidence, not LIVE EXTERNAL VERIFIED evidence, and this type's own
    ``detail`` should never blur the two."""

    ok: bool
    detail: str = ""

    @classmethod
    def success(cls, detail: str = "") -> "ValidationOutcome":
        return cls(ok=True, detail=detail)

    @classmethod
    def failure(cls, detail: str) -> "ValidationOutcome":
        if not str(detail).strip():
            raise ValueError("a failed ValidationOutcome must explain itself")
        return cls(ok=False, detail=detail)


class CategoryAdapter(ABC):
    """What one ecosystem category must supply to participate in the
    generic registry - never the other way around. The registry below
    consumes this contract and holds no category-specific logic of its
    own, which is what lets a future category join by implementing this
    one interface rather than by editing the registry.
    """

    @property
    @abstractmethod
    def category(self) -> str:
        """This category's stable name, e.g. "llm_provider"."""

    @abstractmethod
    def available(self) -> tuple[ComponentDescriptor, ...]:
        """Every backend this category could currently be configured
        with - the honest catalog, not merely the active one."""

    @abstractmethod
    def active(self) -> "ComponentDescriptor | None":
        """The backend currently configured for this category, or
        ``None`` when nothing is active - an honest absence, never a
        fabricated default."""

    def supported_operations(self) -> frozenset[LifecycleOperation]:
        """Which lifecycle stages make sense for this category. The
        conservative default (discover, select) is what a read-only or
        not-yet-validatable category gets; a real adapter overrides
        this to advertise what it genuinely supports rather than
        claiming a stage it cannot perform."""
        return frozenset({LifecycleOperation.DISCOVER, LifecycleOperation.SELECT})

    def validate(self, key: str, *, credential: "str | None" = None) -> ValidationOutcome:
        """Attempt real construction of ``key`` and report honestly.
        The default admits this category cannot validate - never
        silently reports success for something never attempted."""
        return ValidationOutcome.failure(f"'{self.category}' does not support validation")

    def dependents(self) -> tuple[str, ...]:
        """What - in plain language - depends on this category being
        configured, so a caller can be warned before disabling or
        replacing it. Empty by default: most categories have none."""
        return ()

    def describe(self) -> str:
        active = self.active()
        lines = [f"{self.category}:"]
        for descriptor in self.available():
            marker = "*" if active is not None and active.key == descriptor.key else " "
            cred = " (credential required)" if descriptor.requires_credential else ""
            lines.append(f"  [{marker}] {descriptor.key}{cred}")
        if not self.available():
            lines.append("  (no backends registered)")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} category={self.category!r}>"


class GenericCategoryAdapter(CategoryAdapter):
    """The one concrete ``CategoryAdapter`` most real categories need -
    built from the ``available_*``/``build_*`` functions each axis
    already has, never a new implementation of what those functions do.

    This is deliberately the *only* concrete adapter class this project
    ships: the brief this module answers explicitly forbids "hundreds
    of empty classes", and every existing axis already differs only in
    which functions it closes over - never in what shape a category
    adapter needs to be. A genuinely different-shaped category (one
    that cannot be described by "list backends, get the active one,
    build a candidate to validate it") is free to implement
    ``CategoryAdapter`` directly instead - the contract does not require
    this class.
    """

    def __init__(
        self,
        category: str,
        *,
        list_backends: Callable[[], Iterable[str]],
        active_key: Callable[[], "str | None"],
        build: "Callable[[str, str | None], object] | None" = None,
        requires_credential: "Callable[[str], bool]" = lambda key: False,
        dependents: Iterable[str] = (),
        operations: "frozenset[LifecycleOperation] | None" = None,
    ) -> None:
        self._category = " ".join(str(category).split()).lower()
        if not self._category:
            raise ValueError("a GenericCategoryAdapter must name a category")
        self._list_backends = list_backends
        self._active_key = active_key
        self._build = build
        self._requires_credential = requires_credential
        self._dependents = tuple(dependents)
        self._operations = operations

    @property
    def category(self) -> str:
        return self._category

    def available(self) -> tuple[ComponentDescriptor, ...]:
        active = self._active_key()
        return tuple(
            ComponentDescriptor(
                category=self._category,
                key=key,
                requires_credential=self._requires_credential(key),
                is_active=(active is not None and key == active),
            )
            for key in self._list_backends()
        )

    def active(self) -> "ComponentDescriptor | None":
        key = self._active_key()
        if key is None:
            return None
        return ComponentDescriptor(
            category=self._category,
            key=key,
            requires_credential=self._requires_credential(key),
            is_active=True,
        )

    def supported_operations(self) -> frozenset[LifecycleOperation]:
        if self._operations is not None:
            return self._operations
        ops = {LifecycleOperation.DISCOVER, LifecycleOperation.SELECT}
        if self._build is not None:
            ops |= {LifecycleOperation.VALIDATE, LifecycleOperation.ACTIVATE}
        return frozenset(ops)

    def validate(self, key: str, *, credential: "str | None" = None) -> ValidationOutcome:
        if self._build is None:
            return ValidationOutcome.failure(
                f"'{self._category}' has no real construction path to validate against"
            )
        try:
            self._build(key, credential)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch, mirrors ToolSet.invoke
            return ValidationOutcome.failure(f"{type(exc).__name__}: {exc}")
        return ValidationOutcome.success(f"'{key}' constructed successfully")

    def dependents(self) -> tuple[str, ...]:
        return self._dependents


class EcosystemRegistry:
    """The one place every registered category can be found, listed,
    inspected, and validated through - never a second source of truth
    for any individual axis's own configuration. A caller that wants to
    *use* a component still goes through that axis's own real factory
    (``build_provider``, ``build_media``, ...); this registry answers
    "what exists and what is active", never "give me a working
    instance".
    """

    def __init__(self) -> None:
        self._adapters: dict[str, CategoryAdapter] = {}

    def register(self, adapter: CategoryAdapter) -> None:
        """Add or replace one category's adapter. Replacing is
        deliberate, not an error - the same "re-registering is a
        replacement, never a silent duplicate" rule
        ``CredentialStore.add`` already follows."""
        self._adapters[adapter.category] = adapter

    def categories(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def adapter_for(self, category: str) -> "CategoryAdapter | None":
        return self._adapters.get(" ".join(str(category).split()).lower())

    def __iter__(self) -> Iterator[CategoryAdapter]:
        return iter(self._adapters[name] for name in self.categories())

    def __len__(self) -> int:
        return len(self._adapters)

    def impact_of(self, category: str) -> tuple[str, ...]:
        """What - in plain language - would be affected by disabling
        or replacing ``category``. An unknown category has no adapter
        to ask, so it honestly reports nothing rather than guessing."""
        adapter = self.adapter_for(category)
        return adapter.dependents() if adapter is not None else ()

    def describe_all(self) -> str:
        if not self._adapters:
            return "no ecosystem categories are registered"
        return "\n\n".join(adapter.describe() for adapter in self)
