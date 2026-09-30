"""The honest "nothing here can run an artifact" runtime.

Selected when no runtime is configured, and the reason it exists rather than a
``None`` runtime: a caller asking "does this work?" deserves the answer "I
cannot tell you, and here is why", not a missing attribute to guard against at
every call site. The identical role every other ``null_*`` provider in this
directory already plays.
"""

from __future__ import annotations

from src.providers.artifact_runtime import ArtifactRuntimeProvider, RuntimeObservation


class NullArtifactRuntime(ArtifactRuntimeProvider):
    @property
    def name(self) -> str:
        return "null"

    @property
    def describes(self) -> str:
        return "nothing - no runtime is configured"

    def run(self, *, document: str, label: str = "") -> RuntimeObservation:
        return RuntimeObservation(
            ran=False,
            detail=(
                "no artifact runtime is configured, so nothing here can run this and "
                "report what it actually did - select one in the Control Center "
                "('provider artifact_runtime')"
            ),
        )
