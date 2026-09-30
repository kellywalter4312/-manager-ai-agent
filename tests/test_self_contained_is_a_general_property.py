"""Is `self_contained` a general capability property, or did it merely encode
the capabilities that happen to exist today?

The distinction this file exists to settle:

    ARCHITECTURE            `ManagerCapability.self_contained` - a declared
                            property, validated once, read in exactly one
                            place (`ManagerSkills._a_capability_claims`)
    CURRENT REGISTRY DATA   which of today's capabilities declare it. A count
                            and a list; not a category, not a rule
    VALIDATION FIXTURE      the capabilities and the specialist below, which
                            exist only in this file
    RUNTIME VERIFICATION    the decisions asserted here, taken by the real
                            `ManagerAgent.decide` / `collaboration_candidates`

Everything below introduces capabilities and a domain that exist **nowhere in
the shipped tree** and are registered by injection, never by mutating the
shipped registry - the identical discipline
`tests/test_manager_capabilities.py`'s own `WEATHER` fixture already follows,
"so no test can leak a capability into another - and so the project keeps
shipping exactly what it shipped."

No production file is modified by these tests, no routing branch is added, and
no agent name or capability name appears in any routing decision. If the
mechanism were really "the 33 names", every assertion here would fail.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src.core.contracts import Task, TaskResult
from src.manager.capabilities import (
    MANAGER_CAPABILITIES,
    OPEN_CONVERSATION,
    CapabilityRequest,
    ManagerCapability,
)
from src.manager.collaboration_entry import collaboration_candidates
from src.agents.echo_agent import EchoAgent
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.services.identity.identity import AgentIdentity

REPO_ROOT = Path(__file__).resolve().parent.parent

IDENTITY = AgentIdentity(
    name="Alfred",
    role="Orchestrator",
    description="Coordinates work.",
    # The Manager's AgentSpec is the intersection of these words with what
    # the injected registry actually answers, so these are the fallback
    # capability's own words - present in every registry built below.
    capabilities=frozenset({"explain", "teach", "discuss"}),
)


# ---------------------------------------------------------------------------
# Two capabilities and one domain that exist only here
# ---------------------------------------------------------------------------
#
# "tessellation" and "marginalia" appear nowhere in src/, scripts/ or ui/ -
# asserted below rather than claimed.

TESSELLATION_WORDS = frozenset({"tessellation", "tiling"})
MARGINALIA_WORDS = frozenset({"marginalia", "colophon"})


def _claims_words(words: frozenset[str]):
    def claims(task: Task, participants=()) -> bool:
        spoken = {w.strip(".,?!").lower() for w in str(task.description).split()}
        return bool(words & spoken)
    return claims


def _handle(request: CapabilityRequest) -> TaskResult:
    return TaskResult.success(request.task, request.own_name, "done")


def _capability(name: str, words: frozenset[str], **declared) -> ManagerCapability:
    return ManagerCapability(
        name=name, capabilities=words, handle=_handle,
        description="Exists only inside this test file.",
        claims=_claims_words(words), **declared,
    )


# The new capability, declaring itself the Manager's own work through the
# generic contract - nothing else about it is special.
OWNED = _capability(
    "tessellation_report", TESSELLATION_WORDS,
    self_contained=True,
    self_contained_reason="a fixture capability proving the declaration is what decides ownership",
)
# The identical capability, declaring nothing.
OPEN_TO_DELEGATION = _capability("tessellation_report", TESSELLATION_WORDS)
# A second unrelated new capability, to show one fixture is not the mechanism.
SECOND_OWNED = _capability(
    "marginalia_report", MARGINALIA_WORDS,
    self_contained=True,
    self_contained_reason="a second, unrelated fixture capability on the identical seam",
)
SECOND_OPEN = _capability("marginalia_report", MARGINALIA_WORDS)


def _Specialist(name: str, words) -> EchoAgent:
    """A real registered agent with its own declared capability words - the
    project's own `EchoAgent`, whose whole purpose is exactly this. No name
    used here appears in any production file."""
    return EchoAgent(name=name, capabilities=frozenset(words))


def _manager(capability: ManagerCapability, specialists=()) -> ManagerAgent:
    """A real ManagerAgent whose registry holds one injected capability plus
    the fallback - never the shipped registry."""
    manager = ManagerAgent()
    registry = {capability.name: capability, OPEN_CONVERSATION.name: OPEN_CONVERSATION}
    manager.own_skills = ManagerSkills(IDENTITY, roster=manager.registry.all_agents, capabilities=registry)
    manager.registry.register(manager.own_skills)
    for specialist in specialists:
        manager.register_agent(specialist)
    return manager


# ---------------------------------------------------------------------------
# 0. The words really are new
# ---------------------------------------------------------------------------


class TestTheFixtureIsGenuinelyUnseen:
    @pytest.mark.parametrize("word", ["tessellation", "tiling", "marginalia", "colophon"])
    def test_no_production_file_mentions_it(self, word):
        """Whole words only. "annotation" is a substring of `annotations`
        (`from __future__ import annotations`), which is a Python future
        import and not a mention of this fixture's domain - a substring
        search here would assert something untrue."""
        pattern = re.compile(rf"\b{word}\b", re.IGNORECASE)
        for folder in ("src", "scripts", "ui"):
            for path in (REPO_ROOT / folder).rglob("*"):
                if path.suffix not in (".py", ".html", ".js", ".css") or "__pycache__" in str(path):
                    continue
                assert not pattern.search(path.read_text(encoding="utf-8", errors="replace")), path


# ---------------------------------------------------------------------------
# 1 + 3. A brand-new capability changes ownership purely by declaring it
# ---------------------------------------------------------------------------


class TestANewCapabilityOwnsItsWorkByDeclaringSo:
    REQUEST = "produce a tessellation of the courtyard"

    def test_declared_self_contained_the_manager_keeps_it(self):
        specialist = _Specialist("Fixturely", TESSELLATION_WORDS)
        manager = _manager(OWNED, [specialist])
        decision = manager.decide(Task(description=self.REQUEST))
        assert decision.agent_name == "Alfred", decision.reason
        assert manager.own_skills.owns(Task(description=self.REQUEST)) is True

    def test_not_declared_the_specialist_receives_it(self):
        """Same capability, same words, same specialist - only the declaration
        differs, and the decision follows it."""
        specialist = _Specialist("Fixturely", TESSELLATION_WORDS)
        manager = _manager(OPEN_TO_DELEGATION, [specialist])
        task = Task(description=self.REQUEST)
        assert manager.own_skills.claims(task) is True, "recognition is unchanged"
        assert manager.own_skills.owns(task) is False, "ownership is not"
        decision = manager.decide(task)
        assert decision.agent_name == "Fixturely", decision.reason

    def test_collaboration_follows_the_same_declaration(self):
        specialist = _Specialist("Fixturely", TESSELLATION_WORDS)
        task = Task(description=self.REQUEST)
        assert collaboration_candidates(_manager(OWNED, [specialist]), task) == ()
        assert collaboration_candidates(_manager(OPEN_TO_DELEGATION, [specialist]), task) == ("Fixturely",)

    @pytest.mark.parametrize("owned,open_,request_text", [
        (OWNED, OPEN_TO_DELEGATION, "produce a tessellation of the courtyard"),
        (SECOND_OWNED, SECOND_OPEN, "collect the marginalia from that volume"),
    ])
    def test_the_same_seam_works_for_a_second_unrelated_capability(self, owned, open_, request_text):
        specialist = _Specialist("Fixturely", owned.capabilities)
        task = Task(description=request_text)
        assert _manager(owned, [specialist]).decide(task).agent_name == "Alfred"
        assert _manager(open_, [specialist]).decide(task).agent_name == "Fixturely"


# ---------------------------------------------------------------------------
# 5. A genuinely new specialist receives a genuinely new capability
# ---------------------------------------------------------------------------


class TestANewSpecialistNeedsNoMentionAnywhere:
    def test_it_receives_new_work_with_no_routing_change(self):
        for name in ("Fixturely", "Ordinate", "Palisade"):
            specialist = _Specialist(name, TESSELLATION_WORDS)
            manager = _manager(OPEN_TO_DELEGATION, [specialist])
            decision = manager.decide(Task(description="produce a tessellation of the courtyard"))
            assert decision.agent_name == name, decision.reason

    def test_a_specialist_with_nothing_in_common_is_not_given_the_work(self):
        specialist = _Specialist("Fixturely", frozenset({"something", "unrelated"}))
        manager = _manager(OPEN_TO_DELEGATION, [specialist])
        decision = manager.decide(Task(description="produce a tessellation of the courtyard"))
        assert decision.agent_name == "Alfred", decision.reason

    def test_two_new_specialists_are_still_decided_by_the_one_router(self):
        near = _Specialist("Ordinate", TESSELLATION_WORDS)
        far = _Specialist("Palisade", frozenset({"unrelated"}))
        manager = _manager(OPEN_TO_DELEGATION, [near, far])
        decision = manager.decide(Task(description="produce a tessellation of the courtyard"))
        assert decision.agent_name == "Ordinate", decision.reason


# ---------------------------------------------------------------------------
# 2. Routing knows none of today's names, counts or domains
# ---------------------------------------------------------------------------


class TestRoutingKnowsNothingItShouldNot:
    ROUTING_SOURCES = (
        "src/manager/manager.py",
        "src/manager/manager_skills.py",
        "src/manager/collaboration_entry.py",
    )

    def _code(self, relative: str) -> str:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        return "\n".join(
            line.split("#", 1)[0] for line in text.splitlines()
            if not line.strip().startswith("#")
        )

    # The decision surface, named exactly. Not whole files: `manager_skills.py`
    # is also where the Manager's own equipment is bound (its calendar, its
    # knowledge source, its voice facts), so domain words legitimately appear
    # in that module as identifiers of things Alfred *has*. The property being
    # asserted is that no domain word takes part in deciding who does the work,
    # which is a claim about these functions.
    DECISION_SURFACE = (
        ("src/manager/manager.py", "decide"),
        ("src/manager/manager.py", "_authorised_for"),
        ("src/manager/manager.py", "_unclaimed_work_decision"),
        ("src/manager/manager_skills.py", "owns"),
        ("src/manager/manager_skills.py", "claims"),
        ("src/manager/manager_skills.py", "_a_capability_claims"),
        ("src/manager/manager_skills.py", "_session_in_progress"),
        ("src/manager/collaboration_entry.py", "collaboration_candidates"),
    )

    def _function(self, relative: str, name: str):
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        raise AssertionError(f"{relative} has no function {name!r} - the decision surface moved")

    def _tokens_of(self, relative: str, name: str) -> "list[str]":
        """Every name, attribute and string literal one decision function
        actually *executes* - its docstring excluded.

        The question is whether the decision depends on a domain word, not
        whether it is allowed to explain itself. These functions' docstrings
        legitimately discuss knowledge, media, diagrams and translation while
        describing the defect this mechanism repaired; a prose search would
        fail on the explanation rather than on the code, which is the opposite
        of the property being asserted.
        """
        return self._executable_tokens(self._function(relative, name))

    def _executable_strings_and_names(self, relative: str) -> "list[str]":
        return self._executable_tokens(ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8")))

    def _executable_tokens(self, tree) -> "list[str]":
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        found: "list[str]" = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                found.append(node.value)
            elif isinstance(node, ast.Name):
                found.append(node.id)
            elif isinstance(node, ast.Attribute):
                found.append(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.append(node.name)
            elif isinstance(node, ast.arg):
                found.append(node.arg)
        return found

    def test_no_capability_name_appears_as_a_literal(self):
        for relative in self.ROUTING_SOURCES:
            code = self._code(relative)
            for name in MANAGER_CAPABILITIES:
                assert f'"{name}"' not in code, f"{relative} names {name!r}"
                assert f"'{name}'" not in code, f"{relative} names {name!r}"

    @pytest.mark.parametrize("word", [
        "3d", "translation", "diagram", "media", "knowledge", "artifact", "voice",
        "video", "image", "document", "email", "gmail", "calendar", "german",
        "airpods", "seoul", "minecraft", "mario", "turtle", "kokoro", "anthropic",
    ])
    def test_no_current_domain_or_vendor_word_appears(self, word):
        for relative, function in self.DECISION_SURFACE:
            for token in self._tokens_of(relative, function):
                assert word not in token.lower(), (
                    f"{relative}::{function} executes {token!r}, which names {word!r}"
                )

    def test_no_count_or_threshold_decides_ownership(self):
        section = self._code("src/manager/manager_skills.py")
        body = section[section.index("def _a_capability_claims"):section.index("def owns")]
        assert "capability.self_contained" in body
        # No numeric literal at all decides this - a count of capabilities or a
        # score threshold would be the defect in a different shape.
        assert not any(character.isdigit() for character in body), body

    def test_the_flag_is_read_in_exactly_one_place(self):
        readers = []
        for path in (REPO_ROOT / "src").rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or "self_contained" not in stripped:
                    continue
                if "capability.self_contained" in stripped:
                    readers.append(f"{path.name}:{number}")
        assert len(readers) == 1, readers


# ---------------------------------------------------------------------------
# 7. The reason is metadata, and its validation restricts only the claim
# ---------------------------------------------------------------------------


class TestTheReasonIsDocumentationNotAGate:
    def test_claiming_ownership_requires_stating_why(self):
        with pytest.raises(ValueError, match="states no reason"):
            _capability("probe_report", frozenset({"probe"}), self_contained=True)

    def test_a_reason_without_the_claim_is_allowed(self):
        """Repaired in this pass: a first version refused this. The idiom it
        copies (`authority_reason`) has no such rule, and a capability
        documenting why it is *open* to delegation is legitimate metadata on
        the same axis - a restriction here would be one future capability
        types had to obey for no reason."""
        capability = _capability(
            "probe_report", frozenset({"probe"}),
            self_contained_reason="deliberately open to delegation, and here is why",
        )
        assert capability.self_contained is False
        assert capability.self_contained_reason

    def test_a_capability_that_says_nothing_at_all_is_open_to_delegation(self):
        capability = _capability("probe_report", frozenset({"probe"}))
        assert capability.self_contained is False
        assert capability.self_contained_reason == ""

    def test_the_declaration_is_immutable_once_made(self):
        with pytest.raises(Exception):
            OWNED.self_contained = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 6. Today's lists are data, and this test says so out loud
# ---------------------------------------------------------------------------


class TestTodaysListsAreDataNotArchitecture:
    def test_the_registry_is_a_mix_and_neither_side_is_a_category(self):
        owned = [c.name for c in MANAGER_CAPABILITIES.values() if c.self_contained]
        open_ = [c.name for c in MANAGER_CAPABILITIES.values()
                 if c.claims is not None and not c.self_contained]
        assert owned and open_, "a registry entirely on one side would be the defect"
        # The mechanism is proven above against capabilities in neither list.
        assert "tessellation_report" not in MANAGER_CAPABILITIES
        assert "marginalia_report" not in MANAGER_CAPABILITIES
