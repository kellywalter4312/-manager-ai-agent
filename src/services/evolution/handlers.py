"""Component-specific evolution behavior, supplied by the component -
never branched on inside the governed engine itself.

``src/evolution.py``'s own docstring is explicit that wiring an ACTIVE
proposal's change into the real, running component it names is "a
reviewed, explicit change elsewhere" - this module is that elsewhere,
organised so a *future* component never requires touching the engine
(``ecosystem_evolution.py``) to participate: it registers one small
``EvolutionHandler`` naming which component ids it owns, and the engine
only ever asks "which handler owns this id" and calls it. Nothing in
``ecosystem_evolution.py`` branches on a category string; every branch
lives here, one handler at a time.

**No fake generic "apply anything".** Every handler below performs one,
specific, real, independently-verifiable side effect for a named
category it genuinely understands - never a blind "write this string
somewhere" pass-through. A component with no registered handler is
refused honestly by the engine, by name, never silently accepted.

**Five real handlers, backing the five categories this repository's
own current ecosystem can genuinely support today:**

    provider.<category>          an account's real ``model`` field
                                  (52.26/52.27's own CredentialStore)
    alfred / specialist.<key>    a specialist's (or Alfred's own) real
                                  identity document content - staged,
                                  backed up, re-parsed to verify, and
                                  restorable byte-for-byte
    capability.general_conversation
                                  how many recent conversation turns a
                                  reasoning-dependent capability is
                                  offered as context (a real, currently
                                  hardcoded value, made governable)
    tool.document_generation     the real output directory
                                  DocumentGenerationTool writes to
    connection.<backend>         a built-in connection's own real adapter
                                  version, for any connection that declares
                                  more than one (``connection_factory.
                                  connection_adapter_versions``) - never the
                                  account/credential, which stays exactly as
                                  52.27 already governs it, structurally
                                  untouched by this handler.

**What every handler answers for the engine (Phase 11).** Besides
``apply``/``rollback``: ``observe`` - the component's real current version,
read from where the handler actually keeps it, so the engine verifies the
real state instead of its own ledger (a handler that reports success without
changing anything is caught, not activated); ``default_version`` - what the
component is when that store holds nothing for it; ``can_roll_back``; and
``activation`` - how an applied change reaches a running session, so no
surface has to branch on a category's name to know.

Every other category (Brain reasoning code, a capability's own
implementation, a tool's own class, a connection's own credential) has
no handler here and is refused by the engine with a precise, honest
reason - not because this module could not think of one, but because
this repository has no safe, real way to change any of those without
editing source code, and this checkpoint's own brief forbids pretending
otherwise.
"""

from __future__ import annotations

import hashlib
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.evolution import EvolutionProposal
from src.services.credentials.account import ProviderAccount
from src.services.credentials.account_store_file import load_credential_store, save_credential_store
from src.services.evolution.governed_config import load_governed_config, save_governed_config
from src.services.identity.specialist import identity_path, load_specialist_identity


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class HandlerContext:
    """Everything a handler might need to perform its one real side
    effect - never more than that, and never the engine's own ledger or
    persistence, which stays the engine's job."""

    settings: object
    accounts_path: "Path | str"
    config_path: "Path | str"
    staging_dir: "Path | str" = Path("data") / "evolution" / "specialist_staging"
    backups_dir: "Path | str" = Path("data") / "evolution" / "specialist_backups"


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    detail: str


# How an applied (or rolled-back) change reaches a running session - declared
# by the handler that owns the component, read by whichever surface applies
# it live. Mechanisms, not component names.
ACTIVATION_REASONING = "reasoning"            # rebind the session's reasoning
ACTIVATION_PER_TURN = "per_turn"              # read fresh on every turn already
ACTIVATION_ON_READ = "described_on_read"      # described fresh on every read
ACTIVATION_NEXT_SESSION = "next_session"      # read when a session starts
ACTIVATION_BACKENDS = "rebind_backends"       # rebuild the session's backends
ACTIVATIONS = (
    ACTIVATION_REASONING, ACTIVATION_PER_TURN, ACTIVATION_ON_READ,
    ACTIVATION_NEXT_SESSION, ACTIVATION_BACKENDS,
)


