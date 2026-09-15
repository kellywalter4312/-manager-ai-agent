"""PowerShell-backed recognisers must print what they actually recognised.

``powershell.exe`` encodes its own standard output in the console code page -
437 on this machine - not UTF-8. Every recogniser in this project prints
through it: the OCR reader's page marker (U+241E), any accented character in
recognised text, a voice name. All of them arrived as a literal '?'.

The visible cost was a scanned PDF producing **zero pages and raising
nothing**: the marker that separates pages could not survive the encoding, so
``pdf_pages`` found no page boundaries and returned an empty tuple - a
misconfiguration disguised as an ordinary empty result, which is the one shape
this project refuses everywhere else.
"""

from __future__ import annotations

import pytest

from src.providers import windows_speech_provider
from src.providers.media import local_ocr, local_transcription


class TestRecognisersPrintInUTF8:
    """PowerShell encodes stdout in the console code page (437 here), which
    cannot represent the page marker or an accented character."""

    @pytest.mark.parametrize("module", [local_ocr, local_transcription, windows_speech_provider])
    def test_every_powershell_seam_pins_the_output_encoding(self, module, monkeypatch):
        seen = {}

        def fake_run(args, **kwargs):
            seen["script"] = args[-1]
            seen["encoding"] = kwargs.get("encoding")
            raise RuntimeError("stop here - the command line is what is under test")

        monkeypatch.setattr(module.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError):
            module._run_powershell("Write-Output 'x'", timeout=1)
        assert seen["script"].startswith("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8")
        assert seen["encoding"] == "utf-8"

    def test_the_page_marker_is_not_representable_in_the_console_code_page(self):
        """The regression itself, pinned: this is why the prefix is needed."""
        with pytest.raises(UnicodeEncodeError):
            local_ocr._PAGE_MARKER.encode("cp437")


