"""What a real, already-produced artifact rendition can actually be used
for right now - the fourth, genuinely distinct concern this project's
artifact architecture separates:

    Artifact       identity + version chain (store.py::Artifact)
    Rendition      which real files exist for one version
                   (store.py::ArtifactVersion.renditions)
    Presentation   how an interface shows an artifact to a person
                   (src/control_center/snapshot.py, ui/index.html)
    Delivery       what a person can actually DO with it - view it,
                   save it, send it, print it (this module)

None of the first three decide the fourth, and this module decides
nothing about them: it is a pure, computed fact over a real
``ArtifactVersion``, never a configuration a caller sets and never
persisted alongside it - a version's real renditions can only grow
(a re-render, a new provider configured) or the process can restart
with a different one configured, so delivery must always be computed
fresh, never cached as though it were part of the artifact's own
record.

**One real method today: "download".** Retrieving the actual rendered
file over the real, already-existing ``GET /artifact/<id>.<format>``
route (``scripts/serve_ui.py``) - a browser given that URL can both
view a renderable format (an image displays inline; most browsers
preview a PDF natively) and save it, so this one real mechanism
already covers both "view/open" and "download/save" honestly, rather
than this module inventing two names for one thing.

**Deliberately absent, and why.** Printing has no mechanism anywhere in
this codebase to honestly claim - no printer driver, no OS print
integration. Sharing/sending has none either:
``src/services/email/message.py``'s own docstring states plainly that
attachments are "deliberately not modelled", and no other governed
connection in this project can carry a file. Device-to-device sync and
external-service delivery are equally unbuilt. None of these are
listed here, on purpose - a delivery method appears in this module only
once a real mechanism for it exists, never as a placeholder for one
that does not. Adding a genuine one later (say, once attachments exist)
means adding one more real check to ``available_delivery_methods``,
never a redesign of ``Artifact``, ``ArtifactVersion``, or any capability
that already produces one.
"""

from __future__ import annotations

from src.services.artifacts.store import ArtifactVersion

# The complete, honest catalog of delivery methods this project can
# currently back with a real mechanism. Extending this project's real
# capabilities (an attachment-capable connection, a print integration)
# means adding a name here and a real condition in
# available_delivery_methods below - never inventing a new concept.
DOWNLOAD = "download"


def available_delivery_methods(version: "ArtifactVersion") -> "tuple[str, ...]":
    """Which real delivery methods ``version`` can actually use right
    now. Empty when no rendition exists yet (no rendering/generation
    engine configured, or generation genuinely failed) - an honest
    absence, never a method listed because it exists for *some*
    artifact somewhere."""
    if not version.renditions:
        return ()
    return (DOWNLOAD,)
