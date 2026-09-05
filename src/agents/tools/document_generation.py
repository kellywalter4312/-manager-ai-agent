"""The governed boundary between Alfred and a real, saved text document.

**A real file, never a placeholder reply.** "Create a report from these
findings" (Section 6's own example) means something different from an
ordinary conversational answer: the person wants a real, persisted
artifact they can open again later, the identical distinction
``MediaProductionTool`` already draws between an ordinary reply and a
real produced asset. This tool is that same governed production
boundary, applied to text - content-addressed, written once, and
returned by path exactly as ``MediaProductionTool._write`` already
works for narration/image/video.

**The Brain composes. This only writes.** Identical discipline to every
other ``Tool`` in this project: this class has no route to a
conversation, an objective, or a reasoning step - it is handed finished
text and a title, and it writes exactly that, nothing synthesised here.
Composing the document's actual content is the capability handler's job
(``src/manager/capabilities.py``), through the real Brain, the same
"acquire/compose, then this tool only persists" separation
``MediaProductionTool`` already keeps between a provider's own output
and the write.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from src.agents.tools.tool import Tool, ToolSpec

DEFAULT_DOCUMENTS_DIR = Path("data") / "documents" / "generated"

_UNSAFE_TITLE_CHARS = re.compile(r"[^\w\- ]+")


class DocumentGenerationTool(Tool):
    """Writes finished text to a real, real Markdown file and returns
    its path - the whole of what this tool does."""

    def __init__(self, *, output_dir: "Path | None" = None) -> None:
        self._output_dir = output_dir or DEFAULT_DOCUMENTS_DIR

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="document_generation",
            description=(
                "Saves finished text as a real document file and returns its path - "
                "never a placeholder reply standing in for a real generated artifact."
            ),
            schema={"title": str, "content": str},
        )

    def run(self, **arguments: str) -> str:
        title = str(arguments["title"]).strip()
        content = str(arguments["content"]).strip()
        if not title:
            raise ValueError("title must not be empty")
        if not content:
            raise ValueError("content must not be empty")

        self._output_dir.mkdir(parents=True, exist_ok=True)

        safe_title = _UNSAFE_TITLE_CHARS.sub("", title).strip().replace(" ", "_") or "document"
        # Content-addressed, the identical reason MediaProductionTool._write
        # already is: the same title and content never produce two
        # different files, and a genuine edit produces a genuinely new one.
        digest = hashlib.sha256((title + "\n" + content).encode("utf-8")).hexdigest()[:12]
        path = self._output_dir / f"{safe_title}_{digest}.md"

        if not path.exists():
            path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")

        return f"Document produced: {path.as_posix()} ({len(content)} chars)"
