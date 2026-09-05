"""The governed boundary between Alfred and the user's own documents.

**The Brain reasons. This acquires.** A ``Tool`` is handed its arguments
and nothing else - no toolset, no registry, no agent, no manager (see
``tool.py``'s own docstring) - so this class cannot read an arbitrary
path, run a shell command, reach the network or execute anything beyond
calling the one ``KnowledgeSourceProvider`` it was constructed with.
Whatever provider that is - null, a scripted test double, or a real
folder of the person's own books and notes - decides how a query is
actually answered; this class only decides how that answer crosses into
the rest of the system, exactly as every other ``Tool`` implementation
already does.

Registered under the tool name ``knowledge_lookup`` - one name, matching
the capability that invokes it (``src/manager/capabilities.py``'s
``KNOWLEDGE``) - never a file format's name, a folder's name or a
document's own name, so a future backend is a change to which
``KnowledgeSourceProvider`` this class is constructed with, never a
change to anything that calls it.

**Distinct from ``live_information`` on purpose.** That tool means "go
and look at the world as it is right now"; this one means "look at what
this person actually gave me". Two names, two tools, two provider
contracts, granted independently - so an installation can have documents
and no live source, a live source and no documents, both or neither, and
every one of those four states is describable rather than guessed at.
"""

from __future__ import annotations

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.knowledge import KnowledgeSourceProvider


class KnowledgeLookupTool(Tool):
    """Wraps one ``KnowledgeSourceProvider`` behind the ordinary ``Tool`` contract."""

    def __init__(self, provider: KnowledgeSourceProvider) -> None:
        self._provider = provider

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="knowledge_lookup",
            description=(
                "Finds passages in the documents, books and notes the user "
                "supplied, each carrying the document it came from - never "
                "current information, and never Alfred's own trained knowledge."
            ),
            schema={"query": str},
        )

    def run(self, **arguments: str) -> str:
        """Return the matching passages, each carrying its own provenance.

        Raises like ordinary Python on failure - an empty query, the
        provider raising, or the provider genuinely finding nothing -
        which is the contract every ``Tool`` already lives under:
        ``ToolSet.invoke`` catches this at its own boundary and converts
        it into an honest, structured ``ToolResult.failed``, the same path
        any other tool's failure already takes.

        "Found nothing" is raised rather than returned as empty text for
        the identical reason ``LiveInformationTool`` raises it: a caller
        must not be able to mistake silence for material, and a reasoning
        step must never receive an empty citation it could fill in.

        The document name and locator embedded in each returned line (see
        ``KnowledgeExcerpt.cite``) are what stop this from becoming
        indistinguishable from Alfred's own trained knowledge once it
        reaches a reasoning step.
        """
        query = arguments["query"]
        result = self._provider.lookup(query)
        if result.is_empty:
            raise RuntimeError(
                f"nothing in the user's own documents matches this, according to "
                f"'{self._provider.name}'"
            )
        return "\n".join(result.citations())