class EvolutionHandler(ABC):
    """What one real, governed component category must supply. The
    engine holds a list of these and asks each, in turn, "do you own
    this id" - never the reverse."""

    @abstractmethod
    def applies_to(self, component_id: str) -> bool: ...

    @abstractmethod
    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult: ...

    @abstractmethod
    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult: ...

    def initial_version(self, component_id: str) -> "str | None":
        """The version a component this handler owns starts from when nothing
        has recorded one yet - None (the default) keeps "discover it first",
        so only a category that can genuinely begin from nothing supplies one
        (Pre-Stage-9 Campaign 5)."""
        return None

    def governed_components(self, *, config) -> "tuple":
        """The components this handler governs that the inventory does not
        already discover - ``ComponentSnapshot`` values, read from the governed
        ``config``. Empty by default: every built-in category is discovered by
        the inventory itself. A category added through ``register_handler``
        declares its own here, so it joins the Control Center's lifecycle with
        no engine change (Pre-Stage-9 Campaign 12)."""
        return ()

    def observe(self, component_id: str, *, context: HandlerContext) -> "str | None":
        """The component's real current version, read from where this handler
        actually keeps it - or None when that store holds nothing for it.

        The default reads the governed configuration, which is where every
        configuration-backed handler writes; a handler that keeps its state
        anywhere else must say so here. The engine never activates a change
        this cannot confirm, so a handler that does not override it and
        writes elsewhere is refused honestly rather than trusted."""
        value = load_governed_config(context.config_path).get(component_id)
        return None if value is None else str(value)

    def default_version(self, component_id: str) -> "str | None":
        """What the component truly is when this handler's store holds
        nothing for it - None (the default) when that cannot be known from
        the store alone. Used to find drift between the record and reality,
        so it must be the real effective value, never a guess."""
        return None

    def absence_restores(self, component_id: str, version: str) -> bool:
        """Whether removing the stored value is how this handler restores
        ``version`` on rollback (so "nothing stored" confirms it). False by
        default: a rollback is confirmed by reading the version back."""
        return False

    def can_roll_back(self, component_id: str) -> bool:
        """Whether a governed rollback of this component is possible."""
        return True

    def activation(self, component_id: str) -> str:
        """How an applied change to this component reaches a running session
        (one of ``ACTIVATIONS``)."""
        return ACTIVATION_BACKENDS


# What the inventory shows for an account that pins no model of its own - a
# label, never a model name, so it must never be written back as one.
DEFAULT_MODEL_LABEL = "(default model)"


def _model_segment(version: str) -> "str | None":
    """A ``provider.*`` component's version is always the full
    ``backend:account_id:model`` composite (``inventory.py``'s own
    ``ComponentSnapshot.version`` format) - this extracts just the
    model segment, the only part ever actually written."""
    return version.rsplit(":", 1)[-1] if ":" in version else None


def _stored_model(segment: "str | None") -> "str | None":
    """The model to store for a version's model segment: the default-model
    label means "no model pinned", never a model literally called that."""
    return None if segment in (None, DEFAULT_MODEL_LABEL) else segment


