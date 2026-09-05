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
    connection.gmail             GmailConnection's own real adapter
                                  version (v1: limit-only; v2: also
                                  honours a label filter the underlying
                                  Mailbox contract already supports) -
                                  never the account/credential, which
                                  stays exactly as 52.27 already governs
                                  it, structurally untouched by this
                                  handler.

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


def _model_segment(version: str) -> "str | None":
    """A ``provider.*`` component's version is always the full
    ``backend:account_id:model`` composite (``inventory.py``'s own
    ``ComponentSnapshot.version`` format) - this extracts just the
    model segment, the only part ever actually written."""
    return version.rsplit(":", 1)[-1] if ":" in version else None


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
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        category = proposal.target.removeprefix("provider.")
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

        new_model = _model_segment(proposal.proposed_version)
        if new_model is None:
            return HandlerResult(
                False,
                f"'{proposal.proposed_version}' does not name a model - a provider "
                "proposal's version must be the full 'backend:account_id:model' form",
            )

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

        restored_model = _model_segment(proposal.current_version)
        restored = ProviderAccount(
            provider=account.provider, account_id=account.account_id,
            credential=account.credential, model=restored_model,
        )
        store.add(restored, activate=True)
        save_credential_store(store, context.accounts_path)
        return HandlerResult(True, f"restored {backend}/{account.account_id}'s model to {restored_model!r}")


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
        return component_id == "alfred" or component_id.startswith("specialist.")

    def _resolve_path(self, component_id: str, settings) -> Path:
        if component_id == "alfred":
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
            if proposal.target == "alfred":
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
    """Gmail's own real adapter *implementation* version - distinct
    from, and never touching, the account/credential 52.27's own
    ``connections`` command already governs. v1 (today's default)
    forwards only ``limit`` to ``Mailbox.fetch``; v2 also forwards an
    optional ``label`` filter the underlying ``Mailbox`` contract
    (``src/services/email/mailbox.py``) already declares and
    ``GmailMailbox`` already implements - a real, additive capability,
    never fabricated.

    Compatibility is verified for real: a fresh ``GmailConnection`` is
    actually constructed at the proposed version, and its
    ``authorization`` must remain unchanged - proving the account's own
    credential state is genuinely unaffected by the adapter change.
    """

    COMPONENT_ID = "connection.gmail"
    KNOWN_VERSIONS = ("1", "2")

    def applies_to(self, component_id: str) -> bool:
        return component_id == self.COMPONENT_ID

    def _build(self, adapter_version: str, context: HandlerContext):
        from dataclasses import replace as dc_replace

        from src.providers.connection_factory import build_connection

        return build_connection(dc_replace(context.settings, connection_provider="gmail"), adapter_version=adapter_version)

    def apply(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        if proposal.proposed_version not in self.KNOWN_VERSIONS:
            return HandlerResult(False, f"'{proposal.proposed_version}' is not a known Gmail adapter version. Known: {', '.join(self.KNOWN_VERSIONS)}")

        before = self._build(proposal.current_version, context)
        after = self._build(proposal.proposed_version, context)
        if after.authorization != before.authorization:
            return HandlerResult(
                False,
                f"the account's authorization state would change ({before.authorization.value} -> "
                f"{after.authorization.value}) merely from an adapter version bump - refusing, since "
                "this checkpoint's own invariant is that an adapter upgrade never disturbs the account",
            )

        config = load_governed_config(context.config_path)
        config = config.with_value(self.COMPONENT_ID, proposal.proposed_version)
        save_governed_config(config, context.config_path)

        reloaded = load_governed_config(context.config_path)
        if reloaded.get(self.COMPONENT_ID) != proposal.proposed_version:
            return HandlerResult(False, "wrote the new adapter version, but the independent re-read did not confirm it landed")
        return HandlerResult(
            True,
            f"Gmail adapter is now v{proposal.proposed_version} (independently re-read from disk; "
            f"authorization unchanged: {after.authorization.value})",
        )

    def rollback(self, proposal: EvolutionProposal, *, context: HandlerContext) -> HandlerResult:
        config = load_governed_config(context.config_path)
        config = config.with_value(self.COMPONENT_ID, proposal.current_version)
        save_governed_config(config, context.config_path)
        return HandlerResult(True, f"Gmail adapter restored to v{proposal.current_version}")


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
]


def register_handler(handler: EvolutionHandler) -> None:
    """Add a new component category's own evolution behavior - the
    governed extension point future components use, never a change to
    ``ecosystem_evolution.py`` itself."""
    _HANDLERS.append(handler)


def handler_for(component_id: str) -> "EvolutionHandler | None":
    for handler in _HANDLERS:
        if handler.applies_to(component_id):
            return handler
    return None
