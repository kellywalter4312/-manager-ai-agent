"""CDPBrowser.close() process-cleanup fix.

The genuine defect this checkpoint closes: a single, unverified
``Stop-Process`` sweep in ``_kill_by_profile_dir`` could return before
every real process a launched browser spawned (the main process, plus
its GPU/renderer/crashpad-handler helpers) had actually left the process
table - two full smoke-test runs each left double-digit orphaned headless
Edge processes behind even though ``close()`` ran in a ``finally`` block
and the script exited 0. Fixed by polling and re-issuing the kill until a
round genuinely finds nothing left, or honestly reporting it could not be
verified (``CDPBrowser.cleanup_verified``) - never silently claimed clean.

**Why this test mocks the OS call rather than launching a real browser.**
``tests/conftest.py``'s own ``_no_network`` fixture blocks every outbound
socket connection - including to 127.0.0.1 - for the whole pytest
session, unconditionally, by deliberate project policy (see that
fixture's own docstring: "A test that genuinely wants to prove a request
would be attempted asserts on the loud failure rather than being
exempted"). A real ``CDPBrowser`` launch needs a real localhost HTTP/
WebSocket connection to prove anything, so it cannot run inside this
suite at all - not a gap in this test, a property of the suite. What
*can*, and must, be verified here deterministically is the actual fix:
the retry-until-verified logic in ``_kill_by_profile_dir`` itself,
exercised through a scripted ``subprocess.run`` double that reports
exactly what a real ``Get-CimInstance``/``Stop-Process`` round would.

The genuine, practical, real-process proof - that a real browser session
leaves nothing behind after ``close()`` - is what the project's own
standalone smoke-test script (run directly with ``python``, outside
pytest, exactly like every other "live" verification in this project's
history) exists for, and was run and inspected via real OS process
enumeration as part of this same checkpoint.
"""

from __future__ import annotations

import subprocess

import pytest

from src.providers.browser.cdp_browser import _kill_by_profile_dir


class _ScriptedRun:
    """A drop-in double for ``subprocess.run`` that returns a scripted
    sequence of "how many processes still match" counts, one per call -
    exactly what ``_kill_by_profile_dir``'s own PowerShell script reports
    on its last line, real or not."""

    def __init__(self, counts: "list[int]"):
        self._counts = list(counts)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        count = self._counts[min(self.calls, len(self._counts)) - 1]
        return subprocess.CompletedProcess(args, 0, stdout=f"{count}\n", stderr="")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The retry loop's own poll delay is real, deliberate pacing for
    production use - irrelevant to what this test verifies (the retry
    *logic*), and only ever slow here."""
    monkeypatch.setattr("src.providers.browser.cdp_browser.time.sleep", lambda _seconds: None)


class TestVerifiedRetryUntilGenuinelyClean:
    def test_a_clean_first_round_is_verified_immediately(self, monkeypatch):
        run = _ScriptedRun([0])
        monkeypatch.setattr(subprocess, "run", run)

        assert _kill_by_profile_dir(r"C:\fake\alfred-browser-abc") is True
        assert run.calls == 1

    def test_stragglers_are_re_killed_until_a_round_finds_none(self, monkeypatch):
        """The actual bug this fixes: a single Stop-Process pass is not
        trustworthy - a helper process (GPU/renderer/crashpad) can still
        be alive, or newly spawned, right after the first kill. The fix
        must keep re-querying and re-killing rather than declaring
        victory on the first attempt."""
        run = _ScriptedRun([3, 1, 0])
        monkeypatch.setattr(subprocess, "run", run)

        assert _kill_by_profile_dir(r"C:\fake\alfred-browser-abc") is True
        assert run.calls == 3

    def test_giving_up_after_the_attempt_bound_is_reported_honestly_not_silently(self, monkeypatch):
        """Never claim success it cannot back up: if every round still
        finds a match, the caller must be told, not reassured."""
        run = _ScriptedRun([2, 2, 2, 2, 2, 2, 2, 2])  # more than the default bound
        monkeypatch.setattr(subprocess, "run", run)

        assert _kill_by_profile_dir(r"C:\fake\alfred-browser-abc", attempts=3) is False
        assert run.calls == 3

    def test_a_powershell_failure_is_treated_as_not_yet_verified_and_retried_not_swallowed_as_success(self, monkeypatch):
        calls = {"n": 0}

        def flaky_then_clean(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("powershell not found")
            return subprocess.CompletedProcess(args, 0, stdout="0\n", stderr="")

        monkeypatch.setattr(subprocess, "run", flaky_then_clean)

        assert _kill_by_profile_dir(r"C:\fake\alfred-browser-abc") is True
        assert calls["n"] == 2

    def test_scoped_to_exactly_the_given_profile_dir_never_a_broad_process_name_match(self, monkeypatch):
        """Structural proof against "broad process-name killing": the
        profile directory string is the only thing the PowerShell
        filter is ever built from - captured here and asserted on
        directly, rather than trusted from reading the source."""
        captured: "list[str]" = []

        def capture(args, **kwargs):
            captured.append(args[-1])
            return subprocess.CompletedProcess(args, 0, stdout="0\n", stderr="")

        monkeypatch.setattr(subprocess, "run", capture)

        profile_dir = r"C:\Users\test\AppData\Local\Temp\alfred-browser-xyz123"
        _kill_by_profile_dir(profile_dir)

        assert len(captured) == 1
        script = captured[0]
        assert "alfred-browser-xyz123" in script
        assert "Name=" not in script and "Name -eq" not in script  # never filtered by process name

    def test_an_embedded_quote_in_the_profile_dir_refuses_rather_than_risking_injection(self, monkeypatch):
        run = _ScriptedRun([0])
        monkeypatch.setattr(subprocess, "run", run)

        assert _kill_by_profile_dir("C:\\fake\\alfred-browser-'; rm -rf /") is False
        assert run.calls == 0  # never even attempted - refused outright
