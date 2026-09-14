"""A real knowledge source: the documents the user put in a folder.

**Real, not speculative.** No account, no API key, no vendor SDK, no
network, no index to build - one directory, read with the standard
library (plain text) or a real, lazily-imported, optional library
(PDF - see ``_paragraphs`` below), whenever a lookup actually happens.
This is the smallest genuinely working user-supplied knowledge source
available to a project that has no way to provision anything else, and
it is the whole mechanism by which a person's own books, notes,
articles, course material, transcripts or PDFs become something Alfred
can quote.

**PDF, spreadsheet and presentation support (Phase 3), each added
deliberately narrow.** ``pypdf``/``openpyxl``/``python-pptx`` are real,
optional dependencies (see ``requirements.txt``), each imported only
when a file of its own format is actually encountered - the identical
lazy-import discipline every other optional capability in this project
already holds itself to (Pillow, imageio-ffmpeg, kokoro-onnx).
Extraction is always real, structural text - PDF pages, spreadsheet
rows, presentation slide text frames - never OCR and never image
content: a scanned PDF or an image-only slide honestly yields nothing,
exactly as an empty document would, rather than a guess. Images
themselves remain genuinely unsupported *here* - see
``src/agents/tools/image_analysis.py`` for the separate, honestly
bounded visual-reasoning capability this checkpoint adds instead. No
format is claimed anywhere in this file that it cannot actually read.

**A real, stated limitation, not hidden: token-overlap retrieval is
not full-dataset aggregation.** A spreadsheet row that never shares a
word with the query ("what is the highest value") will not be
retrieved by overlap scoring alone - a search built for prose passages,
applied honestly to structured rows. ``_spreadsheet_rows`` widens the
excerpt cap for tabular data specifically (see ``SPREADSHEET_MAX_EXCERPTS``)
so a realistically-sized sheet's rows mostly all reach the Brain, which
is what actually makes "the highest value" answerable in practice - but
this remains bounded retrieval, not a guaranteed complete scan, and is
recorded as such in ARCHITECTURE.md rather than claimed as unlimited.

**Generic, not a catalogue.** There is no list of books here, no list of
subjects, no title, no author and no topic, and there must never be one.
What this class knows is a directory and a file suffix; *what is in the
directory* is entirely the user's decision, and a document dropped in
tomorrow needs no change to this file. A history book, a biography, a
motivational book, a syllabus, a set of lecture notes and a business
report are all the same thing here: text with a name on it.

**Deterministic, not clever.** Ranking is literal token overlap - the
identical rule ``Memory.recall`` already uses, and stated in that
module's own docstring as a deliberate choice: no embeddings, no semantic
search, no model. Those are later implementation decisions that belong
behind ``KnowledgeSourceProvider``, as a different provider, not as
cleverness smuggled into this one. The same folder and the same query
therefore always produce the same excerpts in the same order.

**It never answers.** Every excerpt crosses the boundary carrying the
document it came from (see ``KnowledgeExcerpt.cite``). This class
composes nothing, summarises nothing and concludes nothing - the Brain
does that, over evidence, one layer up. A provider that produced prose
would be a second, ungoverned brain with a filesystem attached.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Sequence

from src.core.contracts import tokenize
from src.providers.knowledge import (
    KnowledgeExcerpt,
    KnowledgeResult,
    KnowledgeSourceProvider,
    utc_now,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# What counts as a readable document. Plain text formats need no
# dependency at all; ".pdf"/".xlsx"/".csv"/".pptx" each need exactly one
# real, optional, lazily imported library (see _paragraphs below) -
# added deliberately, not by accident, because each is a commonly
# supplied "document" format this project now has a real, working way
# to read. Every other binary format (images) remains genuinely
# unsupported here: a dependency added for a format nobody asked to
# verify is how a "knowledge source" quietly becomes an unverified
# document pipeline. A future format is a new suffix handled in
# _paragraphs, or a new provider behind the same contract.
DEFAULT_SUFFIXES: tuple[str, ...] = (
    ".md", ".markdown", ".txt", ".text", ".pdf", ".xlsx", ".csv", ".pptx", ".docx",
)

_SPREADSHEET_SUFFIXES = frozenset({".xlsx", ".csv"})
_PRESENTATION_SUFFIXES = frozenset({".pptx"})
# Word documents: an OOXML package (a zip of XML), read with the standard
# library alone - no new dependency (Pre-Stage-9 Campaign 3).
_WORD_SUFFIXES = frozenset({".docx"})
_WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A passage is a paragraph: the unit an author already chose. Splitting on
# blank lines needs no knowledge of what the document is about, which is
# the property that matters here.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

# Bounds, so one enormous file cannot fill a prompt or a log. Mechanics of
# reading, never anything that could express a topic.
MAX_EXCERPTS = 3
MAX_EXCERPT_CHARS = 600
MAX_DOCUMENT_BYTES = 4_000_000

# A spreadsheet row is a much smaller unit than a prose paragraph, and an
# aggregate question ("what is the highest value") genuinely needs most
# of a realistically-sized sheet's rows to be answerable at all - see
# this module's own docstring for the honest limit this still has.
# Mechanics of reading, never a claim about what the data means.
SPREADSHEET_MAX_EXCERPTS = 40
MAX_SPREADSHEET_ROWS = 500
MAX_PRESENTATION_SLIDES = 200


class DocumentKnowledgeProvider(KnowledgeSourceProvider):
    """Searches the user's own documents directory for relevant passages."""

    def __init__(
        self,
        directory: str | Path,
        *,
        suffixes: Sequence[str] = DEFAULT_SUFFIXES,
        max_excerpts: int = MAX_EXCERPTS,
        max_excerpt_chars: int = MAX_EXCERPT_CHARS,
    ) -> None:
        """``directory`` is where the user put their material.

        A missing directory is not an error and never becomes one. Most
        installations will never have one, and "the folder is not there"
        and "the folder has nothing on this" are the same honest answer -
        an empty result - for the same reason ``NullKnowledgeProvider``
        returns one rather than raising.
        """
        if max_excerpts <= 0:
            raise ValueError("max_excerpts must be positive")
        if max_excerpt_chars <= 0:
            raise ValueError("max_excerpt_chars must be positive")
        self._directory = Path(directory)
        self._suffixes = tuple(
            s if s.startswith(".") else f".{s}" for s in (suffixes or ()) if str(s).strip()
        )
        if not self._suffixes:
            raise ValueError("at least one document suffix must be readable")
        self._max_excerpts = max_excerpts
        self._max_excerpt_chars = max_excerpt_chars

    @property
    def name(self) -> str:
        return "documents"

    @property
    def directory(self) -> Path:
        """Where this provider reads from. Exposed for a status screen,
        never as a way to change it: the path is decided at construction
        by the composition root, exactly as every other provider's
        configuration is."""
        return self._directory

    def documents(self) -> tuple[Path, ...]:
        """Every readable document currently present, sorted.

        Read per call rather than cached at construction, so a book added
        while Alfred is running is found on the next question - the same
        reasoning the per-run profile and schedule documents already
        follow. Sorted so results are deterministic.
        """
        if not self._directory.is_dir():
            return ()
        return tuple(
            sorted(
                (
                    path
                    for path in self._directory.rglob("*")
                    if path.is_file() and path.suffix.lower() in self._suffixes
                ),
                key=lambda p: str(p).lower(),
            )
        )

    def lookup(self, query: str) -> KnowledgeResult:
        """The passages in the user's own material that mention this query.

        Raises on an empty query, as the contract requires. Everything
        else degrades to an empty result rather than an exception: a
        document that cannot be decoded is skipped and logged, because
        one unreadable file must not make every other book unreachable.
        """
        text = str(query or "").strip()
        if not text:
            raise ValueError("query must not be empty")

        wanted = tokenize(text)
        # One shared word is a coincidence; two is a reason to quote a
        # passage. Derived from the query rather than fixed, so a
        # one-word question still works - and expressed as a threshold
        # rather than as a list of words to ignore, because a stop-word
        # list is a list of English facts this project has no business
        # holding.
        threshold = min(2, len(wanted)) or 1

        scored: list[tuple[int, str, int, KnowledgeExcerpt]] = []
        for path in self.documents():
            # A spreadsheet row is compact, structured data, not prose -
            # excluding a row for sharing zero words with an aggregate
            # question ("what is the highest value") would make that
            # question unanswerable no matter how the evidence is used
            # downstream. Every row is offered instead, bounded by
            # SPREADSHEET_MAX_EXCERPTS above rather than by word overlap -
            # the honest trade this module's own docstring already states.
            is_spreadsheet = path.suffix.lower() in _SPREADSHEET_SUFFIXES
            for index, paragraph in self._paragraphs(path):
                overlap = len(wanted & tokenize(paragraph))
                if overlap < threshold and not is_spreadsheet:
                    continue
                scored.append(
                    (
                        overlap,
                        str(path).lower(),
                        index,
                        KnowledgeExcerpt(
                            document=path.name,
                            content=self._truncate(paragraph),
                            locator=self._locator(path, index),
                        ),
                    )
                )

        # Best overlap first, then document path, then position - a total
        # order, so the same folder and query always produce the same
        # answer in the same sequence.
        scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
        # A spreadsheet's rows need a wider cap to be genuinely useful for
        # an aggregate question - see this module's own docstring for why,
        # and for the honest limit this still has. Applied only when a
        # spreadsheet is actually among the matches, so an ordinary
        # text/PDF lookup is completely unaffected.
        cap = self._max_excerpts
        if any(entry[1].endswith(tuple(_SPREADSHEET_SUFFIXES)) for entry in scored):
            cap = max(cap, SPREADSHEET_MAX_EXCERPTS)
        excerpts = tuple(entry[3] for entry in scored[:cap])

        return KnowledgeResult(
            query=text,
            excerpts=excerpts,
            source=self.name,
            consulted_at=utc_now(),
        )

    def passages(self, path: "str | Path") -> tuple[tuple[str, str], ...]:
        """Every readable unit of one specific document, in order, each
        with its honest locator ("page 3", "row 12", "slide 2",
        "paragraph 4") - the identical per-format reading ``lookup`` above
        already uses, exposed for a single file rather than a whole
        folder search.

        Exists so a file the user supplied *directly* (a supplied-file
        artifact, see ``src/services/artifacts/content.py``) is read by
        exactly this code rather than by a second PDF/spreadsheet/
        presentation reader. ``path`` need not live under this provider's
        own directory - reading a named file is not a search of the
        folder. Nothing readable honestly yields an empty tuple, exactly
        as an unreadable file already does for ``lookup``.
        """
        target = Path(path)
        if not target.is_file():
            return ()
        return tuple((self._locator(target, index), text) for index, text in self._paragraphs(target))

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def _paragraphs(self, path: Path) -> Iterable[tuple[int, str]]:
        """Numbered paragraphs of one document, or nothing if it cannot be read.

        A PDF's "paragraphs" are its pages - a page is the unit ``pypdf``
        actually gives back, the identical "use the unit the format
        already hands you" reasoning this module's own docstring already
        states for a plain-text paragraph. Locator text distinguishes
        the two ("paragraph N" vs "page N") so a citation is honest
        about which kind of document it came from.
        """
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._pdf_pages(path)
        if suffix in _SPREADSHEET_SUFFIXES:
            return self._spreadsheet_rows(path)
        if suffix in _PRESENTATION_SUFFIXES:
            return self._presentation_slides(path)
        if suffix in _WORD_SUFFIXES:
            return self._word_paragraphs(path)

        try:
            if path.stat().st_size > MAX_DOCUMENT_BYTES:
                logger.warning(
                    "skipping '%s': larger than %d bytes", path.name, MAX_DOCUMENT_BYTES
                )
                return ()
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            # One unreadable document must not make the rest unreachable.
            logger.warning("could not read '%s': %s", path.name, exc)
            return ()

        return [
            (number, " ".join(block.split()))
            for number, block in enumerate(_PARAGRAPH_BREAK.split(raw), start=1)
            if block.strip()
        ]

    def _pdf_pages(self, path: Path) -> Iterable[tuple[int, str]]:
        """Real, extracted text per page - never OCR, never a guess at
        what a scanned image might say. A PDF with no embedded text
        layer honestly yields nothing, exactly as an empty document
        would, rather than fabricating content that was never there.

        ``pypdf`` is imported here, lazily, so every installation that
        never puts a PDF in its documents folder never imports it - the
        identical discipline this project already holds every other
        optional dependency to.
        """
        try:
            if path.stat().st_size > MAX_DOCUMENT_BYTES:
                logger.warning(
                    "skipping '%s': larger than %d bytes", path.name, MAX_DOCUMENT_BYTES
                )
                return ()
            import pypdf

            reader = pypdf.PdfReader(str(path))
            pages = []
            for number, page in enumerate(reader.pages, start=1):
                text = " ".join((page.extract_text() or "").split())
                if text:
                    pages.append((number, text))
            return pages
        except ImportError:
            logger.warning(
                "skipping '%s': the 'pypdf' package is not installed "
                "(see requirements.txt)", path.name,
            )
            return ()
        except Exception as exc:  # noqa: BLE001 - a malformed PDF must not
            # make every other document unreachable, the identical rule
            # OSError already follows for a plain-text file above.
            logger.warning("could not read '%s': %s", path.name, exc)
            return ()

    def _spreadsheet_rows(self, path: Path) -> Iterable[tuple[int, str]]:
        """Real cell data, one row per unit - the identical "use the
        unit the format already hands you" reasoning ``_pdf_pages``
        already applies to a page. A header row (when present) names
        each following row's own cells, so "Name: Alice, Revenue: 4200"
        is what a row actually reads as, never a bare, unlabelled list
        of values - real, if a spreadsheet genuinely has no header row,
        cells are still returned, just unlabelled (Column 1, Column 2, ...).

        ``.csv`` uses the standard library (``csv``) - no dependency at
        all, the identical "plain formats need nothing extra" rule this
        module already holds for ``.txt``/``.md``. ``.xlsx`` uses
        ``openpyxl``, lazily imported, real per-sheet, per-row reading -
        every sheet in a workbook is read, its name carried in the
        locator, so a person's own sheet naming is never lost.
        """
        suffix = path.suffix.lower()
        try:
            if path.stat().st_size > MAX_DOCUMENT_BYTES:
                logger.warning(
                    "skipping '%s': larger than %d bytes", path.name, MAX_DOCUMENT_BYTES
                )
                return ()

            if suffix == ".csv":
                return self._csv_rows(path)
            return self._xlsx_rows(path)
        except ImportError:
            logger.warning(
                "skipping '%s': the 'openpyxl' package is not installed "
                "(see requirements.txt)", path.name,
            )
            return ()
        except Exception as exc:  # noqa: BLE001 - a malformed spreadsheet
            # must not make every other document unreachable.
            logger.warning("could not read '%s': %s", path.name, exc)
            return ()

    def _csv_rows(self, path: Path) -> list[tuple[int, str]]:
        import csv

        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
        if not rows:
            return []

        header, body = rows[0], rows[1 : MAX_SPREADSHEET_ROWS + 1]
        return [
            (index, self._row_text(header, row))
            for index, row in enumerate(body, start=2)  # row 1 is the header
            if any(str(cell).strip() for cell in row)
        ]

    def _xlsx_rows(self, path: Path) -> list[tuple[int, str]]:
        import openpyxl

        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        try:
            rows: list[tuple[int, str]] = []
            for sheet in workbook.worksheets:
                header: list[str] = []
                for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                    cells = ["" if cell is None else str(cell) for cell in row]
                    if not any(cell.strip() for cell in cells):
                        continue
                    if row_number == 1:
                        header = cells
                        continue
                    if len(rows) >= MAX_SPREADSHEET_ROWS:
                        break
                    text = self._row_text(header, cells)
                    rows.append((row_number, f"[sheet: {sheet.title}] {text}"))
            return rows
        finally:
            workbook.close()

    def _row_text(self, header: list[str], row: list[str]) -> str:
        cells = [str(c).strip() for c in row]
        if header:
            labels = [h.strip() or f"Column {i + 1}" for i, h in enumerate(header)]
        else:
            labels = [f"Column {i + 1}" for i in range(len(cells))]
        pairs = [
            f"{labels[i] if i < len(labels) else f'Column {i + 1}'}: {value}"
            for i, value in enumerate(cells)
            if value
        ]
        return ", ".join(pairs)

    def _presentation_slides(self, path: Path) -> Iterable[tuple[int, str]]:
        """Real slide text - every text frame on a slide, joined - never
        a rendering of the slide and never OCR on its images. A slide
        with only pictures and no text honestly yields nothing, the
        identical "no guess" rule ``_pdf_pages`` already follows for a
        scanned page.

        ``python-pptx`` is imported here, lazily, so an installation that
        never puts a presentation in its documents folder never imports
        it.
        """
        try:
            if path.stat().st_size > MAX_DOCUMENT_BYTES:
                logger.warning(
                    "skipping '%s': larger than %d bytes", path.name, MAX_DOCUMENT_BYTES
                )
                return ()
            from pptx import Presentation

            presentation = Presentation(str(path))
            slides = []
            for number, slide in enumerate(presentation.slides, start=1):
                if number > MAX_PRESENTATION_SLIDES:
                    break
                frames = [
                    shape.text_frame.text.strip()
                    for shape in slide.shapes
                    if shape.has_text_frame and shape.text_frame.text.strip()
                ]
                text = " ".join(" ".join(frame.split()) for frame in frames)
                if text:
                    slides.append((number, text))
            return slides
        except ImportError:
            logger.warning(
                "skipping '%s': the 'python-pptx' package is not "
                "installed (see requirements.txt)", path.name,
            )
            return ()
        except Exception as exc:  # noqa: BLE001 - a malformed presentation
            # must not make every other document unreachable.
            logger.warning("could not read '%s': %s", path.name, exc)
            return ()

    def _word_paragraphs(self, path: Path) -> Iterable[tuple[int, str]]:
        """Real paragraph text from a Word document's own body - table cells
        included, each its own paragraph, exactly as the document stores them.
        Never a rendering and never OCR on embedded images.

        A .docx is a zip of XML; the body lives in ``word/document.xml``. Read
        with ``zipfile`` and ``xml.etree`` so an installation needs nothing
        new to read one.
        """
        import zipfile
        from xml.etree import ElementTree

        try:
            if path.stat().st_size > MAX_DOCUMENT_BYTES:
                logger.warning(
                    "skipping '%s': larger than %d bytes", path.name, MAX_DOCUMENT_BYTES
                )
                return ()
            with zipfile.ZipFile(path) as package:
                body = package.read("word/document.xml")
            root = ElementTree.fromstring(body)
        except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            # A malformed or password-protected document must not make every
            # other document unreachable.
            logger.warning("could not read '%s': %s", path.name, exc)
            return ()

        paragraphs = []
        for element in root.iter(f"{_WORD_NAMESPACE}p"):
            pieces = []
            for node in element.iter():
                if node.tag == f"{_WORD_NAMESPACE}t" and node.text:
                    pieces.append(node.text)
                elif node.tag in (f"{_WORD_NAMESPACE}tab", f"{_WORD_NAMESPACE}br"):
                    pieces.append(" ")
            text = " ".join("".join(pieces).split())
            if text:
                paragraphs.append((len(paragraphs) + 1, text))
        return paragraphs

    def _locator(self, path: Path, index: int) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return f"page {index}"
        if suffix in _SPREADSHEET_SUFFIXES:
            return f"row {index}"
        if suffix in _PRESENTATION_SUFFIXES:
            return f"slide {index}"
        return f"paragraph {index}"

    def _truncate(self, paragraph: str) -> str:
        """Bounded, and visibly bounded - a passage cut short says so, so
        nothing downstream can mistake a fragment for a whole thought."""
        if len(paragraph) <= self._max_excerpt_chars:
            return paragraph
        return paragraph[: self._max_excerpt_chars].rstrip() + " ..."
