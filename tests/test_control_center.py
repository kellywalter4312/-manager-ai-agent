"""The front door: three ways in, and no ownership of the house.

The Control Center is a presentation layer. Everything it shows was decided by
the architecture underneath it, and the tests that matter most here are the
ones proving it stayed that way - that it routes nothing, assesses no brain,
registers no specialist, invents no tool and predicts no wake.

Three claims run through the file:

    it shows the truth        every value traces to a real read; an absent tool
                              is absent, an unproven model is Half Brain, an
                              unrun heartbeat has no last wake

    it shows enough           a person can see routing, delegation, capability,
                              brain mode and tool availability happening - the
                              difference between an agent system and a chat box

    it shows no more          decision summaries, never a model's private
                              reasoning; an overview, never the whole system on
                              one screen
"""

import ast
from pathlib import Path

import pytest

from src.control_center.observer import ControlCenter
from src.control_center.render import (
    NARROW,
    render_agent,
    render_history,
    render_overview,
    render_system,
    render_turn,
    render_voice,
)
from src.control_center.trail import observe
from src.control_center.views import (
    AgentView,
    ConversationTurn,
    OverviewView,
    VoiceView,
)
from src.providers.availability import Availability, AvailabilityState
from src.core.contracts import Task
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.sample_inbox import sample_inbox

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_CENTER = REPO_ROOT / "src" / "control_center"


