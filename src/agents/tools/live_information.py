"""The governed boundary between Alfred and current, external information.

**The Brain reasons. This acquires.** A ``Tool`` is handed its arguments
and nothing else - no toolset, no registry, no agent, no manager (see
``tool.py``'s own docstring) - so this class cannot make an arbitrary HTTP
request, run a shell command, scrape an arbitrary website, or execute
anything beyond calling the one ``LiveInfoProvider`` it was constructed
with. Whatever provider that is - null, a scripted test double, or a real
backend one day - decides how a query actually gets answered; this class
only decides how that answer crosses into the rest of the system, exactly
as every other ``Tool`` implementation already does for its own
capability.

Registered under the tool name ``live_information`` - one name, matching
the capability that invokes it (``src/manager/capabilities.py``'s
``LIVE_INFORMATION``) - never a search engine's or a news provider's own
name, so a future backend is a change to which ``LiveInfoProvider`` this
class is constructed with, never a change to anything that calls it.
"""

from __future__ import annotations

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.live_info import LiveInfoProvider


class LiveInformationTool(Tool):
    """Wraps one ``LiveInfoProvider`` behind the ordinary ``Tool`` contract."""

    def __init__(self, provider: LiveInfoProvider) -> None:
        self._provider = provider

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="live_information",
            description=(
                "Retrieves current, external information for a query, with "
                "provenance - never Alfred's own remembered or trained knowledge."
            ),
            schema={"query": str},
        )

    def run(self, **arguments: str) -> str:
        """Fetch and return text carrying its own provenance.

        Raises like ordinary Python on failure - an empty query, the
        provider raising, or the provider genuinely finding nothing -
        which is the contract every ``Tool`` already lives under:
        ``ToolSet.invoke`` catches this at its own boundary and converts
        it into an honest, structured ``ToolResult.failed``, the same
        path any other tool's failure already takes.

        The provenance embedded in the returned text - source and the
        moment it was retrieved - is what stops this from becoming
        indistinguishable from Alfred's own knowledge once it reaches a
        reasoning step: see ``LiveInfoResult``'s own docstring.
        """
        query = arguments["query"]
        result = self._provider.fetch(query)
        if result.is_empty:
            raise RuntimeError(
                f"no current information is available from '{self._provider.name}'"
            )
        return f"[source: {result.source}, retrieved: {result.retrieved_at.isoformat()}] {result.content}"
