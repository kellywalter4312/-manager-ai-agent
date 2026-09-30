"""An ``ArtifactRuntimeProvider`` backed by the real browser this machine
already has - genuine Chromium rendering and genuine JavaScript execution.

Built entirely on the already-verified ``CDPBrowser``
(``src/providers/browser/cdp_browser.py``) rather than a second driver, the
identical reuse ``WebsiteConnection`` already makes of it. Nothing about CDP,
tabs or sockets appears above this module.

**How a run is actually observed.** ``CDPBrowser`` offers exactly
``navigate``/``evaluate``/``close``, and nothing can be injected into a page
*before* it loads - so an error thrown during load would be gone by the time
anything could ask about it. The document is therefore loaded inside a small
generated harness page that installs its collectors first and only then brings
the artifact in, so what the artifact does on load is caught rather than
inferred. The harness is generated here, per run, and is never part of the
artifact: what is saved in the store stays exactly what was created.

**What it reports.** Whether the page loaded, its title, every runtime error
the run produced, and a count of what the page actually contains - elements,
interactive controls, canvases, scripts. Those counts are what let a caller
tell a real interactive artifact from a blank page that merely parsed: a
simulation with no controls and no canvas did not fail to load, it failed to be
a simulation, and only looking at the running page can say so.

**It never judges correctness.** "Ran cleanly, one canvas, three controls" is
an observation, not a verdict that the optics are right. Deciding whether the
observation satisfies the goal belongs above this module, with whoever knows
what was asked for.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from src.providers.artifact_runtime import ArtifactRuntimeProvider, RuntimeObservation
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Long enough for a page's own load handlers and first frame to run, short
# enough that a person waiting on an answer is not left wondering. The same
# "settle, then look" convention CDPBrowser.navigate already uses.
DEFAULT_SETTLE_SECONDS = 1.2

# How many errors are worth reporting. A page in a failing animation loop can
# produce thousands of identical ones; the first few are the finding.
MAX_REPORTED_ERRORS = 8

_HTML_DOCUMENT = re.compile(r"<\s*(?:!doctype|html|body|div|canvas|svg|script|style)\b", re.IGNORECASE)

# Where a collector can be inserted so that it runs before anything the
# artifact itself does. An opening <head> is the first choice; a <html> or
# <body> opening tag is the fallback; a fragment with neither gets it prepended.
# Tried in this order rather than as one alternation: a single pattern matches
# leftmost-first, which would put the collector after <html> in a document that
# has a <head> further along - correct, but not what this says it does.
_INSERTION_POINTS = (
    re.compile(r"<\s*head\b[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*html\b[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*body\b[^>]*>", re.IGNORECASE),
)

# Installed as the artifact's own first script, which is the only way a load
# time error can be caught: a handler attached from outside the page, after its
# load event, has already missed everything its own scripts did. This is
# generated per run and inserted into a copy - what is saved in the store keeps
# exactly what was created, and nothing else about the document is altered.
_COLLECTOR = (
    "<script>window.__errors=[];"
    "window.addEventListener('error',function(e){try{window.__errors.push("
    "String((e&&(e.message||e.type))||'error'));}catch(_){}} ,true);"
    "window.addEventListener('unhandledrejection',function(e){try{window.__errors.push("
    "'unhandled rejection: '+((e&&e.reason)||'unknown'));}catch(_){}} );"
    "</script>"
)


def instrumented(page: str) -> str:
    """``page`` with the error collector as its first script."""
    for pattern in _INSERTION_POINTS:
        match = pattern.search(page)
        if match is not None:
            return page[: match.end()] + _COLLECTOR + page[match.end():]
    return _COLLECTOR + page


def looks_like_html(document: str) -> bool:
    """Whether this text is already a page rather than a bare script.

    Shape only - a tag this early in the text is what makes it a document. A
    script is wrapped in a minimal page below so that both kinds of artifact
    can be run through one mechanism instead of two.
    """
    return bool(_HTML_DOCUMENT.search(str(document or "")[:2000]))


# The harness. Its collectors are installed before the artifact exists on the
# page, which is the whole reason it is generated at all (see the module
# docstring). The artifact goes into an iframe via srcdoc, so its own document
# is loaded as a document - scripts, styles and markup exactly as written -
# while its errors surface here.
_HARNESS = """<!doctype html>
<html><head><meta charset="utf-8"><title>artifact runtime</title></head>
<body style="margin:0">
<script>window.__errors = [];
window.addEventListener('error', function (e) {
  try { window.__errors.push(String((e && (e.message || e.type)) || 'error')); } catch (_) {}
}, true);
</script>
<iframe id="__stage" style="border:0;width:100vw;height:100vh" srcdoc="__SRCDOC__"></iframe>
</body></html>
"""

# Read back in one evaluate: nothing here is specific to any kind of artifact.
_REPORT = """(function () {
  var out = {errors: (window.__errors || []).slice(0), title: '', observations: {}};
  var stage = document.getElementById('__stage');
  var d = null;
  try { d = stage && stage.contentDocument; } catch (e) { out.errors.push('stage unreadable: ' + e); }
  try {
    var own = stage && stage.contentWindow && stage.contentWindow.__errors;
    if (own && own.length) { out.errors = out.errors.concat([].slice.call(own)); }
  } catch (e) { out.errors.push('the artifact\\'s own errors were unreadable: ' + e); }
  if (!d) { out.observations.loaded = false; return JSON.stringify(out); }
  out.title = d.title || '';
  out.observations.loaded = true;
  out.observations.elements = d.querySelectorAll('*').length;
  out.observations.controls = d.querySelectorAll(
    'input,button,select,textarea,[role="slider"],[contenteditable="true"]').length;
  out.observations.canvases = d.querySelectorAll('canvas').length;
  out.observations.svg = d.querySelectorAll('svg').length;
  out.observations.scripts = d.querySelectorAll('script').length;
  out.observations.text_length = ((d.body && d.body.innerText) || '').trim().length;
  return JSON.stringify(out);
})()"""


class BrowserArtifactRuntime(ArtifactRuntimeProvider):
    """Runs a document in the real installed browser and observes it."""

    def __init__(self, *, browser: object | None = None, settle_seconds: float = DEFAULT_SETTLE_SECONDS) -> None:
        # Injectable for the same reason WebsiteConnection's is: a test proves
        # this module's own observation logic without launching anything, and
        # production passes nothing and gets the real driver.
        self._browser = browser
        self._settle_seconds = float(settle_seconds)

    @property
    def name(self) -> str:
        return "browser"

    @property
    def describes(self) -> str:
        return "web pages and scripts, in the real installed browser"

    def _driver(self):
        if self._browser is not None:
            return self._browser, False
        from src.providers.browser.cdp_browser import CDPBrowser

        return CDPBrowser(), True

    def run(self, *, document: str, label: str = "") -> RuntimeObservation:
        text = str(document or "")
        if not text.strip():
            return RuntimeObservation(ran=False, detail="there is nothing to run - the artifact is empty")

        from src.providers.browser.cdp_browser import BrowserUnavailable

        page = instrumented(
            text if looks_like_html(text) else (
                '<!doctype html><html><head><meta charset="utf-8"></head><body>'
                f"<script>{text}</script></body></html>"
            )
        )
        # srcdoc carries the whole document as one attribute value, so only the
        # quote that delimits it has to be escaped.
        harness = _HARNESS.replace("__SRCDOC__", page.replace("&", "&amp;").replace('"', "&quot;"))

        directory = Path(tempfile.mkdtemp(prefix="alfredrun"))
        try:
            host = directory / "runtime.html"
            host.write_text(harness, encoding="utf-8")
            browser, owned = self._driver()
            try:
                receipt = browser.navigate(host.as_uri(), settle_seconds=self._settle_seconds)
                raw = browser.evaluate(_REPORT)
            except BrowserUnavailable as exc:
                return RuntimeObservation(
                    ran=False,
                    detail=f"no browser runtime is available on this machine: {exc}",
                )
            finally:
                if owned:
                    try:
                        browser.close()
                    except Exception:  # pragma: no cover - best-effort teardown
                        logger.debug("closing the runtime browser failed", exc_info=True)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

        try:
            report = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            return RuntimeObservation(
                ran=False, detail=f"the runtime returned something unreadable: {str(raw)[:200]}",
            )

        observations = dict(report.get("observations") or {})
        loaded = bool(observations.get("loaded"))
        errors = tuple(str(e) for e in (report.get("errors") or ()))[:MAX_REPORTED_ERRORS]
        title = str(report.get("title") or "") or str(getattr(receipt, "title", "") or "")
        if not loaded:
            return RuntimeObservation(
                ran=False, title=title, errors=errors, observations=observations,
                detail="the browser opened but the artifact itself never loaded",
            )
        return RuntimeObservation(
            ran=True, title=title, errors=errors, observations=observations,
            detail=f"ran in {self.name}" + (f" as '{label}'" if label else ""),
        )