def docstring_nodes(tree):
    """Every string Constant that is actually a docstring.

    Identified by position rather than by value: ast.get_docstring cleans and
    dedents what it returns, so comparing text finds nothing. A docstring is
    the first statement of a module, class or function, and that is what this
    matches - so prose may name an agent while code may not.
    """
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ.

    ``memory="ephemeral"`` by default - this file's own helper never had
    it, unlike every other test module's own settings() in this project,
    which meant every test built through centre()/with_profile() was
    silently reading/writing the real default data/memory/manager.json
    the whole time. That went unnoticed only because the file never
    happened to exist; the moment something else in this project
    (Stage 17's own live-served Avatar Studio) genuinely wrote to it for
    the first time, tests here that assume empty history started
    failing on real, unrelated user data. Explicit now, matching the
    isolation every other test file already has.
    """
    from src.config.settings import Settings

    return Settings.from_env(env={}, memory="ephemeral", **overrides)


def centre(mailbox=None, profile=None, **overrides):
    """A Control Center over a system built by the real composition root."""
    from src.composition import build_manager, build_runtime

    runtime = build_runtime(settings(log_level="CRITICAL", **overrides))
    manager = build_manager(
        runtime,
        mailbox=mailbox if mailbox is not None else InMemoryMailbox(),
        profile=profile,
    )
    return ControlCenter(runtime, manager)


def door(window):
    """The channel the composition root builds for one Control Center.

    Built through build_channel rather than constructed here, so a test that
    drives run_command is travelling the same door production does. A
    hand-made TextChannel would pass just as readily and prove nothing about
    what main() actually assembles.
    """
    from src.composition import build_channel

    return build_channel(window._runtime, window.manager)


def room(window):
    """The conversation the composition root opens for one Manager.

    Built through build_conversation for the same reason door() is built
    through build_channel: a hand-made Conversation would pass and prove
    nothing about what main() actually assembles.
    """
    from src.composition import build_conversation

    return build_conversation(window.manager)


def with_profile(**overrides):
    from src.services.profile.loader import parse_profile

    return centre(
        mailbox=sample_inbox(),
        profile=parse_profile("## Skills\n### Strong\npython, testing\n"),
        **overrides,
    )


# ---------------------------------------------------------------------------
# The three ways in
# ---------------------------------------------------------------------------


class TestTheManagerRaisesRealDelegationEvents:
    """Alfred reports his own orchestration. Nothing downstream infers it.

    A delegation is a decision the Manager made, so the Manager is the only
    thing that can truthfully say it happened. A screen noticing that a result
    carried somebody else's name would be inferring the decision rather than
    reporting it, and the first time the two disagreed there would be no way to
    say which was real.
    """

    def test_delegating_raises_an_event_from_the_manager(self):
        window = with_profile()
        assert len(window.manager.events) == 0

        window.manager.handle_task(Task(description="scan my inbox for opportunities"))

        raised = window.manager.events.of_kind("delegation")
        assert raised, "delegating must raise a delegation event"
        assert raised[0].fact("specialist") == "Arthur"

    def test_the_event_carries_the_routers_own_reasoning(self):
        """Facts the architecture already produced, never a phrase invented
        for the occasion."""
        window = with_profile()
        task = Task(description="scan my inbox for opportunities")
        expected = window.manager.diagnose(task).reason

        window.manager.handle_task(task)

        assert window.manager.events.of_kind("delegation")[0].fact("reason") == expected

    def test_handling_it_himself_raises_no_delegation(self):
        """Alfred is not a router. Work he takes on is not a handover, and
        recording one would make the count of handovers quietly wrong."""
        window = centre()

        result = window.manager.handle_task(Task(description="roster"))

        assert result.agent_name == "Alfred"
        assert window.manager.events.of_kind("delegation") == ()

    def test_the_manager_can_still_do_small_work_itself(self):
        """Depth, not an absolute wall. A task within Alfred's own capabilities
        is answered by Alfred, and no specialist is involved."""
        window = centre()
        result = window.manager.handle_task(Task(description="list your capabilities"))
        assert result.succeeded
        assert result.agent_name == "Alfred"
        assert window.manager.events.of_kind("delegation") == ()

    def test_delegation_remains_capability_driven(self):
        """The event describes a routing outcome; it does not cause one. Which
        agent runs is still decided by scoring declared capabilities."""
        window = with_profile()
        task = Task(description="scan my inbox for opportunities")
        assert window.manager.diagnose(task).agent_name == "Arthur"

        window.manager.handle_task(task)

        assert window.manager.events.of_kind("delegation")[0].fact("specialist") == "Arthur"

    def test_the_event_reaches_the_control_center_history(self):
        """Through the existing event architecture: the Manager's log, phrased
        by the narrator, rendered as ordinary history."""
        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for opportunities"))

        view = window.history()

        assert "event" in view.categories
        entries = [e for e in view.events if e.category == "event"]
        assert entries, "a raised event must be observable in history"
        assert "Arthur" in entries[0].summary

    def test_the_history_wording_comes_from_the_narrator(self):
        """Not from a sentence the screen wrote for itself."""
        from src.events.narrator import compose

        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for opportunities"))

        raised = window.manager.events.of_kind("delegation")[0]
        entries = [e for e in window.history().events if e.category == "event"]
        assert entries[0].summary == compose(raised)

    def test_the_control_center_raises_no_events_of_its_own(self):
        """Structural. Reading a log is observation; appending to it is being
        the source of truth, and there must be exactly one of those."""
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr != "record", (path.name, "events.record")

    def test_rendering_a_screen_raises_nothing(self):
        """An event must describe something that happened, never the fact that
        somebody looked."""
        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for opportunities"))
        before = len(window.manager.events)

        window.history()
        window.overview()
        render_overview(window.overview(), 80)

        assert len(window.manager.events) == before

    def test_the_event_log_is_not_a_second_memory(self):
        """Different questions, different stores, and no bridge between them.

        "What happened" and "what should change a future decision" are not the
        same claim. Raising an event must not put anything in memory, and what
        memory keeps about a delegation goes through the policy as it always
        did - so the two records of the same moment stay independently
        explainable.
        """
        window = with_profile()
        manager = window.manager
        assert manager.events is not manager.memory

        before = len(manager.memory.recall(limit=200))
        manager.handle_task(Task(description="scan my inbox for opportunities"))

        raised = manager.events.of_kind("delegation")
        assert raised

        # Whatever memory kept, it was admitted by the policy - the event
        # itself was never handed to remember().
        for record in manager.memory.recall(limit=200):
            assert record.kind != raised[0].kind
            assert manager.memory_policy.permits(record)

        # And an event is not a memory: the log grew without memory growing on
        # its account.
        assert len(manager.events) >= 1
        assert len(manager.memory.recall(limit=200)) >= before


class TestTheConversationIsReachableFromProduction:
    """The room is used, not merely available.

    Written in the style of test_production_reachability.py: drive what
    production calls and inspect what came back. A test that built a
    Conversation itself would prove the class works and nothing about whether
    any entry point opens one.
    """

    def test_the_entry_point_opens_a_conversation(self, monkeypatch):
        import scripts.control_center as cc

        monkeypatch.setattr(cc, "load_profile", lambda path: None)
        monkeypatch.setattr(cc, "load_schedule", lambda path: None)
        _, manager, _, conversation = cc.build(settings(log_level="CRITICAL"))

        from src.conversation.conversation import Conversation

        assert isinstance(conversation, Conversation)
        assert conversation.has(manager.own_skills.name)

    def test_the_room_starts_with_the_person_and_the_manager_only(self):
        """Not a roster. Alfred brings a specialist in when work needs one, so
        pre-populating from the registry would make "bring X into this"
        meaningless - X would already be sitting there."""
        window = centre()
        conversation = room(window)

        assert conversation.has(window.manager.own_skills.name)
        assert len(conversation.agents) == 1
        assert not conversation.has("Arthur")

    def test_talking_is_recorded_in_the_room(self):
        import scripts.control_center as cc

        window = centre()
        conversation = room(window)

        cc.run_command(window, "talk roster", 80, door(window), conversation)

        speakers = [u.speaker for u in conversation.utterances]
        assert "you" in speakers
        assert window.manager.own_skills.name in speakers

    def test_talking_without_the_keyword_is_recorded_too(self):
        """Two paths reach Alfred - "talk roster" and bare "roster" - and the
        bare one is the primary mode. A mutation check found this branch
        untested: bypassing the room here passed every other test.
        """
        import scripts.control_center as cc

        window = centre()
        conversation = room(window)

        cc.run_command(window, "roster", 80, door(window), conversation)

        speakers = [u.speaker for u in conversation.utterances]
        assert "you" in speakers
        assert window.manager.own_skills.name in speakers

    def test_the_answer_recorded_is_the_answer_given(self):
        import scripts.control_center as cc

        window = centre()
        conversation = room(window)

        cc.run_command(window, "talk roster", 80, door(window), conversation)

        answer = conversation.said_by(window.manager.own_skills.name)[0]
        assert answer.text.strip()
        assert answer.detail is not None  # the trail rides along, uninterpreted

    def test_addressing_is_recorded_even_though_nothing_routes_on_it_yet(self):
        """The seam voice will use. Recorded now because a transcript that
        never captured it could not be asked afterwards."""
        import scripts.control_center as cc

        window = centre()
        conversation = room(window)
        alfred = window.manager.own_skills.name

        cc.run_command(window, f"talk {alfred}, roster", 80, door(window), conversation)

        assert conversation.utterances[0].addressed == alfred

    def test_the_room_records_an_address_but_does_not_act_on_it(self):
        """The room hands over context; the orchestrator decides what it means.

        This used to assert that an addressed request and a plain one produced
        identical output, which was true only while addressing did nothing.
        Phase 4 is the change that makes an address matter, so asserting
        sameness would now be asserting that the feature does not work.

        The invariant that actually matters survives and is asserted here: the
        Conversation resolves *who was spoken to* and records it, and it holds
        no way to select, invoke or notify anybody.
        """
        window = centre()
        conversation = room(window)
        alfred = window.manager.own_skills.name

        assert conversation.addressed_in(f"{alfred}, roster") == alfred
        for forbidden in ("route", "diagnose", "handle_task", "decide", "delegate"):
            assert not hasattr(conversation, forbidden), forbidden

    def test_a_second_agent_could_join_without_the_model_changing(self):
        """The scaling claim, at the conversation layer: a future specialist
        takes part by being named. Nothing in the room learns it exists."""
        from src.conversation.conversation import Participant, ParticipantKind

        window = centre()
        conversation = room(window)

        conversation.join(Participant("Some Future Specialist", ParticipantKind.AGENT))

        assert conversation.has("Some Future Specialist")
        assert len(conversation.agents) == 2

    def test_the_entry_point_holds_no_conversation_logic(self):
        """Structural. The screen records what was said; it must not grow its
        own idea of participants, addressing or transcripts."""
        source = (REPO_ROOT / "scripts" / "control_center.py").read_text(encoding="utf-8")
        for invented in ("participants =", "transcript =", "def addressed"):
            assert invented not in source, invented


class TestSpecialistsSpeakForThemselves:
    """Alfred orchestrates participation. He does not talk for anybody.

    The words in a delegated answer come from that specialist's own brain,
    memory and tools - verified: the text is byte-identical to what the agent
    produces when driven directly, because the Manager assesses a result and
    never rewrites one. Putting the Manager's name on those words would be a
    lie about who did the work.

    Found by mutation. Attributing a specialist's answer to the Manager passed
    every test in the suite, and the transcript was worse than wrong: because
    the room opens with only the person and the Manager, a delegated answer was
    silently dropped and the record showed a question with no reply.
    """

    def _delegating_window(self):
        from src.services.profile.loader import parse_profile

        return centre(
            mailbox=sample_inbox(),
            profile=parse_profile("## Skills\n### Strong\npython\n"),
        )

    def test_a_delegated_answer_is_recorded_at_all(self):
        """The bug this class was written for: the reply used to vanish."""
        import scripts.control_center as cc

        window = self._delegating_window()
        conversation = room(window)

        cc.run_command(
            window, "talk scan my inbox for opportunities", 80,
            door(window), conversation,
        )

        assert len(conversation) == 2, "a question and its answer"

    def test_the_specialist_is_recorded_as_the_speaker(self):
        import scripts.control_center as cc

        window = self._delegating_window()
        conversation = room(window)

        cc.run_command(
            window, "talk scan my inbox for opportunities", 80,
            door(window), conversation,
        )

        answer = conversation.utterances[-1]
        assert answer.speaker == "Arthur"
        assert answer.speaker != window.manager.own_skills.name

    def test_the_manager_never_utters_the_specialists_words(self):
        """The load-bearing assertion. Whatever else changes, the Manager must
        not appear as the author of an answer he did not produce."""
        import scripts.control_center as cc

        window = self._delegating_window()
        conversation = room(window)
        alfred = window.manager.own_skills.name

        cc.run_command(
            window, "talk scan my inbox for opportunities", 80,
            door(window), conversation,
        )

        spoken_by_alfred = [u.text for u in conversation.said_by(alfred)]
        assert spoken_by_alfred == [], (
            "the Manager spoke a specialist's answer: " + repr(spoken_by_alfred[:1])
        )

    def test_the_specialist_joins_the_room_to_answer(self):
        """Delegation *is* participation. The room opens with the person and
        the Manager and gains a specialist when one does the work."""
        import scripts.control_center as cc

        window = self._delegating_window()
        conversation = room(window)
        assert not conversation.has("Arthur")

        cc.run_command(
            window, "talk scan my inbox for opportunities", 80,
            door(window), conversation,
        )

        assert conversation.has("Arthur")
        assert len(conversation.agents) == 2

    def test_joining_to_answer_hands_over_no_agent(self):
        """Participation must not become access. The specialist is in the room
        as a name and a kind, exactly like everybody else."""
        import scripts.control_center as cc

        window = self._delegating_window()
        conversation = room(window)

        cc.run_command(
            window, "talk scan my inbox for opportunities", 80,
            door(window), conversation,
        )

        arthur = conversation.participant("Arthur")
        assert set(vars(arthur)) == {"name", "kind"}
        for forbidden in ("brain", "memory", "tools", "registry", "agent"):
            assert not hasattr(arthur, forbidden), forbidden

    def test_the_specialists_own_words_are_what_is_recorded(self):
        """Not a summary, not a paraphrase - the result the specialist
        produced, unchanged by the Manager assessing it."""
        import scripts.control_center as cc

        window = self._delegating_window()
        conversation = room(window)

        cc.run_command(
            window, "talk scan my inbox for opportunities", 80,
            door(window), conversation,
        )

        answer = conversation.utterances[-1]
        assert answer.detail.handled_by == "Arthur"
        assert answer.text.startswith(answer.detail.outcome[:40])

    def test_work_the_manager_does_himself_is_still_his(self):
        """The other half. Alfred is not a router: what he handles, he says."""
        import scripts.control_center as cc

        window = centre()
        conversation = room(window)
        alfred = window.manager.own_skills.name

        cc.run_command(window, "talk roster", 80, door(window), conversation)

        assert conversation.utterances[-1].speaker == alfred
        assert not conversation.has("Arthur")


class TestTheChannelIsReachableFromProduction:
    """The channel seam stopped being a thing only tests could build.

    Written in the style of test_production_reachability.py, and for its
    reason: `wired != reachable != durable`. A test that constructs a
    TextChannel and calls ask() proves the class works and proves nothing about
    whether any entry point uses one. So these drive `build()` and `main()` -
    the functions production actually calls - and inspect what came back.
    """

    def test_the_composition_root_builds_the_channel_not_the_screen(self):
        """Composition owns the decision. Before this the entry point reached
        the Manager directly and made its own speech adapter."""
        import src.composition as composition

        window = centre()
        channel = composition.build_channel(window._runtime, window.manager)
        assert channel.manager is window.manager

    def test_the_entry_point_hands_back_a_composed_channel(self, monkeypatch):
        """build() is what main() calls, so this is the production assembly."""
        import scripts.control_center as cc

        monkeypatch.setattr(cc, "load_profile", lambda path: None)
        monkeypatch.setattr(cc, "load_schedule", lambda path: None)
        runtime, manager, channel, conversation = cc.build(
            settings(log_level="CRITICAL")
        )

        from src.channels.channel import Channel

        assert isinstance(channel, Channel)
        assert channel.manager is manager

    def test_the_channel_carries_the_speech_runtime_composed(self):
        """Not an adapter the channel made for itself, and not one the screen
        made: the object Runtime holds."""
        window = centre()
        channel = door(window)
        assert channel.speech is window._runtime.speech

    def test_talking_travels_through_the_channel(self):
        """The decisive one. run_command must not be able to answer without the
        door, so a channel that refuses to carry anything produces no answer.

        Asserting that some text came back would pass just as well with the old
        direct call to observe(), which is exactly the weak test this replaces.
        """
        import scripts.control_center as cc

        window = centre()

        class RefusingChannel:
            manager = window.manager
            name = "refusing"

            def ask(self, request, *, explain=True, addressed=None):
                raise AssertionError("the request must reach this channel")

        with pytest.raises(AssertionError, match="must reach this channel"):
            cc.run_command(window, "talk roster", 80, RefusingChannel(), room(window))

    def test_the_answer_is_the_channels_answer(self):
        """And it is a real turn, not a rendered string handed back."""
        import scripts.control_center as cc

        window = centre()
        channel = door(window)
        screen = cc.run_command(window, "roster", 80, channel, room(window))
        assert channel.ask("roster").turn.handled_by in screen

    def test_the_channel_still_goes_through_the_one_observe(self):
        """Structural. Routing talk through a channel must not have created a
        second orchestration: the channel calls observe(), as it always did,
        and the entry point no longer calls it at all.

        Checked against parsed code rather than raw text, for the reason
        TestTheThreeAxesNeverMerge gives: both files *discuss* observe() in
        comments explaining this very rule, and a substring search would find
        the explanation instead of testing the rule.
        """

        def called(path):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
            return names

        entry = called(REPO_ROOT / "scripts" / "control_center.py")
        channel = called(REPO_ROOT / "src" / "channels" / "channel.py")

        assert "observe" in channel
        assert "observe" not in entry
        assert "handle_task" not in entry

    def test_composing_a_channel_did_not_give_runtime_an_engine(self):
        """Runtime holds the interface, never knowledge of how speech is done."""
        source = (REPO_ROOT / "src" / "composition.py").read_text(encoding="utf-8")
        for engine in ("pyttsx", "whisper", "pyaudio", "sounddevice", "elevenlabs"):
            assert engine not in source.lower(), engine

    def test_the_composed_channel_has_no_availability_pre_gate(self):
        """R1 must not come back through the composition root. A fresh UNKNOWN
        engine is still attempted, or it could never prove itself."""
        source = (REPO_ROOT / "src" / "channels" / "channel.py").read_text(encoding="utf-8")
        assert "if not self._speech.available:" not in source

    def test_a_channel_still_holds_no_brain_memory_or_registry(self):
        """Composing one must not have widened what it carries."""
        window = centre()
        channel = door(window)
        for forbidden in ("brain", "memory", "registry", "router", "tools"):
            assert not hasattr(channel, forbidden), forbidden


class TestVoiceStatusReportsEvidenceNotAClaim:
    """The screen shows the adapter's Availability, and adds nothing to it.

    A boolean used to decide this, and got it wrong in the one case that
    matters: an engine that is installed but unproven rendered as "not
    installed", which is a different situation and a false one. Three states
    arrive here and three states are shown.
    """

    def voice(self, availability, engine="test-engine", columns=80):
        return render_voice(VoiceView(engine=engine, availability=availability), columns)

    def test_a_missing_engine_is_reported_with_the_reason_it_gave(self):
        from src.channels.speech import NoSpeech

        speech = NoSpeech()
        screen = self.voice(speech.availability, engine=speech.name)
        assert "no speech engine is installed" in screen
        assert "none" in screen

    def test_the_real_voice_command_reports_what_the_system_actually_has(self):
        """Pins the live path, not just the renderer: whatever `voice` builds
        must carry the shipped adapter's own reason."""
        import scripts.control_center as cc

        window = centre()
        screen = cc.run_command(window, "voice", 80, door(window), room(window))
        assert "no speech engine is installed" in screen

    def test_an_unproven_engine_is_not_reported_as_available(self):
        """The case the boolean got wrong. UNKNOWN is not usable and is also
        not missing, and the screen must not collapse it into either."""
        screen = self.voice(Availability())
        assert "verified usable" not in screen
        assert "not installed" not in screen
        assert "not yet verified" in screen

    def test_a_proven_engine_is_reported_as_available(self):
        screen = self.voice(
            Availability(state=AvailabilityState.AVAILABLE, reason="a call succeeded")
        )
        assert "verified usable" in screen

    def test_a_failed_engine_is_reported_with_its_recorded_reason(self):
        screen = self.voice(
            Availability(
                state=AvailabilityState.UNAVAILABLE,
                reason="no default output device",
                error_type="OSError",
            )
        )
        assert "unusable" in screen
        assert "no default output device" in screen

    def test_the_three_states_are_distinguishable_on_screen(self):
        """Whatever the wording, the three must not render alike - a screen
        that showed the same line for all of them would have re-created the
        boolean it replaced."""
        unknown = self.voice(Availability())
        available = self.voice(
            Availability(state=AvailabilityState.AVAILABLE, reason="a call succeeded")
        )
        unavailable = self.voice(
            Availability(state=AvailabilityState.UNAVAILABLE, reason="nothing here")
        )
        assert len({unknown, available, unavailable}) == 3

    def test_the_view_holds_the_availability_rather_than_copying_it(self):
        """No second source of truth: the view carries the record itself, so
        it cannot drift from what the adapter reported."""
        from src.channels.speech import NoSpeech

        speech = NoSpeech()
        view = VoiceView(engine=speech.name, availability=speech.availability)
        assert view.availability is speech.availability
        assert view.status == speech.availability.describe()

    def test_hearing_and_speaking_are_not_reported_as_separate_verdicts(self):
        """One adapter publishes one Availability, so printing two conclusions
        from it would re-invent the conflation at the screen."""
        screen = self.voice(Availability())
        assert "hearing" not in screen.lower()
        assert "speaking" not in screen.lower()


class TestTheThreeModes:
    def test_status_history_and_talk_all_exist(self):
        import scripts.control_center as cc

        window = centre()
        channel, conversation = door(window), room(window)
        for command in ("status", "history"):
            assert cc.run_command(window, command, 80, channel, conversation).strip()
        assert cc.run_command(window, "talk roster", 80, channel, conversation).strip()

    def test_talking_needs_no_keyword(self):
        """Conversation is the primary mode, so it should not need one."""
        import scripts.control_center as cc

        window = centre()
        channel = door(window)
        keyed = cc.run_command(
            window, "talk list your capabilities", 80, channel, room(window)
        )
        bare = cc.run_command(
            window, "list your capabilities", 80, channel, room(window)
        )
        assert "RESULT" in keyed and "RESULT" in bare

    def test_the_user_never_picks_a_specialist(self):
        """You say what you want. Alfred decides who does it."""
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for job opportunities"))
        assert turn.handled_by == "Arthur"
        assert turn.delegated is True

    def test_an_unknown_agent_is_reported_rather_than_guessed(self):
        import scripts.control_center as cc

        window = centre()
        answer = cc.run_command(
            window, "status Nobody", 80, door(window), room(window)
        )
        assert "no agent called" in answer
        assert "Alfred" in answer and "Arthur" in answer


# ---------------------------------------------------------------------------
# Talk to Manager shows an agent system working
# ---------------------------------------------------------------------------


class TestTalkToManagerIsObservable:
    def test_a_delegation_shows_the_whole_path(self):
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for job opportunities"))
        rendered = render_turn(turn, 80)

        assert "Alfred -> Arthur" in rendered
        assert "Routing decision" in rendered
        assert "Manager brain" in rendered
        assert "Specialist brain" in rendered
        assert "Assessed" in rendered

    def test_direct_handling_says_so(self):
        window = centre()
        turn = observe(window.manager, Task(description="tell me about anything at all"))
        assert turn.delegated is False
        assert turn.handled_by == "Alfred"
        assert "Handled directly" in render_turn(turn, 80)

    def test_a_missing_tool_is_named_in_the_trail(self):
        """The honest case, and the one a chat box could not show."""
        window = centre()
        turn = observe(window.manager, Task(description="research a subject"))
        assert turn.tool_used == "web_search"
        assert turn.tool_available is False
        assert "NOT INSTALLED" in render_turn(turn, 80)

    def test_both_brain_modes_appear_when_a_specialist_is_involved(self):
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for jobs"))
        assert turn.brain_mode == "HALF BRAIN"
        assert turn.specialist_brain_mode == "HALF BRAIN"

    def test_memory_activity_is_reported_as_counts(self):
        """That something was learned, not the text of it - the content lives
        in the agent's own memory view."""
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for jobs"))
        assert isinstance(turn.memory_written, int)

    def test_no_private_reasoning_is_exposed(self):
        """The boundary that separates a decision summary from a chain of
        thought. Steps describe what happened; none quotes a model thinking."""
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for jobs"))
        rendered = render_turn(turn, 80).lower()

        for leaked in (
            "chain of thought", "chain-of-thought", "thinking:", "internal reasoning",
            "let me think", "reasoning trace", "scratchpad",
        ):
            assert leaked not in rendered, leaked

    def test_the_trail_can_be_skipped_when_a_router_would_charge_for_it(self):
        """Reading the routing decision costs a second call on a model-driven
        router, so a caller can decline it."""
        window = centre()
        turn = observe(window.manager, Task(description="roster"), explain=False)
        assert not any(step.label == "Routing decision" for step in turn.steps)
        assert turn.succeeded

    def test_a_long_result_is_capped_rather_than_dumped(self):
        """A conversation shows an answer, not a report."""
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for job opportunities"))
        rendered = render_turn(turn, 80)
        assert "more line(s)" in rendered
        assert len(rendered.splitlines()) < 40


# ---------------------------------------------------------------------------
# Overview first, detail on demand
# ---------------------------------------------------------------------------


class TestProgressiveDisclosure:
    def test_the_overview_is_short(self):
        """The first screen is a summary. If this grows without bound, the
        Control Center is turning into the wall of everything."""
        rendered = render_overview(centre().overview(), 80)
        assert len(rendered.splitlines()) < 40

    def test_the_overview_does_not_dump_memory_or_capabilities(self):
        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for jobs"))
        rendered = render_overview(window.overview(), 100)

        # Counts, not contents.
        assert "STRONG MATCH" not in rendered
        assert "job_opportunity" not in rendered
        for capability in ("recruiter", "vacancy", "reporting"):
            assert capability not in rendered

    def test_a_wide_screen_shows_the_same_screens_not_more_of_them(self):
        """Extra width is comfort, never permission to show everything."""
        overview = centre().overview()
        narrow = render_overview(overview, 40)
        wide = render_overview(overview, 120)

        assert "SPECIALISTS (1)" in narrow and "SPECIALISTS (1)" in wide
        assert len(wide.splitlines()) - len(narrow.splitlines()) < 10

    def test_detail_lives_behind_a_drill_down(self):
        window = with_profile()
        overview = window.overview()
        arthur = overview.agent("Arthur")
        assert isinstance(arthur, AgentView)

        detail = render_agent(arthur, 80)
        assert "BRAIN" in detail and "MEMORY" in detail and "CAPABILITIES" in detail
        assert "recruiter" in detail

    def test_the_system_view_is_separate_from_the_agents(self):
        rendered = render_system(centre().overview(), 80)
        assert "HEARTBEAT" in rendered and "PERMISSIONS" in rendered
        assert "BRAIN" not in rendered


# ---------------------------------------------------------------------------
# Brain and Memory stay distinct, and per agent
# ---------------------------------------------------------------------------


class TestBrainAndMemoryAreNeverCollapsed:
    def test_each_agent_has_its_own_brain_view(self):
        window = with_profile()
        overview = window.overview()
        assert overview.manager.brain.agent == "Alfred"
        assert overview.specialists[0].brain.agent == "Arthur"

    def test_each_agent_has_its_own_memory_view(self):
        window = with_profile()
        overview = window.overview()
        assert overview.manager.memory.owner == "Alfred"
        assert overview.specialists[0].memory.owner == "Arthur"

    def test_a_brain_view_carries_no_memory_and_a_memory_view_no_brain(self):
        overview = centre().overview()
        brain, memory = overview.manager.brain, overview.manager.memory
        for forbidden in ("records", "recent", "recall", "content"):
            assert not hasattr(brain, forbidden), forbidden
        for forbidden in ("mode", "provider", "verified", "reason"):
            assert not hasattr(memory, forbidden), forbidden

    def test_the_manager_brain_view_does_not_report_a_specialist_mode(self):
        """Alfred being Full Brain must never make Arthur Full Brain."""
        from src.core.contracts import Task as _Task
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        class Works(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                return Completion(text="{}", provider="anthropic")

        window = with_profile()
        window.manager.brain = LLMManagerBrain(Works())
        window.manager.brain.deliberate(_Task(description="warm up"))

        overview = window.overview()
        assert overview.manager.brain.mode == "FULL BRAIN"
        assert overview.specialists[0].brain.mode == "HALF BRAIN"

    def test_full_brain_is_never_shown_for_a_merely_configured_model(self):
        """The Control Center must not be the place the definition slips."""
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        class Configured(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                return Completion(text="{}", provider="anthropic")

        window = centre()
        window.manager.brain = LLMManagerBrain(Configured())

        view = window.manager_view().brain
        assert view.mode == "HALF BRAIN"
        assert view.verified is False
        assert "[--]" in render_agent(window.manager_view(), 80)


# ---------------------------------------------------------------------------
# Specialists are discovered, never listed
# ---------------------------------------------------------------------------


class TestSpecialistDiscovery:
    def test_specialists_come_from_the_registry(self):
        overview = centre().overview()
        assert [s.name for s in overview.specialists] == ["Arthur"]

    def test_no_agent_name_is_written_into_the_control_center(self):
        """Not Arthur, not Alfred, and certainly not a future specialist.

        Names arrive from identity documents through the registry. A name in
        this package would be the start of the redesign-per-specialist problem
        the architecture exists to avoid.
        """
        forbidden = (
            "arthur", "alfred", "research_agent", "coding_agent",
            "sports_agent", "movie_agent", "media_agent", "jarvis",
        )
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = docstring_nodes(tree)
            for node in ast.walk(tree):
                # An identifier naming an agent is a dependency on that agent.
                if isinstance(node, ast.Name):
                    for name in forbidden:
                        assert name not in node.id.lower(), (path.name, node.id)
                # So is a live string literal. A docstring is documentation,
                # and prose explaining the architecture may name the Manager.
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstrings:
                        continue
                    for name in forbidden:
                        assert name not in node.value.lower(), (path.name, node.value[:40])

    def test_a_second_specialist_appears_without_any_change(self, tmp_path, monkeypatch):
        """The scaling claim. One entry in the registry, one identity document,
        and the Control Center shows it with its own brain and memory."""
        from src.agents.base_agent import BaseAgent
        from src.agents.specialists import SPECIALISTS
        from src.composition import build_manager, build_runtime
        from src.core.contracts import AgentSpec, TaskResult

        identities = tmp_path / "identities"
        identities.mkdir()
        (identities / "email_agent.md").write_text(
            (REPO_ROOT / "identities" / "email_agent.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (identities / "ledger_agent.md").write_text(
            "## Name\nTesting Specialist\n## Role\nA stand-in.\n"
            "## Area of responsibility\nbookkeeping\n",
            encoding="utf-8",
        )

        class Second(BaseAgent):
            def __init__(self, context):
                self.context = context
                self.brain = context.brain
                self.memory = context.memory()

            @property
            def spec(self):
                return AgentSpec(
                    name=self.context.wiring.name,
                    description="A stand-in used by the Control Center tests.",
                    capabilities=frozenset({"ledger", "reconcile"}),
                )

            def execute(self, task):
                return TaskResult.success(task, self.name, "reconciled")

        monkeypatch.setitem(SPECIALISTS, "ledger_agent", Second)

        runtime = build_runtime(
            settings(log_level="CRITICAL", identities_dir=str(identities))
        )
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        overview = ControlCenter(runtime, manager).overview()

        names = [s.name for s in overview.specialists]
        assert "Testing Specialist" in names
        assert "Arthur" in names

        second = overview.agent("Testing Specialist")
        assert second.brain.agent == "Testing Specialist"
        assert second.memory.owner == "Testing Specialist"
        assert "bookkeeping" in second.area
        assert "Testing Specialist" in render_overview(overview, 80)


# ---------------------------------------------------------------------------
# Nothing is fabricated
# ---------------------------------------------------------------------------


class TestTheControlCenterTellsTheTruth:
    def test_an_absent_tool_is_shown_as_absent(self):
        rendered = render_system(centre().overview(), 80)
        assert "none installed" in rendered
        assert "Web Search: Available" not in rendered

    def test_a_capability_without_its_tool_is_marked_not_installed(self):
        view = centre().manager_view()
        research = next(c for c in view.capabilities if c.name == "research")
        assert research.tool == "web_search"
        assert research.tool_available is False
        assert research.is_performable is False
        assert "NOT INSTALLED" in render_agent(view, 80)

    def test_a_capability_needing_no_tool_claims_none(self):
        view = centre().manager_view()
        roster = next(c for c in view.capabilities if c.name == "roster")
        assert roster.tool is None
        assert roster.is_performable is True

    def test_an_unrun_heartbeat_has_no_last_wake(self, tmp_path):
        window = centre(state_path=str(tmp_path / "nothing" / "processed.json"))
        beat = window.heartbeat_view()
        assert beat.last_wake is None
        assert "never" in render_overview(window.overview(), 80)

    def test_no_next_wake_is_predicted(self):
        """Scheduling lives outside this process, so naming a time would
        describe a mechanism the project does not have."""
        beat = centre().heartbeat_view()
        assert beat.next_wake is None
        assert "scheduled externally" in render_system(centre().overview(), 80)

    def test_an_empty_history_says_so(self, tmp_path):
        window = centre(reports_dir=str(tmp_path / "none"))
        rendered = render_history(window.history(), 80)
        assert "nothing recorded yet" in rendered


# ---------------------------------------------------------------------------
# History is filtered, not dumped
# ---------------------------------------------------------------------------


class TestHistory:
    def test_history_is_separate_from_status(self):
        window = with_profile()
        assert "HISTORY" in render_history(window.history(), 80)
        assert "HISTORY" not in render_overview(window.overview(), 80)

    def test_history_can_be_filtered_by_category(self):
        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for jobs"))
        view = window.history(category="memory")
        assert view.is_filtered
        assert all(event.category == "memory" for event in view.events)

    def test_history_can_be_filtered_by_actor(self):
        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for jobs"))
        view = window.history(actor="Arthur")
        assert all(event.actor == "Arthur" for event in view.events)

    def test_history_is_limited_before_it_reaches_a_screen(self):
        window = with_profile()
        view = window.history(limit=2)
        assert len(view.events) <= 2


# ---------------------------------------------------------------------------
# The Control Center owns nothing
# ---------------------------------------------------------------------------


class TestItOwnsNoBusinessLogic:
    FORBIDDEN_IMPORTS = {
        "src.manager.router",
        "src.manager.llm_router",
        "src.manager.fallback_router",
        "src.manager.assessor",
        "src.manager.router_factory",
        "src.manager.brain_factory",
        "src.agents.brain_factory",
        "src.services.memory.factory",
        "src.services.memory.policy",
        "src.services.email.gmail_mailbox",
        "src.providers.factory",
        "src.heartbeat.runner",
        "src.heartbeat.lock",
    }

    def test_it_imports_no_engine_it_would_be_tempted_to_reimplement(self):
        """Routing, assessment, brain construction, memory admission,
        scheduling and provider selection all stay where they are."""
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module not in self.FORBIDDEN_IMPORTS, (path.name, node.module)

    def test_it_defines_no_router_assessor_or_brain(self):
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    lowered = node.name.lower()
                    for role in ("router", "assessor", "brain", "memory", "agent"):
                        if role in lowered:
                            assert lowered.endswith("view"), (path.name, node.name)

    def test_it_starts_no_timer_and_schedules_nothing(self):
        timers = {"threading", "asyncio", "sched", "signal", "subprocess"}
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in timers, path.name
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in ("sleep", "tick", "save"), path.name

    def test_it_adds_no_dependency(self):
        requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for absent in ("flask", "fastapi", "uvicorn", "django", "streamlit", "textual", "rich"):
            assert absent not in requirements, absent

    def test_the_observer_only_reads(self):
        """No write reaches the system through this layer."""
        source = (CONTROL_CENTER / "observer.py").read_text(encoding="utf-8")
        for mutation in (".remember(", ".forget(", ".clear(", ".register_agent(", ".save("):
            assert mutation not in source, mutation


# ---------------------------------------------------------------------------
# The safety boundary is unchanged by having a front door
# ---------------------------------------------------------------------------


class TestNoPermissionWasExpanded:
    def test_the_system_view_reports_read_only(self):
        system = centre().system_view()
        assert system.gmail_scope.endswith("gmail.readonly")
        assert system.can_send is False
        assert system.is_read_only is True

    def test_sending_is_still_refused_through_the_front_door(self):
        window = with_profile()
        turn = observe(window.manager, Task(description="send an email to my team"))
        assert turn.succeeded is False

    def test_the_control_center_cannot_grant_a_tool(self):
        window = centre()
        window.overview()
        window.history()
        assert window.manager.own_skills.tools.is_empty

    def test_no_outbox_is_created_by_opening_the_front_door(self):
        from src.services.email.mailbox import Outbox

        centre().overview()
        with pytest.raises(TypeError):
            Outbox()

    def test_the_scope_is_unchanged_after_every_screen(self):
        from src.services.email.gmail_auth import SCOPES

        window = with_profile()
        window.overview()
        window.history()
        render_system(window.overview(), 80)
        observe(window.manager, Task(description="research something"))
        assert SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)


# ---------------------------------------------------------------------------
# Responsive
# ---------------------------------------------------------------------------


class TestResponsive:
    def test_a_phone_width_stacks_into_one_column(self):
        rendered = render_overview(centre().overview(), 40)
        assert "Brain:" in rendered
        assert max(len(line) for line in rendered.splitlines()) <= 42

    def test_a_laptop_width_aligns_instead(self):
        rendered = render_overview(centre().overview(), 100)
        assert "  Brain          " in rendered

    def test_every_screen_renders_at_both_widths(self):
        window = with_profile()
        overview = window.overview()
        turn = observe(window.manager, Task(description="research something"))

        for columns in (36, NARROW - 1, NARROW, 80, 132):
            for rendered in (
                render_overview(overview, columns),
                render_agent(overview.manager, columns),
                render_system(overview, columns),
                render_history(window.history(), columns),
                render_turn(turn, columns),
            ):
                assert rendered.strip()
                assert max(len(line) for line in rendered.splitlines()) <= max(columns, 80) + 2

    def test_nothing_is_padded_to_look_busy(self):
        """One specialist shows one specialist."""
        rendered = render_overview(centre().overview(), 80)
        assert "SPECIALISTS (1)" in rendered
