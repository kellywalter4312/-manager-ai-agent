"""Stage 17.22 - the agent's own identity reaches what it says.

Four probes against the real running system found the defect, and none of
them needed a browser:

    Alfred  "hello"        -> "Hello! What can I help you with?"
    Alfred  "who are you?" -> "I don't have a way to properly reason about
                              that yet ... (no reasoning capability is
                              configured)."
    Arthur  "hello"        -> six inbox subjects
    Arthur  "who are you?" -> the same six inbox subjects

The context pipeline was never broken. ``ManagerSkills`` builds every
``CapabilityRequest`` with the loaded ``AgentIdentity``, the ``UserProfile``,
the recognition and the roster, and all of it arrived. Three separate things
were wrong with what the receiving code did with it:

1. The conversational floor had the identity in hand and discarded it - its
   own docstring said "Personality is not read here" - so every agent in the
   system greeted with one anonymous string.
2. Nothing could answer "who are you" at all, and the fallback blamed the
   absent model. Naming yourself from a document that names you requires no
   reasoning; reporting a missing capability as a missing model is worse than
   either failing.
3. A specialist had no conversational floor whatsoever, so *every* message
   reaching it was domain work by construction.

This file pins the fix and, just as importantly, the boundaries it had to
respect: no canned-response library, no fabricated reasoning, no second
conversation system, no upward dependency from an agent to the orchestrator,
and Half Brain still honest about what it cannot do.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.core.contracts import Task
from src.manager.capabilities import MANAGER_CAPABILITIES, select_capability
from src.services.identity.introduction import (
    greeting_from_identity,
    identity_question,
    self_introduction,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Identity:
    """A stand-in with exactly the four fields both real identity types
    declare - which is the point of the structural protocol."""

    def __init__(self, name="Ada", role="", purpose="", description=""):
        self.name, self.role = name, role
        self.purpose, self.description = purpose, description


# ---------------------------------------------------------------------------
# 1. Recognising the question
# ---------------------------------------------------------------------------


class TestRecognisingAQuestionAboutIdentity:
    def test_the_real_phrasings_are_recognised(self):
        for text in (
            "who are you?", "who are you", "Who Are You?",
            "what is your name", "what's your name",
            "what do you do", "what can you do",
            "what are you", "what is your role",
            "introduce yourself", "tell me about yourself",
        ):
            assert identity_question(text), text

    def test_ordinary_requests_that_merely_contain_the_words_are_not(self):
        """The exact failure a bag-of-words score would produce here."""
        for text in (
            "who should I send this to",
            "what do you think about the Harborview role",
            "tell me about the inbox",
            "check my email",
            "",
        ):
            assert not identity_question(text), text

    def test_a_long_request_that_opens_with_the_phrase_is_not_claimed(self):
        """Real work with a turn of phrase in front of it. Same cap, same
        reason, conversational_intent already applies to "thanks"."""
        assert not identity_question(
            "who are you going to delegate the quarterly review to, and what "
            "do you do about the ones that come back failed"
        )


# ---------------------------------------------------------------------------
# 2. Composing the answer from the document, and only from the document
# ---------------------------------------------------------------------------


class TestTheAnswerIsComposedNeverInvented:
    def test_every_content_word_comes_from_the_identity(self):
        text = self_introduction(
            _Identity(name="Ada", role="Scheduler and planner",
                      description="Keeps a calendar and warns about clashes.")
        )
        assert "Ada" in text
        assert "scheduler and planner" in text
        assert "Keeps a calendar and warns about clashes" in text

    def test_a_document_that_declares_only_a_name_still_answers_truthfully(self):
        text = self_introduction(_Identity(name="Ada"))
        assert text == "I'm Ada."

    def test_a_purpose_stands_in_when_role_and_description_are_absent(self):
        text = self_introduction(_Identity(name="Ada", purpose="To watch one inbox."))
        assert "To watch one inbox" in text

    def test_nothing_to_say_is_reported_as_nothing_rather_than_padded(self):
        assert self_introduction(None) is None
        assert self_introduction(_Identity(name="   ")) is None
        assert greeting_from_identity(_Identity(name="")) is None

    def test_the_gloss_after_a_dash_is_left_out_of_the_opening_clause(self):
        """These documents write "Role - the long explanation of the role".
        An introduction wants the role, not the essay."""
        text = greeting_from_identity(
            _Identity(name="Ada", role="Scheduler - the agent that owns the diary "
                                       "and is accountable for every clash in it")
        )
        assert text == "Ada here - scheduler. What do you need?"

    def test_a_greeting_is_a_greeting_and_not_a_biography(self):
        text = greeting_from_identity(
            _Identity(name="Ada", role="Scheduler and planner",
                      description="Keeps a calendar and warns about clashes.")
        )
        assert "Keeps a calendar" not in text
        assert len(text) < 120

    def test_no_router_vocabulary_is_ever_recited_at_a_person(self):
        """The first attempt ended every introduction with the agent's own
        real capability set. Honest, and unreadable: those are router match
        words, so the orchestrator introduced itself with fifty-seven of
        them. Backend vocabulary is not product language."""
        text = self_introduction(
            _Identity(name="Ada", role="Scheduler"),
            capabilities=("afternoon", "alright", "appreciate", "awesome", "bring"),
        )
        for word in ("afternoon", "alright", "appreciate", "awesome", "bring"):
            assert word not in text, word

    def test_it_holds_no_table_of_agents(self):
        """Generic over an identity, exactly as the interface layer is: a
        third agent introduces itself correctly with nothing added here."""
        source = (REPO_ROOT / "src/services/identity/introduction.py").read_text("utf-8")
        for name in ("Alfred", "Arthur"):
            assert name not in source, name
        # and it reaches for nothing it was not handed
        tree = ast.parse(source)
        imported = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert imported <= {"__future__", "typing"}, imported


# ---------------------------------------------------------------------------
# 3. The orchestrator answers as itself
# ---------------------------------------------------------------------------


class TestTheOrchestratorsOwnHandlers:
    def test_a_question_about_identity_reaches_the_identity_handler(self):
        for text in ("who are you?", "what is your name", "introduce yourself"):
            assert select_capability(Task(description=text)).name == "about_self", text

    def test_it_claims_by_phrase_and_declares_almost_no_words(self):
        """The first version declared "who", "name" and "role" as scoring
        words too, and scoring - which runs for everything the claim does
        not catch - then handed "who should I send this to" to the identity
        handler on a one-word overlap. Measured, not reasoned about."""
        assert select_capability(
            Task(description="who should I send this to")
        ).name != "about_self"
        capability = MANAGER_CAPABILITIES["about_self"]
        assert capability.capabilities == frozenset({"yourself", "introduce"})
        assert capability.claims is not None

    def test_it_never_consults_a_model(self):
        """The whole point: this is answerable with no reasoning at all, and
        the old fallback claiming otherwise was the sharpest evidence that
        identity was not reaching the product."""
        import inspect

        from src.manager.capabilities import describe_self

        # The body only - a docstring that explains why no model is
        # consulted necessarily contains the words "brain" and "model", and
        # a check that trips over its own explanation is a bad check.
        tree = ast.parse(inspect.getsource(describe_self).lstrip())
        function = tree.body[0]
        if (function.body and isinstance(function.body[0], ast.Expr)
                and isinstance(function.body[0].value, ast.Constant)):
            function.body = function.body[1:]
        body = ast.unparse(function).lower()
        for forbidden in ("brain", "deliberate", "provider", "llm", "model"):
            assert forbidden not in body, forbidden


# ---------------------------------------------------------------------------
# 4. A specialist stands on the same floor - not a second one
# ---------------------------------------------------------------------------


class TestTheSpecialistFloor:
    def test_the_vocabulary_lives_in_one_place_now(self):
        """Copying the greeting words down into the agent layer would have
        created two lists that drift; importing the orchestrator's
        capability module into an agent would point a dependency upward.
        Neither happened - the shared part moved into services, which both
        layers already depend on."""
        floor = (REPO_ROOT / "src/services/conversation_floor.py").read_text("utf-8")
        assert "def conversational_intent(" in floor
        capabilities = (REPO_ROOT / "src/manager/capabilities.py").read_text("utf-8")
        assert "from src.services.conversation_floor import (" in capabilities
        # and the orchestrator's own names still resolve, so nothing that
        # already read them had to change
        from src.manager.capabilities import (  # noqa: F401
            _CONVERSATIONAL_WORDS,
            _GREETING_WORDS,
            _THANKS_WORDS,
            conversational_intent,
        )

    def test_no_agent_imports_the_orchestrator(self):
        """The layering guard tests/test_architecture_audit.py exists partly
        to protect."""
        for rel in ("src/agents/cognitive_agent.py", "src/agents/email_agent.py"):
            source = (REPO_ROOT / rel).read_text("utf-8")
            assert "src.manager" not in source, rel

    def test_the_floor_is_on_the_shared_base_not_on_one_specialist(self):
        """So a second specialist gets it without a line changing."""
        from src.agents.cognitive_agent import CognitiveAgent

        assert hasattr(CognitiveAgent, "conversational_reply")

    def test_domain_work_still_falls_straight_through(self):
        """The floor returns None for anything that is not conversation, so
        the specialist's own execute() continues exactly as before - which
        is the only reason this could be added ahead of it."""
        email_agent = (REPO_ROOT / "src/agents/email_agent.py").read_text("utf-8")
        body = email_agent.split("def execute(self, task: Task) -> TaskResult:", 1)[1]
        floor_at = body.index("self.conversational_reply(task)")
        assert floor_at < body.index("self._mailbox.fetch")
        assert "if spoken is not None:" in body


# ---------------------------------------------------------------------------
# 5. Half Brain is still honest
# ---------------------------------------------------------------------------


class TestHonestyIsUnchanged:
    def test_nothing_here_fabricates_reasoning(self):
        """Saying who you are is not reasoning, and this pass did not turn
        the deterministic floor into a pretend one: a real question that
        needs a model still says so."""
        result = select_capability(Task(description="what should I do about the offer"))
        assert result.name != "about_self"

    def test_no_response_library_was_created(self):
        """The whole fix is one renderer over loaded data. If a table of
        replies had been added, it would be here."""
        source = (REPO_ROOT / "src/services/identity/introduction.py").read_text("utf-8")
        tree = ast.parse(source)
        literals = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.List, ast.Dict)) and len(getattr(node, "elts", getattr(node, "keys", []))) > 12
        ]
        assert not literals, "a response table appeared in the renderer"
