"""The local assistant - a small model on this machine that capabilities may
consult for what deterministic Half Brain cannot do alone.

**Not the Manager's brain.** Routing, memory, acknowledgements, governance and
every deterministic answer stay with Half Brain. This assistant is asked only
for a bounded piece of language work - answer an open question, summarise or
explain given text, write code to a stated specification, describe an image -
and its words are always returned with ``ATTRIBUTION`` so a person knows a
small local model produced them. It is slower and less capable than a
vendor-backed model and is never presented as one.

**Off by default.** ``Settings.local_assist`` is "null" unless the owner
chooses "local" (Control Center: ``provider local_assist_provider use local``);
with no model file installed the assistant is simply absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ATTRIBUTION = "answered by the small local model on this machine - not verified reasoning"
_SYSTEM = (
    "You are a careful assistant running as a small local model. Answer briefly and "
    "plainly. If you do not know, say you do not know. Never invent personal facts "
    "about the user."
)


class LocalAssistant:
    """Bounded language help from a local ``LLMProvider``-shaped model."""

    name = "local"

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    @property
    def provider(self) -> Any:
        return self._provider

    def _ask(self, prompt: str, *, system: str = _SYSTEM) -> "str | None":
        try:
            completion = self._provider.complete(prompt, system=system)
        except Exception:  # noqa: BLE001 - a local model failing is an honest absence
            return None
        text = (getattr(completion, "text", "") or "").strip()
        return text or None

    def answer(self, question: str, *, context: "tuple[str, ...]" = ()) -> "str | None":
        notes = "\n".join(f"- {line}" for line in context if str(line).strip())
        prompt = (f"Context you may use:\n{notes}\n\n" if notes else "") + f"Question: {question.strip()}\nAnswer:"
        return self._ask(prompt)

    def summarise(self, text: str, *, focus: str = "") -> "str | None":
        body = text.strip()[:6000]
        ask = f"Summarise the following in a few sentences{(' focusing on ' + focus) if focus else ''}:"
        return self._ask(f"{ask}\n\n{body}\n\nSummary:")

    def explain(self, text: str, *, question: str = "") -> "str | None":
        body = text.strip()[:6000]
        ask = question.strip() or "Explain this plainly."
        return self._ask(f"{ask}\n\nText:\n{body}\n\nExplanation:")

    def write_code(self, specification: str, *, language: str = "python") -> "str | None":
        return self._ask(
            f"Write {language} code for this request. Reply with only the code in one fenced block.\n\n"
            f"Request: {specification.strip()}",
            system="You write small, correct, well-commented code. Reply with only a fenced code block.",
        )

    def describe_image(self, image: bytes, prompt: str) -> "str | None":
        analyze = getattr(self._provider, "analyze_image", None)
        if analyze is None:
            return None
        try:
            completion = analyze(image, prompt)
        except Exception:  # noqa: BLE001
            return None
        text = (getattr(completion, "text", "") or "").strip()
        return text or None


def available_local_assist_backends() -> "list[str]":
    return ["local", "null"]


def build_local_assistant(settings: Any) -> "LocalAssistant | None":
    """The assistant ``settings.local_assist`` names, or None - "null", or a
    local model file that is not installed, means no assistant at all."""
    choice = str(getattr(settings, "local_assist", "null") or "null").strip().lower()
    if choice == "null":
        return None
    if choice != "local":
        raise ValueError(f"Unknown local assist backend {choice!r}. Available: local, null")
    if not Path(settings.local_model_path).is_file():
        return None
    from src.providers.local_llm_provider import LocalLLMProvider

    return LocalAssistant(LocalLLMProvider(
        model_path=settings.local_model_path, max_tokens=320,
        vision_model_path=settings.local_vision_model_path,
        vision_projector_path=settings.local_vision_projector_path,
    ))
