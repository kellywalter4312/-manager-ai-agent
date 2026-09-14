"""Read-anything follow-up (PDF-06) - a scanned PDF with no text layer is read
through the one content pipeline.

Real image-only PDFs are written with Pillow (pages that are pictures of text,
no text layer). Recognition is Windows' own offline engine on Windows (skipped
elsewhere); the page parsing and the "only when there is no text layer" rule
are tested through the recogniser seam everywhere.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.providers.document_knowledge_provider import DocumentKnowledgeProvider
from src.providers.media import local_ocr
from src.providers.media.local_ocr import OCRError, WindowsOCRReader

ON_WINDOWS = sys.platform.startswith("win")


def _scan(path, pages):
    images = []
    for lines in pages:
        image = Image.new("RGB", (1240, 700), "white")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("arial.ttf", 56)
        except OSError:
            font = ImageFont.load_default()
        for index, line in enumerate(lines):
            draw.text((60, 80 + 140 * index), line, fill="black", font=font)
        images.append(image)
    images[0].save(path, "PDF", save_all=True, append_images=images[1:], resolution=150)
    return path


def _system(tmp_path):
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


class TestScannedPages:
    def test_an_image_only_pdf_has_no_text_layer(self, tmp_path):
        scan = _scan(tmp_path / "scan.pdf", [["Invoice 2041"]])
        assert DocumentKnowledgeProvider(tmp_path).passages(scan) == ()

    def test_pages_are_parsed_from_the_recogniser_page_by_page(self, tmp_path, monkeypatch):
        scan = _scan(tmp_path / "scan.pdf", [["a"], ["b"], ["c"]])
        marker = local_ocr._PAGE_MARKER
        out = f"{marker}\nInvoice 2041\nTotal due\n{marker}\n{marker}\nPlease call\n"
        calls = []
        monkeypatch.setattr(local_ocr, "_run_powershell", lambda script, *, timeout: calls.append(script) or subprocess.CompletedProcess([], 0, out, ""))
        monkeypatch.setattr(WindowsOCRReader, "available", staticmethod(lambda: True))
        reader = WindowsOCRReader()
        assert reader.pdf_pages(scan) == ("Invoice 2041 Total due", "", "Please call")
        assert reader.pdf_pages(scan) and len(calls) == 1

    def test_a_recogniser_failure_is_an_honest_error(self, tmp_path, monkeypatch):
        scan = _scan(tmp_path / "scan.pdf", [["a"]])
        monkeypatch.setattr(local_ocr, "_run_powershell", lambda script, *, timeout: subprocess.CompletedProcess([], 1, "", "cannot render"))
        monkeypatch.setattr(WindowsOCRReader, "available", staticmethod(lambda: True))
        with pytest.raises(OCRError, match="cannot render"):
            WindowsOCRReader().pdf_pages(scan)

    def test_a_pdf_with_a_text_layer_never_reaches_the_recogniser(self, tmp_path, monkeypatch):
        from src.providers.documents.pdf import SimplePDFRenderer
        from src.services.artifacts.models import DocumentContent, DocumentSection

        def refuse(*args, **kwargs):
            raise AssertionError("the recogniser must not run for a PDF with a text layer")

        monkeypatch.setattr(WindowsOCRReader, "pdf_pages", refuse)
        system = _system(tmp_path)
        data = SimplePDFRenderer().render(DocumentContent("Plan", (DocumentSection("Week 1", "Revise chapter one."),)))
        status, body, _ = su.route_upload(system.manager.own_skills.name, "plan.pdf", "application/pdf", base64.b64encode(data).decode(), "conversation", system)
        assert status == 200 and json.loads(body)["readable_units"] >= 1

    @pytest.mark.skipif(not ON_WINDOWS, reason="Windows' own PDF renderer and offline recogniser")
    def test_a_shared_scan_is_read_aloud_page_by_page(self, tmp_path):
        system = _system(tmp_path)
        scan = _scan(tmp_path / "scan.pdf", [["Invoice 2041", "Total due 128 pounds by 30 September"], ["Page two of the letter", "Please call the office to confirm"]])
        agent = system.manager.own_skills.name
        status, body, _ = su.route_upload(agent, "scanned-letter.pdf", "application/pdf", base64.b64encode(scan.read_bytes()).decode(), "conversation", system)
        assert status == 200 and json.loads(body)["readable_units"] == 2
        whole = exchange(system.channel, system.conversation, "read this document to me", addressed=agent)
        assert whole.capability == "read_content" and "Invoice 2041" in whole.outcome and "Please call the office" in whole.outcome
        second = exchange(system.channel, system.conversation, "read page 2 to me", addressed=agent)
        assert "(page 2)" in second.outcome and "Page two of the letter" in second.outcome
