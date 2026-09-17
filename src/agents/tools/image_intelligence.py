"""The governed boundary between a request about a real image and the image
techniques that act on it - transform, enhance or reconstruct one image into a
new, derived image artifact, list what techniques exist, or compare two images.

**Environment access only.** Every technique is a deterministic operation on
pixels (``src/providers/media/image_techniques.py``); nothing here holds or
calls reasoning machinery (``tests/test_provenance.py``). Understanding what an
image *shows* stays the Brain's (``image_analysis``).

**The source is never touched.** A transformed image is a brand-new artifact
linked to its source through ``related_artifact_ids`` - the same rule a derived
document already follows - so the person's own photo stays byte-for-byte what
they gave.

**Live governance.** ``governed_values`` is read on every call, so a technique
the owner composed, enabled or disabled through Ecosystem Evolution is what the
very next request uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.media.image_techniques import (
    ImageTechniqueError,
    compare_images,
    image_techniques,
    technique_for_request,
)
from src.services.artifacts.models import ImageContent
from src.services.artifacts.registration import register_new_artifact
from src.services.artifacts.store import ArtifactError, ArtifactStore

IMAGE_TYPE = "image"
_IMAGE_FORMATS = ("png", "jpg", "jpeg", "webp", "gif", "bmp")


class ImageIntelligenceTool(Tool):
    def __init__(
        self,
        store: ArtifactStore,
        *,
        owner_agent: str,
        governed_values: "Callable[[], Mapping[str, str]] | None" = None,
    ) -> None:
        self._store = store
        self._owner_agent = owner_agent
        self._governed_values = governed_values or (lambda: {})

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="image_intelligence",
            description=(
                "Transforms, enhances or reconstructs a real image into a new derived image artifact "
                "with a governed, open set of techniques, lists those techniques, or compares two real "
                "images by measurement - never inventing content in a picture."
            ),
            schema={"operation": str, "content": str},
        )

    def techniques(self):
        return image_techniques(dict(self._governed_values()))

    def run(self, **arguments: str) -> str:
        operation = arguments["operation"].strip().lower()
        content = arguments["content"]
        if operation == "techniques":
            return json.dumps([
                {"id": t.technique_id, "label": t.label, "purpose": t.purpose, "words": list(t.words)}
                for t in self.techniques()
            ])
        if operation == "transform":
            return self._transform(json.loads(content))
        if operation == "compare":
            return self._compare(json.loads(content))
        raise ValueError(f"unknown image_intelligence operation {operation!r}; expected techniques, transform or compare")

    def _image_bytes(self, artifact_id: str) -> "tuple[object, bytes]":
        artifact = self._store.get(str(artifact_id).strip().lower())
        if artifact is None or artifact.owner_agent != self._owner_agent:
            raise ArtifactError(f"no artifact found with id {artifact_id!r}")
        renditions = artifact.latest.renditions
        path = next((renditions[fmt] for fmt in _IMAGE_FORMATS if fmt in renditions), None)
        if path is None or not Path(path).is_file():
            raise ImageTechniqueError(f"'{artifact.latest.title}' has no stored image to work on")
        return artifact, Path(path).read_bytes()

    def _transform(self, request: dict) -> str:
        techniques = self.techniques()
        wanted = str(request.get("technique") or "").strip().lower()
        technique = next((t for t in techniques if t.technique_id == wanted), None) if wanted else None
        if technique is None:
            technique = technique_for_request(str(request.get("request") or ""), techniques)
        if technique is None:
            available = ", ".join(t.label for t in techniques) or "none"
            raise ImageTechniqueError(f"no image technique matches that request - available now: {available}")
        source, data = self._image_bytes(str(request.get("artifact_id") or ""))
        output = technique.apply(data)
        title = f"{source.latest.title} ({technique.label.lower()})"
        artifact = register_new_artifact(
            self._store, type=IMAGE_TYPE, owner_agent=self._owner_agent, title=title[:120],
            content_bytes=ImageContent(prompt=f"{technique.label} of '{source.latest.title}': {output.detail}").to_json().encode("utf-8"),
            rendition_format="png", rendition_bytes=output.data,
            related_artifact_ids=(source.artifact_id,),
            metadata={"content_kind": "image", "technique": technique.technique_id, "media_type": "image/png"},
        )
        return json.dumps({
            "artifact_id": artifact.artifact_id, "source_id": source.artifact_id, "title": artifact.latest.title,
            "technique": technique.technique_id, "label": technique.label, "purpose": technique.purpose,
            "detail": output.detail, "width": output.width, "height": output.height,
            "rendition": dict(artifact.latest.renditions).get("png", ""),
        })

    def _compare(self, request: dict) -> str:
        first, first_bytes = self._image_bytes(str(request.get("first") or ""))
        second, second_bytes = self._image_bytes(str(request.get("second") or ""))
        comparison = compare_images(first_bytes, second_bytes)
        return json.dumps({
            "text": comparison.describe(first.latest.title, second.latest.title),
            "verdict": comparison.verdict, "hash_distance": comparison.hash_distance,
            "changed_fraction": comparison.changed_fraction, "first": first.artifact_id, "second": second.artifact_id,
        })
