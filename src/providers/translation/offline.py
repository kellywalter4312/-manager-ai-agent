"""Offline text translation over installed Argos Translate language packs.

**Why this owns the pipeline instead of calling ``argostranslate.translate``.**
Measured on this machine (2026-09-15):

- Argos's sentence splitter downloads a Stanza model the first time a pack is
  used, so "offline" failed without a network;
- its BPE tokenizer runs Moses with HTML escaping, which turned Spanish->English
  (an OPUS-MT BPE pack) into an endless "mainstremainstre...".

The same model, tokenised unescaped and decoded with bounded length, translated
correctly. So Argos is used only to *find* installed packs; each pack's own
tokenizer (SentencePiece, or Moses + BPE unescaped) and CTranslate2 model are
driven here, sentences are split deterministically, and a language pair with no
direct pack pivots through English.

**Honest limits.** Quality is that of small offline models: good for ordinary
sentences, weaker for idiom, names and long context, and a pivot compounds
errors. Nothing here reaches a network.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PIVOT = "en"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])")


class TranslationError(RuntimeError):
    """Translation could not be produced."""


@dataclass(frozen=True)
class Translation:
    text: str
    source: str
    target: str
    route: tuple[str, ...]


def split_sentences(text: str) -> "list[str]":
    parts = [part.strip() for part in _SENTENCE_END.split(text.strip()) if part and part.strip()]
    return parts or [text.strip()]


class LanguagePack:
    """One installed pack: a direction, its tokenizer and its model - loaded on
    first use, then reused."""

    def __init__(self, path: Path) -> None:
        meta = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        self.path = path
        self.source = str(meta["from_code"]).lower()
        self.target = str(meta["to_code"]).lower()
        self.target_prefix = str(meta.get("target_prefix") or "")
        if (path / "sentencepiece.model").exists():
            self.kind = "sentencepiece"
        elif (path / "bpe.model").exists():
            self.kind = "bpe"
        else:
            raise TranslationError(f"the language pack at {path} has no tokenizer")
        self._lock = threading.Lock()
        self._model = None
        self._encoder = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import ctranslate2

        if self.kind == "sentencepiece":
            import sentencepiece

            processor = sentencepiece.SentencePieceProcessor(model_file=str(self.path / "sentencepiece.model"))
            self._encoder = ("sentencepiece", processor)
        else:
            from argostranslate.apply_bpe import BPE
            from sacremoses.normalize import MosesPunctNormalizer
            from sacremoses.tokenize import MosesDetokenizer, MosesTokenizer

            with open(self.path / "bpe.model", encoding="utf-8") as handle:
                bpe = BPE(handle)
            self._encoder = (
                "bpe", (MosesPunctNormalizer(self.source), MosesTokenizer(self.source), MosesDetokenizer(self.target), bpe)
            )
        self._model = ctranslate2.Translator(str(self.path / "model"), device="cpu", intra_threads=2)

    def _encode(self, sentence: str) -> "list[str]":
        kind, parts = self._encoder
        if kind == "sentencepiece":
            return parts.encode(sentence, out_type=str)
        normalizer, tokenizer, _detokenizer, bpe = parts
        return bpe.segment_tokens(tokenizer.tokenize(normalizer.normalize(sentence), escape=False))

    def _decode(self, tokens: "list[str]") -> str:
        kind, parts = self._encoder
        if kind == "sentencepiece":
            return parts.decode_pieces(tokens).replace("▁", " ").strip()
        _normalizer, _tokenizer, detokenizer, _bpe = parts
        words = " ".join(tokens).replace("@@ ", "").replace("@@", "").split()
        return detokenizer.detokenize(words)

    def translate(self, text: str) -> str:
        with self._lock:
            self._load()
            batch = [self._encode(sentence) for sentence in split_sentences(text)]
            prefix = [[self.target_prefix]] * len(batch) if self.target_prefix else None
            results = self._model.translate_batch(
                batch, target_prefix=prefix, beam_size=2, replace_unknowns=True,
                max_decoding_length=256, repetition_penalty=1.1, no_repeat_ngram_size=4,
            )
            sentences = []
            for result in results:
                tokens = list(result.hypotheses[0])
                if prefix and tokens and tokens[0] == self.target_prefix:
                    tokens = tokens[1:]
                sentences.append(self._decode(tokens))
        return " ".join(sentence for sentence in sentences if sentence)


def installed_pack_paths() -> "list[Path]":
    """Where Argos installed its packs - read from Argos itself, locally."""
    import argostranslate.package

    return [Path(package.package_path) for package in argostranslate.package.get_installed_packages()]


class OfflineTranslator:
    name = "argos"

    def __init__(self, pack_paths: Iterable[Path]) -> None:
        packs: dict[tuple[str, str], LanguagePack] = {}
        for path in pack_paths:
            try:
                pack = LanguagePack(Path(path))
            except (OSError, ValueError, KeyError, TranslationError):
                continue
            packs[(pack.source, pack.target)] = pack
        self._packs = packs

    @classmethod
    def from_installed(cls) -> "OfflineTranslator":
        return cls(installed_pack_paths())

    def pairs(self) -> "tuple[tuple[str, str], ...]":
        return tuple(sorted(self._packs))

    def languages(self) -> "tuple[str, ...]":
        return tuple(sorted({code for pair in self._packs for code in pair}))

    def can_translate(self, source: str, target: str) -> bool:
        if source == target or (source, target) in self._packs:
            return True
        return (source, PIVOT) in self._packs and (PIVOT, target) in self._packs

    def translate(self, text: str, source: str, target: str) -> Translation:
        words = str(text or "").strip()
        if not words:
            raise TranslationError("there is nothing to translate")
        if source == target:
            return Translation(words, source, target, (source,))
        if (source, target) in self._packs:
            return Translation(self._packs[(source, target)].translate(words), source, target, (source, target))
        if self.can_translate(source, target):
            middle = self._packs[(source, PIVOT)].translate(words)
            return Translation(self._packs[(PIVOT, target)].translate(middle), source, target, (source, PIVOT, target))
        raise TranslationError(f"no installed language pack translates {source} into {target}")
