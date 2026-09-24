"""Phase 10 - Multi-Agent Intelligence.

Pins, through the real composition root (``scripts.serve_ui.build_live_system``
-> ``scripts.control_center.build``) with every store sandboxed, and through
the pure records underneath it:

  * turn-taking is data: any order, any count, only the holder speaks, a
    participant who left is skipped on the record, a person may interject;
  * a session - room, discussions (shared context + floor), hand-overs -
    survives a save, a genuine rebuild and a resume, and a resumed open
    discussion carries on;
  * shared context is explicit, attributed and marked shared, reaches each
    participant's own brain, and never carries anybody's memory;
  * explicit delegation hands the named participant the work itself, only the
    orchestrator delegates, and a person's direct address is not a hand-over;
  * a discussion is held through each participant's own runtime: each speaker
    is shown what the others said and its words are recorded under its own
    runtime name; a failed or declined answer is never published as a view;
  * who may open a discussion, delegate or remove somebody is governed by the
    existing capability authority - specialists cannot;
  * each speaker is voiced in its own VoiceProfile;
  * none of it knows any current agent: every participant here is a
    temporary specialist created through the governed ``agent create``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
import src.services.sessions.store as session_store
from scripts.exchange import default_session_store, exchange
from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter, SpeechUnavailable
from src.config.settings import Settings
from src.conversation.conversation import (
    Conversation,
    ConversationError,
    Participant,
    ParticipantKind,
    Utterance,
)
from src.conversation.collaboration import CollaborationError, Contribution
from src.conversation.floor import Floor, FloorStatus, TurnOutcome
from src.conversation.session import DelegationRecord, Session
from src.core.contracts import Task
from src.core.shared_context import SHARED_PREFIX, SharedNote, shared_notes, with_shared_context
from src.manager.capabilities import (
    DISCUSSION,
    MANAGER_CAPABILITIES,
    PARTICIPANT_DISMISSAL,
    discussion_intent,
    dismissal_intent,
)
from src.manager.delegation import DELEGATED_BY_KEY, delegation_request
from src.providers import factory
from src.providers.availability import Availability, AvailabilityState
from src.providers.base import Completion, LLMProvider
from src.services.sessions.store import SessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

# Temporary, test-only participants - created through the governed mechanism,
# never known to production code.
ALPHA = "agent create probealpha Probealpha|planning and scheduling"
BETA = "agent create probebeta Probebeta|budget review"
GAMMA = "agent create probegamma Probegamma|quality checks"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _sandbox(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)


def _settings(tmp_path, **extra):
    return Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **extra,
    )


class Scripted(LLMProvider):
    """A reasoning provider that records every prompt and answers a
    discussion turn with a view naming its own call number - enough to prove
    what each brain was shown, without a real model."""

    def __init__(self):
        self.prompts: list[str] = []

    @property
    def name(self):
        return "phase10_scripted"

    def complete(self, prompt, *, system=None):
        self.prompts.append(prompt)
        if "Conclude the discussion" in prompt:
            return Completion(text="We agreed to decide after the review.", provider=self.name)
        if "We are discussing" in prompt:
            return Completion(text=f"view number {len(self.prompts)}", provider=self.name)
        return Completion(text='{"should_retain": false, "content": null}', provider=self.name)


@pytest.fixture
def scripted(monkeypatch):
    provider = Scripted()
    monkeypatch.setitem(factory._BUILDERS, "phase10_scripted", lambda settings, env=None, account=None: provider)
    return provider


def _build(tmp_path, monkeypatch, **extra):
    _sandbox(tmp_path, monkeypatch)
    return su.build_live_system(_settings(tmp_path, **extra))


def _run(system, command):
    return cc.run_command(system.centre, command, 110, system.channel, system.conversation)


def _say(system, text, **kw):
    return exchange(system.channel, system.conversation, text, sessions=default_session_store(), **kw)


@pytest.fixture
def full(tmp_path, monkeypatch, scripted):
    system = _build(tmp_path, monkeypatch, provider="phase10_scripted", brain="llm", specialist_brain="llm")
    for create in (ALPHA, BETA):
        assert "Created the specialist" in _run(system, create)
    return system


@pytest.fixture
def half(tmp_path, monkeypatch):
    system = _build(tmp_path, monkeypatch)
    for create in (ALPHA, BETA):
        assert "Created the specialist" in _run(system, create)
    return system


def _session(system) -> Session:
    return default_session_store().session_for(system.conversation)


def room(*agents, person="someone"):
    return Conversation([Participant(person, ParticipantKind.PERSON),
                         *(Participant(a, ParticipantKind.AGENT) for a in agents)])


# ---------------------------------------------------------------------------
# Turn-taking is data
# ---------------------------------------------------------------------------


class TestTheFloor:
    def test_the_order_is_whatever_the_round_was_given(self):
        conversation = room("c", "a", "b")
        floor = Floor(conversation)
        assert floor.open(["b", "c", "a"]) == "b"
        assert floor.finish_turn("b", TurnOutcome.SPOKE) == "c"
        assert floor.finish_turn("c", TurnOutcome.SPOKE) == "a"
        assert floor.finish_turn("a", TurnOutcome.SPOKE) is None
        assert floor.status is FloorStatus.COMPLETE
        assert [r.speaker for r in floor.history] == ["b", "c", "a"]

    def test_any_number_of_speakers_uses_the_same_code(self):
        names = [f"speaker-{i}" for i in range(40)]
        floor = Floor(room(*names))
        floor.open(names)
        while floor.holder:
            floor.finish_turn(floor.holder, TurnOutcome.SPOKE)
        assert len(floor.history) == 40

    def test_only_the_holder_may_take_the_turn(self):
        floor = Floor(room("a", "b"))
        floor.open(["a", "b"])
        with pytest.raises(ConversationError):
            floor.finish_turn("b", TurnOutcome.SPOKE)

    def test_nobody_outside_the_room_can_be_given_a_turn(self):
        with pytest.raises(ConversationError):
            Floor(room("a")).open(["a", "stranger"])

    def test_somebody_who_left_is_skipped_on_the_record(self):
        conversation = room("a", "b", "c")
        floor = Floor(conversation)
        floor.open(["a", "b", "c"])
        conversation.leave("b")
        assert floor.finish_turn("a", TurnOutcome.SPOKE) == "c"
        assert (floor.history[1].speaker, floor.history[1].outcome) == ("b", TurnOutcome.SKIPPED)

    def test_a_person_interjects_without_anybody_losing_their_place(self):
        floor = Floor(room("a", "b"))
        floor.open(["a", "b"])
        floor.interject("someone")
        assert floor.holder == "a"
        assert floor.history[-1].outcome is TurnOutcome.INTERJECTED

    def test_an_agent_cannot_interject(self):
        floor = Floor(room("a", "b"))
        floor.open(["a", "b"])
        with pytest.raises(ConversationError):
            floor.interject("b")

    def test_cancelling_records_everyone_still_due(self):
        floor = Floor(room("a", "b", "c"))
        floor.open(["a", "b", "c"])
        floor.cancel("stopped")
        assert floor.status is FloorStatus.CANCELLED and floor.holder is None
        assert [r.outcome for r in floor.history] == [TurnOutcome.CANCELLED] * 3

    def test_a_floor_survives_a_round_trip(self):
        conversation = room("a", "b")
        floor = Floor(conversation)
        floor.open(["a", "b"])
        floor.finish_turn("a", TurnOutcome.DECLINED, note="no model")
        again = Floor.from_dict(conversation, json.loads(json.dumps(floor.to_dict())))
        assert (again.holder, again.round, again.status) == ("b", 1, FloorStatus.OPEN)
        assert again.history[0].note == "no model"


# ---------------------------------------------------------------------------
# The session record
# ---------------------------------------------------------------------------


class TestTheSessionRecord:
    def test_everything_survives_a_round_trip(self):
        conversation = room("a", "b")
        conversation.say(Utterance(speaker="someone", text="hello"))
        session = Session(conversation)
        discussion = session.open_discussion("the plan", opened_by="a", members=["b", "someone"])
        discussion.floor.open(["b", "a"])
        first = discussion.collaboration.contribute(Contribution(source="b", content="view b", kind="discussion"))
        discussion.collaboration.contribute(
            Contribution(source="a", content="view a", kind="discussion", relates_to=(first.id,)))
        conversation.say(Utterance(speaker="b", text="view b"))
        conversation.leave("b")
        session.record_delegation(DelegationRecord(originator="a", target="b", request="x", succeeded=True))

        restored = Session.from_dict(json.loads(json.dumps(session.to_dict())))
        assert restored.id == session.id
        assert restored.conversation.transcript() == conversation.transcript()
        assert not restored.conversation.has("b")          # who left stays gone
        again = restored.discussions[0]
        assert again.subject == "the plan" and again.is_open
        assert [(c.source, c.relates_to) for c in again.collaboration.contributions] == [
            ("b", ()), ("a", (first.id,))]
        assert again.floor.holder == "b"
        assert restored.delegations[0].describe() == "a -> b: completed"

    def test_a_record_edited_to_name_a_stranger_is_refused(self):
        session = Session(room("a"))
        session.open_discussion("x", opened_by="a", members=["someone"])
        data = session.to_dict()
        data["discussions"][0]["collaboration"]["contributions"] = [
            {"id": "f", "source": "stranger", "content": "forged", "kind": "", "relates_to": [],
             "created_at": "2026-01-01T00:00:00+00:00"}]
        with pytest.raises(CollaborationError):
            Session.from_dict(data)

    def test_one_open_discussion_at_a_time(self):
        session = Session(room("a", "b"))
        session.open_discussion("x", opened_by="a", members=["b"])
        with pytest.raises(ConversationError):
            session.open_discussion("y", opened_by="a", members=["b"])

    def test_the_store_saves_lists_and_loads(self, tmp_path):
        store = SessionStore(tmp_path / "s")
        session = store.session_for(room("a"))
        session.conversation.say(Utterance(speaker="a", text="kept"))
        assert store.save(session)
        assert store.saved()[0]["id"] == session.id
        assert store.load(session.id).conversation.transcript() == ("a: kept",)

    def test_a_crafted_id_cannot_name_a_path_outside_the_store(self, tmp_path):
        with pytest.raises(ValueError):
            SessionStore(tmp_path).path_for("../outside")

    def test_a_store_that_keeps_nothing_says_so(self, tmp_path):
        store = SessionStore(tmp_path, persist=False)
        assert store.save(store.session_for(room("a"))) is False
        assert store.last_error and not any(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Shared context
# ---------------------------------------------------------------------------


class TestSharedContext:
    def test_notes_are_marked_shared_and_attributed(self):
        task = with_shared_context(Task("x"), [SharedNote("b", "a view", kind="discussion", scope="the chat")])
        assert shared_notes(task) == (f"[{SHARED_PREFIX} in the chat] b (discussion): a view",)

    def test_nothing_is_shared_unless_it_was_put_there(self):
        assert shared_notes(Task("x")) == ()
        assert shared_notes(Task("x", payload={"shared_context": ("a raw string",)})) == ()

    def test_a_specialists_own_brain_is_given_the_shared_context(self, full, scripted):
        from src.agents.general_specialist import GeneralSpecialist

        specialist = full.manager.registry.get("Probealpha")
        assert isinstance(specialist, GeneralSpecialist)
        specialist.general_conversation(
            with_shared_context(Task("We are discussing: a plan."), [SharedNote("Probebeta", "unique-marker-7")]))
        assert "unique-marker-7" in scripted.prompts[-1]


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


class TestDelegation:
    NAMES = ("Chair", "Research Desk", "Research", "Ali")

    def test_the_named_participant_is_handed_the_work_itself(self):
        handed = delegation_request("ask Research Desk to compare the two options", self.NAMES, orchestrator="Chair")
        assert (handed.target, handed.work) == ("Research Desk", "compare the two options")

    @pytest.mark.parametrize("text", [
        "Chair, ask Research to find the sources",
        "could you please ask Research to find the sources",
        "have Research find the sources",
        "let Research find the sources please",
    ])
    def test_ordinary_phrasings(self, text):
        handed = delegation_request(text, self.NAMES, orchestrator="Chair")
        assert handed.target == "Research" and handed.work == "find the sources"

    @pytest.mark.parametrize("text", [
        "ask Chair to find the sources",          # the orchestrator is never a target
        "tell Research about my week",            # talking about somebody
        "ask Research about the sources",         # a question, not a hand-over
        "get Research's opinion",                 # a possessive
        "ask Alison to find the sources",         # not a registered name
        "let me know when it is done",
        "ask Research to",                        # no work
    ])
    def test_what_is_not_a_hand_over(self, text):
        assert delegation_request(text, self.NAMES, orchestrator="Chair") is None

    def test_a_hand_over_runs_through_the_real_loop(self, half):
        manager = half.manager
        turn = _say(half, "ask Probealpha to tell me who you are")
        assert (turn.handled_by, turn.delegated) == ("Probealpha", True)
        assert any("handed over at the person's request" in s.detail for s in turn.steps)
        record = _session(half).delegations[-1]
        assert (record.originator, record.target) == (manager.own_skills.name, "Probealpha")
        assert record.request == "ask Probealpha to tell me who you are"

    def test_the_delegated_task_carries_who_handed_it_over(self, half):
        seen = []
        agent = half.manager.registry.get("Probebeta")
        original = agent.execute
        agent.execute = lambda task: (seen.append(task), original(task))[1]
        half.manager.handle_task(Task("ask Probebeta to check the totals"))
        assert seen[0].description == "check the totals"
        assert seen[0].payload[DELEGATED_BY_KEY] == half.manager.own_skills.name

    @pytest.mark.parametrize("text, to", [
        ("Probealpha, ask Probebeta to review the budget", None),   # spoken address
        ("ask Probebeta to review the budget", "Probealpha"),        # the agent selector
    ])
    def test_a_specialist_cannot_hand_work_to_another(self, half, text, to):
        calls = []
        other = half.manager.registry.get("Probebeta")
        other.execute = lambda task: calls.append(task)
        turn = _say(half, text, addressed=to)
        assert turn.handled_by == "Probealpha" and calls == []
        assert not [r for r in _session(half).delegations if r.target == "Probebeta"]

    def test_speaking_to_somebody_directly_is_not_a_hand_over(self, half):
        _say(half, "Probealpha, what do you think?")
        assert _session(half).delegations == ()

    def test_no_collaboration_is_drafted_for_a_named_hand_over(self, half):
        from src.manager.collaboration_entry import collaboration_candidates

        assert collaboration_candidates(half.manager, Task("ask Probealpha to plan and schedule it")) == ()


# ---------------------------------------------------------------------------
# Discussions
# ---------------------------------------------------------------------------


class TestRecognisingADiscussion:
    ROSTER = ("Chair", "Probealpha", "Probebeta")

    @pytest.mark.parametrize("text", [
        "let's discuss the launch plan with Probealpha and Probebeta",
        "hold a meeting about pricing with Probebeta",
        "let's brainstorm names with everyone",
        "continue the discussion",
        "conclude the meeting",
        "cancel the discussion",
    ])
    def test_claimed(self, text):
        assert discussion_intent(Task(text), self.ROSTER)

    @pytest.mark.parametrize("text", [
        "Chair, let's discuss the budget",                       # conversation with the orchestrator
        "let's discuss all the options",                          # no participant, no room word
        "schedule a meeting with Probebeta on Friday",            # the calendar's
        "let's discuss Probebeta's report",                       # a possessive
        "what is on the agenda for the meeting tomorrow",
    ])
    def test_not_claimed(self, text):
        assert not discussion_intent(Task(text), self.ROSTER)

    def test_removal_needs_the_name_right_after_the_verb(self):
        assert dismissal_intent(Task("dismiss Probebeta"), self.ROSTER)
        assert not dismissal_intent(Task("excuse me, Probebeta"), self.ROSTER)
        assert not dismissal_intent(Task("remove the meeting with Probebeta"), self.ROSTER)

    def test_both_are_the_orchestrators_alone_with_a_stated_reason(self):
        for capability in (DISCUSSION, PARTICIPANT_DISMISSAL):
            assert capability.authority == "manager" and capability.authority_reason
            assert MANAGER_CAPABILITIES[capability.name] is capability
            assert capability.scoreable is False


class TestADiscussionThroughTheRealSystem:
    OPEN = "let's discuss the launch date with Probealpha and Probebeta"

    def test_each_speaker_is_shown_what_the_others_said(self, full, scripted):
        turn = _say(full, self.OPEN)
        spoken = [t for t in turn.discussion["turns"] if t["outcome"] == "spoke"]
        assert [t["speaker"] for t in spoken] == ["Probealpha", "Probebeta", full.manager.own_skills.name]
        discussion_prompts = [p for p in scripted.prompts if "We are discussing" in p]
        assert spoken[0]["text"] not in discussion_prompts[0]
        assert spoken[0]["text"] in discussion_prompts[1]
        assert spoken[0]["text"] in discussion_prompts[2] and spoken[1]["text"] in discussion_prompts[2]

    def test_words_are_recorded_under_the_runtime_speaker_with_what_they_answered(self, full):
        _say(full, self.OPEN)
        discussion = _session(full).discussions[0]
        contributions = discussion.collaboration.contributions
        assert [c.source for c in contributions] == ["Probealpha", "Probebeta", full.manager.own_skills.name]
        assert contributions[1].relates_to == (contributions[0].id,)
        said = [u for u in full.conversation.utterances if isinstance(u.detail, dict)]
        assert [u.speaker for u in said] == [c.source for c in contributions]

    def test_the_person_takes_part_and_the_next_round_is_shown_it(self, full, scripted):
        _say(full, self.OPEN)
        _say(full, "remember the budget allows one campaign only")
        _say(full, "continue the discussion")
        round_two = [p for p in scripted.prompts if "We are discussing" in p][3:]
        assert round_two and all("the budget allows one campaign only" in p for p in round_two)
        assert _session(full).discussions[0].floor.round == 2

    def test_the_conclusion_is_the_orchestrators_own_synthesis(self, full):
        _say(full, self.OPEN)
        turn = _say(full, "conclude the discussion")
        assert turn.discussion["status"] == "concluded"
        assert turn.discussion["turns"][0]["text"] == "We agreed to decide after the review."

    def test_without_a_model_nobody_is_given_invented_views(self, half):
        turn = _say(half, self.OPEN)
        assert {t["outcome"] for t in turn.discussion["turns"]} == {"declined"}
        assert _session(half).discussions[0].collaboration.contributions == ()
        conclusion = _say(half, "conclude the discussion").discussion["turns"][0]["text"]
        assert "No synthesis was made" in conclusion

    def test_a_failed_answer_is_never_published_as_a_view(self, half):
        _say(half, self.OPEN)
        _say(half, "Probebeta, what do you think?")
        assert [c.source for c in _session(half).discussions[0].collaboration.contributions] == ["you"]

    def test_a_participant_named_nowhere_is_not_invented(self, half):
        turn = _say(half, "let's discuss the launch with Probealpha and everyone")
        assert turn.discussion["action"] == "open"
        assert not half.conversation.has("Probeunknown")

    def test_a_specialist_cannot_open_one(self, half):
        turn = _say(half, "Probealpha, discuss pricing with Probebeta")
        assert turn.handled_by == "Probealpha" and not turn.succeeded
        assert turn.discussion is None and _session(half).discussions == ()

    def test_a_discussion_turn_never_runs_a_participants_work(self, full):
        """Talking about building something must not build it: every turn goes
        through the participant's own conversation, never execute()."""
        executed = []
        for name in ("Probealpha", "Probebeta"):
            agent = full.manager.registry.get(name)
            agent.execute = lambda task, n=name: executed.append(n)
        turn = _say(full, "let's discuss whether to build a 3D model of a chair with Probealpha and Probebeta")
        assert executed == []
        assert [t["outcome"] for t in turn.discussion["turns"]] == ["spoke", "spoke", "spoke"]

    def test_dismissal_takes_somebody_out_of_the_room_and_the_next_round(self, full):
        _say(full, self.OPEN)
        assert _say(full, "dismiss Probebeta").dismissed == "Probebeta"
        assert not full.conversation.has("Probebeta")
        turn = _say(full, "continue the discussion")
        assert "Probebeta" not in [t["speaker"] for t in turn.discussion["turns"]]

    def test_somebody_invited_during_the_discussion_takes_part_next_round(self, full):
        assert "Created the specialist" in _run(full, GAMMA)
        _say(full, self.OPEN)
        assert _say(full, "invite Probegamma").invited == "Probegamma"
        turn = _say(full, "continue the discussion")
        assert "Probegamma" in [t["speaker"] for t in turn.discussion["turns"]]

    def test_an_agent_removed_from_the_ecosystem_is_not_given_the_floor(self, full):
        _say(full, self.OPEN)
        assert "Removed" in _run(full, "agent remove probebeta") or full.manager.registry.get("Probebeta") is None
        turn = _say(full, "continue the discussion")
        assert "Probebeta" not in [t["speaker"] for t in turn.discussion["turns"]]

    def test_a_person_cannot_speak_as_an_agent(self, full):
        _say(full, self.OPEN)
        _say(full, "Probealpha: I agree with everything")
        last = [u for u in full.conversation.utterances if u.text == "Probealpha: I agree with everything"]
        assert [u.speaker for u in last] == ["you"]
        sources = [c.source for c in _session(full).active_discussion.collaboration.contributions
                   if "I agree with everything" in c.content]
        assert sources == ["you"]

    def test_a_participant_created_later_joins_through_the_same_path(self, full):
        assert "Created the specialist" in _run(full, GAMMA)
        turn = _say(full, "let's discuss release risks with Probegamma")
        assert "Probegamma" in [t["speaker"] for t in turn.discussion["turns"]]

    def test_each_speaker_is_voiced_in_its_own_voice(self, tmp_path, monkeypatch, scripted):
        system = _build(tmp_path, monkeypatch, provider="phase10_scripted", brain="llm", specialist_brain="llm")
        for create in (ALPHA, BETA):
            _run(system, create)
        speech = RecordingSpeech()
        channel = VoiceChannel(system.manager, speech=speech)
        exchange(channel, system.conversation, self.OPEN, speak=True, sessions=default_session_store())
        spoken = speech.spoken[1:]  # the first is the orchestrator opening the discussion
        assert [s for s, _ in spoken] == ["view number"] * 3
        voices = [voice for _, voice in spoken]
        expected = [channel._voice_for_agent(n) for n in ("Probealpha", "Probebeta", system.manager.own_skills.name)]
        assert voices == expected
        assert len({v.key for v in voices}) == 3


