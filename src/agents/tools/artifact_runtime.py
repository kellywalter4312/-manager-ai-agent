"""Runs an artifact this agent owns and reports what it actually did - what
fills the ``artifact_runtime`` extension point.

**The half of creation that was missing.** ``code_assistant`` saves finished
source as a real, versioned code artifact; ``artifact_production`` does the same
for documents and images. Both end there. Nothing asked the next question, and
for an artifact meant to be *interactive* that is the only question that
matters: a source file proves something was written, never that it runs. This
tool closes that gap for every artifact kind that has runnable content, through
one mechanism rather than one per kind.

**It owns no engine.** Which engine runs the document is
``ArtifactRuntimeProvider``'s (``src/providers/artifact_runtime.py``) - a
browser today, a game engine or a simulation runner just as well tomorrow, each
selected by name at runtime. This tool resolves *which artifact*, hands its
content over, and turns the observation into something a person and a capability
can both read. Nothing here knows what CDP is.

**It never rewrites the artifact.** Running is a read: the stored content, its
identity and its version chain are untouched, and the observation is reported
rather than silently filed. Correcting a failing artifact is a revision, made
through the capability that owns revisions, with the person in the loop - not a
side effect of looking.
"""

from __future__ import annotations

import json

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.artifact_runtime import ArtifactRuntimeProvider
from src.agents.tools.code_assistant import CODE_TYPE
from src.services.artifacts.models import CodeContent, DocumentContent
from src.services.artifacts.store import ArtifactError, ArtifactStore

# The artifact types whose content can be handed to a runtime as-is, and how to
# get the runnable text out of each. An artifact type absent from here is not
# refused for being unknown - it is refused for having nothing runnable, which
# is a different and honest answer (see ``_runnable``).
DOCUMENT_TYPE = "document"


class ArtifactRuntimeTool(Tool):
    def __init__(
        self,
        store: ArtifactStore,
        runtime: ArtifactRuntimeProvider,
        *,
        owner_agent: str,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="artifact_runtime",
            description=(
                "Runs an artifact this agent owns in a real runtime and reports what actually "
                f"happened - whether it loaded, what it contains, and any runtime errors. Engine: "
                f"{self._runtime.name} ({self._runtime.describes}). Never changes the artifact."
            ),
            schema={"operation": str},
            # Naming an artifact is optional: with no id, the most recent runnable
            # one this agent owns is resolved from the store.
            optional={"content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        content = str(arguments.get("content") or "")
        if operation == "run":
            return self._run(content)
        if operation == "engine":
            return json.dumps({"name": self._runtime.name, "describes": self._runtime.describes})
        raise ValueError(f"unknown artifact_runtime operation {operation!r}; expected run or engine")

    # -- resolution ---------------------------------------------------------

    def _owned(self, artifact_id: str):
        artifact = self._store.get(artifact_id.strip().lower())
        if artifact is None or artifact.owner_agent != self._owner_agent:
            raise ArtifactError(f"no artifact of mine found with id {artifact_id.strip()!r}")
        return artifact

    def _latest_runnable(self):
        """The most recent artifact of this agent's that has runnable content -
        resolved from the store, never assumed to be the newest artifact of any
        kind (the newest thing made may well be a narration)."""
        best = None
        for artifact in self._store.all():
            if artifact.owner_agent != self._owner_agent or artifact.type not in (CODE_TYPE, DOCUMENT_TYPE):
                continue
            if self._runnable(artifact) is None:
                continue
            created = str(getattr(artifact.latest, "created_at", "") or "")
            # ``>=`` rather than ``>``, scanning the store's own order: two
            # artifacts created inside the same clock tick carry identical
            # timestamps, and the later-registered one is then the most recent.
            # A strict comparison silently kept the earlier one, which is a
            # guess dressed up as evidence.
            if best is None or created >= best[0]:
                best = (created, artifact)
        if best is None:
            raise ArtifactError(
                "I have nothing runnable yet - create something first, or name one with its id"
            )
        return best[1]

    def _runnable(self, artifact) -> "tuple[str, str] | None":
        """``(runnable text, label)`` for this artifact, or None when it holds
        nothing a runtime could run."""
        latest = artifact.latest
        try:
            with open(latest.content_path, encoding="utf-8") as handle:
                raw = handle.read()
        except OSError:
            return None
        if artifact.type == CODE_TYPE:
            try:
                code = CodeContent.from_json(raw)
            except ValueError:
                return None
            # A runtime runs what it can run; a language it cannot is reported
            # by the runtime itself, not guessed at here.
            return code.source, f"{latest.title} ({code.language})"
        if artifact.type == DOCUMENT_TYPE:
            try:
                document = DocumentContent.from_json(raw)
            except ValueError:
                return None
            body = "\n".join(section.body for section in document.sections)
            from src.providers.browser_artifact_runtime import looks_like_html

            # A document is runnable only when its own content genuinely is a
            # page. Prose is a document, and saying so beats running an empty
            # page and calling it verified.
            return (body, latest.title) if looks_like_html(body) else None
        return None

    # -- the one operation --------------------------------------------------

    def _run(self, content: str) -> str:
        reference = content.strip()
        artifact = self._owned(reference) if reference else self._latest_runnable()
        runnable = self._runnable(artifact)
        if runnable is None:
            raise ArtifactError(
                f"artifact {artifact.artifact_id} ('{artifact.latest.title}') holds nothing runnable - "
                f"it is a {artifact.type} with no runnable content"
            )
        document, label = runnable
        observation = self._runtime.run(document=document, label=label)
        return json.dumps({
            "artifact_id": artifact.artifact_id,
            "version": artifact.latest.version,
            "title": artifact.latest.title,
            "engine": self._runtime.name,
            "ran": observation.ran,
            "clean": observation.clean,
            "page_title": observation.title,
            "errors": list(observation.errors),
            "observations": dict(observation.observations),
            "summary": observation.summary(),
        })
