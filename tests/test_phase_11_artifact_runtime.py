"""Phase 11 closure: running what was made, and reporting what it actually did.

The gap this covers. The generalized creation path ended at a saved artifact -
real, versioned, persisted and entirely unexamined. A verified browser driver
(``src/providers/browser/cdp_browser.py``) had existed in this repository for
some time and was connected to nothing Alfred created, so an interactive
artifact could only ever be reported on the strength of its source text. A
source file is evidence that something was written, never that it runs.

What is asserted here, in the order the mechanism is built:

1.  the provider contract's own honesty (``RuntimeObservation``)
2.  ``NullArtifactRuntime`` - honest absence, never a claimed run
3.  ``BrowserArtifactRuntime`` against a fake browser that answers exactly what
    the real one answers, so the observation logic is proven without launching
    anything (the identical double ``tests/test_website_connection.py`` uses,
    for the identical reason)
4.  ``ArtifactRuntimeTool`` - which artifact, resolved from the real store
5.  ``run_artifact_intent`` - the routing boundary, including what it must not take
6.  ``run_artifact`` - the capability's own honest reporting
7.  one real end-to-end run in the real installed browser, skipped (never
    silently passed) when this machine has none
"""

from __future__ import annotations

import json

import pytest

from src.agents.tools.artifact_runtime import ArtifactRuntimeTool
from src.agents.tools.code_assistant import CodeAssistantTool
from src.core.contracts import Task
from src.manager.capabilities import run_artifact_intent
from src.providers.artifact_runtime import RuntimeObservation
from src.providers.browser.cdp_browser import BrowserUnavailable, PageReceipt, find_browser_executable
from src.providers.browser_artifact_runtime import (
    BrowserArtifactRuntime,
    instrumented,
    looks_like_html,
)
from src.providers.null_artifact_runtime import NullArtifactRuntime
from src.services.artifacts.store import ArtifactError, ArtifactStore

WORKING_PAGE = (
    "<!doctype html><html><head><meta charset=utf-8><title>Bench</title></head>"
    "<body><canvas id=s></canvas><input id=c type=range><button id=r>Reset</button>"
    "<script>document.getElementById('c').addEventListener('input',function(){});</script>"
    "</body></html>"
)
BROKEN_PAGE = (
    "<!doctype html><html><head><meta charset=utf-8><title>Broken</title></head>"
    "<body><canvas id=s></canvas>"
    "<script>document.getElementById('missing').addEventListener('input',function(){});</script>"
    "</body></html>"
)


class FakeBrowser:
    """A real driver's exact surface - ``navigate``/``evaluate``/``close`` - and
    nothing more. It answers the report expression the way the real browser
    does, so a bug in how the observation is assembled shows up here exactly as
    it would against Chromium."""

    def __init__(self, *, errors=(), loaded=True, title="Bench", unavailable=False, garbage=False):
        self.errors = list(errors)
        self.loaded = loaded
        self.title = title
        self.unavailable = unavailable
        self.garbage = garbage
        self.navigated: "list[str]" = []
        self.closed = False

    def navigate(self, url: str, *, settle_seconds: float = 0.4) -> PageReceipt:
        if self.unavailable:
            raise BrowserUnavailable("no real browser installed")
        self.navigated.append(url)
        return PageReceipt(url=url, title="artifact runtime")

    def evaluate(self, expression: str):
        if self.unavailable:
            raise BrowserUnavailable("no real browser installed")
        if self.garbage:
            return "not json at all"
        observations = {"loaded": self.loaded}
        if self.loaded:
            observations.update(
                {"elements": 9, "controls": 2, "canvases": 1, "svg": 0, "scripts": 2, "text_length": 5}
            )
        return json.dumps({"errors": self.errors, "title": self.title, "observations": observations})

    def close(self) -> None:
        self.closed = True


def _runtime(**kwargs) -> BrowserArtifactRuntime:
    return BrowserArtifactRuntime(browser=FakeBrowser(**kwargs), settle_seconds=0)


# ---------------------------------------------------------------------------
# 1. the contract
# ---------------------------------------------------------------------------


class TestAnObservationNeverOverstatesWhatHappened:
    def test_not_running_is_not_clean(self):
        observation = RuntimeObservation(ran=False, detail="no engine")
        assert not observation.clean
        assert observation.summary().startswith("did not run: no engine")

    def test_running_with_errors_is_not_clean_and_says_so(self):
        observation = RuntimeObservation(ran=True, detail="ran", errors=("TypeError: x",))
        assert observation.ran and not observation.clean
        assert "1 error(s)" in observation.summary() and "TypeError: x" in observation.summary()

    def test_a_clean_run_reports_what_was_counted(self):
        observation = RuntimeObservation(ran=True, detail="ran", observations={"controls": 2})
        assert observation.clean
        assert "ran cleanly" in observation.summary() and "controls=2" in observation.summary()