class ProviderModelHandler(EvolutionHandler):
    """A provider's active account's ``model`` field - reusing
    ``CredentialStore``/``ProviderAccount`` (52.26/52.27) verbatim,
    never a second credential mechanism. Written, then independently
    re-read from disk before reporting success - the same "never
    restate the executor's own report" discipline
    ``src/upgrade_lifecycle.py`` already holds itself to."""

    def applies_to(self, component_id: str) -> bool:
        return component_id.startswith("provider.")

    def _resolve(self, proposal: EvolutionProposal, context: HandlerContext):
        return self._resolve_category(proposal.target, context)

    def _resolve_category(self, component_id: str, context: HandlerContext):
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        category = component_id.removeprefix("provider.")
        registry = build_ecosystem_registry(context.settings)
        adapter = registry.adapter_for(category)
        if adapter is None:
            return None, None, f"'{category}' is not a real, registered provider category"
        active = adapter.active()
        if active is None:
            return None, None, f"'{category}' has no active backend to change"
        return category, active.key, None

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        category, backend, error = self._resolve(proposal, context)
        if error:
            return HandlerResult(False, error)

        segment = _model_segment(proposal.proposed_version)
        if segment is None:
            return HandlerResult(
                False,
                f"'{proposal.proposed_version}' does not name a model - a provider "
                "proposal's version must be the full 'backend:account_id:model' form",
            )
        new_model = _stored_model(segment)

        store = load_credential_store(context.accounts_path)
        account = store.active(backend)
        if account is None:
            return HandlerResult(
                False,
                f"'{backend}' has no account on record - add one first "
                f"(provider {category} add {backend} <account-id> <credential>)",
            )

        updated = ProviderAccount(
            provider=account.provider, account_id=account.account_id,
            credential=account.credential, model=new_model,
        )
        store.add(updated, activate=True)
        save_credential_store(store, context.accounts_path)

        reloaded = load_credential_store(context.accounts_path)
        on_disk = reloaded.active(backend)
        if on_disk is None or on_disk.model != new_model:
            return HandlerResult(
                False,
                f"wrote a new model for {backend}/{account.account_id}, but the "
                f"independent re-read did not confirm it landed (found {on_disk.model if on_disk else None!r})",
            )
        return HandlerResult(True, f"updated {backend}/{account.account_id}'s model to '{new_model}' (independently re-read from disk)")

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        category, backend, error = self._resolve(proposal, context)
        if error:
            return HandlerResult(False, error)

        store = load_credential_store(context.accounts_path)
        account = store.active(backend)
        if account is None:
            return HandlerResult(False, f"'{backend}' has no account on record to roll back")

        restored_model = _stored_model(_model_segment(proposal.current_version))
        restored = ProviderAccount(
            provider=account.provider, account_id=account.account_id,
            credential=account.credential, model=restored_model,
        )
        store.add(restored, activate=True)
        save_credential_store(store, context.accounts_path)
        return HandlerResult(True, f"restored {backend}/{account.account_id}'s model to {restored_model or DEFAULT_MODEL_LABEL!r}")

    def observe(self, component_id: str, *, context: HandlerContext) -> "str | None":
        """The active account's real ``backend:account_id:model``, read back
        from the credential store - the same composite the inventory reports."""
        category, backend, error = self._resolve_category(component_id, context)
        if error:
            return None
        account = load_credential_store(context.accounts_path).active(backend)
        if account is None:
            return None
        return f"{backend}:{account.account_id}:{account.model or DEFAULT_MODEL_LABEL}"


