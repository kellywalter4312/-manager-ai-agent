"""Literal website interaction: WebsiteConnection's own logic, and its
integration with the unmodified connected_action/confirmation/
Control-Center governance every other connection already goes through.

**Why a fake browser, not a real one.** ``tests/conftest.py``'s own
``_no_network`` fixture blocks every outbound socket - including to
127.0.0.1 - for the whole pytest session, by deliberate project policy
(see that fixture's own docstring). A real ``CDPBrowser`` needs a real
localhost connection to prove anything, so it cannot run inside this
suite - not a gap in these tests, a property of the suite, the
identical reason ``tests/test_cdp_browser_cleanup.py`` mocks the OS
call rather than launching a real browser. ``WebsiteConnection`` was
built with exactly this in mind: its ``browser=`` constructor
parameter accepts any object with ``CDPBrowser``'s own narrow surface
(``navigate``/``evaluate``/``set_cookie``/``close``), so this file
proves the connection's real logic - operation classification,
authorization, parameter validation, session priming, JS dispatch,
honest failure - deterministically, with a hand-written double that
never touches a socket.

The genuine, real-browser, real-fixture-website proof - that natural
language actually drives a real Chromium engine through navigate ->
read_page -> fill_field -> click -> confirmation -> real submission ->
result read-back - is what this checkpoint's own standalone smoke
script (run directly with python, outside pytest, exactly like every
other "live" verification in this project's history) exists for, and
was run and inspected as part of this same checkpoint.
"""

from __future__ import annotations

import json

import pytest

from src.agents.tools.connection import ConnectionActionTool, tool_name_for
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.providers.availability import AvailabilityState
from src.providers.connection import AuthorizationState, ConnectionStatus
from src.providers.browser.cdp_browser import BrowserUnavailable, PageReceipt
from src.providers.site_descriptor import SiteDescriptor
from src.providers.website_connection import SUPPORTED_OPERATIONS, WebsiteConnection
from src.services.credentials.account import ProviderAccount


