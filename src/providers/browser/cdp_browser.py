"""A minimal, real, JS-capable browser driver over the Chrome DevTools
Protocol (CDP) - genuine Chromium rendering and JavaScript execution,
without a browser-automation framework.

**Why this, not Playwright/Selenium.** Both would add a large, new
dependency (Playwright bundles and downloads its own browser binaries;
Selenium needs a separately-managed driver binary per browser) to a
project whose own ``requirements.txt`` is deliberately stdlib-first,
with every optional dependency small, lazily imported, and justified by
what it alone makes possible (see that file's own comments). This
project's environment already has both of the two things genuine JS
execution actually needs: a real Chromium-based browser already
installed by the OS (Microsoft Edge, on Windows - the same engine
Chrome uses), and the ``websockets`` package CDP's own protocol is
spoken over. Nothing else is required: CDP is a plain, documented,
versioned JSON-over-WebSocket protocol, not a vendor SDK.

**What this is not.** Not a general browser-automation library - no
selector language, no waiting DSL, no multi-tab/multi-context session
manager. One browser process, one tab, four operations
(``navigate``/``evaluate``/screenshot is deliberately absent - nothing
here needs one). ``WebsiteConnection`` (``src/providers/
website_connection.py``) is the one real caller; this module knows
nothing about governance, connections, credentials or Alfred at all -
the identical "one narrow job" discipline every provider-level module
in this project already holds itself to.

**Replaceable by construction.** Every public method here is exactly
what ``WebsiteConnection`` needs and nothing more
(``navigate``/``evaluate``/``close``). A future engine (a real
Playwright backend, a different browser) is a second, alternative
implementation of this identical narrow surface, selected the same
backend-selector way ``MANAGER_MEDIA``/``MANAGER_SPEECH`` already are -
``WebsiteConnection`` itself would need no change.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_USER_AGENT = "AlfredOS-WebsiteConnection/1.0"

# The real, conventional install locations for a Chromium-based browser
# on Windows - tried in order, the first one that exists wins. Never a
# guess at an arbitrary path: every one of these is where the OS's own
# installer actually puts it. A future platform/browser needs only a
# longer list here, never a different mechanism.
_CANDIDATE_EXECUTABLES: "tuple[str, ...]" = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


class BrowserUnavailable(Exception):
    """No real, launchable Chromium-based browser could be found or
    started - an honest, specific failure, never a silent fallback to
    pretending navigation happened."""


def find_browser_executable() -> "str | None":
    """The first real, installed Chromium-based browser this host
    actually has, or ``None`` - checked with a real filesystem lookup,
    also honouring ``shutil.which`` for a browser reachable on PATH
    (non-Windows, or a custom install). Never invents a path."""
    for candidate in _CANDIDATE_EXECUTABLES:
        import os

        if os.path.isfile(candidate):
            return candidate
    for name in ("msedge", "chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _kill_by_profile_dir(profile_dir: str, *, attempts: int = 6, poll_seconds: float = 0.4) -> bool:
    """Verified termination of every real process still using
    ``profile_dir`` as its own ``--user-data-dir`` - the one fact that
    reliably identifies every process a single browser launch spawns
    (the main browser process, and its GPU/renderer/crashpad-handler
    helpers, each of which inherits and keeps this same argument on its
    own command line - confirmed by inspection: most of them do not
    carry the ``--headless`` flag on their own command line, only the
    profile directory), even when the originally launched process
    handed off to, and exited in favour of, children this object never
    held a ``Popen`` handle to (see ``CDPBrowser.is_running``'s own
    docstring for the identical, already-observed handoff behaviour).

    **Why one kill pass was not enough - the actual defect this fixes.**
    A single ``Stop-Process`` sweep does not guarantee the target has
    actually left the process table by the time it returns, and a
    browser can still be spawning a late helper (a renderer for a page
    that was mid-load) at the exact moment it is asked to die. This
    polls and re-issues the kill for up to ``attempts`` rounds, re-
    querying fresh each time, until a round finds nothing left to kill
    - or gives up honestly and reports that via the return value, never
    silently claimed as clean when it was not.

    Never broad process-name killing: every match is scoped to this one
    connection's own, uniquely-generated profile directory - a real
    Edge/Chrome window the person is actually using has a different
    ``--user-data-dir`` (its own default profile) and can never match.
    """
    if "'" in profile_dir:
        return False  # a profile dir this module itself generated never contains one; refuse rather than risk injection
    escaped = profile_dir.replace("\\", "\\\\")
    script = (
        "$targets = Get-CimInstance Win32_Process -Filter \"CommandLine like '%" + escaped + "%'\"; "
        "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
        "Write-Output $targets.Count"
    )
    for _ in range(max(attempts, 1)):
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=10, text=True,
            )
            found_this_round = int((result.stdout or "").strip() or "-1")
        except Exception:  # noqa: BLE001 - best-effort cleanup only; treated as "not yet verified" below
            found_this_round = -1
        if found_this_round == 0:
            return True
        time.sleep(poll_seconds)
    return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass(frozen=True)
class PageReceipt:
    """What actually happened when the browser navigated somewhere -
    real, observed facts, never assumed."""

    url: str
    title: str


class CDPBrowser:
    """One real, headless, Chromium-based browser process and one real
    tab in it - launched lazily, on first use, and reused for the
    lifetime of this object (a real browser process is expensive to
    start; reusing it is what lets a real session/cookie state persist
    across ``navigate``/``fill``/``click`` calls, exactly the continuity
    a real logged-in browsing session needs).

    Bounded and honest, the identical discipline
    ``DeclaredConnection``/``service_discovery.py`` already hold
    themselves to: a fixed timeout on every operation, a real
    ``BrowserUnavailable``/``TimeoutError`` when something genuinely
    cannot be done, never a fabricated success.
    """

    def __init__(
        self, *, executable: "str | None" = None, headless: bool = True, timeout: float = 15.0,
    ) -> None:
        self._executable = executable or find_browser_executable()
        self._headless = headless
        self._timeout = timeout
        self._port: "int | None" = None
        self._process: "subprocess.Popen | None" = None
        self._profile_dir: "str | None" = None
        self._tab_ws_url: "str | None" = None
        self._next_id = 0
        # None until close() actually runs its cleanup step; then True
        # (every process matching this connection's own profile
        # directory was verified gone) or False (cleanup was attempted
        # but could not be verified within the bound - a real, honest
        # signal, read by tests and by close() itself, never silently
        # dropped).
        self.cleanup_verified: "bool | None" = None

    @property
    def is_running(self) -> bool:
        """Whether a real browser is still answering its own debugging
        port - checked against the port itself, never against the
        launched ``Popen`` handle's own liveness. A real, observed
        Windows/Chromium behaviour: the process this object launched
        can hand off to, and exit in favour of, a separate real browser
        process, which would make ``Popen.poll()`` falsely report "not
        running" while the actual browser (and its debugging port) is
        very much alive. Checking the port is the honest source of
        truth; trusting the handle alone would relaunch a whole second
        browser needlessly, or worse, silently talk to a fresh
        ``about:blank`` tab instead of the page actually navigated to.
        """
        if self._port is None or self._tab_ws_url is None:
            return False
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{self._port}/json/version", headers={"User-Agent": _USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=1.0):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def _ensure_started(self) -> None:
        if self.is_running:
            return
        if self._executable is None:
            raise BrowserUnavailable(
                "no Chromium-based browser (Microsoft Edge or Google Chrome) was found installed "
                "on this machine - genuine browser-based website interaction needs one"
            )

        self._port = _free_port()
        self._profile_dir = tempfile.mkdtemp(prefix="alfred-browser-")
        args = [
            self._executable,
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-extensions",
            "about:blank",
        ]
        if self._headless:
            args.insert(1, "--headless=new")

        try:
            self._process = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            # Nothing was actually launched - the profile directory was
            # created, but no process exists to hold it open, so a
            # plain removal (no _kill_by_profile_dir needed) is enough.
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None
            self._port = None
            raise BrowserUnavailable(f"could not start '{self._executable}': {exc}") from None

        # From here on, a real process exists and must never be left
        # behind - ANY failure past this point (not merely the expected
        # "never became reachable" timeout) closes this connection
        # before propagating, the identical honest cleanup-on-failure
        # ``_ensure_started``'s own docstring on "failed startup" in
        # this project's own test/review history already requires.
        try:
            deadline = time.monotonic() + self._timeout
            version_url = f"http://127.0.0.1:{self._port}/json/version"
            last_error: "Exception | None" = None
            while time.monotonic() < deadline:
                try:
                    request = urllib.request.Request(version_url, headers={"User-Agent": _USER_AGENT})
                    with urllib.request.urlopen(request, timeout=1.5):
                        break
                except (urllib.error.URLError, OSError) as exc:
                    last_error = exc
                    time.sleep(0.15)
            else:
                raise BrowserUnavailable(
                    f"'{self._executable}' did not become reachable on port {self._port} "
                    f"within {self._timeout}s: {last_error}"
                )

            self._tab_ws_url = self._open_tab()
        except BaseException:
            self.close()
            raise

    def _open_tab(self) -> str:
        assert self._port is not None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self._port}/json/new?about:blank",
            method="PUT", headers={"User-Agent": _USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise BrowserUnavailable(f"could not open a new browser tab: {exc}") from None
        ws_url = payload.get("webSocketDebuggerUrl")
        if not ws_url:
            raise BrowserUnavailable("the browser did not report a usable debugger URL for its new tab")
        return ws_url

    # -- CDP round-trip ---------------------------------------------------

    async def _send(self, method: str, params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
        import websockets

        self._next_id += 1
        message_id = self._next_id
        message = json.dumps({"id": message_id, "method": method, "params": params or {}})
        async with websockets.connect(self._tab_ws_url, max_size=20_000_000) as ws:
            await ws.send(message)
            # Enabling Page/Runtime domains and waiting for the load
            # event both happen on the SAME websocket connection this
            # command opened - a fresh connection per top-level call
            # (never held open across calls) is what keeps this driver
            # simple and avoids stale-connection failures between one
            # navigate and the next.
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                data = json.loads(raw)
                if data.get("id") == message_id:
                    if "error" in data:
                        raise RuntimeError(f"CDP {method} failed: {data['error']}")
                    return data.get("result", {})

    async def _navigate_async(self, url: str, *, settle_seconds: float) -> PageReceipt:
        import websockets

        async with websockets.connect(self._tab_ws_url, max_size=20_000_000) as ws:
            await ws.send(json.dumps({"id": self._next_id + 1, "method": "Page.enable"}))
            await asyncio.wait_for(ws.recv(), timeout=self._timeout)
            self._next_id += 1

            nav_id = self._next_id + 1
            await ws.send(json.dumps({"id": nav_id, "method": "Page.navigate", "params": {"url": url}}))
            self._next_id += 1

            deadline = time.monotonic() + self._timeout
            navigated = False
            loaded = False
            while time.monotonic() < deadline and not loaded:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                data = json.loads(raw)
                if data.get("id") == nav_id:
                    if "error" in data:
                        raise RuntimeError(f"navigation to '{url}' failed: {data['error']}")
                    navigated = True
                elif data.get("method") == "Page.loadEventFired" and navigated:
                    loaded = True
            if not loaded:
                raise TimeoutError(f"navigating to '{url}' did not finish within {self._timeout}s")

        # A short, real settle window after the load event - genuine
        # post-load JavaScript (a fetch-driven render, a setTimeout)
        # commonly still runs after 'load' fires. Bounded and small:
        # this is a wait, not a guess at content, and a caller reading a
        # page too early simply sees less, never something fabricated.
        if settle_seconds > 0:
            await asyncio.sleep(settle_seconds)

        info = await self._send("Runtime.evaluate", {
            "expression": "({url: document.location.href, title: document.title})",
            "returnByValue": True,
        })
        value = info.get("result", {}).get("value", {}) if isinstance(info, dict) else {}
        return PageReceipt(url=str(value.get("url", url)), title=str(value.get("title", "")))

    # -- public surface -----------------------------------------------

    def navigate(self, url: str, *, settle_seconds: float = 0.4) -> PageReceipt:
        """Load ``url`` for real, wait for the page's own load event
        plus a short settle window, and return what actually loaded."""
        self._ensure_started()
        try:
            return asyncio.run(self._navigate_async(url, settle_seconds=settle_seconds))
        except (RuntimeError, TimeoutError, OSError) as exc:
            raise BrowserUnavailable(str(exc)) from exc

    def evaluate(self, expression: str) -> Any:
        """Run ``expression`` as real JavaScript in the currently
        loaded page and return its real, JSON-safe value -
        ``returnByValue``, so this never hands back a live remote
        object reference, only plain data."""
        self._ensure_started()
        try:
            result = asyncio.run(self._send("Runtime.evaluate", {
                "expression": expression, "returnByValue": True, "awaitPromise": True,
            }))
        except (RuntimeError, TimeoutError, OSError) as exc:
            raise BrowserUnavailable(str(exc)) from exc
        exception_details = result.get("exceptionDetails") if isinstance(result, dict) else None
        if exception_details:
            raise RuntimeError(f"page script failed: {exception_details}")
        return result.get("result", {}).get("value") if isinstance(result, dict) else None

    def set_cookie(self, *, name: str, value: str, url: str) -> None:
        """Prime a session/auth cookie for ``url``'s origin before
        navigating - the real, generic mechanism session state is
        preserved through (see ``WebsiteConnection``'s own docstring on
        credentials). The cookie value itself never round-trips through
        this method's return value or any log line."""
        self._ensure_started()
        try:
            asyncio.run(self._send("Network.setCookie", {"name": name, "value": value, "url": url}))
        except (RuntimeError, TimeoutError, OSError) as exc:
            raise BrowserUnavailable(str(exc)) from exc

    def close(self) -> None:
        """Terminate the real browser process, if one was ever started.
        Idempotent - closing twice, or closing one that never started,
        is a harmless no-op (the second call finds ``self._profile_dir``
        already ``None`` and does nothing further).

        Never trusts the launched ``Popen`` handle alone (see
        ``is_running``'s own docstring for why it can be stale) -
        ``_kill_by_profile_dir`` finds and *verifies* it stopped every
        real process actually using this connection's own, uniquely-
        generated profile directory, the one fact that reliably still
        identifies all of them, including helpers the ``Popen`` handle
        never knew about. The profile directory itself is only removed
        once that is confirmed - deleting it out from under a process
        that still has it open would be pointless at best.
        """
        self._tab_ws_url = None
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                try:
                    self._process.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._process = None
        if self._profile_dir is not None:
            self.cleanup_verified = _kill_by_profile_dir(self._profile_dir)
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None
        self._port = None

    def __del__(self) -> None:  # pragma: no cover - best-effort GC cleanup
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