class SpecialistIdentityHandler(EvolutionHandler):
    """A specialist's (or Alfred's own) real identity document content -
    never merely a hash. The *proposed content itself* is staged to
    disk at propose time (see ``ecosystem_evolution.propose_identity_content``),
    keyed by proposal id, because free-form markdown prose has nowhere
    safe to live inside ``EvolutionProposal``'s own fixed, short-string
    schema. Apply backs up the real, current file byte-for-byte before
    ever overwriting it, then re-parses the new content to verify it is
    a genuinely loadable identity - a malformed document fails apply
    outright, never lands half-written. Rollback restores the exact
    backed-up bytes.

    Capabilities are structurally untouched by anything this handler
    does: a specialist's real capabilities come from registered code
    (``BASE_CAPABILITIES``/analyzers in e.g. ``src/agents/email_agent.py``),
    never from the identity document - so evolving role/purpose/
    description/voice here can never silently change what a specialist
    can actually do.
    """

    def applies_to(self, component_id: str) -> bool:
        from src.services.evolution.inventory import is_manager_component

        return is_manager_component(component_id) or component_id.startswith("specialist.")

    def _resolve_path(self, component_id: str, settings) -> Path:
        from src.services.evolution.inventory import is_manager_component

        if is_manager_component(component_id):
            return Path(settings.identity_path)
        key = component_id.removeprefix("specialist.")
        return identity_path(settings.identities_dir, key)

    def _staged_content_path(self, proposal_id: str, context: HandlerContext) -> Path:
        return Path(context.staging_dir) / f"{proposal_id}.md"

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        staged = self._staged_content_path(proposal.proposal_id, context)
        if not staged.exists():
            return HandlerResult(
                False,
                f"no staged content found for '{proposal.proposal_id}' - propose this "
                "evolution with the real new document content first",
            )
        # Read and written as raw bytes throughout, deliberately never
        # opened in text mode: text mode's newline translation would
        # silently change the bytes on disk between staging and the
        # real file, making a genuinely correct write fail its own
        # independent verification purely from a line-ending mismatch.
        new_bytes = staged.read_bytes()
        new_text = new_bytes.decode("utf-8")

        # Verify the new content is a genuinely loadable identity BEFORE
        # touching the real file - a malformed document must never
        # land, even briefly.
        try:
            from src.services.evolution.inventory import is_manager_component

            if is_manager_component(proposal.target):
                from src.services.identity.identity import parse_identity

                parse_identity(new_text)
            else:
                from src.services.identity.specialist import parse_specialist_identity

                parse_specialist_identity(new_text)
        except Exception as exc:  # noqa: BLE001 - a malformed document is a real, honest apply failure
            return HandlerResult(False, f"the proposed document does not parse as a valid identity: {exc}")

        real_path = self._resolve_path(proposal.target, context.settings)
        if not real_path.exists():
            return HandlerResult(False, f"no real identity document exists at '{real_path}' to evolve")

        backups_dir = Path(context.backups_dir)
        backups_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backups_dir / f"{proposal.proposal_id}-{_utc_now().strftime('%Y%m%dT%H%M%S%f')}.md"
        shutil.copy2(real_path, backup_path)

        real_path.write_bytes(new_bytes)

        # Independent re-check: re-read and re-parse the file just
        # written, and confirm its content hash actually matches the
        # proposal's own claimed proposed_version.
        on_disk = real_path.read_bytes()
        on_disk_hash = hashlib.sha256(on_disk).hexdigest()[:12]
        if on_disk_hash != proposal.proposed_version:
            shutil.copy2(backup_path, real_path)
            return HandlerResult(
                False,
                f"wrote '{real_path}', but the independent re-read did not confirm the "
                f"expected content (hash {on_disk_hash!r}, expected {proposal.proposed_version!r}) - restored the backup",
            )
        return HandlerResult(True, f"updated '{real_path}' (backup at '{backup_path}')")

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        backups_dir = Path(context.backups_dir)
        candidates = sorted(backups_dir.glob(f"{proposal.proposal_id}-*.md"))
        if not candidates:
            return HandlerResult(False, f"no backup found for '{proposal.proposal_id}' - cannot roll back safely")
        backup_path = candidates[-1]
        real_path = self._resolve_path(proposal.target, context.settings)
        shutil.copy2(backup_path, real_path)

        on_disk_hash = hashlib.sha256(real_path.read_bytes()).hexdigest()[:12]
        if on_disk_hash != proposal.current_version:
            return HandlerResult(
                False,
                f"restored '{backup_path}' to '{real_path}', but the independent re-read "
                f"({on_disk_hash!r}) does not match the recorded prior version ({proposal.current_version!r})",
            )
        return HandlerResult(True, f"restored '{real_path}' from backup '{backup_path}'")

    def observe(self, component_id: str, *, context: HandlerContext) -> "str | None":
        """The identity document's real content hash - the inventory's own
        version format for it - or None when there is no document."""
        real_path = self._resolve_path(component_id, context.settings)
        if not real_path.exists():
            return None
        return hashlib.sha256(real_path.read_bytes()).hexdigest()[:12]

    def activation(self, component_id: str) -> str:
        return ACTIVATION_NEXT_SESSION


