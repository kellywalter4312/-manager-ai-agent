"""Provider accounts: named, provider-specific credentials.

Phase 1.5's own layer, genuinely separate from everything it is not:

    Identity      who an agent is
    Brain         how an agent reasons
    Memory        what an agent remembers
    Voice         how an agent speaks
    Capabilities  what an agent can attempt
    Connections   what an agent may reach on the person's behalf
    ProviderAccount / CredentialStore (this file)   which vendor
                  credential a provider construction should use

Before this existed, a credential was a raw environment variable read
directly inside ``src/providers/factory.py`` - correct as far as it
went (each vendor already owned its own variable, never another's), but
with no way to express more than exactly one credential per provider
name, no way to select among several, and no first-class way to revoke
one independently of unsetting a process environment variable.

**Deliberately narrow.** A ``ProviderAccount`` is a value: which
provider it authorises, which account it represents, and the credential
itself - nothing about a model, a Brain, an agent, or how the two
former are actually authenticated over the wire (that is the concrete
``LLMProvider`` implementation's own job, unchanged, see
``src/providers/factory.py``). A ``CredentialStore`` holds accounts and
which one is *active* per provider - nothing more. Neither imports, is
imported by, or knows anything about ``src/manager/`` or ``src/agents/``.
A future caller wires this in at composition time
(``src/composition.py``), exactly where every other replaceable
dependency in this project is wired.

**Never logged, never repr'd, never returned whole.** The credential
value lives behind ``field(repr=False)`` and every diagnostic method
below (``describe()``, ``__repr__``) names the account, never the
secret - the same discipline ``Settings.api_key``/``Settings.describe()``
already hold themselves to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True)
class ProviderAccount:
    """One named credential for one provider.

    ``account_id`` is a caller-chosen label ("default", "work",
    "personal", "team-b") - never inferred from the credential itself,
    the same "names come from the caller, never invented" discipline
    every registry in this project already applies to agents and
    capabilities.
    """

    provider: str
    account_id: str
    credential: str = field(repr=False)
    model: str | None = None

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        if not provider:
            raise ValueError("ProviderAccount.provider must not be empty")
        object.__setattr__(self, "provider", provider)

        account_id = self.account_id.strip()
        if not account_id:
            raise ValueError("ProviderAccount.account_id must not be empty")
        object.__setattr__(self, "account_id", account_id)

        # An empty or whitespace-only credential is treated as absent
        # rather than as a credential, the identical rule Settings.api_key
        # already applies - a blank secret must never be mistaken for a
        # real one and produce a confusing auth failure later.
        credential = (self.credential or "").strip()
        if not credential:
            raise ValueError("ProviderAccount.credential must not be empty")
        object.__setattr__(self, "credential", credential)

        if self.model is not None:
            model = self.model.strip()
            object.__setattr__(self, "model", model or None)

    def describe(self) -> str:
        """Safe to log or display: names the account, never the secret."""
        return f"{self.provider}/{self.account_id}"

    def __repr__(self) -> str:  # pragma: no cover - trivial, but worth pinning
        return f"<ProviderAccount {self.describe()}>"


class CredentialStore:
    """Holds provider accounts, and which one is active per provider.

    Not a singleton and not global state: a caller constructs one (or
    more - nothing here assumes exactly one store exists), populates it,
    and passes it to whatever needs to resolve an account. This is what
    makes "Alfred uses account A, a future specialist uses account B"
    a matter of which store (or which lookup into it) a caller wires in,
    never a change to Brain, Identity, Memory, Voice, Capability or
    Connection architecture.

    **Fresh lookups, no caching.** Every method below reads its own
    dictionaries directly; nothing here memoises a resolved account
    across calls. A revoked or replaced account is honoured by the very
    next lookup - the same "read fresh every time" guarantee
    ``src/providers/factory.py``'s own environment-based credential
    check already gives, extended to this layer.
    """

    def __init__(self) -> None:
        self._accounts: dict[tuple[str, str], ProviderAccount] = {}
        self._active: dict[str, str] = {}

    def add(self, account: ProviderAccount, *, activate: bool = True) -> None:
        """Register an account. Replaces any existing account of the same
        (provider, account_id) - a deliberate credential *replacement*,
        never a silent duplicate.

        ``activate`` (default True) makes this the provider's active
        account; pass False to register a second, inactive account for
        the same provider (multiple accounts, only one selected) without
        disturbing whichever one is already active.
        """
        key = (account.provider, account.account_id)
        self._accounts[key] = account
        if activate or account.provider not in self._active:
            self._active[account.provider] = account.account_id

    def accounts_for(self, provider: str) -> tuple[ProviderAccount, ...]:
        """Every account registered for ``provider``, in registration
        order - the concrete evidence "multiple accounts" is real,
        rather than an assertion about it."""
        provider = provider.strip().lower()
        return tuple(
            account for (p, _), account in self._accounts.items() if p == provider
        )

    def get(self, provider: str, account_id: str) -> ProviderAccount | None:
        """One specific account, or None if it was never registered or
        has since been revoked."""
        return self._accounts.get((provider.strip().lower(), account_id.strip()))

    def select(self, provider: str, account_id: str) -> None:
        """Make ``account_id`` the active account for ``provider``.

        Raises if that account is not registered - selecting an account
        that does not exist would silently make the provider fall back
        to whatever *was* active, the exact "silent fallback to another
        credential" Phase 1.5 rules out.
        """
        provider = provider.strip().lower()
        account_id = account_id.strip()
        if (provider, account_id) not in self._accounts:
            raise KeyError(
                f"no account '{account_id}' registered for provider '{provider}'"
            )
        self._active[provider] = account_id

    def active(self, provider: str) -> ProviderAccount | None:
        """The account currently selected for ``provider``, or None when
        none has ever been added or the active one has been revoked -
        an honest absence, never a fabricated default."""
        provider = provider.strip().lower()
        account_id = self._active.get(provider)
        if account_id is None:
            return None
        return self._accounts.get((provider, account_id))

    def revoke(self, provider: str, account_id: str) -> None:
        """Remove an account immediately and permanently from this store.

        If it was the active account for its provider, that provider is
        left with none active - never silently reassigned to a
        different account, which would be exactly the "silent fallback"
        Phase 1.5 forbids. Revoking an account nobody registered, or
        one already revoked, is a no-op: removal is idempotent, the same
        way ``Memory.forget()`` already treats an unknown record.

        Unrelated providers, and every other account of *this* provider,
        are untouched - isolation, not just for a different provider,
        but between two accounts of the same one.
        """
        provider = provider.strip().lower()
        account_id = account_id.strip()
        self._accounts.pop((provider, account_id), None)
        if self._active.get(provider) == account_id:
            del self._active[provider]

    def providers(self) -> tuple[str, ...]:
        """Every provider name with at least one registered account."""
        return tuple(dict.fromkeys(p for p, _ in self._accounts))

    def __len__(self) -> int:
        return len(self._accounts)

    def __iter__(self) -> Iterator[ProviderAccount]:
        return iter(self._accounts.values())

    def __repr__(self) -> str:
        """Safe to log: every account's name, never a credential value."""
        names = ", ".join(sorted(a.describe() for a in self._accounts.values()))
        return f"<CredentialStore accounts=[{names}]>"
