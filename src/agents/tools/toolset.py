"""The tools one specialist is allowed to use - and the boundary they run behind.

Permission here is membership. A specialist can invoke exactly what its ToolSet
contains and nothing else; there is no ambient registry to fall back to, no
lookup by import path, and no way to ask for "all tools". A capability a
specialist was not handed is a capability it does not have.

**A ToolSet cannot grow.** There is deliberately no add(), register() or
update() method, and the mapping is copied at construction. A tool cannot put
itself in the set, and a tool that ran cannot widen what runs next - the set a
specialist starts with is the set it finishes with. That is why the empty
default is safe: a specialist nobody equipped can do nothing.

This is also the fault-isolation boundary. Every call goes through invoke(),
which validates the name, validates the arguments, runs the tool and converts
anything that goes wrong into a structured ToolResult. A tool author writes
ordinary code that may raise; the loop never sees an exception escape.

Imports nothing from the manager, the providers or any specialist. A toolset
knows how to look a tool up and call it, and nothing else about the system.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.agents.tools.tool import Tool, ToolError, ToolResult, ToolSpec
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ToolSet:
    """An immutable collection of the tools one specialist may use."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        """Build a set from the tools explicitly granted.

        Copies into a private mapping, so a caller holding the sequence it
        passed cannot add to this set afterwards.
        """
        granted: dict[str, Tool] = {}
        for tool in tools or ():
            if not isinstance(tool, Tool):
                # Validated here, once, so a bad grant fails at construction
                # rather than deep inside a loop. The same reasoning as
                # AgentRegistry.register.
                raise ToolError(f"Expected a Tool, got {type(tool).__name__}")

            name = tool.name
            if name in granted:
                # Two tools answering to one name makes every later call
                # ambiguous, and silently keeping one would hide the mistake.
                raise ToolError(f"more than one tool is named '{name}'")
            granted[name] = tool

        self._tools = granted

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return str(name) in self._tools

    def __repr__(self) -> str:
        return f"<ToolSet tools={len(self._tools)}>"

    @property
    def is_empty(self) -> bool:
        """True when this specialist was granted nothing. The default."""
        return not self._tools

    def names(self) -> tuple[str, ...]:
        """Every tool available here, sorted so listings are deterministic."""
        return tuple(sorted(self._tools))

    def specs(self) -> tuple[ToolSpec, ...]:
        """What each tool says about itself - descriptions, never the tools.

        This is what a reasoning step may be shown. Handing over the specs
        rather than the tools means a brain can decide what to ask for without
        ever holding something it could call.
        """
        return tuple(self._tools[name].spec for name in self.names())

    def describe(self) -> str:
        """The available tools in a form a caller can render."""
        if self.is_empty:
            return "no tools are available"
        return "\n".join(spec.describe() for spec in self.specs())

    def permits(self, name: str) -> bool:
        return str(name).strip() in self._tools

    # ------------------------------------------------------------------
    # The boundary
    # ------------------------------------------------------------------

    def invoke(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        """Run a tool by name, and never raise for an ordinary failure.

        Four checks in order, each producing a result that says what happened:

            unknown name      rejected - it was never granted
            bad arguments     rejected - it would have failed anyway
            tool raised       failed   - it ran and went wrong
            otherwise         succeeded

        ``except Exception`` and not BaseException: a tool that is interrupted
        must still let Ctrl-C through, and SystemExit must still exit.
        """
        requested = str(name).strip()
        tool = self._tools.get(requested)

        if tool is None:
            available = ", ".join(self.names()) or "none"
            logger.debug("refused unavailable tool '%s'", requested)
            return ToolResult.rejected(
                requested or "(unnamed)",
                f"'{requested}' is not available to this specialist "
                f"(available: {available})",
            )

        problem = tool.spec.validate(arguments or {})
        if problem is not None:
            return ToolResult.rejected(requested, problem)

        try:
            output = tool.run(**dict(arguments or {}))
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            logger.exception("tool '%s' raised %s", requested, type(exc).__name__)
            return ToolResult.failed(requested, f"{type(exc).__name__}: {exc}")

        if not isinstance(output, str):
            # A tool returning an object would put a live reference in front of
            # a reasoning step. Refused rather than coerced, so the tool gets
            # fixed instead of quietly returning "<object at 0x...>".
            return ToolResult.failed(
                requested,
                f"the tool returned {type(output).__name__}, not text",
            )

        result = ToolResult.succeeded(requested, output)
        logger.debug("%s", result.describe())
        return result
