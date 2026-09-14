"""The governed boundary between Alfred's real reasoning/production work
and a real, persistent, versioned artifact - Alfred's multimodal
workspace foundation. Genuinely different from every prior production
tool in this project: ``DocumentGenerationTool`` writes one flat,
disposable text file per call; this tool creates and revises a real
``Artifact`` (``src/services/artifacts/store.py``) with a stable
identity, a real version chain, and, where a rendering/generation
engine is configured, a real exported file (a PDF for documents, a PNG
for images/diagrams today) - reopenable, revisable through the same
identity, and visible to an operator (the Control Center) through the
identical, authoritative store this tool itself writes through.

**Two artifact types today, never a closed catalog.** ``document`` (a
real, structured, multi-page-capable handout, rendered to PDF by a
``DocumentRenderer``) and ``image`` (a real picture generated from a
text description by the identical ``ImageProvider``
``media_production``'s own ``illustrate`` operation already uses - a
diagram, a map, an illustration, or any other visual an ``ImageProvider``
can produce, never a hardcoded subject). Both go through the identical
create/revise/get/find_latest shape below; a future third type (video,
audio, code, ...) adds its own content schema
(``src/services/artifacts/models.py``) and its own
``_create_<type>``/``_revise_<type>`` pair here, never a redesign of
``ArtifactStore`` or this tool's own contract.

**Agent-independent, on purpose.** Nothing here names Alfred. ``owner_agent``
is a plain string supplied at construction, the identical "identity is a
string, never a class" discipline ``ManagerCapability``/``ProviderAccount``
already hold themselves to - a future specialist gets its own artifacts
by being handed its own instance of this exact class, never a
duplicated implementation. ``get``/``revise_*`` check ownership before
acting - an artifact belonging to a different agent (a real possibility
once a second agent shares this same ``ArtifactStore``) is refused
exactly as if it did not exist, never revealed or revisable by an
instance that merely knows its id.

**Provider-neutral, on purpose.** ``renderer``/``illustrator`` are the
identical ``DocumentRenderer``/``ImageProvider`` contracts every other
real capability in this project already depends on - never one vendor's
own SDK, never a credential held here. Swapping either changes nothing
about this class, ``create_diagram``/``create_artifact``
(``src/manager/capabilities.py``), or ``ArtifactStore``.

**Composing the content is not this tool's job.** Identical discipline
to every other Tool in this project: this class has no route to a
conversation, an objective, or the Brain. It is handed already-finished,
already-structured content (a ``DocumentContent``/``ImageContent``'s
own JSON) and writes/renders exactly that - see
``src/manager/capabilities.py``'s ``create_artifact``/``revise_artifact``/
``create_diagram``/``revise_diagram`` for where that content actually
gets composed.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.documents.pdf import DocumentRenderError, DocumentRenderer
from src.providers.media.image import ImageError, ImageProvider
from src.services.artifacts.content import TEXT_CONTENT_TYPES, read_artifact_units, units_to_json
from src.services.artifacts.models import DocumentContent, ImageContent
from src.services.artifacts.store import ArtifactError, ArtifactStore

DOCUMENT_TYPE = "document"
IMAGE_TYPE = "image"

_RENDITION_EXTENSION = {DOCUMENT_TYPE: "pdf", IMAGE_TYPE: "png"}


class ArtifactProductionTool(Tool):
    """Wraps one ``ArtifactStore``, one ``DocumentRenderer`` and one
    ``ImageProvider`` behind the ordinary ``Tool`` contract."""

    def __init__(
        self,
        store: ArtifactStore,
        renderer: DocumentRenderer,
        illustrator: ImageProvider,
        *,
        owner_agent: str,
    ) -> None:
        self._store = store
        self._renderer = renderer
        self._illustrator = illustrator
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="artifact_production",
            description=(
                "Creates or revises a real, persistent, versioned artifact - a "
                "document (with a PDF rendition, when configured) or an image/"
                "diagram (with a PNG rendition, when configured) - or reads back "
                "an existing one. Never a fabricated file or record."
            ),
            schema={"operation": str, "content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = arguments["operation"].strip().lower()
        content = arguments["content"]

        if operation == "create_document":
            return self._create_document(content)
        if operation == "revise_document":
            return self._revise_document(content)
        if operation == "create_image":
            return self._create_image(content)
        if operation == "revise_image":
            return self._revise_image(content)
        if operation == "get":
            return self._get(content)
        if operation == "find_latest":
            return self._find_latest(content)
        if operation == "read_content":
            return self._read_content(content)
        if operation == "list_content":
            return self._list_content(content)
        if operation == "derive_document":
            return self._derive_document(content)
        raise ValueError(
            f"unknown artifact_production operation {operation!r}; expected one of: "
            "create_document, revise_document, create_image, revise_image, get, find_latest, "
            "read_content, list_content, derive_document"
        )

    # ------------------------------------------------------------------
    # document
    # ------------------------------------------------------------------

    def _render_document(self, doc: DocumentContent, version_dir: Path) -> tuple[dict, str]:
        """A real plain-text rendition, always - deterministic, needs no
        external engine, and is exactly what keeps a document artifact
        genuinely deliverable even with no PDF renderer configured
        (``document_rendering=null``): the structured content is the
        artifact's real, canonical form regardless of which export
        formats are available, so it must always have at least one real
        way out. A real PDF rendition is added alongside it whenever a
        rendering engine is configured - never instead of the text one,
        the same "a format is a rendition, never the whole definition of
        the artifact" principle this checkpoint's own multi-rendition
        support exists to prove.
        """
        renditions: dict[str, str] = {}
        txt_path = version_dir / "document.txt"
        txt_path.write_text(doc.as_plain_text(), encoding="utf-8")
        renditions["txt"] = txt_path.as_posix()

        try:
            pdf_bytes = self._renderer.render(doc)
        except DocumentRenderError as exc:
            return renditions, str(exc)
        pdf_path = version_dir / "document.pdf"
        pdf_path.write_bytes(pdf_bytes)
        renditions["pdf"] = pdf_path.as_posix()
        return renditions, ""

    def _create_document(self, content: str, *, related_artifact_ids: "tuple[str, ...]" = ()) -> str:
        doc = DocumentContent.from_json(content)

        artifact_id = uuid.uuid4().hex[:12]
        version_dir = self._store.artifact_dir(artifact_id) / "v1"
        version_dir.mkdir(parents=True, exist_ok=True)
        content_path = version_dir / "content.json"
        content_path.write_text(doc.to_json(), encoding="utf-8")

        renditions, render_note = self._render_document(doc, version_dir)

        artifact = self._store.create(
            artifact_id=artifact_id,
            type=DOCUMENT_TYPE,
            owner_agent=self._owner_agent,
            title=doc.title,
            content_path=content_path.as_posix(),
            renditions=renditions,
            related_artifact_ids=related_artifact_ids,
        )
        return self._describe("created", artifact, content_path, renditions, render_note)

    def _derive_document(self, content: str) -> str:
        """A brand-new document artifact produced *from* an existing one -
        a PDF version of a supplied file, an edited copy of it - linked
        back through ``related_artifact_ids``. The source itself is never
        touched: a supplied file stays byte-for-byte what the person gave,
        the identical "source of truth stays read-only" rule this
        project's Memory already lives under."""
        try:
            source_id, json_part = content.split("\n", 1)
        except ValueError:
            raise ValueError(
                "derive_document needs the source artifact id on the first line and the "
                "new document JSON on the rest"
            ) from None
        source = self._owned(source_id)
        return self._create_document(json_part, related_artifact_ids=(source.artifact_id,))

    # ------------------------------------------------------------------
    # content reading - any artifact whose content is text
    # ------------------------------------------------------------------

    def _owned(self, raw_artifact_id: str):
        artifact_id = raw_artifact_id.strip().lower()
        artifact = self._store.get(artifact_id)
        if artifact is None or artifact.owner_agent != self._owner_agent:
            raise ArtifactError(f"no artifact found with id {artifact_id!r}")
        return artifact

    def _read_content(self, content: str) -> str:
        """Every real text unit of one owned artifact, as JSON - a
        supplied PDF's pages, a spreadsheet's rows, a document's sections.
        Raises honestly when there is no readable text (an image, a
        recording, a scanned PDF with no text layer) rather than returning
        an empty reading a caller could mistake for a blank document."""
        artifact = self._owned(content)
        units = read_artifact_units(artifact)
        if not units:
            raise ArtifactError(
                f"artifact {artifact.artifact_id!r} ({artifact.latest.title!r}) has no readable text - "
                "an image, recording or video needs a vision/transcription capability, a scanned "
                "PDF has no text layer, and very large files are not read"
            )
        return units_to_json(artifact, units)

    def _list_content(self, content: str) -> str:
        """This agent's own text-bearing artifacts (supplied files and
        documents), newest first, as JSON - how "the other document" is
        resolved when a conversation named none explicitly."""
        try:
            limit = max(1, int(content.strip() or "5"))
        except ValueError:
            limit = 5
        owned = sorted(
            (
                a for a in self._store.all()
                if a.owner_agent == self._owner_agent and a.type in TEXT_CONTENT_TYPES
            ),
            key=lambda a: a.latest.created_at,
            reverse=True,
        )[:limit]
        if not owned:
            raise ArtifactError(f"no document or supplied file exists yet for {self._owner_agent!r}")
        return json.dumps(
            [{"artifact_id": a.artifact_id, "type": a.type, "title": a.latest.title} for a in owned]
        )

    def _revise_document(self, content: str) -> str:
        try:
            artifact_id, json_part = content.split("\n", 1)
        except ValueError:
            raise ValueError(
                "revise_document needs an artifact id on the first line and the "
                "new document JSON on the rest"
            ) from None
        existing = self._existing_owned(artifact_id, expected_type=DOCUMENT_TYPE)

        doc = DocumentContent.from_json(json_part)
        version_dir, content_path = self._new_version_dir(existing)
        content_path.write_text(doc.to_json(), encoding="utf-8")

        renditions, render_note = self._render_document(doc, version_dir)

        artifact = self._store.revise(
            existing.artifact_id,
            title=doc.title,
            content_path=content_path.as_posix(),
            renditions=renditions,
        )
        return self._describe("revised", artifact, content_path, renditions, render_note)

    # ------------------------------------------------------------------
    # image / diagram
    # ------------------------------------------------------------------

    def _render_image(self, img: ImageContent, version_dir: Path) -> tuple[dict, str]:
        """Real PNG bytes from the configured ``ImageProvider`` - the
        identical provider ``media_production``'s own ``illustrate``
        operation already reaches, never a second image mechanism.
        Generation failure never blocks the artifact's own real content
        (the prompt) from being created and persisted."""
        try:
            asset = self._illustrator.generate(img.prompt)
        except ImageError as exc:
            return {}, str(exc)
        image_path = version_dir / f"image.{asset.format}"
        image_path.write_bytes(asset.data)
        return {asset.format: image_path.as_posix()}, ""

    def _create_image(self, content: str) -> str:
        img = ImageContent.from_json(content)

        artifact_id = uuid.uuid4().hex[:12]
        version_dir = self._store.artifact_dir(artifact_id) / "v1"
        version_dir.mkdir(parents=True, exist_ok=True)
        content_path = version_dir / "content.json"
        content_path.write_text(img.to_json(), encoding="utf-8")

        renditions, render_note = self._render_image(img, version_dir)

        artifact = self._store.create(
            artifact_id=artifact_id,
            type=IMAGE_TYPE,
            owner_agent=self._owner_agent,
            title=img.prompt[:80],
            content_path=content_path.as_posix(),
            renditions=renditions,
        )
        return self._describe("created", artifact, content_path, renditions, render_note)

    def _revise_image(self, content: str) -> str:
        try:
            artifact_id, json_part = content.split("\n", 1)
        except ValueError:
            raise ValueError(
                "revise_image needs an artifact id on the first line and the "
                "new image content JSON on the rest"
            ) from None
        existing = self._existing_owned(artifact_id, expected_type=IMAGE_TYPE)

        img = ImageContent.from_json(json_part)
        version_dir, content_path = self._new_version_dir(existing)
        content_path.write_text(img.to_json(), encoding="utf-8")

        renditions, render_note = self._render_image(img, version_dir)

        artifact = self._store.revise(
            existing.artifact_id,
            title=img.prompt[:80],
            content_path=content_path.as_posix(),
            renditions=renditions,
        )
        return self._describe("revised", artifact, content_path, renditions, render_note)

    # ------------------------------------------------------------------
    # shared reads and helpers
    # ------------------------------------------------------------------

    def _existing_owned(self, raw_artifact_id: str, *, expected_type: str):
        artifact_id = raw_artifact_id.strip()
        existing = self._store.get(artifact_id)
        if existing is None or existing.owner_agent != self._owner_agent:
            # Ownership, not merely existence - a future second agent
            # sharing this same ArtifactStore must not be able to revise
            # another agent's artifact just by knowing its id. The
            # identical boundary ManagerSkills already holds every
            # other agent's own Memory/Brain to.
            raise ArtifactError(f"no artifact found with id {artifact_id!r}")
        if existing.type != expected_type:
            raise ArtifactError(
                f"artifact {artifact_id!r} is a {existing.type!r} artifact, not {expected_type!r}"
            )
        return existing

    def _new_version_dir(self, existing) -> "tuple[Path, Path]":
        new_version = existing.latest.version + 1
        version_dir = self._store.artifact_dir(existing.artifact_id) / f"v{new_version}"
        version_dir.mkdir(parents=True, exist_ok=True)
        return version_dir, version_dir / "content.json"

    def _get(self, content: str) -> str:
        artifact_id = content.strip()
        artifact = self._store.get(artifact_id)
        if artifact is None or artifact.owner_agent != self._owner_agent:
            raise ArtifactError(f"no artifact found with id {artifact_id!r}")
        latest = artifact.latest
        raw = Path(latest.content_path).read_text(encoding="utf-8")
        if artifact.type == IMAGE_TYPE:
            return ImageContent.from_json(raw).prompt
        # DOCUMENT_TYPE, and the honest fallback for any future type
        # this tool does not yet special-case: the canonical content is
        # still real JSON on disk even when this method does not yet
        # know how to summarise it as prose.
        try:
            return DocumentContent.from_json(raw).as_plain_text()
        except ValueError:
            return raw

    def _find_latest(self, content: str) -> str:
        artifact_type = content.strip() or None
        artifact = self._store.find_latest(owner_agent=self._owner_agent, type=artifact_type)
        if artifact is None:
            raise ArtifactError(
                f"no {artifact_type or 'artifact'} exists yet for {self._owner_agent!r}"
            )
        return artifact.artifact_id

    @staticmethod
    def _describe(verb: str, artifact, content_path: Path, renditions: dict, render_note: str) -> str:
        latest = artifact.latest
        line = (
            f"Artifact {verb}: id={artifact.artifact_id} type={artifact.type} "
            f"version={latest.version} title={latest.title!r} content={content_path.as_posix()}"
        )
        wanted = _RENDITION_EXTENSION.get(artifact.type)
        if wanted and wanted in renditions:
            line += f" {wanted}={renditions[wanted]}"
        elif renditions:
            fmt, path = next(iter(renditions.items()))
            line += f" {fmt}={path}"
        else:
            line += f" (no rendition produced: {render_note})"
        return line
