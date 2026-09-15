"""The governed boundary between written code and a real, persistent, versioned
``code`` artifact - what fills the ``code_assistant`` extension point that
``general_assistance``'s own coding GeneralCapability has always named.

**Writing is not this tool's job.** Code is composed by reasoning - the Brain
when one is configured, otherwise the attributed local assistant - in
``src/manager/capabilities.py``; a Tool must never hold reasoning machinery
(``tests/test_provenance.py``). This tool only does the environment half: it
saves finished source as a code artifact (content + a plain-text rendition in
the language's own extension), reads one back, or revises one into a new
version under the same identity - which is what the Workspace's coding panel
shows.

**Agent-independent.** ``owner_agent`` is a plain string; a coding specialist
built later is handed its own instance, and the Manager keeps his.

**Never runs code.** Executing generated code is a separate, consequential act
this tool does not perform.
"""

from __future__ import annotations

import json

from src.agents.tools.tool import Tool, ToolSpec
from src.services.artifacts.models import CodeContent
from src.services.artifacts.registration import register_new_artifact
from src.services.artifacts.store import ArtifactError, ArtifactStore

CODE_TYPE = "code"


class CodeAssistantTool(Tool):
    def __init__(self, store: ArtifactStore, *, owner_agent: str) -> None:
        self._store = store
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="code_assistant",
            description=(
                "Saves finished source code as a real, versioned code artifact in the agent's coding "
                "workspace, reads one back, or revises one - never writes or runs code itself."
            ),
            schema={"operation": str, "content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = arguments["operation"].strip().lower()
        content = arguments["content"]
        if operation == "save":
            return self._save(content)
        if operation == "get":
            return self._get(content)
        if operation == "revise":
            return self._revise(content)
        raise ValueError(f"unknown code_assistant operation {operation!r}; expected save, get or revise")

    def _describe(self, artifact) -> str:
        latest = artifact.latest
        return json.dumps({
            "artifact_id": artifact.artifact_id, "version": latest.version, "title": latest.title,
            "renditions": dict(latest.renditions),
        })

    def _save(self, content: str) -> str:
        code = CodeContent.from_json(content)
        artifact = register_new_artifact(
            self._store, type=CODE_TYPE, owner_agent=self._owner_agent, title=code.title,
            content_bytes=code.to_json().encode("utf-8"), rendition_format=code.extension,
            rendition_bytes=code.source.encode("utf-8"),
        )
        return self._describe(artifact)

    def _owned(self, artifact_id: str):
        artifact = self._store.get(artifact_id.strip().lower())
        if artifact is None or artifact.owner_agent != self._owner_agent or artifact.type != CODE_TYPE:
            raise ArtifactError(f"no code artifact found with id {artifact_id.strip()!r}")
        return artifact

    def _get(self, artifact_id: str) -> str:
        artifact = self._owned(artifact_id)
        with open(artifact.latest.content_path, encoding="utf-8") as handle:
            code = CodeContent.from_json(handle.read())
        return json.dumps({**json.loads(self._describe(artifact)), "language": code.language, "source": code.source})

    def _revise(self, content: str) -> str:
        artifact_id, _sep, body = content.partition("\n")
        artifact = self._owned(artifact_id)
        code = CodeContent.from_json(body)
        version_dir = self._store.artifact_dir(artifact.artifact_id) / f"v{artifact.latest.version + 1}"
        version_dir.mkdir(parents=True, exist_ok=True)
        content_path = version_dir / "content.json"
        content_path.write_text(code.to_json(), encoding="utf-8")
        rendition = version_dir / f"rendition.{code.extension}"
        rendition.write_text(code.source, encoding="utf-8")
        revised = self._store.revise(
            artifact.artifact_id, title=code.title, content_path=content_path.as_posix(),
            renditions={code.extension: rendition.as_posix()},
        )
        return self._describe(revised)