# ---------------------------------------------------------------------------
# 2. honest absence
# ---------------------------------------------------------------------------


class TestWithNoRuntimeNothingIsClaimed:
    def test_the_null_runtime_refuses_and_names_the_surface_that_fixes_it(self):
        observation = NullArtifactRuntime().run(document=WORKING_PAGE)
        assert not observation.ran and not observation.clean
        assert "no artifact runtime is configured" in observation.detail
        assert "provider artifact_runtime" in observation.detail


# ---------------------------------------------------------------------------
# 3. the browser runtime's own logic, without a browser
# ---------------------------------------------------------------------------


class TestTheRuntimeReportsWhatTheEngineActuallySaid:
    def test_a_clean_page_is_reported_clean_with_its_real_counts(self):
        observation = _runtime().run(document=WORKING_PAGE, label="Bench")
        assert observation.ran and observation.clean
        assert observation.observations["controls"] == 2 and observation.observations["canvases"] == 1
        assert observation.title == "Bench"

    def test_a_page_that_threw_is_reported_as_having_thrown(self):
        observation = _runtime(errors=["Uncaught TypeError: x is null"]).run(document=BROKEN_PAGE)
        assert observation.ran and not observation.clean
        assert observation.errors == ("Uncaught TypeError: x is null",)

    def test_a_stage_that_never_loaded_is_not_a_run(self):
        observation = _runtime(loaded=False).run(document=WORKING_PAGE)
        assert not observation.ran
        assert "never loaded" in observation.detail

    def test_an_absent_engine_is_a_finding_not_an_exception(self):
        observation = _runtime(unavailable=True).run(document=WORKING_PAGE)
        assert not observation.ran
        assert "no browser runtime is available" in observation.detail

    def test_an_unreadable_answer_is_reported_rather_than_guessed(self):
        observation = _runtime(garbage=True).run(document=WORKING_PAGE)
        assert not observation.ran
        assert "unreadable" in observation.detail

    def test_an_empty_artifact_is_refused_before_anything_is_started(self):
        browser = FakeBrowser()
        observation = BrowserArtifactRuntime(browser=browser, settle_seconds=0).run(document="   ")
        assert not observation.ran and "nothing to run" in observation.detail
        assert browser.navigated == []

    def test_an_injected_browser_is_never_closed_by_the_runtime(self):
        """Its owner closes it - the same lifetime rule WebsiteConnection keeps."""
        browser = FakeBrowser()
        BrowserArtifactRuntime(browser=browser, settle_seconds=0).run(document=WORKING_PAGE)
        assert browser.closed is False


class TestTheCollectorRunsBeforeTheArtifactDoes:
    """A handler attached from outside the page, after its load event, has
    already missed every error the page's own scripts caused - which is exactly
    what happened before the collector was inserted into the document itself."""

    def test_it_is_inserted_at_the_head_before_any_of_the_page_s_own_scripts(self):
        page = instrumented(WORKING_PAGE)
        assert page.index("window.__errors") < page.index("addEventListener('input'")
        assert page.index("<head") < page.index("window.__errors")

    def test_a_fragment_with_no_head_still_gets_it_first(self):
        page = instrumented("<canvas></canvas><script>boom()</script>")
        assert page.startswith("<script>window.__errors")

    def test_a_bare_script_is_recognised_as_not_being_a_page(self):
        assert looks_like_html(WORKING_PAGE)
        assert not looks_like_html("window.x = 1 + 1;")


# ---------------------------------------------------------------------------
# 4. which artifact - resolved from the real store
# ---------------------------------------------------------------------------


def _tool(tmp_path, *, owner="Alfred", runtime=None):
    store = ArtifactStore(tmp_path / "artifacts")
    code = CodeAssistantTool(store, owner_agent=owner)
    runner = ArtifactRuntimeTool(store, runtime or _runtime(), owner_agent=owner)
    return store, code, runner


def _save(code_tool, source, title="Bench"):
    saved = code_tool.run(operation="save", content=json.dumps(
        {"title": title, "language": "html", "source": source, "request": title}))
    return json.loads(saved)["artifact_id"]


