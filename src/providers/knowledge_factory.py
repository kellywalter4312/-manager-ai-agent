"""Turns a configured knowledge-source provider *name* into a provider
*instance*.

The identical small registry ``src/providers/live_info_factory.py``
already is, applied to user-supplied knowledge instead of current
information: a lookup table mapping a string to something that builds the
real thing, so adding a backend later - a different document format, a
personal wiki, a notes service - means adding one entry here, not editing
composition.py or anything under ``src/manager/``.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.document_knowledge_provider import DocumentKnowledgeProvider
from src.providers.knowledge import KnowledgeSourceProvider
from src.providers.null_knowledge_provider import NullKnowledgeProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Values are factory callables rather than instances, so a provider is only
# constructed when it is actually selected - the same reasoning
# src/providers/factory.py's own _BUILDERS already states.
_BUILDERS: dict[str, Callable[[Settings], KnowledgeSourceProvider]] = {
    "null": lambda settings: NullKnowledgeProvider(),
    "documents": lambda settings: DocumentKnowledgeProvider(settings.knowledge_dir),
}


def available_knowledge_providers() -> list[str]:
    """Every knowledge-source provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_knowledge_provider(
    settings: Settings, env: Mapping[str, str] | None = None
) -> KnowledgeSourceProvider:
    """Construct the knowledge source named by ``settings.knowledge_provider``.

    ``env`` is accepted, unused, for signature symmetry with
    ``build_provider`` and ``build_live_info_provider`` - no builder here
    reads a credential, because a folder of the user's own documents needs
    none, but a future one that did would read it the same way
    ``_build_anthropic`` already does.

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.knowledge_provider]
    except KeyError:
        raise ValueError(
            f"Unknown knowledge provider {settings.knowledge_provider!r}. "
            f"Available: {', '.join(available_knowledge_providers())}"
        ) from None

    provider = builder(settings)
    logger.info("knowledge provider ready: %s", provider.name)
    return provider
