"""Tests for logging setup and DATA-loop observability.

``caplog`` is pytest's built-in fixture for capturing log records, so these
tests assert on what was actually emitted rather than on printed text.
"""

import io
import logging

import pytest

from src.agents.echo_agent import EchoAgent
from src.core.contracts import Task
from src.manager.manager import ManagerAgent
from src.utils.logging import (
    configure_logging,
    get_logger,
    summarize,
    task_logger,
)


class TestSetup:
    def test_get_logger_is_namespaced_under_src(self):
        assert get_logger("src.manager.manager").name == "src.manager.manager"

    def test_configure_logging_is_idempotent(self):
        """Calling twice must not duplicate handlers - that doubles every line."""
        first = configure_logging(stream=io.StringIO())
        before = len(first.handlers)
        again = configure_logging(stream=io.StringIO())
        assert again is first
        assert len(again.handlers) == before

    def test_configure_logging_honours_a_new_level(self):
        logger = configure_logging(level=logging.DEBUG, stream=io.StringIO())
        assert logger.level == logging.DEBUG
        configure_logging(level=logging.INFO, stream=io.StringIO())
        assert logger.level == logging.INFO

    def test_records_reach_the_configured_stream(self):
        stream = io.StringIO()
        configure_logging(level=logging.INFO, stream=stream)
        get_logger("src.test.demo").info("hello")
        assert "hello" in stream.getvalue()


class TestTaskCorrelation:
    def test_adapter_stamps_the_task_id(self, caplog):
        log = task_logger(get_logger("src.test.demo"), "abc123")
        with caplog.at_level(logging.INFO, logger="src.test.demo"):
            log.info("something happened")
        assert "[task=abc123]" in caplog.text

    def test_summarize_shortens_long_text(self):
        assert summarize("x" * 200, limit=20).endswith("…")
        assert len(summarize("x" * 200, limit=20)) == 20

    def test_summarize_collapses_whitespace(self):
        assert summarize("a\n  b   c") == "a b c"


class TestDataLoopIsObservable:
    @pytest.fixture
    def manager(self):
        m = ManagerAgent()
        m.register_agent(EchoAgent(name="alpha_agent", capabilities={"email", "inbox"}))
        return m

    def test_a_successful_task_logs_every_phase(self, manager, caplog):
        with caplog.at_level(logging.DEBUG, logger="src"):
            manager.handle_task(Task(description="send an email"))

        text = caplog.text
        assert "received:" in text          # entry
        assert "D: selected 'alpha_agent'" in text
        assert "A: assembled" in text
        assert "T: delegating" in text
        assert "A: accepted result" in text

    def test_every_line_of_one_task_shares_its_id(self, manager, caplog):
        task = Task(description="send an email")
        with caplog.at_level(logging.DEBUG, logger="src"):
            manager.handle_task(task)

        loop_records = [r for r in caplog.records if r.name == "src.manager.manager"]
        assert loop_records
        assert all(f"[task={task.id}]" in r.getMessage() for r in loop_records)

    def test_routing_logs_explain_the_choice(self, manager, caplog):
        with caplog.at_level(logging.INFO, logger="src"):
            manager.handle_task(Task(description="email me my inbox"))
        assert "score=2" in caplog.text
        assert "matched capabilities: email, inbox" in caplog.text

    def test_unroutable_task_logs_a_warning(self, manager, caplog):
        with caplog.at_level(logging.INFO, logger="src"):
            manager.handle_task(Task(description="book a flight"))
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        assert "no match" in caplog.text

    def test_a_crashing_agent_logs_a_traceback(self, caplog):
        from src.agents.base_agent import BaseAgent
        from src.core.contracts import AgentSpec

        class ExplodingAgent(BaseAgent):
            @property
            def spec(self):
                return AgentSpec(name="boom_agent", description="d", capabilities={"explode"})

            def execute(self, task):
                raise RuntimeError("kaboom")

        m = ManagerAgent()
        m.register_agent(ExplodingAgent())
        with caplog.at_level(logging.ERROR, logger="src"):
            m.handle_task(Task(description="please explode"))

        assert "RuntimeError" in caplog.text
        assert "kaboom" in caplog.text  # traceback was recorded, not swallowed

    def test_payload_contents_are_never_logged(self, manager, caplog):
        """Payloads may hold credentials; only the description is summarized."""
        task = Task(
            description="send an email",
            payload={"api_key": "SUPER_SECRET_VALUE"},
        )
        with caplog.at_level(logging.DEBUG, logger="src"):
            manager.handle_task(task)
        assert "SUPER_SECRET_VALUE" not in caplog.text

    def test_registering_an_agent_is_logged(self, caplog):
        with caplog.at_level(logging.INFO, logger="src"):
            ManagerAgent().register_agent(EchoAgent(name="new_agent", capabilities={"x"}))
        assert "registered 'new_agent'" in caplog.text
