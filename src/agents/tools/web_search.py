"""The governed boundary between Alfred and web research evidence.

**Reuses the live-information provider architecture; does not duplicate
it.** `LiveInformationTool` (`src/agents/tools/live_information.py`)
already wraps a `LiveInfoProvider` for "what is true right now"; this
class wraps the *same* `LiveInfoProvider` contract - today, the same
real, keyless `WikipediaLiveInfoProvider` - for a different request
shape: "look this up and give me sources to compare", which need not be
about anything current at all. Two tool names over one real backend,
never two backends, because this project has exactly one genuinely
working, free, keyless web-evidence source, and pretending otherwise
would be exactly the "second parallel architecture" this phase is told
not to build.

**The Brain reasons. This acquires.** Identical discipline to every
other `Tool` in this project (see `live_information.py`'s own
docstring): no toolset, no registry, no agent - this class cannot browse
an arbitrary URL, only call the one `LiveInfoProvider` it was
constructed with.

Registered under the tool name `web_search` - the name
`general_assistance`'s own "research" `GeneralCapability` already
names as the tool it needs (`src/manager/capabilities.py`), so wiring
this in is what turns that capability's own honest "not installed"
refusal into genuine, real work, with no change to the capability's
own routing.
"""

from __future__ import annotations

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.live_info import LiveInfoProvider


class WebSearchTool(Tool):
    """Wraps one ``LiveInfoProvider`` behind the ``web_search`` tool name -
    real, multi-result evidence with provenance, never a single fabricated
    answer."""

    def __init__(self, provider: LiveInfoProvider) -> None:
        self._provider = provider

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_search",
            description=(
                "Searches the web for real sources on a query, with provenance - "
                "evidence to reason over, never a finished answer by itself."
            ),
            schema={"query": str},
        )

    def run(self, **arguments: str) -> str:
        """Fetch and return text carrying its own provenance.

        Raises like ordinary Python on failure - an empty query, the
        provider raising, or the provider genuinely finding nothing -
        the identical contract every ``Tool`` already lives under;
        ``ToolSet.invoke`` converts it into an honest, structured
        ``ToolResult.failed`` at its own boundary.
        """
        query = arguments["query"]
        result = self._provider.fetch(query)
        if result.is_empty:
            raise RuntimeError(
                f"no search results were found from '{self._provider.name}'"
            )
        return f"[source: {result.source}, retrieved: {result.retrieved_at.isoformat()}] {result.content}"
