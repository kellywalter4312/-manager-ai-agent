"""REPAIR-01 - PDF / document capability, and the one governed ingestion
mechanism it depends on (REPAIR-02's foundation).

What this proves, end to end through the real served system (``build_live_system``
- the exact object ``scripts/serve_ui.py`` serves - over isolated temp
directories, never the production ``data/``):

    user supplies a file (POST /upload, or the terminal's `artifacts add`)
      -> one canonical supplied_file artifact (byte-identical, deduplicated)
      -> its real text units (a PDF's pages, read by the real pypdf reader)
      -> read aloud (Content -> Voice, spoken in full), a named page
      -> reasoning evidence (knowledge / open conversation), incl. comparing two
      -> a derived PDF version (real, decodable, linked to its source)
      -> a user-authorized edit (derived copy; the supplied original untouched)
      -> personal context ("this is my timetable"), superseded by a newer file
      -> still true after a genuine restart

Every PDF produced here is decoded back with ``pypdf``. Brain-dependent paths
use a scripted ``LLMProvider`` bound the way this project's own tests already
bind one - never a vendor call.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pypdf
import pytest

import scripts.serve_ui as su
from scripts.control_center import run_command
from scripts.exchange import exchange
from src.agents.tools.artifact_production import ArtifactProductionTool
from src.channels.channel import SPOKEN_LIMIT, speakable
from src.config.settings import Settings
from src.control_center.views import ConversationTurn
from src.core.contracts import Task
from src.manager.capabilities import (
    MANAGER_CAPABILITIES,
    document_conversion_intent,
    knowledge_intent,
    read_content_intent,
    revise_artifact_intent,
    select_capability,
)
from src.manager.knowledge import SUPPLIED_CONTENT, gather_knowledge_evidence
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.providers.documents.pdf import NullDocumentRenderer, SimplePDFRenderer
from src.providers.media.image import NullImageProvider
from src.services.artifacts import ingestion
from src.services.artifacts.content import (
    ContentUnit,
    artifact_ids_in,
    locator_in,
    read_artifact_units,
    reading_selection,
    select_units,
)
from src.services.artifacts.ingestion import (
    SUPPLIED_FILE_TYPE,
    IngestionError,
    extension_of,
    ingest_supplied_file,
    safe_filename,
)
from src.services.artifacts.models import DocumentContent, DocumentSection
from src.services.artifacts.store import ArtifactError, ArtifactStore
from src.services.memory.personal_context import all_current_personal_context

RENDERER = SimplePDFRenderer()


def _pdf(*pages: "tuple[str, str, str]") -> bytes:
    """A real multi-page PDF - one rendered page per (title, heading, body)."""
    writer = pypdf.PdfWriter()
    for title, heading, body in pages:
        blob = RENDERER.render(DocumentContent(title, (DocumentSection(heading, body),)))
        for page in pypdf.PdfReader(io.BytesIO(blob)).pages:
            writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


TIMETABLE = _pdf(
    ("Semester Timetable", "Monday", "10:00 Mathematics in Room 4. 14:00 Physics lab in Lab 3."),
    ("Exams", "Final exam", "The final exam is on 12 December at 09:00 in Hall B."),
)


def _pdf_text(path: "str | Path") -> str:
    """The decoded text of a real PDF, whitespace-normalised - a renderer
    wraps long lines, so "Physics lab / in Lab 3" is the same words."""
    raw = " ".join((page.extract_text() or "") for page in pypdf.PdfReader(str(path)).pages)
    return " ".join(raw.split())


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _system(tmp_path, **overrides):
    settings = Settings.from_env(
        env={},
        memory=overrides.pop("memory", "ephemeral"),
        artifacts_dir=str(tmp_path / "artifacts"),
        media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"),
        documents_dir=str(tmp_path / "docs"),
        **overrides,
    )
    return su.build_live_system(settings)


def _agent(system) -> str:
    return system.manager.own_skills.name


def _upload(system, data: bytes, name: str = "timetable.pdf", source: str = "conversation"):
    status, body, _ = su.route_upload(
        _agent(system), name, "application/pdf", base64.b64encode(data).decode(), source, system,
    )
    return status, json.loads(body)


def _say(system, text: str):
    return exchange(system.channel, system.conversation, text, addressed=_agent(system))


class _Capturing(LLMProvider):
    def __init__(self, answer: str = "SCRIPTED ANSWER") -> None:
        self.prompts: list[str] = []
        self._answer = answer

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, prompt, *, system=None):
        self.prompts.append(prompt)
        return Completion(text=self._answer, provider="scripted")


def _with_brain(system, provider: LLMProvider) -> None:
    system.manager.brain = LLMManagerBrain(provider, personality=system.runtime.identity.personality)
    system.manager.own_skills._converse = system.manager.brain.converse


# ---------------------------------------------------------------------------
# 1. One governed ingestion mechanism
# ---------------------------------------------------------------------------


class TestIngestion:
    def test_a_supplied_file_becomes_one_canonical_artifact_byte_for_byte(self, tmp_path):
        store = ArtifactStore(tmp_path)
        artifact, created = ingest_supplied_file(
            store, owner_agent="Alfred", filename="timetable.pdf", data=TIMETABLE,
            source="conversation", media_type="application/pdf",
        )
        assert created
        assert artifact.type == SUPPLIED_FILE_TYPE
        meta = artifact.latest.metadata
        assert meta["filename"] == "timetable.pdf"
        assert meta["sha256"] == _sha(TIMETABLE)
        assert meta["size_bytes"] == str(len(TIMETABLE))
        assert meta["source"] == "conversation"
        assert meta["content_kind"] == "document"
        assert Path(artifact.latest.renditions["pdf"]).read_bytes() == TIMETABLE
        content = json.loads(Path(artifact.latest.content_path).read_text(encoding="utf-8"))
        assert content["sha256"] == _sha(TIMETABLE) and content["ingested_at"]

    def test_identical_bytes_for_the_same_agent_are_never_duplicated(self, tmp_path):
        store = ArtifactStore(tmp_path)
        first, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="a.pdf", data=TIMETABLE, source="conversation")
        again, created = ingest_supplied_file(store, owner_agent="Alfred", filename="copy.pdf", data=TIMETABLE, source="control_center")
        assert not created and again.artifact_id == first.artifact_id
        other, created_other = ingest_supplied_file(store, owner_agent="Arthur", filename="a.pdf", data=TIMETABLE, source="conversation")
        assert created_other and other.artifact_id != first.artifact_id
        assert len(store.all()) == 2

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            (dict(data=b""), "empty"),
            (dict(filename="   "), "filename"),
            (dict(source=""), "where it came from"),
            (dict(owner_agent=""), "agent"),
        ],
    )
    def test_an_unacceptable_file_is_refused_before_anything_is_written(self, tmp_path, kwargs, message):
        store = ArtifactStore(tmp_path)
        arguments = dict(owner_agent="Alfred", filename="a.pdf", data=TIMETABLE, source="conversation")
        arguments.update(kwargs)
        with pytest.raises(IngestionError, match=message):
            ingest_supplied_file(store, **arguments)
        assert store.all() == ()
        assert not (tmp_path / "artifacts.json").exists()

    def test_an_oversized_file_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingestion, "MAX_SUPPLIED_FILE_BYTES", 10)
        with pytest.raises(IngestionError, match="limit"):
            ingest_supplied_file(ArtifactStore(tmp_path), owner_agent="Alfred", filename="a.pdf", data=b"x" * 11, source="conversation")

    def test_a_filename_can_never_carry_a_path(self):
        assert safe_filename("../../etc/passwd") == "passwd"
        assert safe_filename(r"C:\Users\someone\notes.pdf") == "notes.pdf"
        assert safe_filename("bad\x00name.pdf") == "badname.pdf"
        assert extension_of("archive.tar.GZ") == "gz"
        assert extension_of("no-extension") == "bin"
        assert extension_of("weird.p$f") == "bin"


# ---------------------------------------------------------------------------
# 2. Content -> usable units (generalized; PDF is one source)
# ---------------------------------------------------------------------------


class TestContentUnits:
    def test_a_supplied_pdf_is_read_page_by_page_by_the_real_reader(self, tmp_path):
        store = ArtifactStore(tmp_path)
        artifact, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="t.pdf", data=TIMETABLE, source="conversation")
        units = read_artifact_units(artifact)
        assert [u.locator for u in units] == ["page 1", "page 2"]
        assert "Physics lab in Lab 3" in units[0].text
        assert "Hall B" in units[1].text

    def test_a_document_alfred_produced_is_read_section_by_section(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        doc = DocumentContent("Notes", (DocumentSection("Intro", "First part."), DocumentSection("Plan", "Second part.")))
        tool.run(operation="create_document", content=doc.to_json())
        units = read_artifact_units(store.all()[0])
        assert [(u.locator, u.text) for u in units] == [("section 1", "Intro: First part."), ("section 2", "Plan: Second part.")]

    def test_content_with_no_text_honestly_has_no_units(self, tmp_path):
        store = ArtifactStore(tmp_path)
        image, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="photo.png", data=b"\x89PNG not really", source="conversation")
        assert image.latest.metadata["content_kind"] == "image"
        assert read_artifact_units(image) == ()

    def test_selection_prefers_a_named_unit_then_overlap_then_the_beginning(self):
        units = (ContentUnit("page 1", "Monday classes"), ContentUnit("page 2", "Exam in Hall B"), ContentUnit("page 3", "Holidays"))
        assert locator_in("What does Page 2 say?") == "page 2"
        assert select_units(units, "what does page 2 say") == (units[1],)
        # Relevance decides what survives a tight budget...
        assert select_units(units, "where is the exam hall", max_chars=len(units[1].text)) == (units[1],)
        # ...never what a short document loses, and results stay in document order.
        assert select_units(units, "where is the exam hall") == units
        assert select_units(units, "summarise this") == units

    def test_request_words_never_count_as_relevance(self):
        """Regression: "the" in a page used to out-score every other page for
        "compare this with the other document", dropping the rest silently."""
        units = (ContentUnit("page 1", "Physics lab in Lab 3"), ContentUnit("page 2", "The final exam is in Hall B"))
        assert select_units(units, "compare this with the other document", max_chars=len(units[0].text)) == (units[0],)

    def test_reading_stops_at_a_unit_boundary_and_says_where_to_continue(self):
        units = tuple(ContentUnit(f"page {i}", "x" * 1000) for i in range(1, 5))
        selected, continue_from = reading_selection(units, "read this to me", max_chars=2500)
        assert [u.locator for u in selected] == ["page 1", "page 2"]
        assert continue_from == "page 3"
        named, after = reading_selection(units, "read page 4 to me")
        assert [u.locator for u in named] == ["page 4"] and after is None

    def test_the_most_recently_mentioned_artifact_comes_first(self):
        lines = ["you: [shared a file] a.pdf (artifact aaaaaa111111)", "Alfred: ok", "you: [shared a file] b.pdf (artifact bbbbbb222222)"]
        assert artifact_ids_in(lines) == ("bbbbbb222222", "aaaaaa111111")


# ---------------------------------------------------------------------------
# 3. The governed artifact tool
# ---------------------------------------------------------------------------


class TestArtifactToolContentOperations:
    def _tool(self, tmp_path, owner="Alfred", renderer=None):
        store = ArtifactStore(tmp_path)
        return ArtifactProductionTool(store, renderer or SimplePDFRenderer(), NullImageProvider(), owner_agent=owner), store

    def test_read_content_returns_real_units_and_refuses_another_agents_file(self, tmp_path):
        tool, store = self._tool(tmp_path)
        mine, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="t.pdf", data=TIMETABLE, source="conversation")
        theirs, _ = ingest_supplied_file(store, owner_agent="Arthur", filename="t.pdf", data=TIMETABLE, source="conversation")
        payload = json.loads(tool.run(operation="read_content", content=mine.artifact_id))
        assert payload["type"] == SUPPLIED_FILE_TYPE and len(payload["units"]) == 2
        with pytest.raises(ArtifactError, match="no artifact found"):
            tool.run(operation="read_content", content=theirs.artifact_id)

    def test_read_content_is_honest_about_content_with_no_text(self, tmp_path):
        tool, store = self._tool(tmp_path)
        image, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="p.png", data=b"\x89PNG", source="conversation")
        with pytest.raises(ArtifactError, match="no readable text"):
            tool.run(operation="read_content", content=image.artifact_id)

    def test_derive_document_links_back_and_never_touches_the_source(self, tmp_path):
        tool, store = self._tool(tmp_path)
        source, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="t.pdf", data=TIMETABLE, source="conversation")
        doc = DocumentContent("t (PDF version)", (DocumentSection("Page 1", "Monday classes."),))
        tool.run(operation="derive_document", content=f"{source.artifact_id}\n{doc.to_json()}")
        derived = next(a for a in store.all() if a.type == "document")
        assert derived.latest.related_artifact_ids == (source.artifact_id,)
        assert "Monday classes." in _pdf_text(derived.latest.renditions["pdf"])
        assert Path(store.get(source.artifact_id).latest.renditions["pdf"]).read_bytes() == TIMETABLE

    def test_list_content_is_newest_first_and_honest_when_empty(self, tmp_path):
        tool, store = self._tool(tmp_path)
        with pytest.raises(ArtifactError, match="no document or supplied file"):
            tool.run(operation="list_content", content="3")
        first, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="a.pdf", data=TIMETABLE, source="conversation")
        second, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="b.txt", data=b"notes", source="conversation")
        listed = json.loads(tool.run(operation="list_content", content="5"))
        assert [e["artifact_id"] for e in listed] == [second.artifact_id, first.artifact_id]


# ---------------------------------------------------------------------------
# 4. Request shapes - and what they must NOT claim
# ---------------------------------------------------------------------------


def _claims(text: str) -> str:
    return select_capability(Task(description=text), MANAGER_CAPABILITIES).name


class TestRequestShapes:
    @pytest.mark.parametrize("text", [
        "Read this PDF to me", "Read it to me", "What does page 4 say?", "Read page 2 to me",
        "read me this document", "read section 3 aloud",
    ])
    def test_reading_requests_are_recognised(self, text):
        assert read_content_intent(Task(description=text))
        assert _claims(text) == "read_content"

    @pytest.mark.parametrize("text", [
        "read my messages to me", "explain page 2", "summarize this document", "read about the history of Rome",
    ])
    def test_non_reading_requests_are_not_claimed_as_reading(self, text):
        assert not read_content_intent(Task(description=text))

    def test_asking_what_a_document_says_is_reasoning_not_reading(self):
        assert _claims("What does this PDF say?") == "knowledge"
        assert _claims("Compare this with the other document") == "knowledge"

    def test_editing_and_conversion_are_not_swallowed_by_knowledge(self):
        for text in ("In this PDF, replace Room 4 with Room 7.", "Create a PDF version of this"):
            assert not knowledge_intent(Task(description=text))
        assert _claims("In this PDF, replace Room 4 with Room 7.") == "revise_artifact"
        assert _claims("Create a PDF version of this") == "create_artifact"
        assert document_conversion_intent("turn this into a PDF")

    def test_an_edit_shape_needs_a_document_to_point_at(self):
        assert not revise_artifact_intent(Task(description="change my voice to Spanish"))
        assert _claims("change my voice to Spanish") != "revise_artifact"
        assert revise_artifact_intent(Task(description="change 10:00 to 11:00 in this document"))


# ---------------------------------------------------------------------------
# 5. The real served system, Half Brain
# ---------------------------------------------------------------------------


class TestUploadRoute:
    def test_a_conversation_upload_registers_and_is_recorded_in_the_room(self, tmp_path):
        system = _system(tmp_path)
        status, body = _upload(system, TIMETABLE)
        assert status == 200
        assert body["created"] and body["readable_units"] == 2 and body["content_kind"] == "document"
        assert body["shared_in_conversation"]
        assert any(f"artifact {body['artifact_id']}" in line for line in system.conversation.transcript())

    def test_a_control_center_upload_says_nothing_in_the_room(self, tmp_path):
        system = _system(tmp_path)
        status, body = _upload(system, TIMETABLE, source="control_center")
        assert status == 200 and not body["shared_in_conversation"]
        assert not system.conversation.transcript()

    def test_malformed_uploads_are_refused_honestly(self, tmp_path, monkeypatch):
        system = _system(tmp_path)
        agent = _agent(system)
        encoded = base64.b64encode(TIMETABLE).decode()
        assert su.route_upload(agent, "t.pdf", "", "not base64!!", "conversation", system)[0] == 400
        assert su.route_upload("Nobody", "t.pdf", "", encoded, "conversation", system)[0] == 404
        assert su.route_upload(agent, "t.pdf", "", encoded, "somewhere", system)[0] == 400
        assert su.route_upload(agent, "t.pdf", "", "", "conversation", system)[0] == 400
        monkeypatch.setattr(ingestion, "MAX_SUPPLIED_FILE_BYTES", 10)
        monkeypatch.setattr(su, "MAX_SUPPLIED_FILE_BYTES", 10)
        assert su.route_upload(agent, "t.pdf", "", encoded, "conversation", system)[0] == 413
        assert system.runtime.artifacts.all() == ()

    def test_a_server_path_is_never_accepted_over_the_network(self, tmp_path):
        system = _system(tmp_path)
        status, body, _ = su.route_command("artifacts add C:/Windows/win.ini", None, system)
        assert status == 400 and b"refused over the network" in body
        assert system.runtime.artifacts.all() == ()


class TestReadingAndVoice:
    def test_read_this_pdf_to_me_reads_the_real_pages_and_is_spoken_in_full(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, TIMETABLE)
        turn = _say(system, "Read this PDF to me")
        assert turn.succeeded and turn.capability == "read_content"
        assert "Physics lab in Lab 3" in turn.outcome and "Hall B" in turn.outcome
        assert turn.speak_in_full

    def test_speak_in_full_speaks_past_the_ordinary_limit(self):
        long_text = "word " * 300
        base = dict(request="r", handled_by="Alfred", delegated=False, steps=(), outcome=long_text, succeeded=True)
        assert len(speakable(ConversationTurn(**base))) <= SPOKEN_LIMIT + 3
        assert speakable(ConversationTurn(**base, speak_in_full=True)) == long_text.strip()

    def test_a_named_page_reads_only_that_page(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, TIMETABLE)
        turn = _say(system, "What does page 2 say?")
        assert turn.succeeded and "Hall B" in turn.outcome and "Physics" not in turn.outcome

    def test_a_page_that_does_not_exist_is_named_honestly(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, TIMETABLE)
        turn = _say(system, "Read page 9 to me")
        assert not turn.succeeded and "no page 9" in turn.outcome

    def test_with_nothing_shared_reading_says_so(self, tmp_path):
        system = _system(tmp_path)
        turn = _say(system, "Read this PDF to me")
        assert not turn.succeeded and "don't have a document" in turn.outcome

    def test_half_brain_reasoning_refuses_but_offers_to_read(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, TIMETABLE)
        turn = _say(system, "What does this PDF say?")
        assert not turn.succeeded
        assert "read it to you word for word" in turn.outcome


class TestGenerationAndEditing:
    def test_a_pdf_version_is_a_real_derived_document_and_the_original_is_untouched(self, tmp_path):
        system = _system(tmp_path)
        _, body = _upload(system, TIMETABLE)
        turn = _say(system, "Create a PDF version of this")
        assert turn.succeeded, turn.outcome
        derived = next(a for a in system.runtime.artifacts.all() if a.type == "document")
        assert derived.latest.related_artifact_ids == (body["artifact_id"],)
        text = _pdf_text(derived.latest.renditions["pdf"])
        assert "Physics lab in Lab 3" in text and "Hall B" in text
        source = system.runtime.artifacts.get(body["artifact_id"])
        assert _sha(Path(source.latest.renditions["pdf"]).read_bytes()) == _sha(TIMETABLE)

    def test_a_literal_edit_produces_an_edited_copy(self, tmp_path):
        system = _system(tmp_path)
        _, body = _upload(system, TIMETABLE)
        turn = _say(system, "In this PDF, replace Room 4 with Room 7.")
        assert turn.succeeded, turn.outcome
        edited = next(a for a in system.runtime.artifacts.all() if a.type == "document")
        assert edited.latest.title.endswith("(edited)")
        text = _pdf_text(edited.latest.renditions["pdf"])
        assert "Room 7" in text and "Room 4" not in text
        source = system.runtime.artifacts.get(body["artifact_id"])
        assert Path(source.latest.renditions["pdf"]).read_bytes() == TIMETABLE

    def test_an_edit_of_text_that_is_not_there_changes_nothing(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, TIMETABLE)
        turn = _say(system, "In this PDF, replace Room 99 with Room 7.")
        assert not turn.succeeded and "couldn't find" in turn.outcome
        assert [a.type for a in system.runtime.artifacts.all()] == [SUPPLIED_FILE_TYPE]

    def test_an_edit_to_alfreds_own_document_revises_the_same_identity(self, tmp_path):
        system = _system(tmp_path)
        # No day/time in the text - "Monday at noon" is (correctly) the
        # calendar capability's own request shape, not a handout's.
        created = _say(system, "make a handout TITLE: Plan SECTION: Venue: Meet in Room 4.")
        assert created.succeeded, created.outcome
        doc = next(a for a in system.runtime.artifacts.all() if a.type == "document")
        turn = _say(system, f"In artifact {doc.artifact_id}, replace Room 4 with Room 9.")
        assert turn.succeeded, turn.outcome
        revised = system.runtime.artifacts.get(doc.artifact_id)
        assert revised.latest.version == 2
        assert "Room 9" in _pdf_text(revised.latest.renditions["pdf"])

    def test_generation_is_honest_when_no_pdf_engine_is_configured(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, NullDocumentRenderer(), NullImageProvider(), owner_agent="Alfred")
        source, _ = ingest_supplied_file(store, owner_agent="Alfred", filename="t.pdf", data=TIMETABLE, source="conversation")
        doc = DocumentContent("t (PDF version)", (DocumentSection("Page 1", "Monday."),))
        output = tool.run(operation="derive_document", content=f"{source.artifact_id}\n{doc.to_json()}")
        derived = next(a for a in store.all() if a.type == "document")
        assert "pdf" not in derived.latest.renditions and "txt" in derived.latest.renditions
        assert "pdf=" not in output


class TestPersonalContextFromADocument:
    def test_this_is_my_timetable_links_the_shared_file_and_a_newer_one_supersedes_it(self, tmp_path):
        system = _system(tmp_path)
        _, first = _upload(system, TIMETABLE)
        assert "timetable.pdf" in _say(system, "This is my timetable").outcome
        updated = _pdf(("Semester Timetable", "Monday", "10:00 Mathematics in Room 11."))
        _, second = _upload(system, updated, name="timetable-v2.pdf")
        _say(system, "This is my updated timetable")
        current = [p for p in all_current_personal_context(recall=system.manager.memory.recall) if p.topic == "timetable"]
        assert len(current) == 1 and current[0].artifact_id == second["artifact_id"]
        # History is kept, never deleted: the first document is still there.
        assert system.runtime.artifacts.get(first["artifact_id"]) is not None

    def test_a_typed_value_is_never_replaced_by_an_earlier_shared_file(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, TIMETABLE)
        _say(system, "This is my new schedule: Mondays 9-3")
        current = [p for p in all_current_personal_context(recall=system.manager.memory.recall) if p.topic == "schedule"]
        assert current and current[0].artifact_id == "" and "Mondays 9-3" in current[0].content


class TestFullBrainReceivesTheRealDocument:
    def test_what_does_this_pdf_say_reaches_the_brain_with_the_pages(self, tmp_path):
        system = _system(tmp_path)
        provider = _Capturing("It is a timetable with a Physics lab on Monday.")
        _with_brain(system, provider)
        _upload(system, TIMETABLE)
        turn = _say(system, "What does this PDF say?")
        assert turn.succeeded and turn.capability == "knowledge"
        assert any("Physics lab in Lab 3" in p and "Hall B" in p for p in provider.prompts)

    def test_a_personal_question_uses_the_current_timetable_document(self, tmp_path):
        system = _system(tmp_path)
        provider = _Capturing("14:00.")
        _with_brain(system, provider)
        _upload(system, TIMETABLE)
        _say(system, "This is my timetable")
        provider.prompts.clear()
        turn = _say(system, "What time is my Physics lab on Monday?")
        assert turn.succeeded
        assert any("Physics lab in Lab 3" in p for p in provider.prompts)

    def test_comparing_two_shared_documents_offers_both(self, tmp_path):
        system = _system(tmp_path)
        provider = _Capturing("They differ.")
        _with_brain(system, provider)
        _upload(system, TIMETABLE)
        _upload(system, _pdf(("Exam Schedule", "Resit", "Resits happen in January in Hall C.")), name="resits.pdf")
        provider.prompts.clear()
        _say(system, "Compare this with the other document")
        assert any("Hall C" in p and "Physics lab in Lab 3" in p for p in provider.prompts)

    def test_ordinary_knowledge_evidence_is_unchanged_without_a_pointed_at_document(self):
        evidence = gather_knowledge_evidence("who was Julius Caesar")
        assert SUPPLIED_CONTENT not in evidence.sources


class TestControlCenterAndPersistence:
    def test_the_terminal_control_center_adds_a_local_file(self, tmp_path):
        system = _system(tmp_path)
        source = tmp_path / "notes.pdf"
        source.write_bytes(TIMETABLE)
        result = run_command(system.centre, f"artifacts add {source}", 100, system.channel, system.conversation)
        assert result.startswith("added:") and "2 readable unit(s)" in result
        again = run_command(system.centre, f"artifacts add {source}", 100, system.channel, system.conversation)
        assert again.startswith("already present")
        assert "no file found" in run_command(system.centre, f"artifacts add {tmp_path / 'ghost.pdf'}", 100, system.channel, system.conversation)
        listing = run_command(system.centre, "artifacts", 100, system.channel, system.conversation)
        assert "[supplied_file]" in listing and "notes.pdf" in listing

    def test_everything_survives_a_genuine_restart(self, tmp_path):
        first = _system(tmp_path, memory="persistent", memory_dir=str(tmp_path / "memory"))
        _, body = _upload(first, TIMETABLE)
        _say(first, "This is my timetable")

        second = _system(tmp_path, memory="persistent", memory_dir=str(tmp_path / "memory"))
        assert second.runtime.artifacts.get(body["artifact_id"]) is not None
        current = [p for p in all_current_personal_context(recall=second.manager.memory.recall) if p.topic == "timetable"]
        assert current and current[0].artifact_id == body["artifact_id"]
        turn = _say(second, "Read page 1 to me")
        assert turn.succeeded and "Physics lab in Lab 3" in turn.outcome

    def test_the_served_page_offers_both_upload_doors(self):
        page = (Path(su.__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
        assert 'class="attach-input"' in page and 'id="cc-upload"' in page
        assert 'fetch("upload"' in page
        assert '"conversation"' in page and '"control_center"' in page

    def test_the_control_center_upload_survives_the_live_redraw(self):
        """Two defects found only by driving a real browser: the live
        five-second redraw replaced the file input between choosing a file
        and clicking Add (the choice was silently lost), and wiped the
        result text afterwards. The upload now starts on the choice itself,
        and #cc-out is carried across a redraw like an unsent draft is."""
        page = (Path(su.__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
        assert 'ccFile.addEventListener("change"' in page
        refresh = page.split("async function refreshFromLive(){", 1)[1].split("\n  }\n", 1)[0]
        assert 'querySelector("#cc-out")?.textContent' in refresh
        assert "newOutput.textContent = ccOutput" in refresh
