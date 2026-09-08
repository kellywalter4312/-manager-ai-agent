"""Turns a configured document-rendering backend *name* into a real
provider instance - the identical small-registry shape every other
factory in this project already is.

Unlike ``video_generation``'s axis, the one real backend here
(``simple_pdf``) needs no vendor credential at all - the identical
"local, deterministic, zero external risk" position
``celebration_provider``/``calendar_event_provider`` already justify
defaulting to their own real implementation rather than an opt-in
"null". Document rendering defaults the same way, for the same reason.
"""

from __future__ import annotations

from typing import Callable

from src.config.settings import Settings
from src.providers.documents.pdf import DocumentRenderer, NullDocumentRenderer, SimplePDFRenderer

_BUILDERS: "dict[str, Callable[[], DocumentRenderer]]" = {
    "null": NullDocumentRenderer,
    "simple_pdf": SimplePDFRenderer,
}


def available_document_rendering_backends() -> list[str]:
    return sorted(_BUILDERS)


def build_document_rendering(settings: Settings) -> DocumentRenderer:
    try:
        builder = _BUILDERS[settings.document_rendering]
    except KeyError:
        raise ValueError(
            f"Unknown document-rendering backend {settings.document_rendering!r}. "
            f"Available: {', '.join(available_document_rendering_backends())}"
        ) from None
    return builder()
