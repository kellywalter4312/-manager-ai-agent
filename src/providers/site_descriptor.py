"""A previously-unknown WEBSITE, declared as data - the literal-
website-interaction counterpart to ``service_descriptor.py``'s own
``ServiceDescriptor``, deliberately shaped differently because a
website genuinely is a different kind of thing than a REST service.

**Why this is not a ``ServiceDescriptor``.** A REST service's own
operations (a fixed method, path and parameter list per operation) are
known and worth declaring once, up front - that is what makes
``services <name> declare`` meaningful. A website's usable fields and
controls are not known ahead of time and are not worth declaring: they
exist only on the page, discovered live, every visit, by
``WebsiteConnection`` itself (see that module's own docstring for the
fixed, universal, four-operation vocabulary every declared site shares
identically - ``navigate``/``read_page``/``fill_field``/``click``).
Forcing a website into ``ServiceDescriptor``'s "operations declared up
front" shape would be a semantic misfit, not a genuine capability - so
a ``SiteDescriptor`` declares only what is actually worth declaring
about a site ahead of time: its name, and the one fixed origin it may
ever be reached at.

**No catalogue.** Nothing here names a real company, website or
industry - a ``SiteDescriptor`` is a value a caller constructs from
whatever a person declares, the identical "names come from the
caller, never invented" discipline ``ServiceDescriptor``/
``ProviderAccount`` already hold themselves to.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

# The two connection names connection_factory.py's own _BUILDERS
# reserves for its built-in connections - a declared site must never
# shadow either, the identical guard ServiceDescriptor's own
# RESERVED_SERVICE_NAMES already enforces for a declared REST service.
RESERVED_SITE_NAMES = frozenset({"null", "gmail"})


@dataclass(frozen=True)
class SiteDescriptor:
    """One previously-unknown website, declared as data: a name and a
    single fixed origin. Constructing one never reaches the network and
    never persists anything - see
    ``src/services/connections/site_registry.py`` for that.

    ``auth_cookie_name`` is optional and empty by default (no
    authentication needed at all - the common case for a public site).
    When set, it names the session/auth cookie ``WebsiteConnection``
    primes from whatever ``ProviderAccount`` is already on file for
    this site's own name in the identical, single ``CredentialStore``
    every other connection's own credential already lives in - never a
    second credential mechanism, and never a username/password
    interactive login flow (a real, separate, larger capability this
    checkpoint does not build).
    """

    name: str
    base_url: str
    auth_cookie_name: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        name = " ".join(str(self.name).split()).lower()
        if not name:
            raise ValueError("SiteDescriptor.name must not be empty")
        if name in RESERVED_SITE_NAMES:
            raise ValueError(
                f"'{name}' is a reserved connection name (built into "
                "connection_factory.py) and cannot be declared as a site"
            )
        object.__setattr__(self, "name", name)

        base_url = str(self.base_url).strip().rstrip("/")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"SiteDescriptor.base_url must be an absolute http(s) URL, got {self.base_url!r}")
        object.__setattr__(self, "base_url", base_url)

        object.__setattr__(self, "auth_cookie_name", " ".join(str(self.auth_cookie_name or "").split()))
        object.__setattr__(self, "description", " ".join(str(self.description or "").split()))

    @property
    def requires_auth(self) -> bool:
        return bool(self.auth_cookie_name)