class _FakeBrowser:
    """A real, hand-written double for CDPBrowser's own narrow surface -
    never a mock that merely records calls without honouring what
    WebsiteConnection actually asks of it, so a bug in how
    WebsiteConnection builds its own JS/URLs would show up here exactly
    as it would against a real browser."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.navigate_calls: "list[str]" = []
        self.evaluate_calls: "list[str]" = []
        self.set_cookie_calls: "list[tuple[str, str, str]]" = []
        self.closed = False
        self._url = "about:blank"
        self._title = ""
        self._elements = [{"locator": "full_name", "tag": "input", "type": "text", "label": "Full name"}]

    def navigate(self, url: str, *, settle_seconds: float = 0.4) -> PageReceipt:
        if self.fail:
            raise BrowserUnavailable("no real browser installed")
        self.navigate_calls.append(url)
        self._url = url
        self._title = "Fake Page"
        return PageReceipt(url=url, title=self._title)

    def evaluate(self, expression: str):
        if self.fail:
            raise BrowserUnavailable("no real browser installed")
        self.evaluate_calls.append(expression)
        if "querySelectorAll" in expression:
            return json.dumps({"url": self._url, "title": self._title, "elements": self._elements})
        if "CSS.escape" in expression and "missing-locator" in expression:
            # A real page's own honest answer: the click/fill_field JS
            # itself throws when the named locator was never discovered -
            # simulated here exactly as the real JS would report it.
            raise RuntimeError("no element with locator 'missing-locator' was found - call read_page first")
        return json.dumps({"url": self._url, "title": self._title})

    def set_cookie(self, *, name: str, value: str, url: str) -> None:
        if self.fail:
            raise BrowserUnavailable("no real browser installed")
        self.set_cookie_calls.append((name, value, url))

    def close(self) -> None:
        self.closed = True


def _site(**overrides) -> SiteDescriptor:
    return SiteDescriptor(name="genericsite", base_url="http://127.0.0.1:9199", **overrides)


class TestOperationVocabularyIsFixedAndUniversal:
    def test_every_site_offers_the_identical_four_operations(self):
        connection = WebsiteConnection(_site(), None, browser=_FakeBrowser())
        assert connection.supported_operations == ("navigate", "read_page", "fill_field", "click")
        assert set(SUPPORTED_OPERATIONS) == {"navigate", "read_page", "fill_field", "click"}

    def test_click_alone_is_consequential_the_other_three_are_not(self):
        connection = WebsiteConnection(_site(), None, browser=_FakeBrowser())
        assert connection.requires_confirmation_for("click") is True
        assert connection.requires_confirmation_for("navigate") is False
        assert connection.requires_confirmation_for("read_page") is False
        assert connection.requires_confirmation_for("fill_field") is False

    def test_an_unknown_operation_is_reported_unsupported_never_attempted(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("scroll_to_top")
        assert result.status is ConnectionStatus.UNSUPPORTED
        assert browser.evaluate_calls == [] and browser.navigate_calls == []


class TestAuthorizationIsSessionWideNeverPerOperation:
    def test_a_public_site_with_no_auth_cookie_needs_no_credential_at_all(self):
        connection = WebsiteConnection(_site(), None, browser=_FakeBrowser())
        assert connection.authorization is AuthorizationState.NOT_REQUIRED
        for op in SUPPORTED_OPERATIONS:
            assert connection.authorization_for(op) is AuthorizationState.NOT_REQUIRED

    def test_a_login_gated_site_with_no_account_is_unauthorized(self):
        connection = WebsiteConnection(_site(auth_cookie_name="session"), None, browser=_FakeBrowser())
        assert connection.authorization is AuthorizationState.UNAUTHORIZED
        result = connection.execute("navigate", path="/")
        assert result.status is ConnectionStatus.UNAUTHORIZED

    def test_a_login_gated_site_with_an_account_is_authorized_and_primes_the_session_once(self):
        account = ProviderAccount(provider="genericsite", account_id="default", credential="genuine-session-token")
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(auth_cookie_name="alfred_session"), account, browser=browser)
        assert connection.authorization is AuthorizationState.AUTHORIZED

        connection.execute("navigate", path="/")
        connection.execute("navigate", path="/again")

        assert browser.set_cookie_calls == [("alfred_session", "genuine-session-token", "http://127.0.0.1:9199")]

    def test_the_credential_itself_never_appears_in_any_result_detail(self):
        account = ProviderAccount(provider="genericsite", account_id="default", credential="TOP-SECRET-TOKEN")
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(auth_cookie_name="alfred_session"), account, browser=browser)

        result = connection.execute("navigate", path="/")
        assert "TOP-SECRET-TOKEN" not in result.detail
        assert "TOP-SECRET-TOKEN" not in result.describe()


class TestRealOperationsReachTheBrowserCorrectly:
    def test_navigate_reaches_the_real_base_url_plus_path(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("navigate", path="/apply")
        assert result.ok
        assert browser.navigate_calls == ["http://127.0.0.1:9199/apply"]
        assert "apply" not in result.detail or "http://127.0.0.1:9199/apply" in json.loads(result.detail)["url"]

    def test_navigate_refuses_a_path_that_smuggles_a_second_origin(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("navigate", path="http://evil.example/steal")
        assert not result.ok
        assert browser.navigate_calls == []

    def test_read_page_returns_the_real_discovered_elements(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("read_page")
        assert result.ok
        payload = json.loads(result.detail)
        assert payload["elements"][0]["locator"] == "full_name"

    def test_fill_field_needs_both_locator_and_value_never_silently_partial(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("fill_field", locator="full_name")
        assert not result.ok
        assert browser.evaluate_calls == []

    def test_fill_field_dispatches_real_js_with_the_real_value(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("fill_field", locator="full_name", value="Ada Lovelace")
        assert result.ok
        assert any("Ada Lovelace" in call for call in browser.evaluate_calls)

    def test_click_on_a_locator_that_was_never_discovered_fails_honestly(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("click", locator="missing-locator")
        assert not result.ok
        assert "missing-locator" in result.detail

    def test_click_settles_then_reads_back_the_resulting_page(self, monkeypatch):
        monkeypatch.setattr("src.providers.website_connection.time.sleep", lambda _s: None)
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("click", locator="submit")
        assert result.ok
        payload = json.loads(result.detail)
        assert "url" in payload and "title" in payload

    def test_a_click_that_triggers_real_navigation_still_succeeds_even_if_its_own_reply_is_lost(self, monkeypatch):
        """The actual bug this checkpoint's live verification found: a
        click whose own JavaScript navigates the page away can destroy
        the CDP execution context before a reply is ever sent, which
        CDPBrowser surfaces as a message-less BrowserUnavailable (an
        asyncio.wait_for timeout with no text at all) - not a real
        failure, the identical observed-from-the-inside evidence that
        the click actually worked. The settle+read-back that follows is
        what proves it either way."""
        monkeypatch.setattr("src.providers.website_connection.time.sleep", lambda _s: None)

        class _NavigatingClickBrowser(_FakeBrowser):
            def evaluate(self, expression: str):
                if "CSS.escape" in expression and self.evaluate_calls == []:
                    self.evaluate_calls.append(expression)
                    raise BrowserUnavailable("")  # the real, observed message-less timeout
                return super().evaluate(expression)

        browser = _NavigatingClickBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("click", locator="submit_btn")
        assert result.ok, f"a click whose own reply was lost to navigation must still succeed: {result.detail}"

    def test_a_click_where_the_browser_is_genuinely_gone_still_fails_honestly(self, monkeypatch):
        """The other half of the same fix: tolerating a lost reply from
        the click's own evaluate must never become tolerating a
        genuinely dead browser - the follow-up settle+read-back is
        never swallowed."""
        monkeypatch.setattr("src.providers.website_connection.time.sleep", lambda _s: None)
        browser = _FakeBrowser(fail=True)
        connection = WebsiteConnection(_site(), None, browser=browser)
        result = connection.execute("click", locator="submit_btn")
        assert not result.ok
        assert "browser" in result.detail.lower()


class TestHonestFailureNeverFabricatedSuccess:
    def test_a_genuinely_unreachable_browser_is_reported_not_pretended_around(self):
        connection = WebsiteConnection(_site(), None, browser=_FakeBrowser(fail=True))
        result = connection.execute("navigate", path="/")
        assert result.status is ConnectionStatus.FAILED
        assert "browser" in result.detail.lower()
        assert connection.availability.state is AvailabilityState.UNAVAILABLE

    def test_close_delegates_to_the_real_browsers_own_close(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        connection.close()
        assert browser.closed is True


class TestIntegratesWithTheUnmodifiedConnectedActionCapability:
    """The real point of this whole checkpoint: connected_action,
    _select_operation, the PendingConfirmation/ActivitySessions
    confirmation boundary, and ConnectionActionTool/ToolSet all already
    duck-type off Connection's own shape - proven here by reaching a
    WebsiteConnection through every one of them completely unmodified,
    the identical governed path a REST DeclaredConnection already
    goes through."""

    def _manager(self, connection):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        tools = ToolSet([
            ConnectionActionTool(
                connection, operation=op, description=f"Performs {op}.",
                parameters=connection.parameters_for(op),
            )
            for op in connection.supported_operations
        ])
        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)
        manager.own_skills._connection = connection
        return manager

    def test_a_safe_read_page_operation_executes_immediately_through_natural_language(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        manager = self._manager(connection)

        result = manager.handle_task(Task(description="check my connected account: read the page"))
        assert result.succeeded
        assert result.payload["operation"] == "read_page"
        assert browser.evaluate_calls  # the real fake browser was genuinely reached

    def test_click_raises_a_real_confirmation_and_only_a_clear_yes_executes_it(self):
        browser = _FakeBrowser()
        connection = WebsiteConnection(_site(), None, browser=browser)
        manager = self._manager(connection)

        raised = manager.handle_task(Task(description="check my connected account: click locator=submit"))
        assert raised.succeeded
        assert raised.payload["awaiting"] == "confirmation"
        assert browser.evaluate_calls == []  # nothing was actually clicked yet

        confirmed = manager.handle_task(Task(description="yes"))
        assert confirmed.succeeded
        assert confirmed.payload["operation"] == "click"
        assert browser.evaluate_calls  # the real click genuinely ran now

    def test_the_tool_names_granted_are_the_generic_connection_prefixed_shape(self):
        connection = WebsiteConnection(_site(), None, browser=_FakeBrowser())
        for op in connection.supported_operations:
            assert tool_name_for(op) == f"connection_{op}"


class TestGenericAcrossAnyDeclaredSiteNeverHardcoded:
    """No production code anywhere in this checkpoint names a real
    website, company or industry - proven here by declaring two
    completely different, invented sites through the identical,
    unmodified WebsiteConnection with zero new branches."""

    @pytest.mark.parametrize("site_name,base_url", [
        ("firstinventedsite", "http://127.0.0.1:9201"),
        ("secondinventedsite", "http://127.0.0.1:9202"),
    ])
    def test_two_unrelated_invented_sites_work_through_the_identical_mechanism(self, site_name, base_url):
        browser = _FakeBrowser()
        connection = WebsiteConnection(SiteDescriptor(name=site_name, base_url=base_url), None, browser=browser)
        result = connection.execute("navigate", path="/")
        assert result.ok
        assert browser.navigate_calls == [f"{base_url}/"]
