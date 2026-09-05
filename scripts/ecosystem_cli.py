"""The operator's practical control surface over Alfred OS's ecosystem
of replaceable components - the "routine provider/component changes
should not require editing source code" requirement, made real.

This is deliberately an *operator* tool, not something Alfred's Brain
can invoke on its own: switching an LLM provider, revoking a
credential, or changing which voice a specialist speaks with is a
consequential, security-sensitive decision, the same standing rule this
project already holds for provider credentials in general ("the Brain
must never directly receive arbitrary provider credentials or
unrestricted provider access"). A human runs this at a terminal; Alfred
never does.

Commands:

    list                                     every category and its backends
    status <category>                        one category's active backend and dependents
    validate <category> <key> [--credential X] [--model Y]
                                              attempt real construction, report honestly
    accounts <category>                      every persisted account for one category
    add-account <category> <account-id> --credential X [--model Y] [--activate/--no-activate]
    select-account <category> <account-id>   make this the active account
    revoke-account <category> <account-id>   remove it immediately

Every command reads/writes the same persisted account file
(``src/services/credentials/account_store_file.py``) and the same real
factories every provider in this project already uses - no second
credential mechanism, no simulated backend list.

Note (universal provider/connection management repair): for
add-account/select-account/revoke-account/accounts, ``<category>`` here
is used directly as the persisted credential's key
(``ProviderAccount.provider``) - it must name the concrete backend a
credential authorises (e.g. ``anthropic``, ``runway``, ``elevenlabs``),
the identical key ``build_provider``/``build_media``/``build_speech``/
``build_communication_provider`` themselves read, not the broader
ecosystem category name (``llm_provider``) that ``list``/``status``/
``validate`` use. This was already this module's own behaviour and its
own tests already exercise it this way; it is unchanged here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config.settings import Settings
from src.services.credentials.account import CredentialStore, ProviderAccount
from src.services.credentials.account_store_file import (
    DEFAULT_ACCOUNTS_PATH,
    load_credential_store,
    save_credential_store,
)
from src.services.ecosystem.registry_factory import build_ecosystem_registry


def _require_adapter(registry, category: str):
    adapter = registry.adapter_for(category)
    if adapter is None:
        known = ", ".join(registry.categories())
        raise SystemExit(f"no such category '{category}'. Known categories: {known}")
    return adapter


def cmd_list(args: argparse.Namespace) -> int:
    registry = build_ecosystem_registry(Settings.from_env())
    print(registry.describe_all())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    registry = build_ecosystem_registry(Settings.from_env())
    adapter = _require_adapter(registry, args.category)
    print(adapter.describe())
    active = adapter.active()
    print(f"active: {active.key if active is not None else '(none)'}")
    ops = ", ".join(op.value for op in sorted(adapter.supported_operations(), key=lambda o: o.value))
    print(f"supported operations: {ops}")
    dependents = adapter.dependents()
    if dependents:
        print("depends on this category being configured:")
        for line in dependents:
            print(f"  - {line}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    registry = build_ecosystem_registry(Settings.from_env())
    adapter = _require_adapter(registry, args.category)
    outcome = adapter.validate(args.key, credential=args.credential)
    print(("OK: " if outcome.ok else "FAILED: ") + outcome.detail)
    print(
        "(This is real construction only - no live external call was made. "
        "See ARCHITECTURE.md's own reality standard: this is IMPLEMENTED/"
        "TEST-VERIFIED evidence, never LIVE EXTERNAL VERIFIED evidence.)"
    )
    return 0 if outcome.ok else 1


def cmd_accounts(args: argparse.Namespace) -> int:
    store = load_credential_store(args.accounts_path)
    accounts = store.accounts_for(args.category)
    if not accounts:
        print(f"no accounts registered for '{args.category}'")
        return 0
    active = store.active(args.category)
    for account in accounts:
        marker = "*" if active is not None and active.account_id == account.account_id else " "
        model = f" model={account.model}" if account.model else ""
        print(f"[{marker}] {account.describe()}{model}")
    return 0


def cmd_add_account(args: argparse.Namespace) -> int:
    store = load_credential_store(args.accounts_path)
    account = ProviderAccount(
        provider=args.category, account_id=args.account_id, credential=args.credential, model=args.model
    )
    store.add(account, activate=args.activate)
    save_credential_store(store, args.accounts_path)
    print(f"added {account.describe()}" + (" (active)" if args.activate else ""))
    return 0


def cmd_select_account(args: argparse.Namespace) -> int:
    store = load_credential_store(args.accounts_path)
    try:
        store.select(args.category, args.account_id)
    except KeyError as exc:
        print(f"FAILED: {exc}")
        return 1
    save_credential_store(store, args.accounts_path)
    print(f"{args.category}/{args.account_id} is now active")
    return 0


def cmd_revoke_account(args: argparse.Namespace) -> int:
    store = load_credential_store(args.accounts_path)
    store.revoke(args.category, args.account_id)
    save_credential_store(store, args.accounts_path)
    print(f"revoked {args.category}/{args.account_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecosystem", description=__doc__)
    parser.add_argument(
        "--accounts-path",
        default=DEFAULT_ACCOUNTS_PATH,
        type=Path,
        help="where persisted accounts are read from/written to (default: data/credentials/accounts.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="every category and its available backends").set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="one category's active backend, operations and dependents")
    p.add_argument("category")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("validate", help="attempt real construction of a candidate backend")
    p.add_argument("category")
    p.add_argument("key")
    p.add_argument("--credential", default=None)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("accounts", help="every persisted account for one category")
    p.add_argument("category")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("add-account", help="register a new persisted account")
    p.add_argument("category")
    p.add_argument("account_id")
    p.add_argument("--credential", required=True)
    p.add_argument("--model", default=None)
    p.add_argument("--activate", dest="activate", action="store_true", default=True)
    p.add_argument("--no-activate", dest="activate", action="store_false")
    p.set_defaults(func=cmd_add_account)

    p = sub.add_parser("select-account", help="make an already-registered account active")
    p.add_argument("category")
    p.add_argument("account_id")
    p.set_defaults(func=cmd_select_account)

    p = sub.add_parser("revoke-account", help="remove an account immediately and permanently")
    p.add_argument("category")
    p.add_argument("account_id")
    p.set_defaults(func=cmd_revoke_account)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