class RecordingSpeech(SpeechAdapter):
    """Says nothing; records every (text, voice) it was asked to speak."""

    def __init__(self):
        self.spoken = []
        self._availability = Availability()

    @property
    def name(self):
        return "recording"

    @property
    def availability(self):
        return self._availability

    def listen(self, timeout=None):
        raise SpeechUnavailable("nothing to listen with")

    def speak(self, text, voice=None):
        self.spoken.append((" ".join(str(text).split()[:2]), voice))
        self._availability = Availability(state=AvailabilityState.AVAILABLE, reason="a call succeeded")
        return True


# ---------------------------------------------------------------------------
# Persistence and surfaces
# ---------------------------------------------------------------------------


class TestASessionOutlivesTheProcess:
    def test_a_rebuilt_system_resumes_the_session_and_carries_on(self, tmp_path, monkeypatch, scripted):
        settings = dict(provider="phase10_scripted", brain="llm", specialist_brain="llm")
        first = _build(tmp_path, monkeypatch, **settings)
        for create in (ALPHA, BETA):
            _run(first, create)
        _say(first, "let's discuss the launch date with Probealpha and Probebeta")
        _say(first, "ask Probealpha to plan the week")
        saved = json.loads((tmp_path / "sessions" / f"{first.conversation.id}.json").read_text("utf-8"))
        assert saved["discussions"][0]["status"] == "open"

        second = su.build_live_system(_settings(tmp_path, **settings))
        assert len(second.conversation) == 0                     # a new start is a new room
        reply = _run(second, f"sessions resume {first.conversation.id}")
        assert "still open" in reply
        assert second.conversation.id == first.conversation.id
        assert second.conversation.transcript() == first.conversation.transcript()
        assert _session(second).delegations[-1].target == "Probealpha"

        turn = _say(second, "continue the discussion")
        assert turn.discussion["round"] == 2
        assert {t["outcome"] for t in turn.discussion["turns"]} == {"spoke"}

    def test_a_resumed_discussion_follows_the_live_room(self, tmp_path, monkeypatch, scripted):
        """After a resume the discussion's floor must follow the room that is
        actually open - somebody invited after resuming takes part."""
        settings = dict(provider="phase10_scripted", brain="llm", specialist_brain="llm")
        first = _build(tmp_path, monkeypatch, **settings)
        for create in (ALPHA, BETA, GAMMA):
            _run(first, create)
        _say(first, "let's discuss the launch date with Probealpha and Probebeta")
        second = su.build_live_system(_settings(tmp_path, **settings))
        _run(second, f"sessions resume {first.conversation.id}")
        _say(second, "invite Probegamma")
        turn = _say(second, "continue the discussion")
        assert "Probegamma" in [t["speaker"] for t in turn.discussion["turns"]]

    def test_resuming_an_unknown_session_changes_nothing(self, half):
        before = half.conversation.id
        assert "no saved session" in _run(half, "sessions resume abcdef123456")
        assert half.conversation.id == before

    def test_session_records_are_not_agent_memory(self, half):
        _say(half, "let's discuss the launch date with Probealpha and Probebeta")
        for agent in half.manager.registry.all_agents():
            memory = half.manager.memory if agent is half.manager.own_skills else getattr(agent, "memory", None)
            if memory is not None:
                assert not any("launch date" in r.content for r in memory.recall(limit=200)
                               if getattr(r, "kind", "") != "delegation_outcome")


