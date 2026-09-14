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


class OCRError(RuntimeError):
    """Text recognition could not run on this image."""


def _run_powershell(script: str, *, timeout: float) -> subprocess.CompletedProcess:
    """The one seam tests replace - never the real subprocess call."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
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


# One process-wide reader, so its cache is shared by every caller.
DEFAULT_OCR_READER = WindowsOCRReader()
