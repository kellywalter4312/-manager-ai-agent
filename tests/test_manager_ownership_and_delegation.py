"""Recognising a request and owning it are two different things (P11Z-09).

**The defect.** ``ManagerAgent.decide`` asked ``own_skills.claims(task)``
before the router, and ``collaboration_candidates`` returned nothing when the
same predicate was true - so any capability that *recognised* a request also
*owned* it, shutting an authorised specialist out of delegation and
collaboration at once. The reasoning for that ordering
(``ManagerSkills.claims``'s own docstring) named seven capabilities and said
none of them benefits from specialist collaboration. That was true of seven.
It became false once nearly every capability carried a ``claims`` predicate -
research, knowledge, artifacts, diagrams, 3D, media, documents, content
analysis, translation: exactly the work an owner creates a specialist and
grants it capabilities to do.

**The repair, and what this file protects.** Ownership is now each
capability's own declaration (``ManagerCapability.self_contained``, which must
state its grounds), and delegation asks governance whether the agent it picked
may actually do the work. Every test below states a *rule*, never a roster:

    1. a capability may be recognised by Manager intent routing
    2. that alone never makes the work the Manager's
    3. only a capability declaring itself self-contained may pre-empt
    4. otherwise an authorised specialist may receive it

The Stage 15 protection is asserted head-on: a deterministic operation of
Alfred's own is never delegated on a specialist's incidental word overlap.

Specialists here are created through the real ``agent create`` path and named
with words that appear nowhere in the shipped tree, so nothing in this file
can be satisfied by a name, a domain or a keyword in source. Two unrelated
unseen domains are used, to show the mechanism is not one example's fix.
"""

from __future__ import annotations


from pathlib import Path

import pytest

