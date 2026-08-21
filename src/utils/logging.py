"""Logging setup for the orchestrator.

Two rules drive this module:

1. **Libraries log, applications configure.** Modules under ``src/`` only ever
   *emit* records via ``get_logger()``. Deciding where those records go - a
   console, a file, nowhere - belongs to the entry point, which calls
   ``configure_logging()`` exactly once. That is why importing anything from
   ``src/`` never hijacks your program's output.

2. **Correlate by task id.** One task passes through four phases, and later
   through several tasks interleaved. A log line that does not say *which*
   task it belongs to is nearly useless. ``task_logger()`` stamps every record
   with its task id so one task's journey can be reconstructed by filtering.

Note this file is named ``logging.py`` but ``import logging`` below still finds
the standard library: Python 3 uses absolute imports, so a local module only
shadows a stdlib one if it is on ``sys.path`` as a top-level name. Ours is
``src.utils.logging``, so there is no clash.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, MutableMapping

DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)-24s | %(message)s"
DEFAULT_DATE_FORMAT = "%H:%M:%S"

# The package logger. Attaching a NullHandler means that if the application
# never calls configure_logging(), records are silently discarded instead of
# triggering Python's "no handlers could be found" fallback. This is the
# standard library-author pattern.
_PACKAGE_LOGGER = logging.getLogger("src")
_PACKAGE_LOGGER.addHandler(logging.NullHandler())

# Marks the handler we installed, so configure_logging() can recognise its own
# work and stay idempotent.
_HANDLER_TAG = "orchestrator_console"


def get_logger(name: str) -> logging.Logger:
    """Return the logger a module should use.

    Callers pass ``__name__``, which yields dotted names like
    ``src.manager.manager``. Because loggers form a hierarchy that mirrors the
    package layout, configuring ``src`` configures every module beneath it, and
    you can raise or lower the verbosity of one subsystem alone.
    """
    return logging.getLogger(name)


def configure_logging(level: int | str = logging.INFO, stream: Any = None) -> logging.Logger:
    """Install console logging for the application. Safe to call more than once.

    The idempotency check matters: adding a handler on every call is a classic
    bug that produces duplicated log lines, doubling again with each call.
    """
    logger = _PACKAGE_LOGGER
    logger.setLevel(level)

    # Records stop here rather than bubbling to the root logger, so we cannot
    # double up with any logging a host application has configured for itself.
    logger.propagate = False

    for handler in logger.handlers:
        if getattr(handler, "_tag", None) == _HANDLER_TAG:
            # Already installed. Update it in place rather than adding a second
            # one: reconfiguring must take effect without duplicating output.
            handler.setLevel(level)
            if stream is not None and isinstance(handler, logging.StreamHandler):
                handler.setStream(stream)
            return logger

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT))
    handler.setLevel(level)
    handler._tag = _HANDLER_TAG  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger


class TaskLogAdapter(logging.LoggerAdapter):
    """Stamps every record with the id of the task being processed.

    A LoggerAdapter wraps a logger and rewrites records passing through it.
    Using one means no call site has to remember to include the task id: it is
    added structurally, so it cannot be forgotten in the one log line you
    later need most.
    """

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        task_id = self.extra.get("task_id", "-") if self.extra else "-"
        return f"[task={task_id}] {msg}", kwargs


def task_logger(logger: logging.Logger, task_id: str) -> TaskLogAdapter:
    """Wrap ``logger`` so all of its records carry ``task_id``."""
    return TaskLogAdapter(logger, {"task_id": task_id})


def summarize(text: str, limit: int = 60) -> str:
    """Shorten free text for a log line.

    Logs are read by humans and are frequently shipped somewhere central, so
    keep entries short and never let one line dump an entire payload.
    """
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
