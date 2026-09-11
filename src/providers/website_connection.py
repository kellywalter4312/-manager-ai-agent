"""One real ``Connection`` implementation for any declared website -
the literal-website-interaction counterpart to ``declared_connection.py``,
built entirely on the already-verified ``CDPBrowser``
(``src/providers/browser/cdp_browser.py``) rather than a second,
parallel connection or governance mechanism.

**A fixed, universal, four-operation vocabulary - never a per-site
catalogue.** Unlike ``DeclaredConnection``, whose operations are
declared once per REST service at ``declare`` time, a website's real
fields and controls are not known ahead of time and are not worth
declaring - they exist only on the page, discovered live, every visit.
So every ``WebsiteConnection``, for every site, offers the identical
four operations:

    navigate      loads a page at a given path within this site's own
                  origin - safe, read-like.
    read_page     inspects the CURRENTLY LOADED page's real, rendered
                  DOM (after any of its own JavaScript has run - see
                  ``CDPBrowser.navigate``'s own settle window) and
                  reports both its real, usable fields/controls/links
                  (each given a stable ``data-alfred-locator`` the
                  very first time it is seen) and a short, bounded
                  excerpt of its real visible text - so a plain result/
                  confirmation page with no controls at all still has
                  something genuine to read back - safe, read-like.
    fill_field    enters a real value into a field a prior read_page
                  discovered, by that same locator - a LOCAL DOM
                  change only, no network request of its own, so this
                  is safe/non-consequential by the identical
                  "local-state-only" reasoning a GET already is.
    click         activates a button or link discovered by read_page -
                  the one operation that can have a genuine external
                  effect (a real form submission, a real state change
                  on the real site), so this is the one, and only,
                  consequential operation, unconditionally - the
                  identical "one clean binary rule, not a guess"
                  ``DeclaredConnection``'s own GET-vs-everything-else
                  convention already uses, re-expressed for a browser
                  where there is no HTTP method to read it from.

Every existing governance boundary already built for this - operation
selection (``_select_operation``), operation-level authorization
(``authorization_for``), the consequential-confirmation gate
(``requires_confirmation_for`` -> ``PendingConfirmation`` ->
``_try_resolve_confirmation``), real parameter data-flow
(``parameters_for`` -> ``_declared_argument_values``), the governed
``ToolSet``/``ConnectionActionTool`` execution boundary, and Control
Center's own connection-status reporting - all already duck-type off
exactly this shape (see ``src/manager/capabilities.py::connected_action``'s
own module comment). None of it needed to change, or even know, that a
connection could now be a real browser instead of a REST client.

**Locators, never a scripting language.** A ``locator`` is always
something the page itself produced (its own ``name``/``id``
attribute, generated once and reused, exactly as ``read_page`` leaves
it), never a CSS selector or XPath the model writes - the model can
only ever act on something it was genuinely shown, never invent a
location on a page it has not actually seen.

**Credentials, never exposed to the reasoning layer.** Exactly the
same discipline ``DeclaredConnection.execute`` already holds itself to
for its own ``Authorization`` header: a site's credential
(``ProviderAccount.credential``) is read only inside this class's own
``_ensure_session_primed``, handed straight to
``CDPBrowser.set_cookie`` (which itself never returns or logs the
value - see that method's own docstring), and never appears in a
``ConnectionResult.detail``, a ``TaskResult.output``, or a Brain-
prompt note.
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.providers.availability import Availability, AvailabilityState
from src.providers.browser.cdp_browser import BrowserUnavailable, CDPBrowser
from src.providers.connection import (
    AuthorizationState,
    Connection,
    ConnectionResult,
    ConnectionStatus,
    utc_now,
)
from src.providers.service_descriptor import ServiceParameter
from src.providers.site_descriptor import SiteDescriptor
from src.services.credentials.account import ProviderAccount

# A response this large has nothing more to tell a capability handler
# and everything to do with a page that misbehaves - the identical
# "still usable, honestly labelled" truncation DeclaredConnection's
# own _MAX_DETAIL_CHARS already applies.
_MAX_DETAIL_CHARS = 2000

# How long to let the page settle after a click that may have
# triggered navigation, before reading back the resulting state - the
# identical short, bounded, honest "a wait, not a guess" discipline
# CDPBrowser.navigate's own settle_seconds already uses. No new CDP
# event-waiting machinery is added for this; a fixed short sleep after
# a real click is the smallest thing that genuinely works.
_CLICK_SETTLE_SECONDS = 0.5

SUPPORTED_OPERATIONS: "tuple[str, ...]" = ("navigate", "read_page", "fill_field", "click")

# The one, and only, consequential operation - see this module's own
# docstring for why "click" alone earns that, unconditionally, for
# every site.
_CONSEQUENTIAL_OPERATIONS = frozenset({"click"})

_OPERATION_PARAMETERS: "dict[str, tuple[ServiceParameter, ...]]" = {
    "navigate": (ServiceParameter(name="path", location="query", required=True),),
    "read_page": (ServiceParameter(name="query", location="query", required=False),),
    "fill_field": (
        ServiceParameter(name="locator", location="query", required=True),
        ServiceParameter(name="value", location="query", required=True),
    ),
    "click": (ServiceParameter(name="locator", location="query", required=True),),
}

_OPERATION_DESCRIPTIONS: "dict[str, str]" = {
    # Deliberately free of every OTHER operation's own name/word-parts
    # (never "page" outside read_page's own, never "click" outside
    # click's own, and so on) - _select_operation
    # (src/manager/capabilities.py) scores an operation's combined
    # name+description vocabulary by literal token overlap against the
    # request's own words, so one description merely mentioning a
    # sibling operation's name would make an unrelated request look
    # equally plausible for both and turn a genuinely unambiguous
    # request into a manufactured tie.
    #
    # Also deliberately free of any possessive apostrophe ("website's"):
    # tokenize()'s own word pattern (``[a-z0-9]+``) does not include
    # apostrophes, so "website's" silently becomes two tokens,
    # "website" and a stray single-letter "s" - which then collides
    # with ANY possessive phrase in a real request ("someone else's
    # account", "my own account's settings"), the identical
    # manufactured-tie failure a shared operation name already causes,
    # found by exactly that scenario during this checkpoint's own
    # governance test.
    "navigate": "loads a different location while staying on this same website",
    "read_page": "inspects what is currently rendered on screen and lists the real, usable fields, controls and links found there",
    "fill_field": "enters a real value into a control that was already found and identified - a local change only, nothing is sent yet",
    "click": "activates a button or link that was already found and identified - a consequential action on the real site",
}

# Real JavaScript, run inside the page via CDPBrowser.evaluate -
# never a selector language exposed to the model (see this module's
# own docstring on locators). String-formatted with json.dumps'd
# arguments only, never raw interpolation of anything model-supplied.
_READ_PAGE_JS = """
(function(query) {
  function locatorFor(el, index) {
    if (!el.hasAttribute('data-alfred-locator')) {
      var base = el.name || el.id || (el.tagName.toLowerCase() + '-' + index);
      el.setAttribute('data-alfred-locator', String(base));
    }
    return el.getAttribute('data-alfred-locator');
  }
  var elements = Array.prototype.slice.call(
    document.querySelectorAll('input, select, textarea, button, a[href]')
  );
  var out = [];
  elements.forEach(function(el, i) {
    var locator = locatorFor(el, i);
    var label = '';
    if (el.labels && el.labels.length) { label = el.labels[0].innerText || ''; }
    if (!label) { label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.innerText || el.value || ''; }
    label = String(label || '').trim();
    var rec = {
      locator: locator, tag: el.tagName.toLowerCase(), type: el.type || '', label: label
    };
    var q = (query || '').toLowerCase();
    if (!q || label.toLowerCase().indexOf(q) !== -1 || locator.toLowerCase().indexOf(q) !== -1) {
      out.push(rec);
    }
  });
  var bodyText = (document.body && document.body.innerText) ? document.body.innerText.trim() : '';
  return JSON.stringify({
    url: document.location.href, title: document.title, elements: out,
    text: bodyText.slice(0, 1000)
  });
})(%s)
"""

_FILL_FIELD_JS = """
(function(locator, value) {
  var el = document.querySelector('[data-alfred-locator="' + CSS.escape(locator) + '"]');
  if (!el) { throw new Error("no element with locator '" + locator + "' was found - call read_page first"); }
  var proto = Object.getPrototypeOf(el);
  var desc = proto ? Object.getOwnPropertyDescriptor(proto, 'value') : null;
  if (desc && desc.set) { desc.set.call(el, value); } else { el.value = value; }
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
})(%s, %s)
"""

_CLICK_JS = """
(function(locator) {
  var el = document.querySelector('[data-alfred-locator="' + CSS.escape(locator) + '"]');
  if (!el) { throw new Error("no element with locator '" + locator + "' was found - call read_page first"); }
  el.click();
  return true;
})(%s)
"""

_PAGE_RECEIPT_JS = (
    "JSON.stringify({url: document.location.href, title: document.title, "
    "text: ((document.body && document.body.innerText) ? document.body.innerText.trim() : '').slice(0, 1000)})"
)


class WebsiteConnection(Connection):
    """A governed connection to one declared website, built from a
    ``SiteDescriptor`` (what the site is) plus, optionally, the
    ``ProviderAccount`` already on file for it - never a second,
    parallel credential mechanism - and a real ``CDPBrowser`` this
    class owns and drives.
    """

    def __init__(
        self,
        descriptor: SiteDescriptor,
        account: "ProviderAccount | None",
        *,
        browser: "CDPBrowser | None" = None,
    ) -> None:
        self._descriptor = descriptor
        self._account = account
        # Injectable so a test can supply a double without this class
        # needing to know anything about that - the identical
        # dependency-injection shape every other provider in this
        # project already follows. None (the default, every real
        # caller) lazily builds one real CDPBrowser, reused for the
        # lifetime of this connection - see that class's own docstring
        # on why reuse is what preserves real session continuity.
        self._browser = browser if browser is not None else CDPBrowser()
        self._availability = Availability(state=AvailabilityState.UNKNOWN)
        self._session_primed = False

    @property
    def name(self) -> str:
        return self._descriptor.name

    @property
    def availability(self) -> Availability:
        return self._availability

    @property
    def authorization(self) -> AuthorizationState:
        if not self._descriptor.requires_auth:
            return AuthorizationState.NOT_REQUIRED
        return AuthorizationState.AUTHORIZED if self._account is not None else AuthorizationState.UNAUTHORIZED

    def authorization_for(self, operation: str) -> AuthorizationState:
        """Every operation on a website shares the identical,
        session-wide authorization state - unlike a REST service,
        where different operations can genuinely need different
        credentials, a browser session is either logged in or it is
        not. Raises ``KeyError`` for an unknown operation, the
        identical "an unknown operation is the caller's mistake"
        discipline ``DeclaredConnection.authorization_for`` already
        holds itself to."""
        if operation not in SUPPORTED_OPERATIONS:
            raise KeyError(f"'{self.name}' declares no operation named '{operation}'")
        return self.authorization

    def requires_confirmation_for(self, operation: str) -> bool:
        if operation not in SUPPORTED_OPERATIONS:
            raise KeyError(f"'{self.name}' declares no operation named '{operation}'")
        return operation in _CONSEQUENTIAL_OPERATIONS

    def parameters_for(self, operation: str) -> "tuple[ServiceParameter, ...]":
        return _OPERATION_PARAMETERS.get(operation, ())

    def operation_description_for(self, operation: str) -> str:
        return _OPERATION_DESCRIPTIONS.get(operation, "")

    @property
    def supported_operations(self) -> "tuple[str, ...]":
        return SUPPORTED_OPERATIONS

    def close(self) -> None:
        """Real, additive cleanup - not part of the ``Connection`` ABC
        (no sibling connection needs one), but genuinely necessary for
        anything, test or production, that owns this connection's own
        lifetime rather than letting it live for the whole process.
        Delegates straight to the already-verified
        ``CDPBrowser.close()`` - never a second cleanup mechanism."""
        self._browser.close()

    def _ensure_session_primed(self) -> None:
        if self._session_primed:
            return
        if self._descriptor.requires_auth and self._account is not None:
            self._browser.set_cookie(
                name=self._descriptor.auth_cookie_name,
                value=self._account.credential,
                url=self._descriptor.base_url,
            )
        self._session_primed = True

    def execute(self, operation: str, **params: Any) -> ConnectionResult:
        op = " ".join(str(operation).split()).lower()
        if op not in SUPPORTED_OPERATIONS:
            return ConnectionResult(
                operation=operation, status=ConnectionStatus.UNSUPPORTED,
                detail=f"'{self.name}' declares no operation named '{operation}'",
                source=self.name, executed_at=utc_now(),
            )

        if self._descriptor.requires_auth and self._account is None:
            return ConnectionResult(
                operation=operation, status=ConnectionStatus.UNAUTHORIZED,
                detail=f"'{self.name}' is not authorized - no credential is on file for it",
                source=self.name, executed_at=utc_now(),
            )

        try:
            self._ensure_session_primed()
            detail = self._run(op, params)
        except BrowserUnavailable as exc:
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=str(exc))
            return ConnectionResult(
                operation=operation, status=ConnectionStatus.FAILED,
                detail=f"could not reach the browser: {exc}", source=self.name, executed_at=utc_now(),
            )
        except _ParameterError as exc:
            return ConnectionResult(
                operation=operation, status=ConnectionStatus.FAILED,
                detail=str(exc), source=self.name, executed_at=utc_now(),
            )
        except RuntimeError as exc:
            # A real page-script failure (fill_field/click's own thrown
            # Error - the named locator does not exist on this page).
            # The browser WAS genuinely reached; the failure is the
            # page's own honest answer, never fabricated.
            self._availability = Availability(state=AvailabilityState.AVAILABLE, reason="a call succeeded")
            return ConnectionResult(
                operation=operation, status=ConnectionStatus.FAILED,
                detail=str(exc), source=self.name, executed_at=utc_now(),
            )

        self._availability = Availability(state=AvailabilityState.AVAILABLE, reason="a call succeeded")
        return ConnectionResult(
            operation=operation, status=ConnectionStatus.SUCCEEDED,
            detail=detail[:_MAX_DETAIL_CHARS], source=self.name, executed_at=utc_now(),
        )

    def _run(self, op: str, params: "dict[str, Any]") -> str:
        if op == "navigate":
            path = str(params.get("path") or "/")
            if "://" in path or any(ord(c) < 0x20 for c in path):
                raise _ParameterError(f"'navigate' on '{self.name}' needs a plain relative path, got {path!r}")
            if not path.startswith("/"):
                path = "/" + path
            receipt = self._browser.navigate(self._descriptor.base_url + path)
            return json.dumps({"url": receipt.url, "title": receipt.title})

        if op == "read_page":
            query = str(params.get("query") or "")
            return str(self._browser.evaluate(_READ_PAGE_JS % json.dumps(query)))

        if op == "fill_field":
            locator = params.get("locator")
            value = params.get("value")
            if not locator or value is None:
                raise _ParameterError(f"'fill_field' on '{self.name}' needs both 'locator' and 'value'")
            self._browser.evaluate(_FILL_FIELD_JS % (json.dumps(str(locator)), json.dumps(str(value))))
            return f"filled '{locator}'"

        if op == "click":
            locator = params.get("locator")
            if not locator:
                raise _ParameterError(f"'click' on '{self.name}' needs 'locator'")
            try:
                self._browser.evaluate(_CLICK_JS % json.dumps(str(locator)))
            except BrowserUnavailable:
                # A real, known CDP behaviour, not a failure: when the
                # click's own JavaScript causes the page to navigate
                # away (a real form submission, exactly what a
                # consequential click is expected to do), the browser
                # can destroy the execution context the click was
                # running in before ever sending a reply - the identical
                # navigation this connection's own click is SUPPOSED to
                # cause, observed from the inside rather than a genuine
                # failure. Never assumed silently: the settle+read-back
                # immediately below is the real, honest proof either
                # way - if the browser is genuinely gone, that call
                # fails too, and its own BrowserUnavailable is not
                # caught here, so a real failure still surfaces.
                pass
            time.sleep(_CLICK_SETTLE_SECONDS)
            return str(self._browser.evaluate(_PAGE_RECEIPT_JS))

        raise AssertionError(f"unreachable: '{op}' is in SUPPORTED_OPERATIONS with no _run branch")


class _ParameterError(ValueError):
    """A caller supplied parameters this connection will not attempt -
    never silently dropped, never guessed at. Internal only; ``execute``
    always turns this into an honest ``ConnectionResult``, never lets
    it escape."""
