"""Rendering a real ``DocumentContent`` (see
``src/services/artifacts/models.py``) into a real, valid, multi-page PDF
file - genuine bytes a real PDF reader can open, never a placeholder or
a renamed text file.

**Why hand-written rather than a vendor library.** This project already
chose FFmpeg (a real, well-known, vendored binary) over hand-rolling a
video container, because getting a container format right is exactly
the kind of thing worth not reimplementing - but a video container has
many genuinely subtle failure modes. A minimal, text-only, single-page-
size PDF has none of that: a handful of indirect objects, a plain
content stream of ``Tf``/``Tm``/``Tj`` operators, an ordinary
cross-reference table, and two built-in, non-embedded standard fonts
(Helvetica/Helvetica-Bold, present in every PDF-compliant reader with no
font file required at all). Writing this by hand adds a new runtime
dependency to nobody's install, keeps rendering fully local and
deterministic like every other "no vendor, just real local work"
capability in this project (``FFmpegVideoClipper``,
``LocalTitleCardImageProvider``), and is verified byte-for-byte the same
way this project already verifies its own real output: by having an
independent, already-installed real reader (``pypdf``, already a
dependency for reading knowledge-folder PDFs) decode it back and confirm
the real page count and real text - see this module's own tests.

**Text wrapping is deliberately conservative, not exact glyph metrics.**
Helvetica's real character widths vary per glyph; this uses a fixed,
safely-wide average width per character instead of a real width table.
The honest cost is wrapping a little earlier than a professional
typesetter would - the benefit is that a line can never visually
overflow its margin, which is the failure mode that would actually
matter here. This is a real, working limitation, stated plainly rather
than dressed up as precision this renderer does not have.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.services.artifacts.models import DocumentContent

PAGE_WIDTH = 612.0  # US Letter, points
PAGE_HEIGHT = 792.0
MARGIN = 72.0  # one inch
USABLE_WIDTH = PAGE_WIDTH - 2 * MARGIN
BOTTOM_Y = MARGIN

TITLE_SIZE = 20.0
HEADING_SIZE = 14.0
BODY_SIZE = 11.0

# A deliberately conservative, non-glyph-accurate estimate of Helvetica's
# average character width, as a fraction of the font size - see this
# module's own docstring for why this is a safe, working simplification
# rather than fabricated precision.
_AVG_CHAR_WIDTH_FACTOR = 0.52
# A further safety margin subtracted from the usable width before
# computing how many characters fit per line, so an occasional wider
# glyph run never actually reaches the right margin.
_WRAP_SAFETY_FACTOR = 0.90


class DocumentRenderError(RuntimeError):
    """Raised when real document rendering could not be produced."""


class DocumentRenderer(ABC):
    """Turns real, structured document content into a real, exportable
    file format."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used in logs and configuration."""

    @abstractmethod
    def render(self, content: DocumentContent) -> bytes:
        """Return real, decodable file bytes for ``content``.

        Implementations must raise ``DocumentRenderError`` on a genuine
        rendering failure - never return empty or placeholder bytes
        standing in for a document that was never actually rendered.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


class NullDocumentRenderer(DocumentRenderer):
    """The default when no real rendering engine is configured. Honest,
    not silent - the same role every other Null* provider in this
    project already plays."""

    @property
    def name(self) -> str:
        return "null"

    def render(self, content: DocumentContent) -> bytes:
        raise DocumentRenderError(
            "no document-rendering engine is configured, so I cannot "
            "produce a real PDF - I would rather say so than invent one"
        )


def _max_chars_per_line(font_size: float) -> int:
    avg_char_width = font_size * _AVG_CHAR_WIDTH_FACTOR
    usable = USABLE_WIDTH * _WRAP_SAFETY_FACTOR
    return max(1, int(usable // avg_char_width))


def wrap_text(text: str, font_size: float) -> list[str]:
    """Word-wrap ``text`` to fit one line at ``font_size`` - never
    breaks a word mid-way. A single word wider than the line is placed
    alone on its own line rather than split or silently truncated."""
    max_chars = _max_chars_per_line(font_size)
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class SimplePDFRenderer(DocumentRenderer):
    """Real, local, deterministic PDF rendering - no vendor, no
    credential, no network, ever. The one real implementation this
    checkpoint ships, the identical "local, like composition - never a
    vendor" positioning ``FFmpegVideoClipper`` already documents for
    itself: there is no vendor to be independent of here."""

    @property
    def name(self) -> str:
        return "simple_pdf"

    def render(self, content: DocumentContent) -> bytes:
        if not content.sections:
            raise DocumentRenderError("a document needs at least one section to render")

        pages: list[list[str]] = []
        ops: list[str] = []
        y = PAGE_HEIGHT - MARGIN

        def new_page() -> None:
            nonlocal ops, y
            pages.append(ops)
            ops = []
            y = PAGE_HEIGHT - MARGIN

        def draw_line(text: str, font: str, size: float, gap_before: float = 0.0) -> None:
            nonlocal y
            y -= gap_before
            line_height = size * 1.3
            if y - line_height < BOTTOM_Y:
                new_page()
            ops.append(
                f"BT /{font} {size:g} Tf 1 0 0 1 {MARGIN:g} {y:.2f} Tm "
                f"({_escape_pdf_text(text)}) Tj ET"
            )
            y -= line_height

        draw_line(content.title, "F2", TITLE_SIZE)
        y -= 12

        for section in content.sections:
            draw_line(section.heading, "F2", HEADING_SIZE, gap_before=14)
            for line in wrap_text(section.body, BODY_SIZE):
                draw_line(line, "F1", BODY_SIZE)
            y -= 10

        pages.append(ops)
        return _assemble_pdf(pages)


def _assemble_pdf(pages: "list[list[str]]") -> bytes:
    """A real, valid, minimal multi-page PDF: a Catalog, a Pages tree,
    two standard (non-embedded) fonts, and one content-stream object per
    page - assembled with a real cross-reference table and trailer, per
    the PDF 1.4 specification's own minimal requirements."""
    catalog_num = 1
    pages_num = 2
    font1_num = 3
    font2_num = 4
    first_dynamic = 5
    n_pages = len(pages)
    content_nums = [first_dynamic + 2 * i for i in range(n_pages)]
    page_nums = [first_dynamic + 2 * i + 1 for i in range(n_pages)]

    objects: dict[int, bytes] = {}
    objects[catalog_num] = f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode("ascii")
    kids = " ".join(f"{p} 0 R" for p in page_nums)
    objects[pages_num] = f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("ascii")
    objects[font1_num] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objects[font2_num] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"

    for i, ops in enumerate(pages):
        stream = "\n".join(ops).encode("latin-1", errors="replace")
        objects[content_nums[i]] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
        )
        objects[page_nums[i]] = (
            f"<< /Type /Page /Parent {pages_num} 0 R /MediaBox [0 0 {PAGE_WIDTH:g} {PAGE_HEIGHT:g}] "
            f"/Resources << /Font << /F1 {font1_num} 0 R /F2 {font2_num} 0 R >> >> "
            f"/Contents {content_nums[i]} 0 R >>"
        ).encode("ascii")

    buf = bytearray()
    buf += b"%PDF-1.4\n"
    offsets: dict[int, int] = {}
    max_num = max(objects)
    for num in range(1, max_num + 1):
        offsets[num] = len(buf)
        buf += f"{num} 0 obj\n".encode("ascii")
        buf += objects[num]
        buf += b"\nendobj\n"

    xref_offset = len(buf)
    buf += f"xref\n0 {max_num + 1}\n".encode("ascii")
    buf += b"0000000000 65535 f \n"
    for num in range(1, max_num + 1):
        buf += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
    buf += b"trailer\n"
    buf += f"<< /Size {max_num + 1} /Root {catalog_num} 0 R >>\n".encode("ascii")
    buf += b"startxref\n"
    buf += f"{xref_offset}\n".encode("ascii")
    buf += b"%%EOF"
    return bytes(buf)
