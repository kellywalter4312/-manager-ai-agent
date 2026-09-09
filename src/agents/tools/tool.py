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
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


def utc_now() -> datetime:
    """When something was observed. Injectable so tests can control time.

    Defined here rather than imported from services.memory, which already has
    one, because this module must not depend on memory - the toolset touching
    memory is precisely what `TestToolResultsDoNotBecomeMemories` forbids. Two
    small clocks are cheaper than that dependency.
    """
    return datetime.now(timezone.utc)


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
    # Where this came from, and when the world was actually looked at.
    #
    # **This is what stops a memory being mistaken for the present.** A
    # MemoryRecord's created_at says when something was written down; it says
    # nothing about when the fact was true. Remember "Dublin is 15.6C" today
    # and read it back tomorrow and the two are indistinguishable - unless the
    # observation carried the moment it was made.
    #
    # ``source`` is what was reached for, and is caller-owned in the same way
    # Insight.source and LLMJudgement's provenance are: an observation may not
    # claim its own origin, because a thing that could would be trusted about
    # the one fact nobody can check.
    source: str = ""
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not str(self.tool).strip():
            raise ToolError("ToolResult.tool must name the tool that was called")
        object.__setattr__(self, "tool", str(self.tool).strip())
        object.__setattr__(self, "source", " ".join(str(self.source or "").split()))

        # A non-success must say what went wrong. "It failed" with no reason is
        # not something a brain or a person can act on.
        if self.status is not ToolStatus.SUCCEEDED and not str(self.error or "").strip():
            raise ToolError(
                f"a {self.status.value} ToolResult must explain itself"
            )
        if self.error is not None:
            object.__setattr__(self, "error", " ".join(str(self.error).split()))

        # Only a successful call observed anything. A rejected call never ran,
        # and a failed one ran and produced nothing to look at - so neither may
        # carry an observation time. Allowing it would let "we tried at 14:03"
        # be read later as "the world was like this at 14:03", which is the
        # exact confusion this field exists to prevent.
        if self.observed_at is not None and self.status is not ToolStatus.SUCCEEDED:
            raise ToolError(
                f"a {self.status.value} ToolResult observed nothing, so it must "
                "not carry an observed_at"
            )
        if self.observed_at is not None and not isinstance(self.observed_at, datetime):
            raise ToolError("ToolResult.observed_at must be a datetime")

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.SUCCEEDED

    @property
    def is_observation(self) -> bool:
        """Whether this is a look at the world, dated and attributed.

        The question a caller has to be able to ask before treating a result as
        current truth. A rejected or failed call is not an observation, and a
        successful one that nobody dated is a fact with no moment attached.
        """
        return self.ok and self.observed_at is not None

    def describe(self) -> str:
        """A short line for logs.

        Excludes ``output``: a tool may return domain content - message text,
        posting bodies - and this project keeps that out of log files. The same
        rule Completion.describe() and Insight.describe() follow. Provenance is
        included, because where and when something was observed is not content.
        """
        detail = f" error={self.error}" if self.error else f" chars={len(self.output)}"
        origin = f" source={self.source}" if self.source else ""
        seen = f" observed_at={self.observed_at.isoformat()}" if self.observed_at else ""
        return f"tool={self.tool} {self.status.value}{detail}{origin}{seen}"

    @classmethod
    def succeeded(
        cls,
        tool: str,
        output: str,
        source: str = "",
        observed_at: datetime | None = None,
    ) -> "ToolResult":
        """A call that ran and produced something - so, an observation.

        ``observed_at`` defaults to now rather than to None, because a
        successful call *did* look at the world and the moment it did so is
        known here. Leaving it unset would make every observation undatable
        unless somebody remembered to date it, and the one that got forgotten
        would be the one later read as current.
        """
        return cls(
            tool=tool,
            status=ToolStatus.SUCCEEDED,
            output=output,
            source=source or tool,
            observed_at=observed_at if observed_at is not None else utc_now(),
        )

    @classmethod
    def rejected(cls, tool: str, reason: str, source: str = "") -> "ToolResult":
        """Refused before running. Nothing was observed, so nothing is dated."""
        return cls(
            tool=tool,
            status=ToolStatus.REJECTED,
            error=reason,
            source=source or tool,
        )

    @classmethod
    def failed(cls, tool: str, reason: str, source: str = "") -> "ToolResult":
        """Ran and went wrong. It reached for a source and saw nothing usable,
        so the source is recorded and the observation time is not."""
        return cls(
            tool=tool,
            status=ToolStatus.FAILED,
            error=reason,
            source=source or tool,
        )


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
    # Goal-level-planning-foundation checkpoint: a parameter a tool may
    # accept but does not require - distinct from ``schema`` (every
    # entry there is mandatory). Defaults to empty, so every existing
    # Tool in this project - none of which has ever populated this -
    # is completely unaffected; only a caller that actually declares an
    # optional parameter (``ConnectionActionTool``, for one it genuinely
    # declares optional) changes behaviour at all.
    optional: Mapping[str, type] = field(default_factory=dict)

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

        for parameter, expected in (self.optional or {}).items():
            if not isinstance(expected, type):
                raise ToolError(
                    f"tool '{name}' declares optional parameter '{parameter}' "
                    f"with {expected!r}, which is not a type"
                )
        overlap = set(self.schema) & set(self.optional or {})
        if overlap:
            raise ToolError(
                f"tool '{name}' declares {sorted(overlap)} as both required and optional"
            )
        object.__setattr__(self, "optional", dict(self.optional or {}))

    def describe(self) -> str:
        """One line describing the tool and its parameters."""
        required = [f"{name}: {kind.__name__}" for name, kind in sorted(self.schema.items())]
        optional = [
            f"{name}: {kind.__name__} = optional" for name, kind in sorted(self.optional.items())
        ]
        parameters = ", ".join(required + optional)
        return f"{self.name}({parameters}) - {self.description}"

    def validate(self, arguments: Mapping[str, Any]) -> str | None:
        """Why ``arguments`` are unacceptable, or None if they are fine.

        Checked before the tool runs, so a malformed request is refused rather
        than turned into an obscure TypeError from inside somebody's function.
        Every ``schema`` parameter is required; every ``optional`` parameter
        may be supplied or omitted; nothing declared as neither is allowed.
        """
        if not isinstance(arguments, Mapping):
            return f"arguments must be a mapping, got {type(arguments).__name__}"

        supplied = set(arguments)
        required = set(self.schema)
        optional = set(self.optional)

        missing = sorted(required - supplied)
        if missing:
            return f"missing required argument(s): {', '.join(missing)}"

        unexpected = sorted(supplied - required - optional)
        if unexpected:
            return f"unexpected argument(s): {', '.join(unexpected)}"

        for parameter, expected in sorted({**self.schema, **self.optional}.items()):
            if parameter not in arguments:
                continue  # a declared-optional parameter this call did not supply
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
