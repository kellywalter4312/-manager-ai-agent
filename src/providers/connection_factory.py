"""Turns a configured connection *name* into a connection *instance*.

The identical small registry ``live_info_factory.py``/
``communication_factory.py``/``celebration_factory.py``/
``calendar_events_factory.py`` already are. "null" (honestly
unavailable) is the default - the same choice
``communication_provider`` already makes, and for the identical reason:
a connection is consequential in exactly the way a phone/messaging
provider is, so a real one is always a deliberate
``MANAGER_CONNECTION_PROVIDER=gmail``, never a side effect of upgrading.

"gmail" reuses the existing, already-governed Gmail read boundary (see
``gmail_connection.py``'s own docstring) rather than adding a new
external dependency - no new package, no new service, no new risk
beyond what this project's Gmail integration already carries.

**Open-ended service discovery (Phase 6 foundation).** ``_BUILDERS``
above stays exactly two entries - "null" and "gmail" are the only
connections this project ships bespoke Python for. A name that is
*not* one of them is no longer an immediate failure: it is looked up in
the declared-service registry
(``src/services/connections/service_registry.py``), populated only
through the confirmed, audited ``services <name> declare`` Control
Center write - never by this factory, never by Alfred mid-conversation.
A match becomes a real ``DeclaredConnection``
(``src/providers/declared_connection.py``), built from that
descriptor plus whatever ``ProviderAccount`` the existing
``CredentialStore`` already holds for the identical name - the same
credential layer Gmail's own account already lives beside, never a
second one. This is what closes the actual gap: a legitimate new
service can become a real, usable connection - reachable through the
unmodified ``connected_action`` capability - without a new subclass or
a new entry in ``_BUILDERS`` itself. A name that matches neither a
builder nor a declared service still fails exactly as before.

**Adapter version (Full Alfred OS Ecosystem Evolution).**
``adapter_version`` is threaded straight through to whichever builder
accepts it - today, only Gmail's own ``GmailConnection`` does anything
with it (see that class's own docstring). Every builder that ignores it
(``null``, and any future connection with no adapter-version axis of
its own) keeps working unchanged; the default ("1") reproduces this
factory's prior behaviour exactly for every existing caller that omits
it.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.connection import Connection
from src.providers.null_connection import NullConnection
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _build_gmail(settings: Settings, adapter_version: str = "1") -> Connection:
    """Build the Gmail connection, importing its module only when
    selected - the identical reason ``mailbox_factory.py``'s own
    ``_build_gmail`` imports ``GmailMailbox`` locally: the default path
    never loads Gmail code, and the project runs where the Google
    packages are not installed."""
    from src.providers.gmail_connection import GmailConnection
    from src.services.email.mailbox_factory import build_mailbox

    return GmailConnection(
        build_mailbox(settings), token_path=settings.gmail_token_path, adapter_version=adapter_version
    )


_BUILDERS: "dict[str, Callable[..., Connection]]" = {
    "null": lambda settings, adapter_version="1": NullConnection(),
    "gmail": _build_gmail,
}


def available_connection_providers() -> list[str]:
    """Every connection provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_connection(
    settings: Settings, env: "Mapping[str, str] | None" = None, adapter_version: str = "1"
) -> Connection:
    """Construct the connection named by ``settings.connection_provider``.

    ``adapter_version`` is the identical, optional, additive seam
    ``build_provider``/``build_media``/``build_speech``/
    ``build_communication_provider`` already accept for their own,
    different axis (which credential) - applied here to which real
    *implementation* a builder that has more than one should use.
    Omitted, every existing caller reproduces this function's prior
    behaviour exactly.

    Fails fast and loudly on a name that matches neither a built-in
    builder nor a declared service, the same rule every other factory
    in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.connection_provider]
    except KeyError:
        declared = _build_declared(settings.connection_provider)
        if declared is not None:
            logger.info("connection ready: %s (declared service)", declared.name)
            return declared
        site = _build_site(settings.connection_provider)
        if site is not None:
            logger.info("connection ready: %s (declared site)", site.name)
            return site
        raise ValueError(
            f"Unknown connection provider {settings.connection_provider!r}. "
            f"Available built-in: {', '.join(available_connection_providers())}. "
            "A name not listed there must first be registered with "
            "'services <name> declare ...' or 'sites <name> declare ...' "
            "(see Control Center)."
        ) from None

    connection = builder(settings, adapter_version)
    logger.info("connection ready: %s", connection.name)
    return connection


def _build_declared(name: str) -> "Connection | None":
    """``name`` as a declared service, built into a real
    ``DeclaredConnection`` with whatever credential is already on file
    for it - or ``None`` if nothing was ever declared under that name.
    Imported locally, the identical reason ``_build_gmail`` imports its
    own dependencies only when selected: a plain "null"/"gmail" run
    never touches this module, or the credential-store file, at all.
    """
    from src.providers.declared_connection import DeclaredConnection
    from src.services.connections import service_registry as service_registry_module
    from src.services.credentials import account_store_file as account_store_file_module

    # Read the module's own DEFAULT_*_PATH name fresh on every call
    # (never a function's own default-parameter value, which would bind
    # once at that function's *definition* time) - the identical
    # explicit-argument shape run_connections_command's own
    # DEFAULT_ACCOUNTS_PATH import already uses, so a test can
    # monkeypatch either module's path constant and have this function
    # honour it.
    registry = service_registry_module.load_service_registry(
        service_registry_module.DEFAULT_SERVICES_PATH
    )
    descriptor = registry.get(name)
    if descriptor is None:
        return None
    store = account_store_file_module.load_credential_store(
        account_store_file_module.DEFAULT_ACCOUNTS_PATH
    )
    account = store.active(descriptor.name)
    return DeclaredConnection(descriptor, account)


def _build_site(name: str) -> "Connection | None":
    """``name`` as a declared website, built into a real
    ``WebsiteConnection`` with whatever credential is already on file
    for it - or ``None`` if nothing was ever declared under that name.
    Tried only after ``_build_declared`` finds nothing (a name is
    either a declared REST service or a declared site, never both -
    ``ServiceDescriptor``/``SiteDescriptor`` each reserve "null"/
    "gmail" for the same reason, and a person declaring the identical
    name under both registries gets the REST one, deterministically,
    never an ambiguity surfaced mid-conversation). Imported locally,
    the identical reason ``_build_declared`` imports its own
    dependencies only when selected: a plain "null"/"gmail" run - or
    one selecting a declared REST service - never touches the browser
    backend or the site registry file at all.
    """
    from src.providers.site_descriptor import SiteDescriptor  # noqa: F401 - documents the real shape read below
    from src.providers.website_connection import WebsiteConnection
    from src.services.connections import site_registry as site_registry_module
    from src.services.credentials import account_store_file as account_store_file_module

    registry = site_registry_module.load_site_registry(site_registry_module.DEFAULT_SITES_PATH)
    descriptor = registry.get(name)
    if descriptor is None:
        return None
    store = account_store_file_module.load_credential_store(
        account_store_file_module.DEFAULT_ACCOUNTS_PATH
    )
    account = store.active(descriptor.name)
    return WebsiteConnection(descriptor, account)
