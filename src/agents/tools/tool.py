"""What a tool is, and what comes back from calling one.

A tool is a capability a specialist may be granted. Deliberately small and
model-agnostic: nothing here knows about Anthropic, OpenAI, function-calling
formats or any agent framework, because those are transport concerns and this
is a contract. A tool that only works with one vendor's calling convention
would have to be rewritten the first time that convention changed.

Two halves, split on purpose:

    Tool.run       does the work, and may raise like ordinary Python
    ToolResult     what the *boundary* reports afterwards

Tool authors do not build ToolResults. If they did, every author would have to
remember to catch their own exceptions and every one of them would eventually
forget. Wrapping happens once, at the ToolSet boundary, so a tool that raises
still produces a structured failure rather than taking down the loop.

Note what a tool does not receive: no toolset, no registry, no agent, no
manager. A tool is handed its arguments and nothing else, so one tool cannot
reach another or reach back into the system that called it. Any tool that
needed to would have to be given that capability explicitly, and none is.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ToolError(Exception):
    """Raised when a tool cannot be defined or a result cannot be formed."""


class ToolStatus(str, Enum):
    """How a tool call ended.

    Three outcomes, and the distinction between the first two matters: a
    rejected call never ran, while a failed call ran and went wrong. A loop
    reasoning about "should I try something else?" needs to tell those apart -
    retrying a rejected call with the same arguments is pointless, retrying a
    failed one may not be.
    """

    SUCCEEDED = "succeeded"  # ran, produced output
    REJECTED = "rejected"    # refused before running: unknown, ungranted, bad arguments
    FAILED = "failed"        # ran and raised

    @property
    def ran(self) -> bool:
        return self is not ToolStatus.REJECTED


@dataclass(frozen=True)
class ToolResult:
    """The structured outcome of one tool call.

    Frozen, and never a bare Python object: whatever a tool returns is rendered
    to text here. A loop that passed live objects back to a brain would be
    handing it references it might be able to act through, and the whole point
    of the boundary is that the brain receives observations rather than
    capabilities.
    """

    tool: str
    status: ToolStatus
    output: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        if not str(self.tool).strip():
            raise ToolError("ToolResult.tool must name the tool that was called")
        object.__setattr__(self, "tool", str(self.tool).strip())

        # A non-success must say what went wrong. "It failed" with no reason is
        # not something a brain or a person can act on.
        if self.status is not ToolStatus.SUCCEEDED and not str(self.error or "").strip():
            raise ToolError(
                f"a {self.status.value} ToolResult must explain itself"
            )
        if self.error is not None:
            object.__setattr__(self, "error", " ".join(str(self.error).split()))

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.SUCCEEDED

    def describe(self) -> str:
        """A short line for logs.

        Excludes ``output``: a tool may return domain content - message text,
        posting bodies - and this project keeps that out of log files. The same
        rule Completion.describe() and Insight.describe() follow.
        """
        detail = f" error={self.error}" if self.error else f" chars={len(self.output)}"
        return f"tool={self.tool} {self.status.value}{detail}"

    @classmethod
    def succeeded(cls, tool: str, output: str) -> "ToolResult":
        return cls(tool=tool, status=ToolStatus.SUCCEEDED, output=output)

    @classmethod
    def rejected(cls, tool: str, reason: str) -> "ToolResult":
        return cls(tool=tool, status=ToolStatus.REJECTED, error=reason)

    @classmethod
    def failed(cls, tool: str, reason: str) -> "ToolResult":
        return cls(tool=tool, status=ToolStatus.FAILED, error=reason)


@dataclass(frozen=True)
class ToolSpec:
    """What a tool says about itself, so a caller can decide whether to use it.

    Separate from the tool so a specification can be shown to a brain - or one
    day rendered into whatever calling format a model expects - without handing
    over anything runnable.
    """

    name: str
    description: str
    schema: Mapping[str, type] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ToolError("a tool must have a name")
        object.__setattr__(self, "name", name)

        if not str(self.description).strip():
            # A tool nobody can tell the purpose of is a tool that will be
            # called wrongly.
            raise ToolError(f"tool '{name}' must describe what it does")
        object.__setattr__(self, "description", " ".join(str(self.description).split()))

        for parameter, expected in (self.schema or {}).items():
            if not isinstance(expected, type):
                raise ToolError(
                    f"tool '{name}' declares parameter '{parameter}' with "
                    f"{expected!r}, which is not a type"
                )
        object.__setattr__(self, "schema", dict(self.schema or {}))

    def describe(self) -> str:
        """One line describing the tool and its parameters."""
        parameters = ", ".join(
            f"{name}: {kind.__name__}" for name, kind in sorted(self.schema.items())
        )
        return f"{self.name}({parameters}) - {self.description}"

    def validate(self, arguments: Mapping[str, Any]) -> str | None:
        """Why ``arguments`` are unacceptable, or None if they are fine.

        Checked before the tool runs, so a malformed request is refused rather
        than turned into an obscure TypeError from inside somebody's function.
        Every declared parameter is required; nothing undeclared is allowed.
        """
        if not isinstance(arguments, Mapping):
            return f"arguments must be a mapping, got {type(arguments).__name__}"

        supplied = set(arguments)
        declared = set(self.schema)

        missing = sorted(declared - supplied)
        if missing:
            return f"missing required argument(s): {', '.join(missing)}"

        unexpected = sorted(supplied - declared)
        if unexpected:
            return f"unexpected argument(s): {', '.join(unexpected)}"

        for parameter, expected in sorted(self.schema.items()):
            value = arguments[parameter]
            # bool is a subclass of int; accepting True where a number was
            # asked for would silently turn a mistake into a 1.
            if expected is not bool and isinstance(value, bool):
                return f"'{parameter}' must be {expected.__name__}, got bool"
            if not isinstance(value, expected):
                return (
                    f"'{parameter}' must be {expected.__name__}, "
                    f"got {type(value).__name__}"
                )

        return None


class Tool(ABC):
    """One capability a specialist may be granted.

    Implementations do their work and return a string, raising on failure like
    ordinary Python. They never construct a ToolResult and never catch their
    own errors - the boundary does both.
    """

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Name, description and declared parameters."""

    @abstractmethod
    def run(self, **arguments: Any) -> str:
        """Do the work and return what was found.

        Returns text rather than an object: whatever comes back is destined for
        a reasoning step, and text is the one shape that cannot smuggle a
        callable across the boundary.
        """

    @property
    def name(self) -> str:
        return self.spec.name

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
