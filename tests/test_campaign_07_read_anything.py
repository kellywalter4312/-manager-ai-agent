"""Pre-Stage-9 Campaign 7 - read anything aloud.

Real isolated served systems through the production composition
(``build_live_system``) and the real upload route. Kept notes, schedule and
tasks are read back from the stores that own them; the printed text in an image
comes from Windows' offline recogniser (a real run on Windows, a seam elsewhere);
saved web pages and e-books are read by the one document reader. Every reading
answer is marked to be spoken in full.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import zipfile

import pytest

import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.manager.capabilities import read_back_collections, read_content_intent
from src.core.contracts import Task
from src.providers.document_knowledge_provider import DocumentKnowledgeProvider, html_blocks
from src.providers.media import local_ocr
from src.providers.media.local_ocr import OCRError, WindowsOCRReader

ON_WINDOWS = sys.platform.startswith("win")


def _system(tmp_path):
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _say(system, text):
    return exchange(system.channel, system.conversation, text, addressed=system.manager.own_skills.name)


def _upload(system, name, mime, data):
    status, body, _ = su.route_upload(
        system.manager.own_skills.name, name, mime, base64.b64encode(data).decode(), "conversation", system,
    )
    assert status == 200, body
    return json.loads(body)


PAGE = b"""<!doctype html><html><head><title>Tide times</title><style>p{color:red}</style>
<script>var hidden = "never read";</script></head><body><nav>Home | News</nav>
<article><h1>High tide moves to evening</h1><p>From Monday the harbour's high tide falls after 7pm.</p>
<p>Moorings on the east wall close an hour earlier.</p><ul><li>Bring a lamp</li></ul></article>
<footer>Copyright</footer></body></html>"""


def _epub() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", (
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
        ))
        book.writestr("OEBPS/content.opf", (
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>'
            '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>'
        ))
        book.writestr("OEBPS/ch1.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter One</h1><p>The keeper counted ships.</p></body></html>')
        book.writestr("OEBPS/ch2.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter Two</h1><p>Fog rolled in before dawn.</p></body></html>')
    return buffer.getvalue()


def _png_with_text(path, lines):
    script = (
        "Add-Type -AssemblyName System.Drawing; "
        "$b = New-Object System.Drawing.Bitmap 900, 260; $g = [System.Drawing.Graphics]::FromImage($b); "
        "$g.Clear([System.Drawing.Color]::White); $f = New-Object System.Drawing.Font('Arial', 34); "
        + "".join(f"$g.DrawString('{line}', $f, [System.Drawing.Brushes]::Black, 20, {40 + 100 * i}); " for i, line in enumerate(lines))
        + f"$b.Save('{path}', [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, capture_output=True, timeout=60)
    return path.read_bytes()


class TestReadingBackWhatIsKept:
    @pytest.mark.parametrize("text, expected", [
        ("read my notes to me", ("notes",)),
        ("read back my schedule for tomorrow", ("schedule",)),
        ("go over my tasks", ("tasks",)),
        ("read my reminders aloud", ("tasks",)),
        ("read my notes and my agenda to me", ("notes", "schedule")),
    ])
    def test_read_back_requests_name_their_collections(self, text, expected):
        assert read_back_collections(text) == expected

    @pytest.mark.parametrize("text", ["I read my notes every morning", "read my notes PDF to me", "my schedule is busy"])
    def test_ordinary_sentences_and_files_are_not_read_back(self, text):
        assert read_back_collections(text) == ()

    def test_notes_schedule_and_tasks_are_read_back_in_full(self, tmp_path):
        from datetime import timedelta

        from src.services.memory.day import Calendar

        system = _system(tmp_path)
        # The schedule listing looks ahead from the real today, so the date is
        # computed from it rather than fixed.
        ahead = (Calendar.configured(system.runtime.settings.timezone).today() + timedelta(days=3)).isoformat()
        for text in (
            "note that the plumber comes on Thursday", "record this: the wifi password is on the fridge",
            f"I have a dentist appointment on {ahead} at 3pm", "add a task to renew my passport",
            "remind me in 30 minutes to stretch",
        ):
            assert _say(system, text).succeeded, text
        notes = _say(system, "read my notes to me")
        assert notes.capability == "read_back" and notes.succeeded
        assert "the plumber comes on Thursday" in notes.outcome and "wifi password" in notes.outcome
        schedule = _say(system, "read back my schedule")
        assert "dentist appointment" in schedule.outcome
        tasks = _say(system, "go over my tasks")
        assert "renew my passport" in tasks.outcome and "stretch - at " in tasks.outcome
        assert "(" not in tasks.outcome.split("\n", 2)[-1], "no internal ids are spoken"

    def test_nothing_kept_is_said_honestly(self, tmp_path):
        system = _system(tmp_path)
        assert "haven't asked me to note anything" in _say(system, "read my notes to me").outcome


class TestTheTextInAnImage:
    @pytest.mark.parametrize("text", [
        "read the text in this photo to me", "what does this sign say?", "read me this screenshot",
        "what's written on the whiteboard", "read the words on this poster aloud",
    ])
    def test_asking_for_the_words_in_a_picture_is_a_reading_request(self, text):
        assert read_content_intent(Task(description=text))

    def test_asking_what_a_picture_shows_is_not(self):
        assert not read_content_intent(Task(description="what's in this image?"))

    def test_recognised_lines_come_from_the_recogniser_and_are_cached(self, tmp_path, monkeypatch):
        image = tmp_path / "sign.png"
        image.write_bytes(b"\x89PNG not really decoded here")
        calls = []

        def fake_run(script, *, timeout):
            calls.append(script)
            return subprocess.CompletedProcess([], 0, stdout="Keep  left\n\nNo parking\n", stderr="")

        monkeypatch.setattr(local_ocr, "_run_powershell", fake_run)
        monkeypatch.setattr(WindowsOCRReader, "available", staticmethod(lambda: True))
        reader = WindowsOCRReader()
        assert reader.lines(image) == ("Keep left", "No parking")
        assert reader.lines(image) == ("Keep left", "No parking") and len(calls) == 1

    def test_a_recogniser_failure_is_an_honest_error(self, tmp_path, monkeypatch):
        image = tmp_path / "sign.png"
        image.write_bytes(b"x")
        monkeypatch.setattr(local_ocr, "_run_powershell", lambda script, *, timeout: subprocess.CompletedProcess([], 2, "", "no OCR language"))
        monkeypatch.setattr(WindowsOCRReader, "available", staticmethod(lambda: True))
        with pytest.raises(OCRError, match="no OCR language"):
            WindowsOCRReader().lines(image)

    @pytest.mark.skipif(not ON_WINDOWS, reason="Windows' own offline recogniser")
    def test_a_shared_image_is_read_aloud_with_the_real_recogniser(self, tmp_path):
        system = _system(tmp_path)
        data = _png_with_text(tmp_path / "board.png", ["Library closes at 6pm on Friday", "Room 214 bring your card"])
        _upload(system, "noticeboard.png", "image/png", data)
        turn = _say(system, "read the text in this photo to me")
        assert turn.capability == "read_content" and turn.succeeded, turn.outcome
        assert "Library closes at 6pm on Friday" in turn.outcome and "Room 214" in turn.outcome
        assert "has no page 9" in _say(system, "read page 9 of this image to me").outcome


class TestWebPagesAndBooks:
    def test_html_blocks_skip_scripts_styles_and_navigation(self):
        blocks = html_blocks(PAGE.decode())
        assert blocks[:4] == [
            "Tide times", "High tide moves to evening",
            "From Monday the harbour's high tide falls after 7pm.", "Moorings on the east wall close an hour earlier.",
        ]
        assert not any("never read" in b or "Home | News" in b or "Copyright" in b for b in blocks)

    def test_an_epub_is_read_in_spine_order(self, tmp_path):
        book = tmp_path / "keeper.epub"
        book.write_bytes(_epub())
        texts = [text for _locator, text in DocumentKnowledgeProvider(tmp_path).passages(book)]
        assert texts == ["Chapter One", "The keeper counted ships.", "Chapter Two", "Fog rolled in before dawn."]

    def test_a_malformed_book_yields_nothing(self, tmp_path):
        book = tmp_path / "broken.epub"
        book.write_bytes(b"not a zip")
        assert DocumentKnowledgeProvider(tmp_path).passages(book) == ()

    def test_a_shared_article_and_book_are_read_aloud(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, "tide-times.html", "text/html", PAGE)
        article = _say(system, "read this article to me")
        assert article.capability == "read_content" and "High tide moves to evening" in article.outcome
        _upload(system, "keeper.epub", "application/epub+zip", _epub())
        book = _say(system, "read paragraph 4 of this book to me")
        assert "Fog rolled in before dawn." in book.outcome

    def test_a_bare_web_address_is_answered_honestly_without_fetching(self, tmp_path):
        system = _system(tmp_path)
        turn = _say(system, "read this article to me https://example.org/story")
        assert not turn.succeeded and "I don't open web addresses myself" in turn.outcome

    def test_a_web_address_never_reads_a_different_shared_file_instead(self, tmp_path):
        # Found at runtime: with a book already shared, the address was
        # ignored and the book was read.
        system = _system(tmp_path)
        _upload(system, "keeper.epub", "application/epub+zip", _epub())
        turn = _say(system, "read this article to me https://example.org/story")
        assert not turn.succeeded and "I don't open web addresses myself" in turn.outcome
        assert "keeper" not in turn.outcome