class CapabilityConfigHandler(EvolutionHandler):
    """How many recent conversation turns a reasoning-dependent
    capability (``open_conversation``/``knowledge_inquiry``/
    ``live_information``) is offered as background - a real value this
    project already hardcodes
    (``scripts/control_center.py::CONVERSATION_HISTORY_LIMIT``), made
    governable through ``GovernedConfig`` rather than a source edit.
    """

    COMPONENT_ID = "capability.general_conversation"

    def applies_to(self, component_id: str) -> bool:
        return component_id == self.COMPONENT_ID

    def _validate(self, value: str) -> "tuple[int, str] | None":
        try:
            parsed = int(value)
        except ValueError:
            return None
        if parsed < 1 or parsed > 200:
            return None
        return parsed, ""

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        validated = self._validate(proposal.proposed_version)
        if validated is None:
            return HandlerResult(False, f"'{proposal.proposed_version}' is not a real turn-count (expected an integer from 1 to 200)")

        config = load_governed_config(context.config_path)
        config = config.with_value(self.COMPONENT_ID, proposal.proposed_version)
        save_governed_config(config, context.config_path)

        reloaded = load_governed_config(context.config_path)
        if reloaded.get(self.COMPONENT_ID) != proposal.proposed_version:
            return HandlerResult(False, "wrote the new turn-count, but the independent re-read did not confirm it landed")
        return HandlerResult(True, f"conversation history context is now {proposal.proposed_version} turn(s) (independently re-read from disk)")

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        config = load_governed_config(context.config_path)
        config = config.with_value(self.COMPONENT_ID, proposal.current_version)
        save_governed_config(config, context.config_path)
        return HandlerResult(True, f"conversation history context restored to {proposal.current_version} turn(s)")

    def activation(self, component_id: str) -> str:
        return ACTIVATION_PER_TURN

    def default_version(self, component_id: str) -> "str | None":
        from src.services.evolution.inventory import DEFAULT_CONVERSATION_HISTORY_LIMIT

        return DEFAULT_CONVERSATION_HISTORY_LIMIT


class ToolConfigHandler(EvolutionHandler):
    """The real output directory ``DocumentGenerationTool`` writes
    generated documents to - a real value this project already
    hardcodes (``settings.documents_dir``), made governable the
    identical way ``CapabilityConfigHandler`` governs the conversation-
    history turn count."""

    COMPONENT_ID = "tool.document_generation"

    def applies_to(self, component_id: str) -> bool:
        return component_id == self.COMPONENT_ID

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        new_dir = Path(proposal.proposed_version)
        try:
            new_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return HandlerResult(False, f"'{proposal.proposed_version}' is not a usable directory: {exc}")
        if not new_dir.is_dir():
            return HandlerResult(False, f"'{proposal.proposed_version}' is not a directory")

        config = load_governed_config(context.config_path)
        config = config.with_value(self.COMPONENT_ID, proposal.proposed_version)
        save_governed_config(config, context.config_path)

        reloaded = load_governed_config(context.config_path)
        if reloaded.get(self.COMPONENT_ID) != proposal.proposed_version:
            return HandlerResult(False, "wrote the new output directory, but the independent re-read did not confirm it landed")
        return HandlerResult(True, f"document generation now writes to '{proposal.proposed_version}' (independently re-read from disk)")

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        config = load_governed_config(context.config_path)
        config = config.with_value(self.COMPONENT_ID, proposal.current_version)
        save_governed_config(config, context.config_path)
        return HandlerResult(True, f"document generation output directory restored to '{proposal.current_version}'")