class TestOneSessionUnderEverySurface:
    def test_the_served_door_and_the_terminal_door_share_the_session(self, full):
        status, body, _ = su.route_message(
            "let's discuss the launch date with Probealpha and Probebeta", full, voice_output="device",
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["discussion"]["status"] == "open"
        assert [t["speaker"] for t in payload["discussion"]["turns"]][:2] == ["Probealpha", "Probebeta"]
        # the terminal door sees the discussion the served door opened
        assert "open: discussion" in _run(full, "sessions current")
        assert (Path(session_store.DEFAULT_SESSIONS_DIR) / f"{full.conversation.id}.json").exists()

    def test_the_terminal_shows_each_speaker(self, full):
        rendered = _run(full, "let's discuss the launch date with Probealpha and Probebeta")
        assert "DISCUSSION - the launch date" in rendered
        assert "Probealpha: view number" in rendered and "Probebeta: view number" in rendered


# ---------------------------------------------------------------------------
# Genericity
# ---------------------------------------------------------------------------


PHASE_10_MODULES = (
    "src/conversation/floor.py", "src/conversation/session.py", "src/core/shared_context.py",
    "src/manager/delegation.py", "src/manager/discussion.py", "src/services/sessions/store.py",
)


class TestNoCurrentEntityIsKnown:
    @pytest.mark.parametrize("module", PHASE_10_MODULES)
    def test_no_agent_name_in_executable_code(self, module):
        tree = ast.parse((REPO_ROOT / module).read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                for name in ("alfred", "arthur", "nova", "jarvis", "kokoro"):
                    assert name not in node.value.lower(), (module, node.value[:60])

    def test_the_orchestrators_identity_is_reached_by_role_not_by_name(self, half, monkeypatch):
        """An evolved identity document is re-read by a new session. The
        orchestrator's component is the role id "manager" (a legacy id is
        normalised to it before this runs), so it must be recognised by role -
        a name literal here never matched and the change was falsely reported
        as applied live."""
        rebound = []
        monkeypatch.setattr(cc, "_apply_backends_live", lambda centre: rebound.append(1) or " Applied live")
        for component_id in ("manager", "alfred", "specialist.probealpha"):
            assert "re-read by every new session" in cc._apply_evolution_live(half.centre, component_id)
        assert rebound == []


    def test_the_new_capabilities_name_nobody(self):
        source = (REPO_ROOT / "src/manager/capabilities.py").read_text(encoding="utf-8")
        start = source.index("# Phase 10 - discussions and participant removal")
        end = source.index("PARTICIPANT_INVITATION = ManagerCapability(")
        block = source[start:end].lower()
        for name in ("alfred", "arthur", "nova"):
            assert f'"{name}' not in block and f"'{name}" not in block


# ---------------------------------------------------------------------------
# Full Brain: the objective loop must not swallow an act meant for the room
# ---------------------------------------------------------------------------


class Pursuing(Scripted):
    """Answers the objective loop the way a real model did when this was found
    live: "keep going", then "complete" with a composed final answer."""

    def complete(self, prompt, *, system=None):
        if "Judge the progress of this objective" in prompt:
            self.prompts.append(prompt)
            judged = sum("Judge the progress" in p for p in self.prompts)
            if judged == 1:
                return Completion(text='{"status": "continue", "next_step": "look into it further"}',
                                  provider=self.name)
            return Completion(text='{"status": "complete", "final_answer": "The objective is complete."}',
                              provider=self.name)
        return super().complete(prompt, system=system)


class TestTheObjectiveLoopLeavesRoomActsAlone:
    @pytest.fixture
    def pursuing(self, tmp_path, monkeypatch):
        provider = Pursuing()
        monkeypatch.setitem(factory._BUILDERS, "phase10_pursuing", lambda settings, env=None, account=None: provider)
        system = _build(tmp_path, monkeypatch, provider="phase10_pursuing", brain="llm", specialist_brain="llm")
        for create in (ALPHA, BETA):
            _run(system, create)
        return system, provider

    def test_a_discussion_request_opens_the_discussion_with_a_reasoning_brain(self, pursuing):
        system, provider = pursuing
        turn = _say(system, "let's discuss the launch date with Probealpha and Probebeta")
        assert turn.discussion and turn.discussion["status"] == "open"
        assert _session(system).active_discussion is not None
        assert not any("Judge the progress" in p for p in provider.prompts)

    def test_an_invitation_survives_a_reasoning_brain_too(self, pursuing):
        system, _ = pursuing
        assert _say(system, "invite Probebeta").invited == "Probebeta"
        assert system.conversation.has("Probebeta")

    def test_ordinary_objectives_are_still_pursued(self, pursuing):
        system, provider = pursuing
        _say(system, "help me plan a garden")
        assert any("Judge the progress" in p for p in provider.prompts)
