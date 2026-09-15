"""A local language model behind the same ``LLMProvider`` seam - llama.cpp
running a small GGUF model on this machine, with no vendor, key, account or
network at inference time.

**What this is, and what it is not.** A small local model (the default is
Qwen2.5-1.5B-Instruct, 4-bit) genuinely produces text: it can rephrase,
summarise, explain, draft and write code at a modest level. It is **not**
equivalent to a frontier model reached through a funded vendor account - it is
slower (seconds per answer on a 2-core laptop), knows less, and makes more
mistakes. It is offered as an honest local tier, named ``local`` everywhere it
appears, never presented as the same thing as a vendor-backed Full Brain.

**Vision.** When a Moondream2 GGUF pair (text model + multimodal projector) is
present, ``describe_image`` answers a question about an image with the same
local runtime. Absent files are reported as unavailable - never a guessed
description.

**Construction is inert.** Nothing is loaded until the first call; a model file
that is missing or unreadable raises ``ProviderConfigurationError``-shaped
errors at that point, reported through the same boundaries every provider uses.
"""

from __future__ import annotations

import base64
import threading
from pathlib import Path
from typing import Any

from src.providers.base import Completion, LLMProvider, ProviderResponseError

DEFAULT_LOCAL_MODEL_PATH = "data/models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf"
DEFAULT_LOCAL_VISION_MODEL_PATH = "data/models/vision/moondream2-text-model-f16.gguf"
DEFAULT_LOCAL_VISION_PROJECTOR_PATH = "data/models/vision/moondream2-mmproj-f16.gguf"
DEFAULT_LOCAL_MAX_TOKENS = 512
DEFAULT_LOCAL_CONTEXT = 4096

_LOAD_LOCK = threading.Lock()
_SHARED: "dict[tuple, Any]" = {}


class LocalModelUnavailable(RuntimeError):
    """The local model cannot run here (runtime not installed, file missing)."""


def _llama_class():
    try:
        from llama_cpp import Llama
    except ImportError as exc:  # pragma: no cover - environment specific
        raise LocalModelUnavailable(
            "the local model runtime (llama-cpp-python) is not installed"
        ) from exc
    return Llama


def _shared_llama(key: tuple, build) -> Any:
    """One loaded model per file per process - loading a GGUF is the expensive
    part, and every provider instance (the Manager's, a rebound one, a
    specialist's) should share it rather than hold several copies in RAM."""
    with _LOAD_LOCK:
        if key not in _SHARED:
            _SHARED[key] = build()
        return _SHARED[key]


class LocalLLMProvider(LLMProvider):
    """Text completions from a local GGUF model through llama.cpp."""

    def __init__(
        self,
        model_path: str = DEFAULT_LOCAL_MODEL_PATH,
        *,
        max_tokens: int = DEFAULT_LOCAL_MAX_TOKENS,
        context_tokens: int = DEFAULT_LOCAL_CONTEXT,
        threads: "int | None" = None,
        vision_model_path: str = DEFAULT_LOCAL_VISION_MODEL_PATH,
        vision_projector_path: str = DEFAULT_LOCAL_VISION_PROJECTOR_PATH,
        client: Any = None,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self._model_path = str(model_path)
        self._max_tokens = max_tokens
        self._context_tokens = context_tokens
        self._threads = threads
        self._vision_model_path = str(vision_model_path)
        self._vision_projector_path = str(vision_projector_path)
        self._client = client

    @property
    def name(self) -> str:
        return "local"

    @property
    def model(self) -> str:
        return Path(self._model_path).name

    @property
    def model_path(self) -> str:
        return self._model_path

    def is_installed(self) -> bool:
        return Path(self._model_path).is_file()

    def vision_installed(self) -> bool:
        return Path(self._vision_model_path).is_file() and Path(self._vision_projector_path).is_file()

    def _llm(self) -> Any:
        if self._client is not None:
            return self._client
        path = Path(self._model_path)
        if not path.is_file():
            raise LocalModelUnavailable(f"the local model file is not installed at '{path}'")
        Llama = _llama_class()
        return _shared_llama(
            ("text", str(path.resolve()), self._context_tokens),
            lambda: Llama(
                model_path=str(path), n_ctx=self._context_tokens, n_threads=self._threads,
                verbose=False,
            ),
        )

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._llm().create_chat_completion(
            messages=messages, max_tokens=self._max_tokens, temperature=0.3,
        )
        try:
            choice = response["choices"][0]
            text = choice["message"]["content"] or ""
            usage = response.get("usage") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(f"the local model returned an unreadable response: {exc}") from exc
        return Completion(
            text=str(text).strip(), provider=self.name, model=self.model,
            input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
        )

    def analyze_image(self, image_bytes: bytes, prompt: str, *, media_type: str = "image/png") -> Completion:
        """Answer ``prompt`` about the image with the local vision model - the
        same additive, beside-the-contract method a vision-capable vendor
        provider offers, found by ``LLMManagerBrain.analyze_image`` through
        ``hasattr``. Raises ``LocalModelUnavailable`` when the vision files are
        not installed, so the Brain reports an honest no-view."""
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        image, question = image_bytes, prompt
        if not image:
            raise ValueError("image_bytes must not be empty")
        if not self.vision_installed():
            raise LocalModelUnavailable(
                "the local vision model is not installed "
                f"(expected '{self._vision_model_path}' and '{self._vision_projector_path}')"
            )
        Llama = _llama_class()
        try:
            from llama_cpp.llama_chat_format import MoondreamChatHandler
        except ImportError as exc:  # pragma: no cover
            raise LocalModelUnavailable("this llama-cpp-python build has no Moondream support") from exc

        def build():
            handler = MoondreamChatHandler(clip_model_path=self._vision_projector_path, verbose=False)
            return Llama(
                model_path=self._vision_model_path, chat_handler=handler, n_ctx=2048,
                n_threads=self._threads, verbose=False,
            )

        vision = _shared_llama(("vision", str(Path(self._vision_model_path).resolve())), build)
        data_url = f"data:{media_type};base64,{base64.b64encode(image).decode()}"
        response = vision.create_chat_completion(
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": question},
            ]}],
            max_tokens=256, temperature=0.2,
        )
        try:
            text = response["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(f"the local vision model returned an unreadable response: {exc}") from exc
        return Completion(text=str(text).strip(), provider=self.name, model=Path(self._vision_model_path).name)
