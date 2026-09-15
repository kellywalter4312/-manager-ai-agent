"""Choosing the translation engine and assembling the interpreter.

``translation`` is its own axis ("argos" | "null"), chosen through the Control
Center category ``translation_provider``. Hearing comes from the transcription
axis (``video_understanding_provider``) when its provider can recognise speech
and name its language - one listener, never a second copy of a model.
"""

from __future__ import annotations

from typing import Any

from src.providers.translation.offline import OfflineTranslator
from src.providers.translation.voice import MultilingualVoice
from src.services.translation.interpreter import Interpreter


def available_translation_backends() -> "list[str]":
    return ["argos", "null"]


def build_translator(settings: Any, *, strict: bool = False) -> "OfflineTranslator | None":
    choice = str(getattr(settings, "translation", "null") or "null").strip().lower()
    if choice == "null":
        return None
    if choice != "argos":
        raise ValueError(f"Unknown translation backend {choice!r}. Available: argos, null")
    try:
        translator = OfflineTranslator.from_installed()
    except ImportError as exc:
        if strict:
            raise ValueError(f"argostranslate is not installed ({exc})") from exc
        return None
    if not translator.pairs():
        if strict:
            raise ValueError("no offline language packs are installed")
        return None
    return translator


def build_interpreter(settings: Any, *, transcriber: Any = None) -> Interpreter:
    translator = build_translator(settings)
    listener = transcriber if callable(getattr(transcriber, "recognise", None)) else None
    voice = MultilingualVoice.if_installed() if translator is not None else None
    return Interpreter(translator=translator, listener=listener, voice=voice)
