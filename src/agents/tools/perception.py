"""The governed boundary to structured perception (GM-03): what is where in a
shared still or recording, followed over time, counted, and kept.

``analyze`` reads one artifact the agent owns (a shared photo, a recording, a
frame a watch captured), runs the configured perception backend
(``runtime.perception`` - a provider axis) through the one analysis every
backend shares, records what was seen through the ecosystem's single entry
point (so conditions, zones and dashboards react), and saves the picture of
what was seen as a new image artifact derived from the source. It never
changes the source, and never names a thing its backend did not name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.perception.contract import PerceptionError
from src.services.artifacts.models import ImageContent
from src.services.artifacts.registration import register_new_artifact
from src.services.artifacts.store import ArtifactError
from src.services.perception.analysis import analyse, frames_from_media, observations_of, utc_now

PERCEPTION_TOOL = "perception"
_MEDIA_SUFFIXES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp",
    ".gif": "image/gif", ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
}


class PerceptionTool(Tool):
    def __init__(self, artifacts: Any, provider: Any, ecosystem: Any, *, owner_agent: str) -> None:
        self._store = artifacts
        self._provider = provider
        self._ecosystem = ecosystem
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=PERCEPTION_TOOL,
            description=(f"Finds what is where in a shared still or recording, follows it over time and counts it - "
                         f"backend: {self._provider.name} ({self._provider.describes})."),
            schema={"operation": str},
            optional={"content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        raw = str(arguments.get("content") or "").strip()
        content = json.loads(raw) if raw else {}
        if operation == "backend":
            return json.dumps({"name": self._provider.name, "describes": self._provider.describes,
                               "labels_objects": self._provider.labels_objects})
        if operation == "analyze":
            return json.dumps(self._analyze(content))
        raise ValueError(f"unknown perception operation {operation!r}; expected analyze or backend")

    def _media(self, artifact_id: str) -> "tuple[Any, bytes, str]":
        artifact = self._store.get(str(artifact_id).strip().lower())
        if artifact is None or artifact.owner_agent != self._owner_agent:
            raise ArtifactError(f"no artifact of mine found with id {artifact_id!r}")
        latest = artifact.latest
        declared = str(dict(latest.metadata).get("media_type") or "")
        for path in list(dict(latest.renditions).values()) + [latest.content_path]:
            candidate = Path(str(path))
            kind = declared or _MEDIA_SUFFIXES.get(candidate.suffix.lower(), "")
            if candidate.is_file() and (kind.startswith("image/") or kind.startswith("video/")):
                return artifact, candidate.read_bytes(), kind
        raise PerceptionError(f"'{latest.title}' holds no picture or recording to look at")

    @staticmethod
    def _named_file(path: str) -> "tuple[None, bytes, str]":
        """A picture or recording named by its path - the same reach
        image_analysis and clip_video already have for a named file."""
        candidate = Path(path)
        kind = _MEDIA_SUFFIXES.get(candidate.suffix.lower(), "")
        if not kind:
            raise PerceptionError(f"'{candidate.name}' is not a picture or recording I can look at")
        if not candidate.is_file():
            raise PerceptionError(f"I couldn't find '{path}'")
        return None, candidate.read_bytes(), kind

    def _analyze(self, content: dict) -> dict:
        if content.get("artifact_id"):
            artifact, data, media_type = self._media(str(content["artifact_id"]))
            title = artifact.latest.title
        elif content.get("path"):
            artifact, data, media_type = self._named_file(str(content["path"]))
            title = Path(str(content["path"])).stem
        else:
            raise PerceptionError("name the picture or recording to look at (an artifact id or a path)")
        source = " ".join(str(content.get("source") or title).split())[:80] or "the recording"
        frames = frames_from_media(data, media_type, count=int(content.get("frames") or 24))
        report = analyse(frames, self._provider, source=source)
        recorded = 0
        if self._ecosystem is not None:
            policy = getattr(self._ecosystem, "sensitivity", None)
            level = policy.classify(source) if policy is not None else ""
            recorded = self._ecosystem.observe(observations_of(report, started=utc_now(), sensitivity=level))
        picture_id = ""
        source_id = artifact.artifact_id if artifact is not None else ""
        if report.picture and self._store is not None:
            picture = register_new_artifact(
                self._store, type="image", owner_agent=self._owner_agent, title=f"What was seen in {source}"[:120],
                content_bytes=ImageContent(prompt=f"Perception ({report.backend}) of '{title}': "
                                                  f"{len(report.tracks)} thing(s) followed").to_json().encode("utf-8"),
                rendition_format="png", rendition_bytes=report.picture,
                related_artifact_ids=(source_id,) if source_id else (),
                metadata={"content_kind": "image", "media_type": "image/png", "technique": "perception"},
            )
            picture_id = picture.artifact_id
        return {
            "report": report.to_dict(), "text": report.describe(asked_about=str(content.get("about") or "")),
            "picture_artifact_id": picture_id, "source_artifact_id": source_id,
            "observations_recorded": recorded, "media_type": media_type,
        }