class ConnectionAdapterHandler(EvolutionHandler):
    """A built-in connection's own real adapter *implementation* version,
    for every connection that declares more than one
    (``connection_factory.connection_adapter_versions``, Phase 11 - this
    handler names none of them) - distinct from, and never touching, the
    account/credential 52.27's own ``connections`` command already governs.
    Gmail's is the one today: v1 forwards only ``limit`` to
    ``Mailbox.fetch``; v2 also forwards an optional ``label`` filter the
    ``Mailbox`` contract already declares.

    Compatibility is verified for real: a fresh connection is actually
    constructed at the proposed version, and its ``authorization`` must
    remain unchanged - proving the account's own credential state is
    genuinely unaffected by the adapter change.
    """

    PREFIX = "connection."

    def _versions(self, component_id: str) -> "tuple[str, ...]":
        from src.providers.connection_factory import connection_adapter_versions

        if not component_id.startswith(self.PREFIX):
            return ()
        return connection_adapter_versions().get(component_id[len(self.PREFIX):], ())

    def applies_to(self, component_id: str) -> bool:
        return bool(self._versions(component_id))

    def _build(self, component_id: str, adapter_version: str, context: HandlerContext):
        from dataclasses import replace as dc_replace

        from src.providers.connection_factory import build_connection

        return build_connection(
            dc_replace(context.settings, connection_provider=component_id[len(self.PREFIX):]),
            adapter_version=adapter_version,
        )

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        known = self._versions(proposal.target)
        name = proposal.target[len(self.PREFIX):]
        if proposal.proposed_version not in known:
            return HandlerResult(False, f"'{proposal.proposed_version}' is not a known {name} adapter version. Known: {', '.join(known)}")

        before = self._build(proposal.target, proposal.current_version, context)
        after = self._build(proposal.target, proposal.proposed_version, context)
        if after.authorization != before.authorization:
            return HandlerResult(
                False,
                f"the account's authorization state would change ({before.authorization.value} -> "
                f"{after.authorization.value}) merely from an adapter version bump - refusing, since "
                "this checkpoint's own invariant is that an adapter upgrade never disturbs the account",
            )

        config = load_governed_config(context.config_path)
        config = config.with_value(proposal.target, proposal.proposed_version)
        save_governed_config(config, context.config_path)

        reloaded = load_governed_config(context.config_path)
        if reloaded.get(proposal.target) != proposal.proposed_version:
            return HandlerResult(False, "wrote the new adapter version, but the independent re-read did not confirm it landed")
        return HandlerResult(
            True,
            f"{name} adapter is now v{proposal.proposed_version} (independently re-read from disk; "
            f"authorization unchanged: {after.authorization.value})",
        )

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        config = load_governed_config(context.config_path)
        config = config.with_value(proposal.target, proposal.current_version)
        save_governed_config(config, context.config_path)
        return HandlerResult(True, f"{proposal.target[len(self.PREFIX):]} adapter restored to v{proposal.current_version}")

    def default_version(self, component_id: str) -> "str | None":
        """No governed value means the connection's own default adapter."""
        versions = self._versions(component_id)
        return versions[0] if versions else None


