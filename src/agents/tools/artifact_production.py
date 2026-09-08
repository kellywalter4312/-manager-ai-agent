"""The governed boundary between Alfred's real reasoning/production work
and a real, persistent, versioned artifact - Alfred's multimodal
workspace foundation. Genuinely different from every prior production
tool in this project: ``DocumentGenerationTool`` writes one flat,
disposable text file per call; this tool creates and revises a real
``Artifact`` (``src/services/artifacts/store.py``) with a stable
identity, a real version chain, and, where a rendering engine is
configured, a real exported file (a PDF today) - reopenable, revisable
through the same identity, and visible to an operator (the Control
Center) through the identical, authoritative store this tool itself
writes through.

**Agent-independent, on purpose.** Nothing here names Alfred. ``owner_agent``
is a plain string supplied at construction, the identical "identity is a
string, never a class" discipline ``ManagerCapability``/``ProviderAccount``
already hold themselves to - a future specialist gets its own artifacts
by being handed its own instance of this exact class, never a
duplicated implementation. ``get``/``revise_document`` check ownership
before acting - an artifact belonging to a different agent (a real
possibility once a second agent shares this same ``ArtifactStore``) is
refused exactly as if it did not exist, never revealed or revisable by
an instance that merely knows its id.

**One tool, four narrow operations** - the same "one name, several
provider-backed questions" shape ``MediaProductionTool`` already
establishes: ``create_document`` and ``revise_document`` write a real
version; ``get`` and ``find_latest`` are real, governed reads a
capability handler needs (to show a Brain an existing version's real
content before revising it, or to resolve "the handout" with no
explicit id named) - reading through a Tool is an ordinary, already-
established pattern (``KnowledgeLookupTool``, ``CommunicationReadTool``),
not something reserved for writes alone.

**Composing the content is not this tool's job.** Identical discipline
to every other Tool in this project: this class has no route to a
conversation, an objective, or the Brain. It is handed already-finished,
already-structured ``DocumentContent`` JSON and writes/renders exactly
that - see ``src/manager/capabilities.py``'s ``create_artifact``/
``revise_artifact`` for where that structure actually gets composed.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.documents.pdf import DocumentRenderError, DocumentRenderer
from src.services.artifacts.models import DocumentContent
from src.services.artifacts.store import ArtifactError, ArtifactStore

DOCUMENT_TYPE = "document"


class ArtifactProductionTool(Tool):
    """Wraps one ``ArtifactStore`` and one ``DocumentRenderer`` behind
    the ordinary ``Tool`` contract."""

    def __init__(
        self, store: ArtifactStore, renderer: DocumentRenderer, *, owner_agent: str
    ) -> None:
        self._store = store
        self._renderer = renderer
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="artifact_production",
            description=(
                "Creates or revises a real, persistent, versioned document artifact "
                "(and its PDF rendition, when a rendering engine is configured), or "
                "reads back an existing one - never a fabricated file or record."
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
        if operation == "get":
            return self._get(content)
        if operation == "find_latest":
            return self._find_latest(content)
        raise ValueError(
            f"unknown artifact_production operation {operation!r}; expected one of: "
            "create_document, revise_document, get, find_latest"
        )

    # ------------------------------------------------------------------

    def _render(self, doc: DocumentContent, version_dir: Path) -> tuple[dict, str]:
        """Real PDF bytes when a rendering engine is configured, an
        honest empty rendition set (never a fabricated one) otherwise -
        rendering failure never blocks the document's own content from
        being created, since the structured content is the artifact's
        real, canonical form regardless of which export formats are
        currently available for it."""
        try:
            pdf_bytes = self._renderer.render(doc)
        except DocumentRenderError as exc:
            return {}, str(exc)
        pdf_path = version_dir / "document.pdf"
        pdf_path.write_bytes(pdf_bytes)
        return {"pdf": pdf_path.as_posix()}, ""

    def _create_document(self, content: str) -> str:
        doc = DocumentContent.from_json(content)

        artifact_id = uuid.uuid4().hex[:12]
        version_dir = self._store.artifact_dir(artifact_id) / "v1"
        version_dir.mkdir(parents=True, exist_ok=True)
        content_path = version_dir / "content.json"
        content_path.write_text(doc.to_json(), encoding="utf-8")

        renditions, render_note = self._render(doc, version_dir)

        artifact = self._store.create(
            artifact_id=artifact_id,
            type=DOCUMENT_TYPE,
            owner_agent=self._owner_agent,
            title=doc.title,
            content_path=content_path.as_posix(),
            renditions=renditions,
        )
        return self._describe("created", artifact, content_path, renditions, render_note)

    def _revise_document(self, content: str) -> str:
        try:
            artifact_id, json_part = content.split("\n", 1)
        except ValueError:
            raise ValueError(
                "revise_document needs an artifact id on the first line and the "
                "new document JSON on the rest"
            ) from None
        artifact_id = artifact_id.strip()

        existing = self._store.get(artifact_id)
        if existing is None:
            raise ArtifactError(f"no artifact found with id {artifact_id!r}")
        if existing.owner_agent != self._owner_agent:
            # Ownership, not merely existence - a future second agent
            # sharing this same ArtifactStore must not be able to revise
            # another agent's artifact just by knowing its id. The
            # identical boundary ManagerSkills already holds every
            # other agent's own Memory/Brain to.
            raise ArtifactError(f"no artifact found with id {artifact_id!r}")

        doc = DocumentContent.from_json(json_part)
        new_version = existing.latest.version + 1
        version_dir = self._store.artifact_dir(artifact_id) / f"v{new_version}"
        version_dir.mkdir(parents=True, exist_ok=True)
        content_path = version_dir / "content.json"
        content_path.write_text(doc.to_json(), encoding="utf-8")

        renditions, render_note = self._render(doc, version_dir)

        artifact = self._store.revise(
            artifact_id,
            title=doc.title,
            content_path=content_path.as_posix(),
            renditions=renditions,
        )
        return self._describe("revised", artifact, content_path, renditions, render_note)

    def _get(self, content: str) -> str:
        artifact_id = content.strip()
        artifact = self._store.get(artifact_id)
        if artifact is None or artifact.owner_agent != self._owner_agent:
            raise ArtifactError(f"no artifact found with id {artifact_id!r}")
        latest = artifact.latest
        doc = DocumentContent.from_json(Path(latest.content_path).read_text(encoding="utf-8"))
        return doc.as_plain_text()

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
        if "pdf" in renditions:
            line += f" pdf={renditions['pdf']}"
        else:
            line += f" (no PDF produced: {render_note})"
        return line
