"""Post-Pre-Stage-9 D3b - universal content in Half Brain.

A supplied document is explained or summarised by the attributed local
assistant over its own passages when no Brain can answer, and translated as a
whole document offline. In-process doubles only; the real models are verified
outside the suite.
"""

from __future__ import annotations

from dataclasses import dataclass

import src.manager.capabilities as capabilities
from src.core.contracts import Task
from src.manager.capabilities import CapabilityRequest, _knowledge_with_local_assist, translate
from src.manager.knowledge import KnowledgeEvidence
from src.services.artifacts.content import ContentUnit
from src.services.identity.identity import AgentIdentity


class FakeAssist:
    def __init__(self):
        self.explained = []
        self.answered = []

    def explain(self, text, *, question=""):
        self.explained.append((text, question))
        return "It is a lunch menu with three dishes."

    def answer(self, question, *, context=()):
        self.answered.append(question)
        return "Rome was founded, by tradition, in 753 BC."


@dataclass(frozen=True)
class _Result:
    text: str
    source: str
    target: str
    route: tuple


def _request(description, **fields):
    return CapabilityRequest(
        task=Task(description=description),
        identity=AgentIdentity(name="Alfred", capabilities=frozenset({"knowledge"})),
        roster=lambda: (), own_name="Alfred", own_capabilities=(), **fields,
    )


class TestKnowledgeInHalfBrain:
    def test_a_supplied_document_is_explained_from_its_own_passages_and_attributed(self):
        assist = FakeAssist()
        evidence = KnowledgeEvidence(notes=("Menu: soup, pasta, cake.",), sources=("supplied_content",))
        result = _knowledge_with_local_assist(_request("summarize this document", assist=assist), evidence)
        assert result.succeeded and result.output.startswith("It is a lunch menu")
        assert "small local model" in result.output
        assert assist.explained[0] == ("Menu: soup, pasta, cake.", "summarize this document")
        assert result.payload["sources"] == ["supplied_content"] and result.payload["assisted"] is True

    def test_with_no_evidence_a_knowledge_question_is_answered_and_attributed(self):
        assist = FakeAssist()
        result = _knowledge_with_local_assist(_request("tell me about the history of Rome", assist=assist), KnowledgeEvidence())
        assert result.succeeded and "753 BC" in result.output and assist.answered

    def test_without_an_assistant_nothing_is_invented(self):
        assert _knowledge_with_local_assist(_request("summarize this document"), KnowledgeEvidence(notes=("x",))) is None


class TestTranslatingADocument:
    def test_a_document_reference_translates_the_documents_own_words(self, monkeypatch):
        monkeypatch.setattr(
            capabilities, "resolve_content_sources",
            lambda *a, **k: [({"title": "Menú del día"}, (ContentUnit("page 1", "Sopa. Pasta. Tarta."),))],
        )
        seen = []

        def offline(text, target, source=None):
            seen.append((text, target))
            return _Result("Soup. Pasta. Cake.", "es", "en", ("es", "en"))

        result = translate(_request("translate this document into English", translate=offline))
        assert seen == [("Sopa. Pasta. Tarta.", "English")]
        assert result.output.startswith("Translation of 'Menú del día' into English:") and "Soup. Pasta. Cake." in result.output
        assert result.payload["document"] == "Menú del día"

    def test_a_document_reference_with_nothing_shared_asks_which_document(self, monkeypatch):
        monkeypatch.setattr(capabilities, "resolve_content_sources", lambda *a, **k: [])
        result = translate(_request("translate this file into Spanish", translate=lambda *a: None))
        assert not result.succeeded and "Which document?" in result.error

    def test_an_ordinary_phrase_is_still_translated_as_a_phrase(self, monkeypatch):
        monkeypatch.setattr(capabilities, "resolve_content_sources", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
        result = translate(_request(
            "translate good morning into Spanish",
            translate=lambda text, target, source=None: _Result("Buenos días", "en", "es", ("en", "es")),
        ))
        assert result.output == "Buenos días"