class BrainModeHandler(EvolutionHandler):
    """Whether an agent reasons with its provider at all - ``brain.manager``
    (the Manager's own reasoning mode) and ``brain.specialist_default`` (every
    specialist's) - a genuinely separate axis from which vendor answers
    (Pre-Stage-9 Campaign 4). Validated against the brains that actually exist,
    written to the governed config every start reads, independently re-read."""

    SETTING_FOR = {"brain.manager": "brain", "brain.specialist_default": "specialist_brain"}

    def applies_to(self, component_id: str) -> bool:
        return component_id in self.SETTING_FOR

    def _available(self, component_id: str) -> "list[str]":
        if component_id == "brain.manager":
            from src.manager.brain_factory import available_brains

            return list(available_brains())
        from src.agents.brain_factory import available_specialist_brains

        return list(available_specialist_brains())

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        available = self._available(proposal.target)
        if proposal.proposed_version not in available:
            return HandlerResult(
                False, f"'{proposal.proposed_version}' is not a real reasoning mode. Available: {', '.join(available)}",
            )
        config = load_governed_config(context.config_path).with_value(proposal.target, proposal.proposed_version)
        save_governed_config(config, context.config_path)
        if load_governed_config(context.config_path).get(proposal.target) != proposal.proposed_version:
            return HandlerResult(False, "wrote the reasoning mode, but the independent re-read did not confirm it landed")
        return HandlerResult(True, f"'{proposal.target}' is now '{proposal.proposed_version}' (independently re-read from disk)")

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        config = load_governed_config(context.config_path).with_value(proposal.target, proposal.current_version)
        save_governed_config(config, context.config_path)
        return HandlerResult(True, f"'{proposal.target}' restored to '{proposal.current_version}'")

    def activation(self, component_id: str) -> str:
        return ACTIVATION_REASONING


class SurfaceHandler(EvolutionHandler):
    """An agent's own interaction surface - offered and rendered, offered and
    planned, or withdrawn - governed like every other component (Pre-Stage-9
    Campaign 5, SRF-03). ``surface.<agent>.<kind>``: any agent, any kind; the
    agent's own declaration stays the default and this records the owner's
    approved evolution of it, applied wherever a surface is described
    (``InterfaceDescriptor.governed``)."""

    def applies_to(self, component_id: str) -> bool:
        from src.control_center.interfaces import SURFACE_COMPONENT_PREFIX

        return component_id.startswith(SURFACE_COMPONENT_PREFIX) and component_id.count(".") == 2

    def initial_version(self, component_id: str) -> "str | None":
        """A surface kind an agent has never declared starts as "undeclared",
        so the owner can grant a brand-new kind through governance."""
        return "undeclared"

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        from src.control_center.interfaces import SURFACE_STATES, SurfaceKind

        if proposal.proposed_version not in SURFACE_STATES:
            return HandlerResult(False, f"'{proposal.proposed_version}' is not a surface state. Expected one of: {', '.join(SURFACE_STATES)}")
        try:
            SurfaceKind(proposal.target.rsplit(".", 1)[-1])
        except ValueError as exc:
            return HandlerResult(False, str(exc))
        config = load_governed_config(context.config_path).with_value(proposal.target, proposal.proposed_version)
        save_governed_config(config, context.config_path)
        if load_governed_config(context.config_path).get(proposal.target) != proposal.proposed_version:
            return HandlerResult(False, "wrote the surface state, but the independent re-read did not confirm it landed")
        return HandlerResult(True, f"'{proposal.target}' is now {proposal.proposed_version} (independently re-read from disk)")

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        from src.control_center.interfaces import SURFACE_STATES

        config = load_governed_config(context.config_path)
        if proposal.current_version in SURFACE_STATES:
            config = config.with_value(proposal.target, proposal.current_version)
        else:
            config = config.without_value(proposal.target)
        save_governed_config(config, context.config_path)
        return HandlerResult(True, f"'{proposal.target}' restored to {proposal.current_version}")

    def absence_restores(self, component_id: str, version: str) -> bool:
        """Rolling back to "undeclared" removes the governed value, returning
        the surface to the agent's own declaration - which, for a kind it
        never declared, is exactly "undeclared". (What absence means for a
        kind it *does* declare depends on the agent, so no default_version is
        claimed.)"""
        return version == "undeclared"

    def activation(self, component_id: str) -> str:
        return ACTIVATION_ON_READ


