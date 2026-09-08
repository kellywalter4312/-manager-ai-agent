"""Voice capability genericity - proof it belongs to the generic Agent
architecture, never to Alfred specifically.

Two independent proofs, neither a fabricated "third agent feature":

1. The purest level: ``select_voice()`` called directly with a bare
   ``CapabilityRequest`` naming an agent key that has never appeared
   anywhere in this project's source or identity documents - proving the
   function branches on nothing but what ``CapabilityRequest`` itself
   supplies (``own_name``, ``discover_voices``, ``recall``/``forget``/
   ``retain``), never a hardcoded "Alfred" or "Arthur" anywhere in its
   own body.
2. The real dispatch path: a fresh ``ManagerSkills`` instance (the exact
   same generic class Alfred's own ``own_skills`` already is - see that
   module's own docstring: "ManagerSkills implements BaseAgent... No
   parallel mechanism, no hidden self-execution path") built with a
   synthetic identity nobody has declared before, proving routing,
   claims and the real, shared ``MANAGER_CAPABILITIES`` dict reach
   ``select_voice`` for an arbitrary agent name with zero
   Alfred-specific code required to add it.

Alfred/Arthur isolation itself (each keeps independent voice state, one
never contaminates the other, on a single shared provider connection)
is covered separately in ``tests/test_voice_autonomous_selection_capability.py``
and ``tests/test_kokoro_voice_installation.py`` - not duplicated here.
"""

from __future__ import annotations

import subprocess

import pytest

from src.core.contracts import Task, TaskResult
from src.manager.capabilities import CapabilityRequest, select_voice
from src.manager.manager_skills import ManagerSkills
from src.services.identity.identity import AgentIdentity
from src.services.identity.voice import VoiceProfile
from src.services.identity.voice_options import VoiceOption
from src.services.identity.voice_preference import autonomous_voice_choice_for, effective_voice_for
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.policy import PolicyVerdict

_SYNTHETIC_AGENT_KEY = "quantum_relay_specialist"


