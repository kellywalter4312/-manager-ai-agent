"""Which registered categories are the *provider axis*, and which backend
keys belong to it - asked of the live ``EcosystemRegistry``, never listed.

Two places need this answer and used to keep their own copy of it: the
ecosystem inventory (``src/services/evolution/inventory.py``), which lists
every provider category and keeps provider backends out of its additive
connections listing, and the Control Center's ``connections`` command
(``scripts/control_center.py``), which does the same for the accounts it
shows and redirects a provider backend to ``provider <category> ...``. Both
copies were hand-kept, both had fallen behind the registry, and they had
fallen behind by different amounts. One derivation here is what keeps them
from disagreeing again.

**One declaration stays a declaration.** ``CONNECTION_CATEGORIES`` is not
inferred. Nothing in the registry says a category is a connection rather than
a provider - both kinds are single-active adapters - so which categories are
reported as connections is architectural metadata, stated once, here.

**The rest is derived.** A provider-axis category is every other registered
category whose adapter advertises ``ACTIVATE``. ``CategoryAdapter.active()``
already makes every category single-active; ``ACTIVATE`` separates the ones
with a real construction path to switch to from a read-only catalog, which
keeps only the conservative ``DISCOVER``/``SELECT`` default because its
entries are never backends anything activates. A category registered later
joins the axis with nothing here changing.
"""

from __future__ import annotations

from src.services.ecosystem.contracts import EcosystemRegistry, LifecycleOperation

# Reported as connections, never as provider backends: each has its own
# registered backend catalog, and each sits beside the additive,
# arbitrarily-named services the `connections` command manages through the
# same CredentialStore.
CONNECTION_CATEGORIES: tuple[str, ...] = ("connection", "communication_provider")


def provider_axis_categories(registry: EcosystemRegistry) -> tuple[str, ...]:
    """Every registered category that is a selectable backend axis."""
    categories = []
    for category in registry.categories():
        if category in CONNECTION_CATEGORIES:
            continue
        adapter = registry.adapter_for(category)
        if adapter is not None and LifecycleOperation.ACTIVATE in adapter.supported_operations():
            categories.append(category)
    return tuple(categories)


def provider_axis_backend_names(registry: EcosystemRegistry) -> frozenset[str]:
    """Every backend key of every provider-axis category - the names an
    account can be stored under without being an external connection."""
    return frozenset(
        descriptor.key
        for category in provider_axis_categories(registry)
        for descriptor in registry.adapter_for(category).available()
    )
