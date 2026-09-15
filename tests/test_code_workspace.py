"""Post-Pre-Stage-9 D5 - code is a real artifact in the coding workspace.

The Brain writes code when one can, otherwise the attributed local assistant
does; the code_assistant tool saves it as a versioned ``code`` artifact and
never writes or runs code itself. In-process doubles only.
"""

from __future__ import annotations

import json

from src.agents.tools.code_assistant import CodeAssistantTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.manager.brain import BrainAction, BrainDecision
from src.manager.capabilities import CapabilityRequest, _code_language, general_assistance
from src.services.artifacts.models import CodeContent
from src.services.artifacts.store import ArtifactStore
from src.services.identity.identity import AgentIdentity

SOURCE = "def add(a, b):\n    return a + b"


class FakeAssist:
    def __init__(self, text=f"```python\n{SOURCE}\n```"):
        self.text = text
        self.asked = []

    def write_code(self, specification, *, language="python"):
        self.asked.append((specification, language))
        return self.text


def _request(tmp_path, description, **fields):
    store = ArtifactStore(tmp_path / "artifacts")
    fields.setdefault("tools", ToolSet([CodeAssistantTool(store, owner_agent="Alfred")]))
    request = CapabilityRequest(
        task=Task(description=description),
        identity=AgentIdentity(name="Alfred", capabilities=frozenset({"code"})),
        roster=lambda: (), own_name="Alfred", own_capabilities=(), **fields,
    )
    return request, store


class TestTheCodeArtifact:
    def test_content_round_trips_and_names_its_extension(self):
        code = CodeContent(title="Add two numbers", language="Python", source=SOURCE)
        assert CodeContent.from_json(code.to_json()) == code and code.extension == "py"
        assert CodeContent("x", "cobol", "PROGRAM").extension == "txt"

    def test_the_tool_saves_reads_and_revises_under_one_identity(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tools = ToolSet([CodeAssistantTool(store, owner_agent="Alfred")])
        saved = json.loads(tools.invoke("code_assistant", {"operation": "save", "content": CodeContent("Add", "python", SOURCE).to_json()}).output)
        artifact = store.get(saved["artifact_id"])
        assert artifact.type == "code" and artifact.owner_agent == "Alfred" and "py" in artifact.latest.renditions
        read = json.loads(tools.invoke("code_assistant", {"operation": "get", "content": saved["artifact_id"]}).output)
        assert read["source"] == SOURCE
        revised = CodeContent("Add", "python", SOURCE + "\n\nprint(add(1, 2))").to_json()
        out = json.loads(tools.invoke("code_assistant", {"operation": "revise", "content": f"{saved['artifact_id']}\n{revised}"}).output)
        assert out["version"] == 2 and out["artifact_id"] == saved["artifact_id"]

    def test_another_agents_code_is_never_revealed(self, tmp_path):
        store = ArtifactStore(tmp_path)
        mine = ToolSet([CodeAssistantTool(store, owner_agent="Alfred")])
        theirs = ToolSet([CodeAssistantTool(store, owner_agent="Coder")])
        saved = json.loads(mine.invoke("code_assistant", {"operation": "save", "content": CodeContent("Add", "python", SOURCE).to_json()}).output)
        assert not theirs.invoke("code_assistant", {"operation": "get", "content": saved["artifact_id"]}).ok


class TestWritingCode:
    def test_half_brain_writes_through_the_local_assistant_and_saves_the_artifact(self, tmp_path):
        assist = FakeAssist()
        request, store = _request(tmp_path, "write a python function that adds two numbers", assist=assist)
        result = general_assistance(request)
        assert result.succeeded and SOURCE in result.output and "small local model" in result.output
        assert result.payload["saved"] is True and result.payload["brain"] == "local_assist"
        artifact = store.get(result.payload["artifact_id"])
        assert artifact.type == "code" and artifact.latest.title.startswith("A python function")

    def test_a_brain_that_decides_writes_first(self, tmp_path):
        assist = FakeAssist()

        def converse(task, notes):
            return BrainDecision(action=BrainAction.REPORT, reason=f"```python\n{SOURCE}\n```", confidence=0.9, source="vendor")

        request, _ = _request(tmp_path, "write a small function to add numbers", assist=assist, converse=converse)
        result = general_assistance(request)
        assert result.payload["brain"] == "vendor" and not assist.asked

    def test_with_nothing_able_to_write_it_refuses_honestly(self, tmp_path):
        request, store = _request(tmp_path, "write a small function to add numbers")
        result = general_assistance(request)
        assert not result.succeeded and "won't guess" in result.error and not store.all()

    def test_without_the_tool_the_missing_capability_is_still_named(self, tmp_path):
        request, _ = _request(tmp_path, "write a small function", assist=FakeAssist(), tools=ToolSet())
        result = general_assistance(request)
        assert not result.succeeded and "code_assistant" in result.error

    def test_the_named_language_is_used(self):
        assert _code_language("write a javascript function to debounce") == "javascript"
        assert _code_language("write a C# script") == "c#"
        assert _code_language("write a function to sort a list") == "python"
