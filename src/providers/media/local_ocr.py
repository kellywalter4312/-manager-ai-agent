"""Local text recognition in images, with no vendor, key, network or new
dependency - Windows' own ``Windows.Media.Ocr`` engine reading an image file.

**Why this exists.** "Read the text in this photo to me" needs the words that
are actually in the picture. Windows ships an offline OCR engine on every
machine this project's own scripts target (the same platform that already
supplies ``local_transcription.py``'s recogniser and the speech voices), so an
image's printed text becomes real readable units through the one content
pipeline (``src/services/artifacts/content.py``) - read aloud, quoted or
reasoned over by whatever already consumes units. Honestly lower accuracy than a
vendor vision model on handwriting or photographs of curved surfaces; never
fabricated: an image with no recognisable text yields nothing.

**What is and is not verified.** Recognition of printed text rendered into a
real PNG was verified directly against this engine. Photographs taken by a
physical camera have not been, and nothing here claims they were.

**Results are cached per file state** (path, size, modification time), so
describing or listing a shared image does not run recognition again.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

IMAGE_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp")
MAX_LINES = 2000
_TIMEOUT_SECONDS = 120

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  function Await($op, [Type]$type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
  $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync("__IMAGE__")) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if (-not $engine) { [Console]::Error.WriteLine("no OCR language is installed for this user"); exit 2 }
  $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $n = 0
  foreach ($line in $result.Lines) {
    if ($n -ge __MAX__) { break }
    [Console]::Out.WriteLine($line.Text)
    $n++
  }
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}
"""


# One page per output block, separated by a marker line no recognised text can
# produce, so a page with no text still counts as a page.
_PAGE_MARKER = "\u241e-page-\u241e"
MAX_PDF_PAGES = 200

_PDF_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $asTaskOp = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  $asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
  function Await($op, [Type]$type) { $t = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
  function AwaitAction($op) { $t = $asTaskAction.Invoke($null, @($op)); $t.Wait(-1) | Out-Null }
  $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
  $null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
  $null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if (-not $engine) { [Console]::Error.WriteLine("no OCR language is installed for this user"); exit 2 }
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync("__PDF__")) ([Windows.Storage.StorageFile])
  $document = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
  $count = [Math]::Min([int]$document.PageCount, __MAXPAGES__)
  for ($i = 0; $i -lt $count; $i++) {
    $page = $document.GetPage($i)
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $options.DestinationWidth = [uint32]([Math]::Max(1600, $page.Size.Width * 2))
    AwaitAction ($page.RenderToStreamAsync($stream, $options))
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    [Console]::Out.WriteLine("__MARKER__")
    foreach ($line in $result.Lines) { [Console]::Out.WriteLine($line.Text) }
    $page.Dispose()
  }
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}
"""


# Every script this module runs prints recognised text, so every one of them
# needs PowerShell's own output encoding to be UTF-8 rather than the console
# code page (see _run_powershell).
_UTF8_PREFIX = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"


class OCRError(RuntimeError):
    """Text recognition could not run on this image."""


def _run_powershell(script: str, *, timeout: float) -> subprocess.CompletedProcess:
    """The one seam tests replace - never the real subprocess call.

    ``powershell.exe`` encodes its own standard output in the console's code
    page, not UTF-8. On this machine that is code page 437, which cannot
    represent ``_PAGE_MARKER`` (U+241E) or an accented character a recogniser
    genuinely read - both arrive as literal '?', before Python ever decodes
    them. A scanned PDF then produced no page boundaries at all and
    ``pdf_pages`` returned no pages *without raising*, which is exactly the
    "a misconfiguration disguised as normal operation" shape this project
    refuses. Setting the output encoding inside the one seam fixes every
    caller at once, and costs nothing when the code page already is UTF-8.
    """
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _UTF8_PREFIX + script],
        capture_output=True, text=True, timeout=timeout, check=False, encoding="utf-8", errors="replace",
    )


class WindowsOCRReader:
    """Real, offline recognition of printed text in an image file."""

    name = "windows"

    def __init__(self) -> None:
        self._cache: "dict[tuple[str, int, int], tuple[str, ...]]" = {}

    @staticmethod
    def available() -> bool:
        """Only on Windows - everywhere else this reader honestly does not exist."""
        return sys.platform.startswith("win")

    @staticmethod
    def reads(path: "str | Path") -> bool:
        return Path(path).suffix.lower() in IMAGE_SUFFIXES

    def lines(self, path: "str | Path") -> "tuple[str, ...]":
        """The lines of text recognised in the image at ``path``, top to
        bottom - empty when the image has none. Raises ``OCRError`` when
        recognition cannot run at all."""
        image = Path(path)
        if not self.available():
            raise OCRError("text recognition in images is only available on Windows")
        if not image.is_file():
            raise OCRError(f"no image at '{image}'")
        stat = image.stat()
        key = (str(image.resolve()), stat.st_size, int(stat.st_mtime_ns))
        if key in self._cache:
            return self._cache[key]
        script = _SCRIPT.replace("__IMAGE__", str(image.resolve()).replace('"', '`"')).replace("__MAX__", str(MAX_LINES))
        try:
            result = _run_powershell(script, timeout=_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OCRError(f"text recognition could not run: {exc}") from exc
        if result.returncode != 0:
            raise OCRError(f"text recognition failed: {(result.stderr or 'recognition failed').strip()}")
        found = tuple(" ".join(line.split()) for line in (result.stdout or "").splitlines() if line.strip())
        self._cache[key] = found
        return found


def _pdf_pages(reader: "WindowsOCRReader", path: "str | Path") -> "tuple[str, ...]":
    """The text recognised on each page of a PDF, page by page (a page with no
    text is an empty string). Raises ``OCRError`` when rendering or recognition
    cannot run."""
    document = Path(path)
    if not reader.available():
        raise OCRError("text recognition in scanned documents is only available on Windows")
    if not document.is_file():
        raise OCRError(f"no document at '{document}'")
    stat = document.stat()
    key = ("pdf", str(document.resolve()), stat.st_size, int(stat.st_mtime_ns))
    if key in reader._cache:
        return reader._cache[key]
    script = (
        _PDF_SCRIPT.replace("__PDF__", str(document.resolve()).replace('"', '`"'))
        .replace("__MAXPAGES__", str(MAX_PDF_PAGES)).replace("__MARKER__", _PAGE_MARKER)
    )
    try:
        result = _run_powershell(script, timeout=_TIMEOUT_SECONDS * 5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OCRError(f"scanned-page recognition could not run: {exc}") from exc
    if result.returncode != 0:
        raise OCRError(f"scanned-page recognition failed: {(result.stderr or 'recognition failed').strip()}")
    pages: list[list[str]] = []
    for line in (result.stdout or "").splitlines():
        if line.strip() == _PAGE_MARKER:
            pages.append([])
        elif pages and line.strip():
            pages[-1].append(" ".join(line.split()))
    found = tuple(" ".join(page) for page in pages)
    reader._cache[key] = found
    return found


WindowsOCRReader.pdf_pages = _pdf_pages


# One process-wide reader, so its cache is shared by every caller.
DEFAULT_OCR_READER = WindowsOCRReader()