def _technique_families():
    """Every technique family, its registering modules imported first."""
    import src.providers.media.image_techniques  # noqa: F401 - registers "image_technique"
    import src.services.observations.techniques  # noqa: F401 - registers "series_technique"
    from src.core.techniques import technique_families

    return technique_families()


def _family(component_id: str):
    return next((f for f in _technique_families() if f.owns(component_id)), None)


class TechniqueHandler(EvolutionHandler):
    """A technique of any registered family - how a real image can be
    transformed (``image_technique.<id>``), what can be found in a series of
    readings (``series_technique.<id>``, GM-09), or any family registered
    later - governed like every other component, by one handler.
    ``enabled``/``disabled`` withdraws or restores a technique;
    ``pipeline:<step>,<step>|<label>|<phrase>;<phrase>`` adds a brand-new one
    composed from existing techniques of the same family, with no code. A newly
    developed implementation registered in code is governed through the same
    id. Read live by the family's tool on every call."""

    def applies_to(self, component_id: str) -> bool:
        return _family(component_id) is not None

    def initial_version(self, component_id: str) -> "str | None":
        from src.core.techniques import ENABLED

        family = _family(component_id)
        return ENABLED if family is not None and family.technique_id(component_id) in family.known_ids() else "undeclared"

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        family = _family(proposal.target)
        problem = family.validate(family.technique_id(proposal.target), proposal.proposed_version)
        if problem is not None:
            return HandlerResult(False, f"'{proposal.proposed_version}' is not a technique state: {problem}")
        config = load_governed_config(context.config_path).with_value(proposal.target, proposal.proposed_version)
        save_governed_config(config, context.config_path)
        if load_governed_config(context.config_path).get(proposal.target) != proposal.proposed_version:
            return HandlerResult(False, "wrote the technique state, but the independent re-read did not confirm it landed")
        return HandlerResult(True, f"'{proposal.target}' is now {proposal.proposed_version} (independently re-read from disk)")

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        family = _family(proposal.target)
        config = load_governed_config(context.config_path)
        if family.validate(family.technique_id(proposal.target), proposal.current_version) is None:
            config = config.with_value(proposal.target, proposal.current_version)
        else:
            config = config.without_value(proposal.target)
        save_governed_config(config, context.config_path)
        return HandlerResult(True, f"'{proposal.target}' restored to {proposal.current_version}")

    def default_version(self, component_id: str) -> "str | None":
        """No governed value leaves a built-in technique enabled and an
        unknown one undeclared - exactly what ``initial_version`` says."""
        return self.initial_version(component_id)

    def activation(self, component_id: str) -> str:
        return ACTIVATION_PER_TURN


# Kept for any caller written against the first family's name.
ImageTechniqueHandler = TechniqueHandler


# The open-ended registration point (Section 11's own requirement): a
# future category registers one handler here (or calls
# ``register_handler`` at import time from its own module) and needs no
# change to the engine that dispatches to it.
_HANDLERS: "list[EvolutionHandler]" = [
    ProviderModelHandler(),
    SpecialistIdentityHandler(),
    CapabilityConfigHandler(),
    ToolConfigHandler(),
    ConnectionAdapterHandler(),
    BrainModeHandler(),
    SurfaceHandler(),
    TechniqueHandler(),
]


def register_handler(handler: EvolutionHandler) -> None:
    """Add a new component category's own evolution behavior - the
    governed extension point future components use, never a change to
    ``ecosystem_evolution.py`` itself."""
    _HANDLERS.append(handler)


def registered_handlers() -> "tuple[EvolutionHandler, ...]":
    """Every registered handler, built-in and added, in registration order."""
    return tuple(_HANDLERS)


def handler_for(component_id: str) -> "EvolutionHandler | None":
    for handler in _HANDLERS:
        if handler.applies_to(component_id):
            return handler
    return None
