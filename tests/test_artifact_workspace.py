"""Alfred's multimodal workspace foundation - a real, persistent,
versioned artifact system, proven end-to-end with one real vertical
slice: a document/handout artifact, real PDF export, real Control
Center visibility, and real UI-facing state.

Every test that produces a PDF decodes it back with ``pypdf`` (already
a project dependency, used elsewhere to read knowledge-folder PDFs) -
never merely checking that some bytes exist. The Brain-assisted path
uses a scripted ``LLMProvider`` standing in for the unfunded vendor
account, the same established discipline every other Brain-touching
test in this project already follows.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from src.agents.tools.artifact_production import ArtifactProductionTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.manager.capabilities import (
    CREATE_ARTIFACT,
    CREATE_DIAGRAM,
    MANAGER_CAPABILITIES,
    REVISE_ARTIFACT,
    REVISE_DIAGRAM,
    CapabilityRequest,
    _parse_document_content,
    create_artifact,
    create_artifact_intent,
    create_diagram,
    create_diagram_intent,
    revise_artifact,
    revise_artifact_intent,
    revise_diagram,
    revise_diagram_intent,
    select_capability,
)
from src.providers.documents.factory import (
    available_document_rendering_backends,
    build_document_rendering,
)
from src.providers.documents.pdf import (
    DocumentRenderError,
    NullDocumentRenderer,
    SimplePDFRenderer,
    wrap_text,
)
from src.providers.media.image import ImageError, LocalTitleCardImageProvider, NullImageProvider
from src.services.artifacts.models import DocumentContent, DocumentSection, ImageContent
from src.services.artifacts.store import ArtifactError, ArtifactStore
from src.services.identity.identity import AgentIdentity


# ---------------------------------------------------------------------------
# DocumentContent / DocumentSection
# ---------------------------------------------------------------------------


class TestDocumentModels:
    def test_a_section_needs_a_real_heading_and_body(self):
        with pytest.raises(ValueError, match="heading"):
            DocumentSection(heading="  ", body="real body")
        with pytest.raises(ValueError, match="body"):
            DocumentSection(heading="real heading", body="   ")

    def test_a_document_needs_a_real_title_and_at_least_one_section(self):
        with pytest.raises(ValueError, match="title"):
            DocumentContent(title="  ", sections=(DocumentSection("H", "B"),))
        with pytest.raises(ValueError, match="section"):
            DocumentContent(title="Real Title", sections=())

    def test_json_round_trip_preserves_content(self):
        doc = DocumentContent(
            title="A Real Title",
            sections=(
                DocumentSection("First", "First body."),
                DocumentSection("Second", "Second body."),
            ),
        )
        restored = DocumentContent.from_json(doc.to_json())
        assert restored.title == doc.title
        assert [(s.heading, s.body) for s in restored.sections] == [
            (s.heading, s.body) for s in doc.sections
        ]

    def test_from_json_rejects_malformed_input(self):
        with pytest.raises(ValueError):
            DocumentContent.from_json("not json at all")
        with pytest.raises(ValueError):
            DocumentContent.from_json("[]")

    def test_as_plain_text_contains_title_and_every_section(self):
        doc = DocumentContent(
            title="My Title", sections=(DocumentSection("Origins", "It started long ago."),)
        )
        text = doc.as_plain_text()
        assert "My Title" in text
        assert "Origins" in text
        assert "It started long ago." in text


class TestImageContentModel:
    def test_needs_a_real_prompt(self):
        with pytest.raises(ValueError, match="prompt"):
            ImageContent(prompt="   ")

    def test_json_round_trip_preserves_the_prompt(self):
        img = ImageContent(prompt="a labeled diagram of the human skeletal system")
        restored = ImageContent.from_json(img.to_json())
        assert restored.prompt == img.prompt

    def test_from_json_rejects_malformed_input(self):
        with pytest.raises(ValueError):
            ImageContent.from_json("not json at all")
        with pytest.raises(ValueError):
            ImageContent.from_json("[]")


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


class TestArtifactStore:
    def test_create_then_get_round_trips(self, tmp_path):
        store = ArtifactStore(tmp_path)
        artifact = store.create(
            artifact_id="abc123", type="document", owner_agent="Alfred",
            title="A Title", content_path="content.json",
        )
        assert artifact.artifact_id == "abc123"
        assert artifact.latest.version == 1
        fetched = store.get("abc123")
        assert fetched is not None
        assert fetched.latest.title == "A Title"

    def test_duplicate_id_is_refused(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(artifact_id="x", type="document", owner_agent="A", title="T", content_path="c")
        with pytest.raises(ArtifactError, match="already exists"):
            store.create(artifact_id="x", type="document", owner_agent="A", title="T2", content_path="c2")

    def test_revising_a_nonexistent_artifact_is_refused(self, tmp_path):
        store = ArtifactStore(tmp_path)
        with pytest.raises(ArtifactError, match="no artifact found"):
            store.revise("ghost", content_path="c")

    def test_revise_adds_a_new_version_under_the_same_identity(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(artifact_id="x", type="document", owner_agent="A", title="T1", content_path="c1")
        revised = store.revise("x", title="T2", content_path="c2")
        assert revised.artifact_id == "x"
        assert len(revised.versions) == 2
        assert revised.latest.version == 2
        assert revised.latest.title == "T2"
        assert revised.versions[0].title == "T1"

    def test_revise_without_a_title_keeps_the_previous_one(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(artifact_id="x", type="document", owner_agent="A", title="Kept", content_path="c1")
        revised = store.revise("x", content_path="c2")
        assert revised.latest.title == "Kept"

    def test_all_returns_every_artifact(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(artifact_id="a", type="document", owner_agent="A", title="T", content_path="c")
        store.create(artifact_id="b", type="document", owner_agent="A", title="T", content_path="c")
        assert {a.artifact_id for a in store.all()} == {"a", "b"}

    def test_find_latest_filters_by_owner_and_type(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(artifact_id="a", type="document", owner_agent="Alfred", title="T", content_path="c")
        store.create(artifact_id="b", type="document", owner_agent="Arthur", title="T", content_path="c")
        assert store.find_latest(owner_agent="Arthur").artifact_id == "b"
        assert store.find_latest(owner_agent="Nobody") is None
        assert store.find_latest(type="image") is None

    def test_find_latest_prefers_the_most_recently_updated(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(artifact_id="a", type="document", owner_agent="Alfred", title="Old", content_path="c")
        store.create(artifact_id="b", type="document", owner_agent="Alfred", title="New", content_path="c")
        store.revise("a", content_path="c2")  # touch "a" last
        assert store.find_latest(owner_agent="Alfred").artifact_id == "a"

    def test_persists_across_a_fresh_instance(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(artifact_id="a", type="document", owner_agent="Alfred", title="T", content_path="c")
        reloaded = ArtifactStore(tmp_path)
        artifact = reloaded.get("a")
        assert artifact is not None
        assert artifact.latest.title == "T"

    def test_a_damaged_index_recovers_to_an_empty_store_rather_than_crashing(self, tmp_path):
        (tmp_path / "artifacts.json").write_text("{ not valid json", encoding="utf-8")
        store = ArtifactStore(tmp_path)
        assert store.all() == ()

    def test_a_missing_index_file_is_not_an_error(self, tmp_path):
        store = ArtifactStore(tmp_path / "does-not-exist-yet")
        assert store.all() == ()

    def test_artifact_dir_is_scoped_per_artifact(self, tmp_path):
        store = ArtifactStore(tmp_path)
        assert store.artifact_dir("abc") == tmp_path / "abc"


# ---------------------------------------------------------------------------
# SimplePDFRenderer
# ---------------------------------------------------------------------------


def _doc(n_sections: int = 2, body_repeat: int = 1) -> DocumentContent:
    sections = tuple(
        DocumentSection(f"Section {i}", ("Real body content. " * body_repeat).strip())
        for i in range(n_sections)
    )
    return DocumentContent(title="A Real Test Document", sections=sections)


class TestSimplePDFRenderer:
    def test_produces_real_decodable_pdf_bytes(self):
        from pypdf import PdfReader

        pdf_bytes = SimplePDFRenderer().render(_doc())
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert pdf_bytes.rstrip().endswith(b"%%EOF")
        reader = PdfReader(io.BytesIO(pdf_bytes))
        assert len(reader.pages) >= 1
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        assert "A Real Test Document" in text
        assert "Section 0" in text

    def test_long_content_produces_multiple_real_pages(self):
        from pypdf import PdfReader

        doc = _doc(n_sections=8, body_repeat=60)
        pdf_bytes = SimplePDFRenderer().render(doc)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        assert len(reader.pages) > 1
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        for i in range(8):
            assert f"Section {i}" in text

    def test_every_section_heading_and_body_is_present(self):
        from pypdf import PdfReader

        doc = DocumentContent(
            title="Volcanoes",
            sections=(
                DocumentSection("Formation", "Magma reaches the surface."),
                DocumentSection("Famous Eruptions", "Pompeii was destroyed in 79 AD."),
            ),
        )
        pdf_bytes = SimplePDFRenderer().render(doc)
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages)
        assert "Formation" in text
        assert "Magma reaches the surface." in text
        assert "Famous Eruptions" in text
        assert "Pompeii was destroyed in 79 AD." in text

    def test_wrap_text_never_breaks_a_word(self):
        long_word = "x" * 500
        lines = wrap_text(f"a short word then {long_word} then more", 11.0)
        assert long_word in lines[[long_word in l for l in lines].index(True)]

    def test_name_is_simple_pdf(self):
        assert SimplePDFRenderer().name == "simple_pdf"


class TestNullDocumentRenderer:
    def test_refuses_honestly_rather_than_inventing_a_pdf(self):
        with pytest.raises(DocumentRenderError, match="no document-rendering engine"):
            NullDocumentRenderer().render(_doc())

    def test_name_is_null(self):
        assert NullDocumentRenderer().name == "null"


# ---------------------------------------------------------------------------
# Delivery - the fourth, distinct concern (Artifact -> Rendition ->
# Presentation -> Delivery). A pure, computed fact, never persisted.
# ---------------------------------------------------------------------------


class TestAvailableDeliveryMethods:
    def _version(self, renditions):
        from datetime import datetime, timezone

        from src.services.artifacts.store import ArtifactVersion

        return ArtifactVersion(
            version=1, title="t", status="ready",
            created_at=datetime.now(timezone.utc), content_path="c", renditions=renditions,
        )

    def test_no_rendition_means_no_delivery_method(self):
        from src.services.artifacts.delivery import available_delivery_methods

        assert available_delivery_methods(self._version({})) == ()

    def test_a_real_rendition_makes_download_available(self):
        from src.services.artifacts.delivery import DOWNLOAD, available_delivery_methods

        methods = available_delivery_methods(self._version({"pdf": "x.pdf"}))
        assert methods == (DOWNLOAD,)

    def test_the_same_method_regardless_of_which_rendition_format(self):
        """The method is genuinely computed from "does a real file
        exist", never keyed to one specific format - a future rendition
        format needs no change here."""
        from src.services.artifacts.delivery import available_delivery_methods

        assert available_delivery_methods(self._version({"png": "x.png"})) == \
            available_delivery_methods(self._version({"pdf": "x.pdf"}))

    def test_never_claims_printing_sharing_or_sync(self):
        """No real mechanism for these exists anywhere in this project -
        asserted here so a future addition cannot silently start
        claiming one without a deliberate, real implementation."""
        from src.services.artifacts.delivery import available_delivery_methods

        methods = available_delivery_methods(self._version({"pdf": "x.pdf"}))
        for unsupported in ("print", "email", "share", "sync", "send"):
            assert unsupported not in methods


# ---------------------------------------------------------------------------
# Document-rendering factory / provider independence
# ---------------------------------------------------------------------------


class TestDocumentRenderingFactory:
    def test_available_backends(self):
        assert available_document_rendering_backends() == ["null", "simple_pdf"]

    def test_default_is_simple_pdf_not_null(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={})
        assert settings.document_rendering == "simple_pdf"
        renderer = build_document_rendering(settings)
        assert isinstance(renderer, SimplePDFRenderer)

    def test_null_backend_selectable(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, document_rendering="null")
        renderer = build_document_rendering(settings)
        assert isinstance(renderer, NullDocumentRenderer)

    def test_unknown_backend_is_refused(self):
        from src.config.settings import Settings

        with pytest.raises(ValueError, match="Unknown document-rendering backend"):
            build_document_rendering(Settings.from_env(env={}, document_rendering="not-real"))

    def test_needs_no_credential_ever(self):
        """Provider independence: rendering never touches an LLM
        provider or any credential store - real, local, deterministic
        regardless of which (if any) LLM vendor is configured."""
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, provider="null", document_rendering="simple_pdf")
        renderer = build_document_rendering(settings)
        assert isinstance(renderer, SimplePDFRenderer)
        # No API key of any kind was read to construct this.
        assert renderer.render(_doc())  # genuinely works with zero LLM configured


# ---------------------------------------------------------------------------
# ArtifactProductionTool
# ---------------------------------------------------------------------------


class TestArtifactProductionTool:
    def _tool(self, tmp_path, renderer=None, illustrator=None, owner_agent="Alfred"):
        store = ArtifactStore(tmp_path / "artifacts")
        return (
            ArtifactProductionTool(
                store, renderer or SimplePDFRenderer(), illustrator or NullImageProvider(),
                owner_agent=owner_agent,
            ),
            store,
        )

    def test_create_document_produces_a_real_artifact_and_pdf(self, tmp_path):
        tool, store = self._tool(tmp_path)
        doc = _doc()
        output = tool.run(operation="create_document", content=doc.to_json())
        assert "Artifact created" in output
        assert "pdf=" in output
        artifacts = store.all()
        assert len(artifacts) == 1
        assert artifacts[0].owner_agent == "Alfred"
        assert artifacts[0].type == "document"
        pdf_path = artifacts[0].latest.renditions["pdf"]
        assert pdf_path and __import__("pathlib").Path(pdf_path).exists()

    def test_create_document_with_null_renderer_still_creates_real_content(self, tmp_path):
        """Rendering failure never blocks the artifact's own real,
        canonical content from being created - and, since a document
        always gets a real plain-text rendition regardless of whether a
        PDF engine is configured, it stays genuinely deliverable even
        with none."""
        tool, store = self._tool(tmp_path, renderer=NullDocumentRenderer())
        output = tool.run(operation="create_document", content=_doc().to_json())
        assert "Artifact created" in output
        assert "txt=" in output
        artifact = store.all()[0]
        assert artifact.latest.status == "ready"
        assert set(artifact.latest.renditions) == {"txt"}
        assert "A Real Test Document" in Path(artifact.latest.renditions["txt"]).read_text(encoding="utf-8")

    def test_create_document_rejects_malformed_content(self, tmp_path):
        tool, _ = self._tool(tmp_path)
        with pytest.raises(ValueError):
            tool.run(operation="create_document", content="not json")

    def test_revise_document_adds_a_real_new_version(self, tmp_path):
        tool, store = self._tool(tmp_path)
        tool.run(operation="create_document", content=_doc().to_json())
        artifact_id = store.all()[0].artifact_id
        new_doc = DocumentContent(title="Revised Title", sections=(DocumentSection("New", "New body."),))
        output = tool.run(operation="revise_document", content=f"{artifact_id}\n{new_doc.to_json()}")
        assert "Artifact revised" in output
        artifact = store.get(artifact_id)
        assert artifact.latest.version == 2
        assert artifact.latest.title == "Revised Title"

    def test_revise_document_of_a_nonexistent_artifact_is_refused(self, tmp_path):
        tool, _ = self._tool(tmp_path)
        with pytest.raises(ArtifactError):
            tool.run(operation="revise_document", content=f"ghost\n{_doc().to_json()}")

    def test_revise_document_without_a_newline_is_refused(self, tmp_path):
        tool, _ = self._tool(tmp_path)
        with pytest.raises(ValueError, match="needs an artifact id"):
            tool.run(operation="revise_document", content="no-newline-here")

    def test_get_returns_the_current_versions_plain_text(self, tmp_path):
        tool, store = self._tool(tmp_path)
        tool.run(operation="create_document", content=_doc().to_json())
        artifact_id = store.all()[0].artifact_id
        text = tool.run(operation="get", content=artifact_id)
        assert "A Real Test Document" in text

    def test_get_of_a_nonexistent_artifact_is_refused(self, tmp_path):
        tool, _ = self._tool(tmp_path)
        with pytest.raises(ArtifactError):
            tool.run(operation="get", content="ghost")

    def test_find_latest_returns_the_id_only(self, tmp_path):
        tool, store = self._tool(tmp_path)
        tool.run(operation="create_document", content=_doc().to_json())
        artifact_id = store.all()[0].artifact_id
        found = tool.run(operation="find_latest", content="document")
        assert found == artifact_id

    def test_find_latest_with_none_existing_is_refused(self, tmp_path):
        tool, _ = self._tool(tmp_path)
        with pytest.raises(ArtifactError, match="no document exists"):
            tool.run(operation="find_latest", content="document")

    def test_unknown_operation_is_refused(self, tmp_path):
        tool, _ = self._tool(tmp_path)
        with pytest.raises(ValueError, match="unknown artifact_production operation"):
            tool.run(operation="bogus", content="x")

    def test_agent_independence_a_different_owner_name_works_identically(self, tmp_path):
        """Nothing in this tool is Alfred-specific - a future specialist
        gets its own artifacts by construction with its own name, no
        code change."""
        tool, store = self._tool(tmp_path, owner_agent="Arthur")
        tool.run(operation="create_document", content=_doc().to_json())
        assert store.all()[0].owner_agent == "Arthur"
        found = tool.run(operation="find_latest", content="document")
        assert found == store.all()[0].artifact_id

    def test_an_agent_cannot_read_another_agents_artifact_by_guessing_its_id(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        alfred_tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        alfred_tool.run(operation="create_document", content=_doc().to_json())
        artifact_id = store.all()[0].artifact_id

        arthur_tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Arthur")
        with pytest.raises(ArtifactError, match="no artifact found"):
            arthur_tool.run(operation="get", content=artifact_id)

    def test_an_agent_cannot_revise_another_agents_artifact_by_guessing_its_id(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        alfred_tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        alfred_tool.run(operation="create_document", content=_doc().to_json())
        artifact_id = store.all()[0].artifact_id

        arthur_tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Arthur")
        new_doc = DocumentContent(title="Hijacked", sections=(DocumentSection("X", "Y"),))
        with pytest.raises(ArtifactError, match="no artifact found"):
            arthur_tool.run(operation="revise_document", content=f"{artifact_id}\n{new_doc.to_json()}")
        assert store.get(artifact_id).latest.version == 1
        assert store.get(artifact_id).latest.title != "Hijacked"

    def test_source_content_survives_a_render_engine_swap(self, tmp_path):
        """Provider independence: the same real content renders through
        whichever DocumentRenderer this tool was built with - never
        coupled to one implementation."""
        store = ArtifactStore(tmp_path / "artifacts")
        tool_a = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        tool_a.run(operation="create_document", content=_doc().to_json())
        artifact_id = store.all()[0].artifact_id
        assert "pdf" in store.get(artifact_id).latest.renditions

        tool_b = ArtifactProductionTool(store, NullDocumentRenderer(), NullImageProvider(), owner_agent="Alfred")
        new_doc = DocumentContent(title="No PDF This Time", sections=(DocumentSection("X", "Y"),))
        tool_b.run(operation="revise_document", content=f"{artifact_id}\n{new_doc.to_json()}")
        # No PDF engine this time, but the real plain-text rendition
        # always exists regardless - the document stays deliverable.
        assert set(store.get(artifact_id).latest.renditions) == {"txt"}


# ---------------------------------------------------------------------------
# ArtifactProductionTool - image/diagram operations. The identical
# create/revise/get/find_latest shape as documents, a different real
# provider (ImageProvider, not DocumentRenderer) backing the actual
# rendition - proof the artifact system is genuinely type-extensible
# rather than redesigned around PDFs.
# ---------------------------------------------------------------------------


def _real_image_bytes_are_decodable(path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as decoded:
        decoded.load()
        return decoded.size


class TestArtifactProductionToolImages:
    def _tool(self, tmp_path, illustrator=None, owner_agent="Alfred"):
        store = ArtifactStore(tmp_path / "artifacts")
        tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), illustrator or LocalTitleCardImageProvider(),
            owner_agent=owner_agent,
        )
        return tool, store

    def test_create_image_produces_a_real_artifact_and_png(self, tmp_path):
        tool, store = self._tool(tmp_path)
        content = ImageContent(prompt="a labeled diagram of the water cycle").to_json()
        output = tool.run(operation="create_image", content=content)
        assert "Artifact created" in output
        assert "png=" in output

        artifacts = store.all()
        assert len(artifacts) == 1
        assert artifacts[0].type == "image"
        png_path = artifacts[0].latest.renditions["png"]
        width, height = _real_image_bytes_are_decodable(png_path)
        assert width > 0 and height > 0

    def test_create_image_with_null_illustrator_still_creates_real_content(self, tmp_path):
        """Generation failure never blocks the artifact's own real,
        canonical content (the prompt) from being created - the
        identical honesty PDF rendering failure already gets."""
        tool, store = self._tool(tmp_path, illustrator=NullImageProvider())
        output = tool.run(operation="create_image", content=ImageContent(prompt="a map of europe").to_json())
        assert "Artifact created" in output
        assert "no rendition produced" in output
        artifact = store.all()[0]
        assert artifact.latest.status == "ready"
        assert artifact.latest.renditions == {}

    def test_create_image_rejects_malformed_content(self, tmp_path):
        tool, _ = self._tool(tmp_path)
        with pytest.raises(ValueError):
            tool.run(operation="create_image", content="not json")

    def test_revise_image_adds_a_real_new_version(self, tmp_path):
        tool, store = self._tool(tmp_path)
        tool.run(operation="create_image", content=ImageContent(prompt="a diagram of a plant cell").to_json())
        artifact_id = store.all()[0].artifact_id

        output = tool.run(
            operation="revise_image",
            content=f"{artifact_id}\n{ImageContent(prompt='a revised diagram of a plant cell with labels').to_json()}",
        )
        assert "Artifact revised" in output
        artifact = store.get(artifact_id)
        assert artifact.latest.version == 2
        assert "labels" in artifact.latest.title

    def test_revise_image_of_a_nonexistent_artifact_is_refused(self, tmp_path):
        tool, _ = self._tool(tmp_path)
        with pytest.raises(ArtifactError):
            tool.run(operation="revise_image", content=f"ghost\n{ImageContent(prompt='x').to_json()}")

    def test_a_document_artifact_cannot_be_revised_as_an_image(self, tmp_path):
        """Type safety: revise_image must refuse an id that names a
        document artifact rather than silently reinterpreting it."""
        store = ArtifactStore(tmp_path / "artifacts")
        tool = ArtifactProductionTool(
            store, SimplePDFRenderer(), LocalTitleCardImageProvider(), owner_agent="Alfred"
        )
        tool.run(operation="create_document", content=_doc().to_json())
        artifact_id = store.all()[0].artifact_id
        with pytest.raises(ArtifactError, match="not 'image'"):
            tool.run(operation="revise_image", content=f"{artifact_id}\n{ImageContent(prompt='x').to_json()}")

    def test_get_returns_the_prompt_for_an_image_artifact(self, tmp_path):
        tool, store = self._tool(tmp_path)
        tool.run(operation="create_image", content=ImageContent(prompt="a diagram of the solar system").to_json())
        artifact_id = store.all()[0].artifact_id
        text = tool.run(operation="get", content=artifact_id)
        assert text == "a diagram of the solar system"

    def test_find_latest_scoped_to_image_type(self, tmp_path):
        tool, store = self._tool(tmp_path)
        tool.run(operation="create_image", content=ImageContent(prompt="a map").to_json())
        artifact_id = store.all()[0].artifact_id
        assert tool.run(operation="find_latest", content="image") == artifact_id

    def test_agent_independence_a_different_owner_name_works_identically(self, tmp_path):
        tool, store = self._tool(tmp_path, owner_agent="Arthur")
        tool.run(operation="create_image", content=ImageContent(prompt="a diagram").to_json())
        assert store.all()[0].owner_agent == "Arthur"

    def test_an_agent_cannot_revise_another_agents_image_by_guessing_its_id(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        alfred_tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), LocalTitleCardImageProvider(), owner_agent="Alfred"
        )
        alfred_tool.run(operation="create_image", content=ImageContent(prompt="a diagram").to_json())
        artifact_id = store.all()[0].artifact_id

        arthur_tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), LocalTitleCardImageProvider(), owner_agent="Arthur"
        )
        with pytest.raises(ArtifactError, match="no artifact found"):
            arthur_tool.run(
                operation="revise_image", content=f"{artifact_id}\n{ImageContent(prompt='hijacked').to_json()}"
            )

    def test_provider_independence_image_generation_swaps_cleanly(self, tmp_path):
        """The document renderer and the image provider are entirely
        independent collaborators - swapping one never touches the
        other, and neither ever depends on which (if any) LLM vendor is
        configured."""
        tool, store = self._tool(tmp_path, illustrator=LocalTitleCardImageProvider())
        tool.run(operation="create_document", content=_doc().to_json())
        tool.run(operation="create_image", content=ImageContent(prompt="a diagram").to_json())
        types = {a.type for a in store.all()}
        assert types == {"document", "image"}
        # The document still has no PDF (NullDocumentRenderer in this
        # tool's own construction, though it still has its own always-
        # real plain-text rendition) while the image genuinely rendered -
        # each provider's own real/null state is independent of the other.
        doc_artifact = next(a for a in store.all() if a.type == "document")
        image_artifact = next(a for a in store.all() if a.type == "image")
        assert set(doc_artifact.latest.renditions) == {"txt"}
        assert "png" in image_artifact.latest.renditions


# ---------------------------------------------------------------------------
# _parse_document_content - shared by the deterministic and Brain-assisted
# paths, and resilient to BrainDecision's own whitespace flattening.
# ---------------------------------------------------------------------------


class TestParseDocumentContent:
    def test_parses_genuine_multiline_input(self):
        text = (
            "TITLE: The History of Volcanoes\n"
            "SECTION: Formation: Volcanoes form where magma reaches the surface.\n"
            "SECTION: Famous Eruptions: Mount Vesuvius destroyed Pompeii in 79 AD.\n"
        )
        doc = _parse_document_content(text)
        assert doc is not None
        assert doc.title == "The History of Volcanoes"
        assert [(s.heading, s.body) for s in doc.sections] == [
            ("Formation", "Volcanoes form where magma reaches the surface."),
            ("Famous Eruptions", "Mount Vesuvius destroyed Pompeii in 79 AD."),
        ]

    def test_parses_a_single_flattened_line_identically(self):
        """The exact shape BrainDecision.reason/ObjectiveVerdict.next_step
        actually deliver - every newline collapsed to a single space."""
        flat = (
            "TITLE: The History of Volcanoes "
            "SECTION: Formation: Volcanoes form where magma reaches the surface. "
            "SECTION: Famous Eruptions: Mount Vesuvius destroyed Pompeii in 79 AD."
        )
        doc = _parse_document_content(flat)
        assert doc is not None
        assert doc.title == "The History of Volcanoes"
        assert len(doc.sections) == 2
        assert doc.sections[0].heading == "Formation"
        assert doc.sections[1].body == "Mount Vesuvius destroyed Pompeii in 79 AD."

    def test_no_title_is_none(self):
        assert _parse_document_content("SECTION: Formation: some body text here") is None

    def test_no_sections_is_none(self):
        assert _parse_document_content("TITLE: Just A Title") is None

    def test_a_section_with_no_colon_is_skipped_not_crashed_on(self):
        text = "TITLE: T SECTION: heading with no colon separator SECTION: Real: A real body."
        doc = _parse_document_content(text)
        assert doc is not None
        assert len(doc.sections) == 1
        assert doc.sections[0].heading == "Real"

    def test_completely_unrelated_text_is_none(self):
        assert _parse_document_content("just an ordinary conversational reply") is None


# ---------------------------------------------------------------------------
# create_artifact / revise_artifact capability routing and handlers
# ---------------------------------------------------------------------------


def _identity() -> AgentIdentity:
    return AgentIdentity(name="Alfred", role="Manager", capabilities=frozenset({"handout"}))


def _request(*, tools: ToolSet, converse=None, description: str, history=None) -> CapabilityRequest:
    return CapabilityRequest(
        task=Task(description=description),
        identity=_identity(),
        roster=lambda: (),
        own_name="Alfred",
        own_capabilities=(),
        tools=tools,
        converse=converse,
        history=history,
    )


class _FakeDecision:
    def __init__(self, reason: str, is_decided: bool = True):
        self.reason = reason
        self.is_decided = is_decided
        self.source = "scripted"


class TestCapabilityRoutingAndBoundaries:
    def test_create_artifact_is_registered(self):
        assert "create_artifact" in MANAGER_CAPABILITIES
        assert MANAGER_CAPABILITIES["create_artifact"] is CREATE_ARTIFACT

    def test_revise_artifact_is_registered(self):
        assert "revise_artifact" in MANAGER_CAPABILITIES
        assert MANAGER_CAPABILITIES["revise_artifact"] is REVISE_ARTIFACT

    def test_neither_is_scoreable(self):
        assert CREATE_ARTIFACT.scoreable is False
        assert REVISE_ARTIFACT.scoreable is False

    @pytest.mark.parametrize(
        "phrase",
        ["make me a handout about volcanoes", "create a handout on cats",
         "prepare a handout about the solar system", "write a handout on jazz history"],
    )
    def test_no_hardcoded_example_every_topic_routes_identically(self, phrase):
        """The routing predicate is topic-independent - nowhere does this
        code special-case any one subject."""
        chosen = select_capability(Task(description=phrase), MANAGER_CAPABILITIES)
        assert chosen is CREATE_ARTIFACT

    def test_generate_document_still_routes_separately(self):
        chosen = select_capability(
            Task(description="create a report from these findings"), MANAGER_CAPABILITIES
        )
        assert chosen is not CREATE_ARTIFACT

    def test_an_incidental_mention_of_handout_alone_does_not_falsely_claim(self):
        assert not create_artifact_intent(Task(description="that handout was useful last week"))

    def test_revise_markers_route_to_revise_artifact(self):
        chosen = select_capability(
            Task(description="revise the handout to add a section about lava"), MANAGER_CAPABILITIES
        )
        assert chosen is REVISE_ARTIFACT

    def test_tool_not_installed_is_refused_honestly(self):
        request = _request(tools=ToolSet(), description="make me a handout")
        result = create_artifact(request)
        assert not result.succeeded
        assert "artifact_production" in result.error

    def test_revise_tool_not_installed_is_refused_honestly(self):
        request = _request(tools=ToolSet(), description="revise the handout")
        result = revise_artifact(request)
        assert not result.succeeded
        assert "artifact_production" in result.error


class TestDiagramCapabilityRoutingAndBoundaries:
    def test_create_diagram_is_registered(self):
        assert "create_diagram" in MANAGER_CAPABILITIES
        assert MANAGER_CAPABILITIES["create_diagram"] is CREATE_DIAGRAM

    def test_revise_diagram_is_registered(self):
        assert "revise_diagram" in MANAGER_CAPABILITIES
        assert MANAGER_CAPABILITIES["revise_diagram"] is REVISE_DIAGRAM

    def test_neither_is_scoreable(self):
        assert CREATE_DIAGRAM.scoreable is False
        assert REVISE_DIAGRAM.scoreable is False

    @pytest.mark.parametrize(
        "phrase",
        [
            "make a diagram of the human body showing the skeleton",
            "create a diagram of the major internal organs",
            "draw a map of the solar system",
            "make me a diagram of a technical circuit board",
            "create a diagram explaining how a car engine works",
        ],
    )
    def test_no_hardcoded_example_every_subject_routes_identically(self, phrase):
        """The routing predicate names no subject anywhere in this
        code - human body, skeleton, organs, maps, circuits and engines
        all reach the identical handler through the identical marker
        shape."""
        chosen = select_capability(Task(description=phrase), MANAGER_CAPABILITIES)
        assert chosen is CREATE_DIAGRAM

    def test_create_artifact_and_create_diagram_never_collide(self):
        assert select_capability(
            Task(description="make me a handout"), MANAGER_CAPABILITIES
        ) is CREATE_ARTIFACT
        assert select_capability(
            Task(description="make me a diagram"), MANAGER_CAPABILITIES
        ) is CREATE_DIAGRAM

    def test_an_incidental_mention_of_diagram_alone_does_not_falsely_claim(self):
        assert not create_diagram_intent(Task(description="that diagram in the textbook was confusing"))

    def test_revise_markers_route_to_revise_diagram(self):
        chosen = select_capability(
            Task(description="revise the diagram to add labels"), MANAGER_CAPABILITIES
        )
        assert chosen is REVISE_DIAGRAM

    def test_tool_not_installed_is_refused_honestly(self):
        request = _request(tools=ToolSet(), description="make a diagram of the water cycle")
        result = create_diagram(request)
        assert not result.succeeded
        assert "artifact_production" in result.error

    def test_revise_tool_not_installed_is_refused_honestly(self):
        request = _request(tools=ToolSet(), description="revise the diagram")
        result = revise_diagram(request)
        assert not result.succeeded
        assert "artifact_production" in result.error


class TestCreateDiagramHalfBrainSafe:
    def test_a_real_description_needs_no_brain(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), LocalTitleCardImageProvider(), owner_agent="Alfred"
        )
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description="make a diagram of the water cycle showing evaporation and precipitation",
        )
        result = create_diagram(request)
        assert result.succeeded, result.error
        assert result.payload.get("handled_by") == "create_diagram"
        assert len(store.all()) == 1
        assert store.all()[0].type == "image"

    def test_a_bare_ask_with_no_brain_is_refused_not_guessed(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), LocalTitleCardImageProvider(), owner_agent="Alfred"
        )
        request = _request(tools=ToolSet([tool]), converse=None, description="make a diagram")
        result = create_diagram(request)
        assert not result.succeeded
        assert "no language model" in result.error
        assert store.all() == ()


class TestCreateDiagramBrainAssisted:
    def test_the_brain_elaborates_a_bare_ask_into_a_real_prompt(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), LocalTitleCardImageProvider(), owner_agent="Alfred"
        )
        seen = []

        def converse(task, notes):
            seen.append(notes)
            return _FakeDecision("a detailed, labeled diagram of the water cycle")

        request = _request(tools=ToolSet([tool]), converse=converse, description="make a diagram")
        result = create_diagram(request)
        assert result.succeeded, result.error
        assert seen, "the Brain must genuinely have been consulted"
        artifact = store.all()[0]
        assert "water cycle" in artifact.latest.title

    def test_an_undecided_brain_is_an_honest_failure(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), LocalTitleCardImageProvider(), owner_agent="Alfred"
        )
        request = _request(
            tools=ToolSet([tool]), converse=lambda task, notes: _FakeDecision("", is_decided=False),
            description="make a diagram",
        )
        result = create_diagram(request)
        assert not result.succeeded
        assert store.all() == ()


class TestReviseDiagramCapability:
    def _seeded_store(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), LocalTitleCardImageProvider(), owner_agent="Alfred"
        )
        tool.run(operation="create_image", content=ImageContent(prompt="a diagram of a plant cell").to_json())
        return store, tool

    def test_revise_by_explicit_artifact_id_deterministically(self, tmp_path):
        store, tool = self._seeded_store(tmp_path)
        artifact_id = store.all()[0].artifact_id
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description=f"revise the diagram artifact {artifact_id} to add labels for the nucleus and mitochondria",
        )
        result = revise_diagram(request)
        assert result.succeeded, result.error
        assert store.get(artifact_id).latest.version == 2

    def test_the_artifact_id_reference_never_leaks_into_the_new_prompt(self, tmp_path):
        """A real bug found during live verification: the explicit
        'artifact <id>' reference used to resolve *which* artifact must
        never bleed into *what it should now show*."""
        store, tool = self._seeded_store(tmp_path)
        artifact_id = store.all()[0].artifact_id
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description=f"revise the diagram artifact {artifact_id} to add labels for the nucleus and mitochondria",
        )
        result = revise_diagram(request)
        assert result.succeeded, result.error
        new_title = store.get(artifact_id).latest.title
        assert artifact_id not in new_title
        assert "artifact" not in new_title.lower()
        assert "nucleus" in new_title

    def test_revise_falls_back_to_the_most_recent_diagram(self, tmp_path):
        store, tool = self._seeded_store(tmp_path)
        artifact_id = store.all()[0].artifact_id
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description="update the diagram to add labels for the nucleus and mitochondria",
        )
        result = revise_diagram(request)
        assert result.succeeded, result.error
        assert store.get(artifact_id).latest.version == 2

    def test_revise_with_nothing_to_revise_is_refused_honestly(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(
            store, NullDocumentRenderer(), LocalTitleCardImageProvider(), owner_agent="Alfred"
        )
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description="revise the diagram to add labels for the nucleus",
        )
        result = revise_diagram(request)
        assert not result.succeeded
        assert "don't have an existing diagram" in result.error


class TestCreateArtifactHalfBrainSafe:
    def test_explicit_structure_needs_no_brain(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description=(
                "make me a handout\n"
                "TITLE: The History of Volcanoes\n"
                "SECTION: Formation: Volcanoes form where magma reaches the surface.\n"
                "SECTION: Famous Eruptions: Mount Vesuvius destroyed Pompeii in 79 AD.\n"
            ),
        )
        result = create_artifact(request)
        assert result.succeeded, result.error
        assert result.payload.get("handled_by") == "create_artifact"
        assert len(store.all()) == 1

    def test_no_structure_and_no_brain_is_refused_not_guessed(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        request = _request(tools=ToolSet([tool]), converse=None, description="make me a handout about volcanoes")
        result = create_artifact(request)
        assert not result.succeeded
        assert "no language model" in result.error
        assert store.all() == ()


class TestCreateArtifactBrainAssisted:
    def test_the_brain_organizes_an_objective_into_a_real_artifact(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        decision = _FakeDecision(
            "TITLE: The History of Volcanoes "
            "SECTION: Formation: Volcanoes form where magma reaches the surface. "
            "SECTION: Famous Eruptions: Mount Vesuvius destroyed Pompeii in 79 AD."
        )
        seen = []

        def converse(task, notes):
            seen.append(notes)
            return decision

        request = _request(
            tools=ToolSet([tool]), converse=converse, description="make me a handout about volcanoes"
        )
        result = create_artifact(request)
        assert result.succeeded, result.error
        assert seen, "the Brain must genuinely have been consulted"
        artifact = store.all()[0]
        assert artifact.latest.title == "The History of Volcanoes"

    def test_an_undecided_brain_is_an_honest_failure(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        request = _request(
            tools=ToolSet([tool]),
            converse=lambda task, notes: _FakeDecision("", is_decided=False),
            description="make me a handout about volcanoes",
        )
        result = create_artifact(request)
        assert not result.succeeded
        assert store.all() == ()

    def test_an_unparseable_brain_answer_is_an_honest_failure_never_a_guess(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        request = _request(
            tools=ToolSet([tool]),
            converse=lambda task, notes: _FakeDecision("I'm not sure what to write here."),
            description="make me a handout about volcanoes",
        )
        result = create_artifact(request)
        assert not result.succeeded
        assert "couldn't organize" in result.error
        assert store.all() == ()

    def test_real_evidence_from_a_prior_objective_step_reaches_the_brain(self, tmp_path):
        """The multi-step objective-loop seam: whatever real evidence a
        prior step's next_step text already carries into this request's
        own description is what the Brain reasons over - never invented
        here."""
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        captured_prompt_notes = []

        def converse(task, notes):
            captured_prompt_notes.append(task.description)
            return _FakeDecision(
                "TITLE: Volcanoes SECTION: Formation: Real research: magma reaches the surface."
            )

        request = _request(
            tools=ToolSet([tool]), converse=converse,
            description="make a handout using this research: magma rises through the crust",
        )
        result = create_artifact(request)
        assert result.succeeded, result.error
        assert "magma rises through the crust" in captured_prompt_notes[0]


class TestReviseArtifactCapability:
    def _seeded_store(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        tool.run(operation="create_document", content=_doc().to_json())
        return store, tool

    def test_revise_by_explicit_artifact_id_deterministically(self, tmp_path):
        store, tool = self._seeded_store(tmp_path)
        artifact_id = store.all()[0].artifact_id
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description=(
                f"revise the handout artifact {artifact_id}\n"
                "TITLE: Updated Title\n"
                "SECTION: New: New real body text.\n"
            ),
        )
        result = revise_artifact(request)
        assert result.succeeded, result.error
        assert store.get(artifact_id).latest.version == 2
        assert store.get(artifact_id).latest.title == "Updated Title"

    def test_revise_falls_back_to_the_most_recent_document(self, tmp_path):
        store, tool = self._seeded_store(tmp_path)
        artifact_id = store.all()[0].artifact_id
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description="update the handout\nTITLE: Updated\nSECTION: New: Body text.\n",
        )
        result = revise_artifact(request)
        assert result.succeeded, result.error
        assert store.get(artifact_id).latest.version == 2

    def test_revise_with_nothing_to_revise_is_refused_honestly(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        request = _request(
            tools=ToolSet([tool]), converse=None,
            description="revise the handout\nTITLE: T\nSECTION: S: body\n",
        )
        result = revise_artifact(request)
        assert not result.succeeded
        assert "don't have an existing handout" in result.error

    def test_revise_brain_assisted_reads_current_content_first(self, tmp_path):
        store, tool = self._seeded_store(tmp_path)
        artifact_id = store.all()[0].artifact_id
        seen_notes = []

        def converse(task, notes):
            seen_notes.append(notes)
            return _FakeDecision("TITLE: Revised SECTION: New: A brand new section.")

        request = _request(
            tools=ToolSet([tool]), converse=converse,
            description="revise the handout to add something new",
        )
        result = revise_artifact(request)
        assert result.succeeded, result.error
        assert any("A Real Test Document" in n for n in seen_notes[0])
        assert store.get(artifact_id).latest.version == 2


# ---------------------------------------------------------------------------
# Full system integration - the real Manager/runtime/Control Center path
# ---------------------------------------------------------------------------


def _built(tmp_path, **overrides):
    import scripts.control_center as cc
    from src.config.settings import Settings

    return cc.build(
        Settings.from_env(
            env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"), **overrides
        )
    )


HANDOUT_TEXT = (
    "make me a handout\n"
    "TITLE: The History of Volcanoes\n"
    "SECTION: Formation: Volcanoes form where magma reaches the surface.\n"
    "SECTION: Famous Eruptions: Mount Vesuvius destroyed Pompeii in 79 AD.\n"
)


class TestFullSystemIntegration:
    def test_the_real_production_build_wires_the_tool_through(self, tmp_path):
        runtime, manager, channel, conversation = _built(tmp_path)
        assert "artifact_production" in manager.own_skills.tools.names()
        assert isinstance(runtime.document_renderer, SimplePDFRenderer)

    def test_a_real_conversation_turn_produces_a_real_persisted_pdf_artifact(self, tmp_path):
        runtime, manager, channel, conversation = _built(tmp_path)
        assert manager.brain.__class__.__name__ == "NullBrain"

        result = manager.handle_task(Task(description=HANDOUT_TEXT))
        assert result.succeeded, result.error

        artifacts = runtime.artifacts.all()
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.type == "document"
        assert artifact.owner_agent == runtime.identity.name

        from pypdf import PdfReader

        reader = PdfReader(artifact.latest.renditions["pdf"])
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        assert "Pompeii" in text

    def test_persistence_across_a_genuine_restart(self, tmp_path):
        runtime, manager, *_ = _built(tmp_path)
        result = manager.handle_task(Task(description=HANDOUT_TEXT))
        assert result.succeeded, result.error
        artifact_id = runtime.artifacts.all()[0].artifact_id

        runtime2, manager2, *_ = _built(tmp_path)
        artifact = runtime2.artifacts.get(artifact_id)
        assert artifact is not None
        assert artifact.latest.title == "The History of Volcanoes"

    def test_revision_preserves_identity_across_versions_and_restart(self, tmp_path):
        runtime, manager, *_ = _built(tmp_path)
        r1 = manager.handle_task(Task(description=HANDOUT_TEXT))
        assert r1.succeeded, r1.error
        artifact_id = runtime.artifacts.all()[0].artifact_id

        revise_text = (
            f"revise the handout artifact {artifact_id}\n"
            "TITLE: The History of Volcanoes (Revised)\n"
            "SECTION: Formation: Volcanoes form where magma reaches the surface.\n"
            "SECTION: Famous Eruptions: Mount Vesuvius destroyed Pompeii in 79 AD.\n"
            "SECTION: Modern Study: Volcanology is now a mature scientific field.\n"
        )
        r2 = manager.handle_task(Task(description=revise_text))
        assert r2.succeeded, r2.error

        runtime2, *_ = _built(tmp_path)
        artifact = runtime2.artifacts.get(artifact_id)
        assert artifact.latest.version == 2
        assert artifact.latest.title == "The History of Volcanoes (Revised)"
        assert len(artifact.versions) == 2

    def test_invalid_revision_reference_is_refused_honestly(self, tmp_path):
        runtime, manager, *_ = _built(tmp_path)
        result = manager.handle_task(
            Task(description="revise the handout artifact deadbeef00\nTITLE: T\nSECTION: S: body\n")
        )
        assert not result.succeeded

    def test_control_center_reads_the_authoritative_state(self, tmp_path):
        import scripts.control_center as cc

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.handle_task(Task(description=HANDOUT_TEXT))
        centre = cc.ControlCenter(runtime, manager)

        listing = cc.run_command(centre, "artifacts", 100, channel, conversation)
        assert "document" in listing
        assert "The History of Volcanoes" in listing

        artifact_id = runtime.artifacts.all()[0].artifact_id
        detail = cc.run_command(centre, f"artifacts {artifact_id}", 100, channel, conversation)
        assert artifact_id in detail
        assert "v1" in detail
        assert "delivery=download" in listing
        assert "delivery=download" in detail

    def test_control_center_shows_no_delivery_when_no_rendition_exists(self, tmp_path):
        """A document always gets a real plain-text rendition regardless
        of document_rendering, so it can no longer demonstrate "nothing
        deliverable yet" - an image/diagram with no illustrator
        configured genuinely can, since image generation has no
        deterministic local fallback the way document text export does."""
        import scripts.control_center as cc

        runtime, manager, channel, conversation = _built(tmp_path, media="null")
        manager.handle_task(Task(description="make a diagram of a real test subject for this scenario"))
        centre = cc.ControlCenter(runtime, manager)

        listing = cc.run_command(centre, "artifacts", 100, channel, conversation)
        assert "delivery=none yet" in listing

    def test_control_center_capability_matrix_reflects_real_state(self, tmp_path):
        import scripts.control_center as cc

        runtime, manager, channel, conversation = _built(tmp_path)
        centre = cc.ControlCenter(runtime, manager)
        out = cc.run_command(centre, "capabilities", 100, channel, conversation)
        assert "Artifact Workspace (documents, images/diagrams, audio, video)" in out
        assert "Document Rendering (PDF)" in out

    def test_snapshot_exposes_the_same_authoritative_artifacts(self, tmp_path):
        import scripts.control_center as cc
        from src.control_center.snapshot import snapshot

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.handle_task(Task(description=HANDOUT_TEXT))
        centre = cc.ControlCenter(runtime, manager)

        data = snapshot(centre, conversation=conversation, channel=channel)
        assert "artifacts" in data
        assert len(data["artifacts"]) == 1
        entry = data["artifacts"][0]
        assert entry["type"] == "document"
        assert entry["owner_agent"] == runtime.identity.name
        assert "pdf" in entry["renditions"]
        assert entry["delivery"] == ["download"]
        assert entry["artifact_id"] == runtime.artifacts.all()[0].artifact_id

    def test_a_document_always_has_a_real_text_rendition_alongside_pdf(self, tmp_path):
        """A format is a rendition, not the definition of the artifact -
        proven here with a real, working second rendition (plain text),
        always present regardless of whether a PDF engine is
        configured, so a document stays genuinely deliverable either
        way."""
        import scripts.control_center as cc
        from src.control_center.snapshot import snapshot

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.handle_task(Task(description=HANDOUT_TEXT))
        centre = cc.ControlCenter(runtime, manager)

        data = snapshot(centre, conversation=conversation, channel=channel)
        entry = data["artifacts"][0]
        assert set(entry["renditions"]) == {"pdf", "txt"}
        assert entry["primary_rendition"] == "pdf"

        txt_path = Path(entry["renditions"]["txt"])
        assert txt_path.is_file()
        assert "The History of Volcanoes" in txt_path.read_text(encoding="utf-8")

    def test_related_artifact_ids_are_exposed_and_empty_for_a_standalone_document(self, tmp_path):
        import scripts.control_center as cc
        from src.control_center.snapshot import snapshot

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.handle_task(Task(description=HANDOUT_TEXT))
        centre = cc.ControlCenter(runtime, manager)

        data = snapshot(centre, conversation=conversation, channel=channel)
        assert data["artifacts"][0]["related_artifact_ids"] == []

    def test_control_center_detail_shows_derived_from(self, tmp_path):
        import scripts.control_center as cc

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.handle_task(Task(description=HANDOUT_TEXT))
        centre = cc.ControlCenter(runtime, manager)
        artifact_id = runtime.artifacts.all()[0].artifact_id

        detail = cc.run_command(centre, f"artifacts {artifact_id}", 100, channel, conversation)
        assert "derived_from=none" in detail

    def test_snapshot_shows_no_delivery_when_no_rendition_exists(self, tmp_path):
        """See test_control_center_shows_no_delivery_when_no_rendition_exists's
        own docstring for why an image, not a document, demonstrates
        this now."""
        import scripts.control_center as cc
        from src.control_center.snapshot import snapshot

        runtime, manager, channel, conversation = _built(tmp_path, media="null")
        manager.handle_task(Task(description="make a diagram of a real test subject for this scenario"))
        centre = cc.ControlCenter(runtime, manager)

        data = snapshot(centre, conversation=conversation, channel=channel)
        assert data["artifacts"][0]["delivery"] == []

    def test_missing_artifact_is_a_real_404_not_a_fabricated_response(self, tmp_path):
        import scripts.serve_ui as su

        runtime, manager, channel, conversation = _built(tmp_path)
        system = su.LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=su.ControlCenter(runtime, manager), lock=__import__("threading").Lock(),
        )
        status, body, content_type = su.route("/artifact/doesnotexist.pdf", system)
        assert status == 404

    def test_a_real_artifact_pdf_is_served_over_the_real_route(self, tmp_path):
        import scripts.serve_ui as su

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.handle_task(Task(description=HANDOUT_TEXT))
        artifact_id = runtime.artifacts.all()[0].artifact_id

        system = su.LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=su.ControlCenter(runtime, manager), lock=__import__("threading").Lock(),
        )
        status, body, content_type = su.route(f"/artifact/{artifact_id}.pdf", system)
        assert status == 200
        assert content_type == "application/pdf"
        assert body.startswith(b"%PDF")

    def test_an_artifact_with_no_pdf_rendition_is_a_real_404_not_empty_bytes(self, tmp_path):
        import scripts.serve_ui as su

        runtime, manager, channel, conversation = _built(tmp_path, document_rendering="null")
        manager.handle_task(Task(description=HANDOUT_TEXT))
        artifact_id = runtime.artifacts.all()[0].artifact_id

        system = su.LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=su.ControlCenter(runtime, manager), lock=__import__("threading").Lock(),
        )
        status, body, content_type = su.route(f"/artifact/{artifact_id}.pdf", system)
        assert status == 404


DIAGRAM_TEXT = "make a diagram of the water cycle showing evaporation, condensation, and precipitation"


class TestFullSystemIntegrationDiagrams:
    """The identical real Manager/runtime/Control Center/HTTP path
    TestFullSystemIntegration already proves for documents, proven again
    for the image/diagram artifact type - the same ArtifactStore, the
    same Control Center commands, the same snapshot field, the same
    /artifact/<id>.<format> route, a genuinely different real rendition
    format (png, not pdf)."""

    def test_a_real_conversation_turn_produces_a_real_persisted_image_artifact(self, tmp_path):
        runtime, manager, channel, conversation = _built(tmp_path, media="local")
        assert manager.brain.__class__.__name__ == "NullBrain"
        assert runtime.media.illustrator.name == "local-title-card"

        result = manager.handle_task(Task(description=DIAGRAM_TEXT))
        assert result.succeeded, result.error

        artifacts = runtime.artifacts.all()
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.type == "image"
        assert artifact.owner_agent == runtime.identity.name

        from PIL import Image

        with Image.open(artifact.latest.renditions["png"]) as img:
            img.load()
            assert img.size[0] > 0 and img.size[1] > 0

    def test_persistence_across_a_genuine_restart(self, tmp_path):
        runtime, manager, *_ = _built(tmp_path, media="local")
        result = manager.handle_task(Task(description=DIAGRAM_TEXT))
        assert result.succeeded, result.error
        artifact_id = runtime.artifacts.all()[0].artifact_id

        runtime2, *_ = _built(tmp_path, media="local")
        artifact = runtime2.artifacts.get(artifact_id)
        assert artifact is not None
        assert artifact.type == "image"
        assert Path(artifact.latest.renditions["png"]).is_file()

    def test_revision_preserves_identity_across_versions_and_restart(self, tmp_path):
        runtime, manager, *_ = _built(tmp_path, media="local")
        r1 = manager.handle_task(Task(description=DIAGRAM_TEXT))
        assert r1.succeeded, r1.error
        artifact_id = runtime.artifacts.all()[0].artifact_id

        r2 = manager.handle_task(
            Task(description=f"revise the diagram artifact {artifact_id} to add clearly labeled arrows")
        )
        assert r2.succeeded, r2.error

        runtime2, *_ = _built(tmp_path, media="local")
        artifact = runtime2.artifacts.get(artifact_id)
        assert artifact.latest.version == 2
        assert len(artifact.versions) == 2

    def test_control_center_reads_the_authoritative_state(self, tmp_path):
        import scripts.control_center as cc

        runtime, manager, channel, conversation = _built(tmp_path, media="local")
        manager.handle_task(Task(description=DIAGRAM_TEXT))
        centre = cc.ControlCenter(runtime, manager)

        listing = cc.run_command(centre, "artifacts", 100, channel, conversation)
        assert "image" in listing
        assert "renditions=png" in listing

    def test_snapshot_exposes_the_same_authoritative_image_artifact(self, tmp_path):
        import scripts.control_center as cc
        from src.control_center.snapshot import snapshot

        runtime, manager, channel, conversation = _built(tmp_path, media="local")
        manager.handle_task(Task(description=DIAGRAM_TEXT))
        centre = cc.ControlCenter(runtime, manager)

        data = snapshot(centre, conversation=conversation, channel=channel)
        entry = data["artifacts"][0]
        assert entry["type"] == "image"
        assert "png" in entry["renditions"]

    def test_a_real_artifact_png_is_served_over_the_real_route(self, tmp_path):
        import scripts.serve_ui as su

        runtime, manager, channel, conversation = _built(tmp_path, media="local")
        manager.handle_task(Task(description=DIAGRAM_TEXT))
        artifact_id = runtime.artifacts.all()[0].artifact_id

        system = su.LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=su.ControlCenter(runtime, manager), lock=__import__("threading").Lock(),
        )
        status, body, content_type = su.route(f"/artifact/{artifact_id}.png", system)
        assert status == 200
        assert content_type == "image/png"
        assert body[:8] == b"\x89PNG\r\n\x1a\n"

    def test_an_image_with_no_illustrator_configured_is_a_real_404_not_empty_bytes(self, tmp_path):
        import scripts.serve_ui as su

        runtime, manager, channel, conversation = _built(tmp_path, media="null")
        manager.handle_task(Task(description=DIAGRAM_TEXT))
        artifact_id = runtime.artifacts.all()[0].artifact_id

        system = su.LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=su.ControlCenter(runtime, manager), lock=__import__("threading").Lock(),
        )
        status, body, content_type = su.route(f"/artifact/{artifact_id}.png", system)
        assert status == 404

    def test_documents_and_diagrams_coexist_as_independent_artifacts(self, tmp_path):
        """The same store, the same tool, two genuinely independent
        artifact types - proof this is one broad system, not two
        parallel ones."""
        runtime, manager, *_ = _built(tmp_path, media="local")
        r1 = manager.handle_task(Task(description=HANDOUT_TEXT))
        r2 = manager.handle_task(Task(description=DIAGRAM_TEXT))
        assert r1.succeeded and r2.succeeded

        artifacts = runtime.artifacts.all()
        assert len(artifacts) == 2
        types = {a.type for a in artifacts}
        assert types == {"document", "image"}


class TestMultiStepIntelligenceIntegration:
    """The genuine 'objective -> intelligence -> capability selection ->
    artifact' chain, through the real, unmodified pursue_objective/
    assess_objective/select_capability machinery - only the LLM vendor
    is a scripted double, the same established discipline this project
    already uses everywhere a funded account does not exist."""

    def test_a_two_step_objective_reaches_create_artifact_and_produces_a_real_pdf(self, tmp_path):
        from src.manager.llm_brain import LLMManagerBrain
        from src.manager.objective_loop import pursue_objective
        from src.providers.base import Completion, LLMProvider

        runtime, manager, *_ = _built(tmp_path)

        structured_answer = (
            "TITLE: History of Volcanoes "
            "SECTION: Formation: Volcanoes form at plate boundaries where magma reaches the surface. "
            "SECTION: Famous Eruptions: Mount Vesuvius destroyed Pompeii in 79 AD."
        )

        class ScriptedProvider(LLMProvider):
            def __init__(self):
                self.calls = []

            @property
            def name(self):
                return "scripted"

            def complete(self, prompt, *, system=None):
                self.calls.append(prompt)
                n = len(self.calls)
                if n == 1:
                    return Completion(text="Volcanoes form where magma reaches the surface.", provider="scripted")
                if n == 2:
                    return Completion(
                        text=json.dumps(
                            {"status": "continue", "next_step": "make a handout on the history of volcanoes"}
                        ),
                        provider="scripted",
                    )
                if n == 3:
                    return Completion(text=structured_answer, provider="scripted")
                return Completion(
                    text=json.dumps({"status": "complete", "final_answer": "Created a handout."}),
                    provider="scripted",
                )

        provider = ScriptedProvider()
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        outcome = pursue_objective(manager, Task(description="What causes volcanoes to form?"))

        assert outcome.status.value == "completed"
        assert len(outcome.steps) == 2
        assert outcome.steps[1].result.payload.get("handled_by") == "create_artifact"

        artifacts = runtime.artifacts.all()
        assert len(artifacts) == 1
        from pypdf import PdfReader

        text = "\n".join(
            p.extract_text() or "" for p in PdfReader(artifacts[0].latest.renditions["pdf"]).pages
        )
        assert "Pompeii" in text

    def test_a_two_step_objective_reaches_create_diagram_and_produces_a_real_png(self, tmp_path):
        from src.manager.llm_brain import LLMManagerBrain
        from src.manager.objective_loop import pursue_objective
        from src.providers.base import Completion, LLMProvider

        runtime, manager, *_ = _built(tmp_path, media="local")

        class ScriptedProvider(LLMProvider):
            def __init__(self):
                self.calls = []

            @property
            def name(self):
                return "scripted"

            def complete(self, prompt, *, system=None):
                self.calls.append(prompt)
                n = len(self.calls)
                if n == 1:
                    return Completion(
                        text="Water evaporates, forms clouds, and falls back as precipitation.",
                        provider="scripted",
                    )
                if n == 2:
                    return Completion(
                        text=json.dumps(
                            {"status": "continue", "next_step": "make a diagram of the water cycle"}
                        ),
                        provider="scripted",
                    )
                if n == 3:
                    return Completion(
                        text="a clearly labeled diagram of the water cycle showing evaporation and precipitation",
                        provider="scripted",
                    )
                return Completion(
                    text=json.dumps({"status": "complete", "final_answer": "Created a diagram."}),
                    provider="scripted",
                )

        provider = ScriptedProvider()
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        outcome = pursue_objective(manager, Task(description="How does the water cycle work?"))

        assert outcome.status.value == "completed"
        assert len(outcome.steps) == 2
        assert outcome.steps[1].result.payload.get("handled_by") == "create_diagram"

        artifacts = runtime.artifacts.all()
        assert len(artifacts) == 1
        assert artifacts[0].type == "image"
        from PIL import Image

        with Image.open(artifacts[0].latest.renditions["png"]) as img:
            img.load()
            assert img.size[0] > 0


# ---------------------------------------------------------------------------
# Memory boundary: creating/revising an artifact must never silently
# become a user-memory record.
# ---------------------------------------------------------------------------


class TestUIRendersTheRealArtifact:
    """The real ui/index.html script, actually executed in Node against a
    real snapshot - the identical vm-based harness
    tests/test_stage17_18_private_agent_interface.py already established -
    proving the artifact link is genuinely rendered by the real frontend
    code, never merely asserted to exist in the backend payload."""

    @staticmethod
    def _node():
        import subprocess

        for candidate in ("node", "node.exe"):
            try:
                subprocess.run([candidate, "--version"], capture_output=True, check=True)
                return candidate
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        return None

    def test_the_real_frontend_script_renders_a_real_artifact_link(self, tmp_path):
        import re
        import subprocess

        node = self._node()
        if node is None:
            pytest.skip("no node runtime available in this environment")

        import scripts.control_center as cc
        from src.config.settings import Settings
        from src.control_center.snapshot import snapshot as build_snapshot

        runtime, manager, channel, conversation = cc.build(
            Settings.from_env(env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"))
        )
        manager.handle_task(Task(description=HANDOUT_TEXT))
        artifact_id = runtime.artifacts.all()[0].artifact_id
        centre = cc.ControlCenter(runtime, manager)
        data = build_snapshot(centre, conversation=conversation, channel=channel)

        repo_root = Path(__file__).resolve().parent.parent
        shell = (repo_root / "ui" / "index.html").read_text(encoding="utf-8")
        script_match = re.search(r"<script>(.*)</script>", shell, re.S)
        assert script_match is not None
        (tmp_path / "ui_script.js").write_text(script_match.group(1), encoding="utf-8")
        (tmp_path / "snapshot.json").write_text(json.dumps(data), encoding="utf-8")
        harness = tmp_path / "harness.js"
        harness.write_text(_UI_NODE_HARNESS, encoding="utf-8")

        result = subprocess.run(
            [node, str(harness), str(tmp_path / "ui_script.js"), str(tmp_path / "snapshot.json"), "agent"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip(), result.stderr
        out = json.loads(result.stdout.strip().splitlines()[-1])
        assert out.get("ok"), out.get("error")

        rendered = out["views"]["boot"]
        assert f"artifact/{artifact_id}.pdf" in rendered
        assert "The History of Volcanoes" in rendered

    def test_the_real_frontend_script_renders_a_real_image_artifact_as_an_inline_preview(self, tmp_path):
        import re
        import subprocess

        node = self._node()
        if node is None:
            pytest.skip("no node runtime available in this environment")

        import scripts.control_center as cc
        from src.config.settings import Settings
        from src.control_center.snapshot import snapshot as build_snapshot

        runtime, manager, channel, conversation = cc.build(
            Settings.from_env(
                env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"), media="local",
            )
        )
        manager.handle_task(Task(description=DIAGRAM_TEXT))
        artifact_id = runtime.artifacts.all()[0].artifact_id
        centre = cc.ControlCenter(runtime, manager)
        data = build_snapshot(centre, conversation=conversation, channel=channel)

        repo_root = Path(__file__).resolve().parent.parent
        shell = (repo_root / "ui" / "index.html").read_text(encoding="utf-8")
        script_match = re.search(r"<script>(.*)</script>", shell, re.S)
        assert script_match is not None
        (tmp_path / "ui_script.js").write_text(script_match.group(1), encoding="utf-8")
        (tmp_path / "snapshot.json").write_text(json.dumps(data), encoding="utf-8")
        harness = tmp_path / "harness.js"
        harness.write_text(_UI_NODE_HARNESS, encoding="utf-8")

        result = subprocess.run(
            [node, str(harness), str(tmp_path / "ui_script.js"), str(tmp_path / "snapshot.json"), "agent"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip(), result.stderr
        out = json.loads(result.stdout.strip().splitlines()[-1])
        assert out.get("ok"), out.get("error")

        rendered = out["views"]["boot"]
        assert f"artifact/{artifact_id}.png" in rendered
        # A genuine inline <img> preview, not merely a download link - the
        # real served file is what the src attribute points at.
        assert f'<img src="artifact/{artifact_id}.png"' in rendered

    def test_an_artifact_with_no_delivery_method_renders_no_link(self, tmp_path):
        """The frontend must show the backend's own real "nothing to
        deliver yet" state honestly - no download link, no fabricated
        href pointing at a file that does not exist. A document always
        gets a real text rendition regardless of document_rendering, so
        an image/diagram with no illustrator configured is what
        genuinely demonstrates "nothing deliverable yet" now."""
        import re
        import subprocess

        node = self._node()
        if node is None:
            pytest.skip("no node runtime available in this environment")

        import scripts.control_center as cc
        from src.config.settings import Settings
        from src.control_center.snapshot import snapshot as build_snapshot

        runtime, manager, channel, conversation = cc.build(
            Settings.from_env(
                env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"), media="null",
            )
        )
        manager.handle_task(Task(description="make a diagram of a real test subject for no-delivery rendering"))
        artifact_id = runtime.artifacts.all()[0].artifact_id
        assert runtime.artifacts.get(artifact_id).latest.renditions == {}
        centre = cc.ControlCenter(runtime, manager)
        data = build_snapshot(centre, conversation=conversation, channel=channel)
        assert data["artifacts"][0]["delivery"] == []

        repo_root = Path(__file__).resolve().parent.parent
        shell = (repo_root / "ui" / "index.html").read_text(encoding="utf-8")
        script_match = re.search(r"<script>(.*)</script>", shell, re.S)
        assert script_match is not None
        (tmp_path / "ui_script.js").write_text(script_match.group(1), encoding="utf-8")
        (tmp_path / "snapshot.json").write_text(json.dumps(data), encoding="utf-8")
        harness = tmp_path / "harness.js"
        harness.write_text(_UI_NODE_HARNESS, encoding="utf-8")

        result = subprocess.run(
            [node, str(harness), str(tmp_path / "ui_script.js"), str(tmp_path / "snapshot.json"), "agent"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip(), result.stderr
        out = json.loads(result.stdout.strip().splitlines()[-1])
        assert out.get("ok"), out.get("error")

        rendered = out["views"]["boot"]
        assert f"artifact/{artifact_id}." not in rendered


# A trimmed copy of the real vm-based harness
# tests/test_stage17_18_private_agent_interface.py's own _HARNESS already
# establishes - executes the real <script> from ui/index.html against a
# real snapshot and reads back its real, rendered boot view. Duplicated
# here (each UI-executing test file owns its own harness, matching this
# project's existing convention) rather than imported, since the source
# module's own _HARNESS is a private, file-local constant.
_UI_NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const scriptSrc = fs.readFileSync(process.argv[2], "utf8");
const snapshot = fs.readFileSync(process.argv[3], "utf8");
const startMode = process.argv[4] || "agent";

class FakeClassList {
  constructor(el) { this.el = el; }
  add(...c) { c.forEach(x => this.el._classes.add(x)); }
  remove(...c) { c.forEach(x => this.el._classes.delete(x)); }
  toggle(c, on) { if (on) this.el._classes.add(c); else this.el._classes.delete(c); }
  contains(c) { return this.el._classes.has(c); }
}

function makeEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    _classes: new Set(), children: [], parentElement: null, dataset: {}, style: {}, attrs: {},
    _text: "", _html: "", listeners: {},
    get className() { return [...el._classes].join(" "); },
    set className(v) { el._classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
    get textContent() { return el._text; },
    set textContent(v) { el._text = String(v); },
    get innerHTML() { return el._html; },
    set innerHTML(v) { el._html = String(v); el.children = []; },
    get value() { return el._value || ""; },
    set value(v) { el._value = v; },
    classList: null,
    setAttribute(k, v) { el.attrs[k] = v; },
    getAttribute(k) { return el.attrs[k]; },
    appendChild(child) { el.children.push(child); child.parentElement = el; return child; },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    scrollIntoView() {}, remove() {}, focus() {},
    getContext(kind) {
      if (kind !== "2d") return null;
      const grad = { addColorStop() {} };
      return {
        clearRect() {}, beginPath() {}, closePath() {}, arc() {}, moveTo() {}, lineTo() {},
        fill() {}, stroke() {}, createLinearGradient: () => grad, createRadialGradient: () => grad,
        set fillStyle(v) {}, get fillStyle() { return ""; },
        set strokeStyle(v) {}, get strokeStyle() { return ""; },
        set lineWidth(v) {}, get lineWidth() { return 1; },
      };
    },
  };
  el.classList = new FakeClassList(el);
  return el;
}

function scanTargets(html, attr) {
  const out = [];
  const re = new RegExp('data-' + attr + '="([^"]*)"([^>]*)', "g");
  let m;
  while ((m = re.exec(html)) !== null) {
    const el = makeEl("button");
    el.dataset[attr] = m[1];
    const agent = /data-agent="([^"]*)"/.exec(m[2] || "");
    if (agent) el.dataset.agent = agent[1];
    out.push(el);
  }
  return out;
}

const stage = makeEl("div");
const _scanCache = new Map();
stage.querySelectorAll = (sel) => {
  const attr = sel === "[data-goto]" ? "goto" : sel === "[data-focus]" ? "focus" : null;
  if (!attr) return [];
  const key = attr + "|" + stage._html;
  if (!_scanCache.has(key)) _scanCache.set(key, scanTargets(stage._html, attr));
  return _scanCache.get(key);
};

const byId = {
  snapshot: (() => { const e = makeEl("script"); e._text = snapshot; return e; })(),
  scene: makeEl("canvas"), stage, modes: makeEl("div"),
  "crown-name": makeEl("div"), "crown-sub": makeEl("div"), stamp: makeEl("div"),
  "voice-toggle": makeEl("button"),
};
const documentStub = {
  getElementById: (id) => byId[id] || null,
  querySelector: (sel) => (sel === "#stage" ? stage : null),
  querySelectorAll: () => [], createElement: (tag) => makeEl(tag),
  addEventListener: () => {}, documentElement: makeEl("html"), body: makeEl("body"),
};
const localStorageStub = {
  _store: { "alfred.mode": startMode },
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._store, k) ? this._store[k] : null; },
  setItem(k, v) { this._store[k] = String(v); },
};
const sandbox = {
  console, document: documentStub, localStorage: localStorageStub,
  location: { protocol: "http:", href: "http://127.0.0.1:8642/" },
  navigator: { userAgent: "node-harness" },
  innerWidth: 1440, innerHeight: 900, devicePixelRatio: 1,
  matchMedia: () => ({ matches: false }),
  ResizeObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
  Math, JSON, Date,
  setInterval: () => 0, setTimeout: () => 0, clearTimeout: () => {},
  fetch: () => Promise.reject(new Error("no network in this harness")),
  CSS: { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&") },
  Event: class {}, addEventListener: () => {}, removeEventListener: () => {},
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);

const out = { ok: false, views: {}, crown: {}, modes: [] };
try {
  vm.runInContext(scriptSrc, sandbox, { filename: "index.html-script" });
  out.views.boot = stage._html;
  out.ok = true;
} catch (e) {
  out.error = String((e && e.stack) || e);
}
console.log(JSON.stringify(out));
"""


class TestArtifactsDoNotBecomeMemoryRecords:
    def test_creating_a_handout_writes_no_memory_record(self, tmp_path):
        runtime, manager, *_ = _built(tmp_path)
        before = len(manager.memory.recall(limit=1000))

        result = manager.handle_task(Task(description=HANDOUT_TEXT))
        assert result.succeeded, result.error

        after = len(manager.memory.recall(limit=1000))
        assert after == before, "creating an artifact must not, by itself, retain a memory record"

    def test_creating_a_diagram_writes_no_memory_record(self, tmp_path):
        runtime, manager, *_ = _built(tmp_path, media="local")
        before = len(manager.memory.recall(limit=1000))

        result = manager.handle_task(Task(description=DIAGRAM_TEXT))
        assert result.succeeded, result.error

        after = len(manager.memory.recall(limit=1000))
        assert after == before, "creating an artifact must not, by itself, retain a memory record"
