"""File-backed persistence for ``CredentialStore`` - a pure load/save
pair operating *around* that class, never a change to it.

``CredentialStore`` (Phase 1.5) is deliberately in-memory and
caller-constructed - "not a singleton and not global state" (its own
docstring). That is still exactly right for how a running process uses
it. What was missing is a way for an *operator* (the ecosystem CLI, a
future admin surface) to add, select, or revoke an account and have
that decision survive past one process - the same gap this project
already solved once for Gmail (``gmail_credentials_path``/
``gmail_token_path``, both files under ``data/``, gitignored) applied
here to every ``ProviderAccount``-shaped credential instead of one
vendor's OAuth tokens.

**Never read by Alfred, the Brain, or any agent.** Only an operator
tool (``scripts/ecosystem_cli.py``) and, at process startup, whichever
entry point chooses to load one, ever touch this file - the identical
boundary ``gmail_credentials_path`` already sits behind. Secrets are
written to disk here (there is no way to persist a credential without
that being true somewhere - Gmail's own token file already does this),
protected by Windows' per-user encryption (DPAPI) where it exists (see
the bottom of this module), never logged, never echoed back in full by
the CLI built on top of this module.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.services.credentials.account import CredentialStore, ProviderAccount

# Deliberately its own file, alongside Gmail's own two - one axis's
# credentials never share a file with another's, the identical
# per-provider isolation this project already holds itself to at the
# in-memory layer, extended to how they are stored at rest.
DEFAULT_ACCOUNTS_PATH = Path("data") / "credentials" / "accounts.json"


def load_credential_store(path: "Path | str" = DEFAULT_ACCOUNTS_PATH) -> CredentialStore:
    """A fresh store, populated from ``path`` if it exists.

    A missing file is not an error - the same "absence is not an
    error" rule every other optional file in this project already
    follows (a missing profile, a missing schedule): a brand-new
    installation gets an honestly empty store, not a crash.
    """
    path = Path(path)
    store = CredentialStore()
    if not path.exists():
        return store

    payload = json.loads(path.read_text(encoding="utf-8"))

    for entry in payload.get("accounts", []):
        store.add(
            ProviderAccount(
                provider=entry["provider"],
                account_id=entry["account_id"],
                credential=_read_credential(entry),
                model=entry.get("model"),
            ),
            activate=False,
        )

    # Applied only after every account is loaded, so the persisted
    # "active" choice always wins over CredentialStore.add's own
    # first-registered-becomes-active default - a round trip restores
    # exactly what was selected, never merely what was registered first.
    active_by_provider = payload.get("active", {})
    for provider, account_id in active_by_provider.items():
        store.select(provider, account_id)

    # CredentialStore.add's own default ("the first account registered
    # for a provider becomes active, absent anything else") means a
    # provider whose accounts were all loaded with activate=False still
    # ends up with one active by the time the loop above runs - correct
    # for a brand-new store, wrong for restoring one where the active
    # account had been *revoked* and never replaced (CredentialStore's
    # own honest "revoking the active account leaves none active, never
    # silently reassigned" rule - see its own docstring). Every provider
    # with registered accounts but no persisted "active" entry is that
    # case, and is corrected here, directly, since this module is
    # CredentialStore's own dedicated persistence companion rather than
    # arbitrary external code reaching into its internals.
    providers_with_accounts = {account.provider for account in store}
    for provider in providers_with_accounts - set(active_by_provider):
        store._active.pop(provider, None)

    return store


def save_credential_store(store: CredentialStore, path: "Path | str" = DEFAULT_ACCOUNTS_PATH) -> None:
    """Write every account currently in ``store``, and which one is
    active per provider, to ``path`` - overwriting whatever was there.
    The whole store is rewritten each time rather than patched, the
    same "read fresh, write whole" simplicity ``PersistentMemory``
    already uses for the identical reason: a partial write can never
    leave the file half-updated and half-stale.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "accounts": [
            {
                "provider": account.provider,
                "account_id": account.account_id,
                **_stored_credential(account.credential),
                "model": account.model,
            }
            for account in store
        ],
        "active": {
            provider: active.account_id
            for provider in store.providers()
            if (active := store.active(provider)) is not None
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# At rest: on Windows a credential is written protected by the operating
# system's own per-user encryption (DPAPI, ``CryptProtectData``) - readable
# only by this Windows account on this machine, useless if the file is copied
# elsewhere or read by another user. Where DPAPI does not exist the value is
# written as before, and nothing pretends otherwise. A plaintext entry from an
# earlier version still loads, and is protected the next time the store is
# saved. Standard library only (ctypes), like the rest of this module.
# ---------------------------------------------------------------------------

_PROTECTED_KEY = "credential_protected"
_DPAPI_PREFIX = "dpapi:"


def _stored_credential(credential: str) -> dict:
    protected = _dpapi(credential.encode("utf-8"), protect=True)
    if protected is None:
        return {"credential": credential}
    import base64

    return {_PROTECTED_KEY: _DPAPI_PREFIX + base64.b64encode(protected).decode("ascii")}


def _read_credential(entry: dict) -> str:
    value = entry.get(_PROTECTED_KEY)
    if not value:
        return entry["credential"]
    import base64

    if not str(value).startswith(_DPAPI_PREFIX):
        raise ValueError("unknown credential protection format")
    clear = _dpapi(base64.b64decode(str(value)[len(_DPAPI_PREFIX):]), protect=False)
    if clear is None:
        raise ValueError(
            "a stored credential is protected by Windows for another user or machine and cannot be read here - "
            "add the account again from the Control Center"
        )
    return clear.decode("utf-8")


def _dpapi(data: bytes, *, protect: bool) -> "bytes | None":
    """``CryptProtectData``/``CryptUnprotectData`` over ``data``; None where
    DPAPI is unavailable (not Windows) or refuses (another user's data)."""
    import sys

    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = call(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result))  # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        return None
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)