import scripts.control_center as cc
import src.services.connections.service_registry as service_registry_module
import src.services.connections.site_registry as site_registry_module
import src.services.credentials.account_store_file as account_store_file_module
import src.services.evolution.governed_config as governed_config_module
import src.services.evolution.ledger_store as ledger_store_module
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.core.contracts import Task
from src.manager.activity_session import GuidedActivityState
from src.manager.capabilities import MANAGER_CAPABILITIES, ManagerCapability
from src.manager.collaboration_entry import collaboration_candidates

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# A real system, with the owner's own installation kept out of it
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every owner-state path redirected. Without this the roster under test
    is whoever the owner happens to have created (P11Z-11)."""
    for module, attribute, leaf in (
        (cc, "DEFAULT_CONFIG_PATH", "config.json"),
        (cc, "DEFAULT_ACCOUNTS_PATH", "accounts.json"),
        (cc, "DEFAULT_LEDGER_PATH", "ledger.json"),
        (cc, "DEFAULT_SERVICES_PATH", "services.json"),
        (cc, "DEFAULT_SITES_PATH", "sites.json"),
        (governed_config_module, "DEFAULT_CONFIG_PATH", "config.json"),
        (ledger_store_module, "DEFAULT_LEDGER_PATH", "ledger.json"),
        (account_store_file_module, "DEFAULT_ACCOUNTS_PATH", "accounts.json"),
        (service_registry_module, "DEFAULT_SERVICES_PATH", "services.json"),
        (site_registry_module, "DEFAULT_SITES_PATH", "sites.json"),
    ):
        monkeypatch.setattr(module, attribute, tmp_path / leaf, raising=False)
    return tmp_path


def _system(tmp_path):
    runtime, manager, channel, conversation = cc.build(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs")))
    return runtime, manager, channel, conversation, ControlCenter(runtime, manager)


def _with_specialist(tmp_path, definition: str):
    """Create a specialist through the real, audited Control Center path, then
    rebuild - the same restart-and-read-the-governed-config a real session does."""
    _runtime, _manager, channel, conversation, centre = _system(tmp_path)
    created = cc.run_command(centre, f"agent create {definition}", 100, channel, conversation,
                             action_log_path=tmp_path / "action_log.json")
    assert created.startswith("Created the specialist"), created
    return _system(tmp_path)


# Two unrelated domains, neither of which appears anywhere in the shipped tree.
BELLS = ("campanology_expert Tenor|campanology: bell founding, tuning and change ringing"
         "|knowledge,general_assistance")
STONES = ("quernstone_expert Grist|quernstone dressing and rotary mill balancing"
          "|knowledge,general_assistance")


# ---------------------------------------------------------------------------
# The rule itself, stated as an invariant over the registry
# ---------------------------------------------------------------------------


class TestRecognitionAndOwnershipAreSeparateConcepts:
    def test_the_two_predicates_are_not_the_same_question(self, tmp_path):
        """The heart of it: a request every capability recognises is not
        therefore the Manager's. Asserted by finding a task the registry
        recognises but does not own - never by naming a capability."""
        _runtime, manager, _channel, _conversation, _centre = _system(tmp_path)
        skills = manager.own_skills
        delegable = [c for c in MANAGER_CAPABILITIES.values()
                     if c.claims is not None and not c.self_contained]
        assert delegable, "the registry has no delegable claiming capability to prove this with"
        assert hasattr(skills, "owns"), "ownership must be its own predicate"
        assert skills.claims is not skills.owns

    def test_declaring_ownership_requires_stating_why(self):
        """A rule that pre-empts delegation says its grounds - the identical
        discipline `authority`/`authority_reason` already holds itself to."""
        with pytest.raises(ValueError, match="states no reason"):
            ManagerCapability(name="probe", capabilities=frozenset({"probe"}),
                              handle=lambda request: None, self_contained=True)

    def test_a_reason_without_the_declaration_is_allowed(self):
        """Only the *claim* of ownership is governed. A first version of this
        refused a reason stated without the flag; that is a restriction the
        idiom it copies (`authority_reason`) does not have, and it would refuse
        a capability documenting why it is open to delegation. The field is
        metadata - see tests/test_self_contained_is_a_general_property.py."""
        capability = ManagerCapability(
            name="probe", capabilities=frozenset({"probe"}), handle=lambda request: None,
            self_contained_reason="deliberately open to delegation, and here is why",
        )
        assert capability.self_contained is False

    def test_every_capability_that_may_pre_empt_states_its_grounds(self):
        missing = [c.name for c in MANAGER_CAPABILITIES.values()
                   if c.self_contained and not c.self_contained_reason.strip()]
        assert missing == []

    def test_ownership_is_off_by_default_so_a_new_capability_never_pre_empts_silently(self):
        probe = ManagerCapability(name="probe", capabilities=frozenset({"probe"}),
                                  handle=lambda request: None)
        assert probe.self_contained is False
        assert probe.self_contained_reason == ""

    def test_generalized_work_is_not_declared_the_managers_own(self):
        """Not a list to maintain - the property being asserted is that the
        registry still contains recognised work that is open to delegation. A
        registry where everything pre-empted would be the defect returning."""
        claiming = [c for c in MANAGER_CAPABILITIES.values() if c.claims is not None]
        delegable = [c for c in claiming if not c.self_contained]
        assert len(delegable) > 0
        assert len(delegable) < len(claiming), "and some work genuinely is the Manager's own"


# ---------------------------------------------------------------------------
# Stage 15: a deterministic Manager operation is never delegated on an
# incidental word overlap. This is the protection the ordering existed for.
# ---------------------------------------------------------------------------


class TestADeterministicManagerOperationStaysWithTheManager:
    @pytest.mark.parametrize("text", [
        "read my recent messages",
        "what day is it today?",
        "add a task to call the plumber",
        "forget what I told you about the workshop",
        "what do you remember about my deadline?",
    ])
    def test_it_is_never_delegated_even_with_specialists_registered(self, tmp_path, text):
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(tmp_path, BELLS)
        decision = manager.decide(Task(description=text))
        assert decision.agent_name == manager.own_skills.name, decision.reason

    def test_the_stage_15_case_survives_a_specialist_declaring_the_same_word(self, tmp_path):
        """A specialist whose own declared domain genuinely contains the word
        "messages" must still not receive a request to read messages."""
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(
            tmp_path, "signal_expert Relay|reading and relaying messages between stations|knowledge",
        )
        relay = next(a for a in manager.registry.all_agents() if a.name == "Relay")
        assert "messages" in {w.lower() for w in relay.spec.capabilities}
        decision = manager.decide(Task(description="read my recent messages"))
        assert decision.agent_name == manager.own_skills.name
        assert manager.own_skills.owns(Task(description="read my recent messages")) is True

    def test_a_self_contained_capability_also_keeps_collaboration_closed(self, tmp_path):
        """Both paths, one predicate: the Manager's own work never drafts
        anyone in either."""
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(
            tmp_path, "signal_expert Relay|reading and relaying messages between stations|knowledge",
        )
        assert collaboration_candidates(manager, Task(description="read my recent messages")) == ()


# ---------------------------------------------------------------------------
# An authorised specialist receives the kind of work it was created for
# ---------------------------------------------------------------------------


class TestAnAuthorisedSpecialistReceivesItsOwnWork:
    @pytest.mark.parametrize("definition,request_text", [
        (BELLS, "research how bell founding alloys change the tuning of a ring"),
        (STONES, "research how quernstone dressing affects rotary mill balancing"),
    ])
    def test_in_domain_work_reaches_it_though_a_broad_capability_recognises_it(
        self, tmp_path, definition, request_text,
    ):
        """The same unseen mechanism for two unrelated unseen domains."""
        task = Task(description=request_text)
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(tmp_path, definition)
        specialist = next(a for a in manager.registry.all_agents()
                          if a.name not in (manager.own_skills.name, "Arthur"))

        # A Manager capability genuinely recognises this request's shape...
        assert manager.own_skills.claims(task) is True
        # ...and that is deliberately not enough to make it the Manager's.
        assert manager.own_skills.owns(task) is False
        decision = manager.decide(task)
        assert decision.agent_name == specialist.name, decision.reason

    def test_collaboration_stays_open_for_work_the_manager_only_recognises(self, tmp_path):
        task = Task(description="research how bell founding alloys change the tuning of a ring")
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(tmp_path, BELLS)
        assert "Tenor" in collaboration_candidates(manager, task)

    def test_addressing_the_specialist_by_name_still_decides_it(self, tmp_path):
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(tmp_path, BELLS)
        for text in ("research how bell founding alloys change the tuning of a ring",
                     "add a task to call the plumber"):
            decision = manager.decide(Task(description=text), addressed="Tenor")
            assert decision.agent_name == "Tenor", decision.reason
            assert "addressed" in decision.reason


class TestDelegationGoesOnlyToAnAgentAuthorisedForTheWork:
    def test_work_the_specialist_was_never_granted_stays_with_the_manager(self, tmp_path):
        """In-domain by declared words, outside its grants. Before delegation
        consulted governance, the specialist received it and could only answer
        with a refusal naming who could have done it - a request that used to
        be completed became one the person had to make twice."""
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(
            tmp_path, "campanology_reader Tenor|campanology: bell founding and change ringing|knowledge",
        )
        tenor = next(a for a in manager.registry.all_agents() if a.name == "Tenor")
        assert "bell" in {w.lower() for w in tenor.spec.capabilities}
        # The router would score Tenor on "bell"; governance says it may not.
        decision = manager.decide(Task(description="make a diagram of a bell founding mould"))
        assert decision.agent_name == manager.own_skills.name, decision.reason

    def test_a_request_with_nothing_of_the_specialist_in_it_is_not_delegated(self, tmp_path):
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(tmp_path, BELLS)
        decision = manager.decide(Task(description="compare two unrelated kitchen kettles"))
        assert decision.agent_name == manager.own_skills.name

    def test_two_eligible_specialists_are_still_decided_by_the_one_router(self, tmp_path):
        """No preference is introduced between specialists - the existing
        generalized scoring still chooses, and it chooses the closer one."""
        _runtime, _manager, channel, conversation, centre = _system(tmp_path)
        for definition in (BELLS, STONES):
            cc.run_command(centre, f"agent create {definition}", 100, channel, conversation,
                           action_log_path=tmp_path / "action_log.json")
        _runtime, manager, _channel, _conversation, _centre = _system(tmp_path)
        names = {a.name for a in manager.registry.all_agents()}
        assert {"Tenor", "Grist"} <= names

        bells = manager.decide(Task(description="research how bell founding alloys change tuning"))
        stones = manager.decide(Task(description="research how quernstone dressing affects balancing"))
        assert bells.agent_name == "Tenor", bells.reason
        assert stones.agent_name == "Grist", stones.reason


# ---------------------------------------------------------------------------
# A session already in progress still settles a remark outright
# ---------------------------------------------------------------------------


class TestAnInProgressSessionStillOutranksEverything:
    def test_a_remark_during_a_guided_activity_is_never_delegated(self, tmp_path):
        _runtime, manager, _channel, _conversation, _centre = _with_specialist(tmp_path, BELLS)
        skills = manager.own_skills
        task = Task(description="research how bell founding alloys change the tuning of a ring")
        assert manager.decide(task).agent_name == "Tenor"

        skills._activity_sessions.start_activity(
            skills.name,
            GuidedActivityState(topic="anything at all", question_number=1, current_question="q"),
        )
        assert skills.owns(task) is True
        assert manager.decide(task).agent_name == skills.name
        assert collaboration_candidates(manager, task) == ()


# ---------------------------------------------------------------------------
# No name, domain or example noun reached the routing source
# ---------------------------------------------------------------------------


class TestTheRepairNamesNobody:
    ROUTING_SOURCES = (
        "src/manager/manager.py",
        "src/manager/manager_skills.py",
        "src/manager/collaboration_entry.py",
    )

    def test_no_agent_name_or_domain_word_is_in_the_routing_source(self):
        forbidden = ("tenor", "grist", "rosin", "sluice", "relay", "campanolog", "quernstone",
                     "lutherie", "hydrolog", "coral", "nova")
        for relative in self.ROUTING_SOURCES:
            text = (REPO_ROOT / relative).read_text(encoding="utf-8").lower()
            for word in forbidden:
                assert word not in text, f"{relative} names {word!r}"

    def test_ownership_is_read_from_the_declaration_never_from_a_list(self):
        """Routing decides by asking each capability what it declares about
        itself - so no capability's own *name* may appear as a literal in any
        of these files. A gate written as a name list would be this defect in
        a new shape."""
        for relative in self.ROUTING_SOURCES:
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            code = "\n".join(
                line.split("#", 1)[0] for line in text.splitlines()
                if not line.strip().startswith("#")
            )
            for name in MANAGER_CAPABILITIES:
                assert f'"{name}"' not in code, f"{relative} names the capability {name!r}"
                assert f"'{name}'" not in code, f"{relative} names the capability {name!r}"

    def test_the_filter_reads_the_declared_property(self):
        body = (REPO_ROOT / "src/manager/manager_skills.py").read_text(encoding="utf-8")
        section = body[body.index("def _a_capability_claims"):body.index("def owns")]
        assert "capability.self_contained" in section