class TestWhichArtifactIsRunComesFromTheStore:
    def test_an_artifact_named_by_id_is_the_one_run(self, tmp_path):
        store, code, runner = _tool(tmp_path)
        first = _save(code, WORKING_PAGE, "First")
        _save(code, WORKING_PAGE, "Second")
        report = json.loads(runner.run(operation="run", content=first))
        assert report["artifact_id"] == first and report["title"] == "First"

    def test_with_no_id_the_most_recent_runnable_one_is_resolved(self, tmp_path):
        store, code, runner = _tool(tmp_path)
        _save(code, WORKING_PAGE, "First")
        second = _save(code, WORKING_PAGE, "Second")
        report = json.loads(runner.run(operation="run", content=""))
        assert report["artifact_id"] == second

    def test_another_agent_s_artifact_is_not_mine_to_run(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        theirs = _save(CodeAssistantTool(store, owner_agent="Someoneelse"), WORKING_PAGE)
        mine = ArtifactRuntimeTool(store, _runtime(), owner_agent="Alfred")
        with pytest.raises(ArtifactError) as refused:
            mine.run(operation="run", content=theirs)
        assert "no artifact of mine" in str(refused.value)

    def test_with_nothing_runnable_it_says_so_rather_than_running_nothing(self, tmp_path):
        store, code, runner = _tool(tmp_path)
        with pytest.raises(ArtifactError) as refused:
            runner.run(operation="run", content="")
        assert "nothing runnable" in str(refused.value)

    def test_the_engine_names_itself_for_the_control_center(self, tmp_path):
        store, code, runner = _tool(tmp_path)
        engine = json.loads(runner.run(operation="engine", content=""))
        assert engine["name"] == "browser" and engine["describes"]

    def test_an_unknown_operation_is_refused(self, tmp_path):
        store, code, runner = _tool(tmp_path)
        with pytest.raises(ValueError):
            runner.run(operation="deploy", content="")

    def test_running_never_adds_a_version(self, tmp_path):
        """An observation is a reading. Correcting a failing artifact is a
        revision the person asks for, never a side effect of looking."""
        store, code, runner = _tool(tmp_path)
        artifact_id = _save(code, WORKING_PAGE)
        before = store.get(artifact_id).latest.version
        runner.run(operation="run", content=artifact_id)
        assert store.get(artifact_id).latest.version == before


# ---------------------------------------------------------------------------
# 5. the routing boundary
# ---------------------------------------------------------------------------


class TestAskingToRunSomethingReachesThisAndNothingElseDoes:
    @pytest.mark.parametrize("text", [
        "run it", "run this", "run that", "preview it", "try it", "test it",
        "does it work?", "does that run?", "did it work?",
        "launch the lens laboratory", "execute the simulation", "preview the focus bench",
        "run artifact 3550984f0984",
    ])
    def test_a_run_request_is_claimed(self, text):
        assert run_artifact_intent(Task(text))

    @pytest.mark.parametrize("text", [
        "check my inbox", "start recording", "stop the recording",
        "open my workspace", "go to my board workspace",
        "read my messages", "launch a new career", "open the front door",
        "create a handout titled Bench with: first - one; second - two",
        "what can you do?", "run a report of your findings",
        # Asking WHY something works is a reasoning question about it, not a
        # request to run it - found live claiming exactly this sentence.
        "I moved the increment before the check like you suggested and it fixed it - why does that work?",
        "why does that work?", "how does it work?", "what makes it work?",
    ])
    def test_everything_else_is_left_alone(self, text):
        assert not run_artifact_intent(Task(text))


# ---------------------------------------------------------------------------
# 6. what the capability actually says
# ---------------------------------------------------------------------------


def _granted(tool=None):
    """A real ``ToolSet`` - the same one the Manager holds - so the capability's
    own tool-permission and invocation path is exercised rather than mimicked."""
    from src.agents.tools.toolset import ToolSet

    return ToolSet((tool,) if tool is not None else ())


def _request(tools, text="run it"):
    from src.manager.capabilities import CapabilityRequest
    from src.services.identity.identity import AgentIdentity

    return CapabilityRequest(
        task=Task(text),
        identity=AgentIdentity(name="Alfred", role="Manager", capabilities=frozenset({"run"})),
        roster=lambda: (),
        own_name="Alfred",
        own_capabilities=(),
        tools=tools,
    )


class TestTheCapabilityReportsTheRunHonestly:
    def test_without_the_tool_it_names_what_is_missing_and_claims_nothing(self):
        from src.manager.capabilities import run_artifact

        result = run_artifact(_request(_granted()))
        assert not result.succeeded
        assert "artifact_runtime" in result.error and "not installed" in result.error

    def test_a_clean_run_reports_what_was_actually_on_the_page(self, tmp_path):
        from src.manager.capabilities import run_artifact

        store, code, runner = _tool(tmp_path)
        _save(code, WORKING_PAGE, "Bench")
        result = run_artifact(_request(_granted(runner)))
        assert result.succeeded, result.error
        assert "no errors" in result.output and "controls 2" in result.output
        assert result.payload["ran"] and result.payload["clean"]

    def test_a_run_that_threw_is_a_failure_even_though_it_ran(self, tmp_path):
        """"It ran" is not the question that was asked."""
        from src.manager.capabilities import run_artifact

        store = ArtifactStore(tmp_path / "artifacts")
        code = CodeAssistantTool(store, owner_agent="Alfred")
        _save(code, BROKEN_PAGE, "Broken")
        runner = ArtifactRuntimeTool(
            store, _runtime(errors=["Uncaught TypeError: x is null"]), owner_agent="Alfred")
        result = run_artifact(_request(_granted(runner)))
        assert not result.succeeded
        assert "1 error(s)" in result.error and "TypeError" in result.error
        assert "revise" in result.error

    def test_a_runtime_that_could_not_run_is_not_reported_as_a_broken_artifact(self, tmp_path):
        from src.manager.capabilities import run_artifact

        store = ArtifactStore(tmp_path / "artifacts")
        code = CodeAssistantTool(store, owner_agent="Alfred")
        _save(code, WORKING_PAGE, "Bench")
        runner = ArtifactRuntimeTool(store, NullArtifactRuntime(), owner_agent="Alfred")
        result = run_artifact(_request(_granted(runner)))
        assert not result.succeeded
        assert "can't tell you whether it works" in result.error
        assert "no artifact runtime is configured" in result.error

    def test_an_artifact_named_by_id_in_the_request_is_the_one_run(self, tmp_path):
        from src.manager.capabilities import run_artifact

        store, code, runner = _tool(tmp_path)
        first = _save(code, WORKING_PAGE, "First")
        _save(code, WORKING_PAGE, "Second")
        result = run_artifact(_request(_granted(runner), f"run artifact {first}"))
        assert result.succeeded, result.error
        assert result.payload["artifact_id"] == first and "First" in result.output


# ---------------------------------------------------------------------------
# 7. one real run, in the real browser
# ---------------------------------------------------------------------------


def _sockets_are_blocked() -> bool:
    """Whether this suite's own offline guard is installed (tests/conftest.py).

    It replaces ``socket.create_connection`` for the whole session, and CDP
    speaks to the browser over a loopback socket - so a real run cannot happen
    inside the suite. That guard is right and is not worked around here: an
    accidental real call must fail in the test that caused it.
    """
    import socket

    return getattr(socket.create_connection, "__name__", "") == "blocked"


def _require_a_real_run() -> None:
    """Skip unless a real run is genuinely possible here, and say which reason.

    Checked inside the test rather than as a ``skipif``: the suite installs its
    offline guard as a session fixture, which happens after collection, so a
    collection-time condition would not see it.
    """
    if find_browser_executable() is None:
        pytest.skip(
            "no Chromium-based browser is installed on this machine - a real run cannot be "
            "observed, and pretending otherwise is the one thing this mechanism exists to prevent"
        )
    if _sockets_are_blocked():
        pytest.skip(
            "this suite blocks every socket, loopback included, so CDP cannot reach the browser "
            "from inside it. The closure campaign's own p22/p23/p24 probes performed exactly "
            "these runs against the real installed browser, outside the guard."
        )


class TestOneRealRunInTheRealBrowser:
    """Everything above proves the assembly. This proves the thing works.

    Deliberately two cases and no more: a page that works and a page that
    throws. If a real engine could not tell those apart, every other test here
    would be measuring a fake.
    """

    def test_a_working_page_runs_cleanly_and_its_controls_are_seen(self):
        _require_a_real_run()
        observation = BrowserArtifactRuntime().run(document=WORKING_PAGE, label="Bench")
        assert observation.ran, observation.detail
        assert observation.clean, observation.errors
        assert observation.title == "Bench"
        assert observation.observations["canvases"] == 1
        assert observation.observations["controls"] == 2

    def test_a_page_that_throws_on_load_has_its_real_error_reported(self):
        _require_a_real_run()
        observation = BrowserArtifactRuntime().run(document=BROKEN_PAGE, label="Broken")
        assert observation.ran, observation.detail
        assert not observation.clean
        assert any("addEventListener" in e or "null" in e for e in observation.errors), observation.errors
        # And the page's own shape says why it is not interactive.
        assert observation.observations["controls"] == 0
