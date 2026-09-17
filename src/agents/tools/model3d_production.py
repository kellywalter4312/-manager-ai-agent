"""The governed boundary between a request for 3D content and the 3D pipeline
(``src/providers/media/model3d.py``).

Environment access only: it reads a description of parts, assembles a real
glTF 2.0 binary, reads that binary back to decide whether it is genuinely 3D,
and saves it as a ``model3d`` artifact with the plan and the measurements beside
it. Nothing is saved that fails the check. No reasoning lives here - restating
a bare subject as parts is the Brain's, reached through the capability.

**Provider-replaceable.** Part assembly is one generator. Another (a local
text-to-3D model, a vendor mesh service) registers with
``register_model3d_generator`` and is offered a subject with no parts; whatever
it returns passes the same ``inspect_glb`` gate before it is called 3D.
"""

from __future__ import annotations

import json
from typing import Callable

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.media.model3d import (
    Model3DError,
    assemble_glb,
    inspect_glb,
    parse_part_description,
    shape_grammar,
)
from src.services.artifacts.registration import register_new_artifact

MODEL3D_TYPE = "model3d"
_GENERATORS: "dict[str, Callable[[str], bytes]]" = {}


def register_model3d_generator(name: str, generate: "Callable[[str], bytes]") -> None:
    """A generator that turns a bare subject into GLB bytes - checked like any other."""
    _GENERATORS[str(name).strip().lower()] = generate


def model3d_generators() -> "tuple[str, ...]":
    return ("part_assembly", *_GENERATORS)


class Model3DProductionTool(Tool):
    def __init__(self, store, *, owner_agent: str) -> None:
        self._store = store
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="model3d",
            description=(
                "Builds genuine 3D content - a glTF 2.0 mesh checked to be three-dimensional - from a subject "
                "described as parts, or through a registered 3D generator; lists the shapes and placements it reads."
            ),
            schema={"operation": str, "content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = arguments["operation"].strip().lower()
        if operation == "grammar":
            return json.dumps({"grammar": shape_grammar(), "generators": list(model3d_generators())})
        if operation == "parse":
            description = parse_part_description(json.loads(arguments["content"]).get("description", ""))
            return json.dumps({"parts": [p.to_dict() for p in description.parts], "not_understood": list(description.not_understood)})
        if operation == "build":
            return self._build(json.loads(arguments["content"]))
        raise ValueError(f"unknown model3d operation {operation!r}; expected grammar, parse or build")

    def _build(self, request: dict) -> str:
        subject = " ".join(str(request.get("subject") or "a model").split())[:100]
        description = parse_part_description(str(request.get("description") or ""))
        generator = "part_assembly"
        if description.parts:
            if description.not_understood:
                raise Model3DError("parts not understood: " + "; ".join(description.not_understood))
            data = assemble_glb(description.parts, title=subject)
        else:
            wanted = str(request.get("generator") or "").strip().lower()
            chosen = wanted if wanted in _GENERATORS else next(iter(_GENERATORS), None)
            if chosen is None:
                raise Model3DError("no parts were described and no 3D generator is registered")
            generator, data = chosen, _GENERATORS[chosen](subject)
        report = inspect_glb(data)
        if not report.genuinely_3d:
            reason = "; ".join(report.problems) or f"its extent {report.extent} is not three-dimensional"
            raise Model3DError(f"the result is not genuine 3D content ({reason}) - nothing was saved")
        plan = {"subject": subject, "generator": generator, "parts": [p.to_dict() for p in description.parts],
                "report": report.to_dict()}
        artifact = register_new_artifact(
            self._store, type=MODEL3D_TYPE, owner_agent=self._owner_agent, title=f"3D model - {subject}"[:120],
            content_bytes=json.dumps(plan, indent=2).encode("utf-8"), rendition_format="glb", rendition_bytes=data,
            metadata={"content_kind": MODEL3D_TYPE, "media_type": "model/gltf-binary", "generator": generator,
                      "triangles": str(report.triangles), "parts": str(len(description.parts))},
        )
        return json.dumps({"artifact_id": artifact.artifact_id, "title": artifact.latest.title, **plan, "bytes": len(data)})
