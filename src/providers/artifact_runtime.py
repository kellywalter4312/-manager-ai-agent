"""The artifact-runtime contract - running a created artifact and observing
what it actually did.

**Why this is its own axis.** Every other provider in this project answers
"make something" (an image, a narration, a document, a 3D mesh). This one
answers the question that comes after: *does it work?* Until it existed, the
generalized creation path ended at a saved artifact - real, versioned,
persisted, and entirely unexamined. A source file is evidence that something
was written, never evidence that it runs, and the difference matters most
exactly where the artifact is meant to be interactive: a simulation whose
control does nothing, a scene that throws on load and renders black, a page
whose script never ran, all look identical on disk.

**What a provider here is, and is not.** It is handed a runnable document and a
label, and it returns a real ``RuntimeObservation`` - what loaded, what the
page actually contains, and what errors the run produced. It is handed no
artifact, no store and no agent: which artifact is being run, and what is done
with the finding, belong to the tool above it (see
``src/agents/tools/artifact_runtime.py``), the identical split
``CDPBrowser``/``WebsiteConnection`` already keeps and for the identical
reason - the thing that drives an engine should know nothing about Alfred.

**The engine is replaceable, by construction.** A browser is one execution
technology (``BrowserArtifactRuntime``, over the real Chromium this machine
already has). A game engine is another; a physics or simulation library,
a headless renderer, a native runner, a future technology are others. Each is
a provider of this contract, selected by name at runtime like every other axis,
and none of them is the architecture. That is the whole point of putting the
contract here rather than letting one engine's API leak upward into a
capability: swapping the engine must never change what running an artifact
*means*, and must never touch the artifact, its identity or its version chain.

**Honest absence.** A provider that cannot run anything says so and returns
``ran=False`` with a real reason (see ``NullArtifactRuntime``). Nothing here
ever reports a successful run it did not observe - an unverified artifact must
be reported as unverified, which is the one answer this axis exists to make
possible.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class RuntimeObservation:
    """What actually happened when an artifact was run.

    ``ran`` is True only when the runtime genuinely loaded and executed the
    document. It says nothing about whether the result was *correct* - errors
    and observations are reported separately, so a caller can tell "it ran and
    threw" from "it never ran at all", which are different failures needing
    different answers.

    ``observations`` is an open mapping on purpose: what is worth counting
    depends on the engine and on the artifact ("canvas": 1, "controls": 3,
    "nodes": 412, "entities": 90). A closed schema here would have to be
    widened for every new engine, which is exactly the coupling this contract
    exists to avoid.
    """

    ran: bool
    detail: str
    title: str = ""
    errors: tuple[str, ...] = ()
    observations: Mapping[str, object] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        """Ran, and produced no errors. Not a judgement about correctness."""
        return self.ran and not self.errors

    def summary(self) -> str:
        """One honest line about the run, for a person to read."""
        if not self.ran:
            return f"did not run: {self.detail}"
        seen = ", ".join(f"{k}={v}" for k, v in sorted(self.observations.items()))
        if self.errors:
            return f"ran with {len(self.errors)} error(s): {'; '.join(self.errors)[:300]}" + (f" ({seen})" if seen else "")
        return f"ran cleanly{f' ({seen})' if seen else ''}"


class ArtifactRuntimeProvider(ABC):
    """Runs a document and reports what it observed. One method, on purpose."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The configured name this provider was selected under."""

    @property
    def describes(self) -> str:
        """What kind of artifact this runtime can run, for the Control Center."""
        return "runnable documents"

    @abstractmethod
    def run(self, *, document: str, label: str = "") -> RuntimeObservation:
        """Run ``document`` and return a real observation of the run.

        Never raises for an unavailable engine or a failing document: both are
        real findings and are returned as an observation. An exception here
        would mean the runtime itself is broken, not the artifact.
        """