def _fixture_is_genuinely_unseen() -> None:
    """The same structural precondition this project's own ecosystem-
    genericity tests already establish (test_ecosystem_open_ended_growth.py) -
    the agent name this file proves genericity with must appear nowhere
    in the shipped source tree, or the proof would rest on a name this
    project already special-cased."""
    result = subprocess.run(
        ["git", "grep", "-il", _SYNTHETIC_AGENT_KEY, "--", "src", "scripts"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "the genericity fixture agent name must be genuinely unseen"


class TestSelectVoiceBranchesOnNothingButTheRequestItGiven:
    """The purest proof: no ManagerAgent, no registry, no identity
    document at all - just the real handler function and a bare
    CapabilityRequest naming an agent nothing in this project has ever
    heard of."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def _request(self, description: str, *, memory: EphemeralMemory, discover):
        def retain(record):
            memory.remember(record)
            return PolicyVerdict(allowed=True, reason="test fixture allows all writes")

        return CapabilityRequest(
            task=Task(description=description),
            identity=AgentIdentity(
                name=_SYNTHETIC_AGENT_KEY, capabilities=frozenset({"voice"}),
            ),
            roster=lambda: (),
            own_name=_SYNTHETIC_AGENT_KEY,
            own_capabilities=("voice",),
            recall=memory.recall,
            forget=memory.forget,
            retain=retain,
            discover_voices=discover,
        )

    def test_an_unconstrained_choice_persists_under_the_given_agents_own_key(self):
        memory = EphemeralMemory()
        options = (VoiceOption("v1", "Real Voice One", "windows", language="english"),)
        result = select_voice(self._request("choose your voice that best suits you", memory=memory, discover=lambda: options))
        assert result.succeeded, result.error
        assert result.payload["voice_id"] == "v1"

        records = memory.recall(kind="user_context", tags={f"voice_autonomous_choice:{_SYNTHETIC_AGENT_KEY}"}, limit=5)
        assert len(records) == 1
        assert records[0].context["agent_key"] == _SYNTHETIC_AGENT_KEY

    def test_a_constrained_choice_works_identically_for_this_never_before_seen_agent(self):
        memory = EphemeralMemory()
        options = (
            VoiceOption("v1", "Italian Voice", "elevenlabs", language="italian"),
            VoiceOption("v2", "English Voice", "windows", language="english"),
        )
        result = select_voice(self._request("find me an Italian voice", memory=memory, discover=lambda: options))
        assert result.succeeded, result.error
        assert result.payload["voice_id"] == "v1"

    def test_an_unsupported_request_refuses_honestly_for_this_agent_too(self):
        memory = EphemeralMemory()
        options = (VoiceOption("v1", "English Voice", "windows", language="english"),)
        result = select_voice(self._request("find me a German voice", memory=memory, discover=lambda: options))
        assert not result.succeeded
        assert "german" in result.error.lower()

    def test_a_rejected_write_never_loses_the_prior_autonomous_choice(self):
        """A real, live-found weakness audit repair: _apply_voice_selection
        used to forget the prior autonomous choice unconditionally,
        before confirming request.retain actually allowed the new one -
        a governance rejection then left the agent with NO choice on
        record at all, discarding a real, valid prior fact for nothing.
        Fixed to retain-first, forget-only-on-success."""
        memory = EphemeralMemory()
        options = (
            VoiceOption("v1", "Voice One", "windows", language="english"),
            VoiceOption("v2", "Voice Two", "windows", language="english"),
        )
        reject = {"next": False}

        def retain(record):
            if reject["next"]:
                return PolicyVerdict(allowed=False, reason="simulated governance rejection")
            memory.remember(record)
            return PolicyVerdict(allowed=True, reason="test fixture allows this write")

        def request(description: str) -> CapabilityRequest:
            return CapabilityRequest(
                task=Task(description=description),
                identity=AgentIdentity(name=_SYNTHETIC_AGENT_KEY, capabilities=frozenset({"voice"})),
                roster=lambda: (), own_name=_SYNTHETIC_AGENT_KEY, own_capabilities=("voice",),
                recall=memory.recall, forget=memory.forget, retain=retain,
                discover_voices=lambda: options,
            )

        first = select_voice(request("choose your voice"))
        assert first.succeeded
        before = autonomous_voice_choice_for(_SYNTHETIC_AGENT_KEY, recall=memory.recall)
        assert before is not None and before.voice_id == first.payload["voice_id"]

        reject["next"] = True
        rejected = select_voice(request("choose another"))
        assert not rejected.succeeded

        after = autonomous_voice_choice_for(_SYNTHETIC_AGENT_KEY, recall=memory.recall)
        assert after is not None
        assert after.voice_id == before.voice_id, "the prior choice must survive a rejected write"


class TestAFreshGenericManagerSkillsAgentReachesSelectVoiceThroughRealDispatch:
    """The real dispatch path (routing -> claims -> handler), built from
    the exact same generic class Alfred's own_skills already is - never
    a subclass, never a specialized variant."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def _build(self):
        memory = EphemeralMemory()

        def retain(record):
            memory.remember(record)
            return PolicyVerdict(allowed=True, reason="test fixture allows all writes")

        identity = AgentIdentity(
            name=_SYNTHETIC_AGENT_KEY,
            capabilities=frozenset({"voice", "accent", "speak"}),
            voice=VoiceProfile(key=_SYNTHETIC_AGENT_KEY),
        )
        agent = ManagerSkills(
            identity,
            recall=memory.recall,
            forget=memory.forget,
            retain=retain,
        )
        return agent, memory

    def test_the_real_execute_path_reaches_select_voice_for_a_brand_new_agent_name(self):
        agent, memory = self._build()
        options = (VoiceOption("v1", "Real Windows-Style Voice", "windows", language="english"),)
        agent.bind_discover_voices(lambda: options)

        result = agent.execute(Task(description="Alfred, choose your voice that best suits you."))
        assert isinstance(result, TaskResult)
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "select_voice"
        assert result.payload["voice_id"] == "v1"

        # The choice is genuinely this agent's own, resolvable through
        # the same effective_voice_for a real speaking channel calls.
        profile = effective_voice_for(agent, recall=memory.recall)
        assert profile.preferred_voice == "Real Windows-Style Voice"

    def test_the_same_agent_gets_a_real_governed_refusal_not_a_crash_with_no_discovery_bound(self):
        agent, memory = self._build()
        # bind_discover_voices never called - the honest "no way to
        # discover options" path, exercised for a never-before-seen
        # agent exactly as it already is for Alfred.
        result = agent.execute(Task(description="find me an Italian voice"))
        assert not result.succeeded
        assert "discover" in result.error.lower()

    def test_release_and_reselect_semantics_are_available_to_this_agent_too(self):
        agent, memory = self._build()
        options = (
            VoiceOption("v1", "Voice One", "windows", language="english"),
            VoiceOption("v2", "Voice Two", "windows", language="english"),
        )
        agent.bind_discover_voices(lambda: options)

        first = agent.execute(Task(description="choose your voice"))
        assert first.succeeded
        again = agent.execute(Task(description="I don't like that. Choose another."))
        assert again.succeeded
        assert again.payload["voice_id"] != first.payload["voice_id"]
