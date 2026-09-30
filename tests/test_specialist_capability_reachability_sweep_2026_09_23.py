"""Continuation of the final ecosystem closure (2026-09-23): representative
specialist runtime verification for capabilities the closure campaign
(section W, ARCHITECTURE.md Sec52.61) had not yet exercised end to end
through an authorised specialist - image_analysis, clip_video and
create_artifact - alongside W-READ-01 (read_content) and W-CAP-02
(create_3d_model), which already proved the generic authorisation
mechanism itself works.

**Genuine software gap found and repaired while doing this.** A
specialist's own Brain had no way to perform real visual reasoning at
all: ``SpecialistBrain`` (src/agents/brain.py) never declared an
``analyze_image`` method, and ``SpecialistCapabilities.request_for``
(scripts/control_center.py) never built one into ``CapabilityRequest`` -
so a specialist granted ``image_analysis``, even with a genuinely
vision-capable provider selected as its own Full Brain, could never be
answered by that reasoning; only the shared local-vision fallback
(``request.assist``, wired from the Manager's own skills) was ever
reachable. Repaired the same way ``converse`` already is for a
specialist: a concrete default on the ``SpecialistBrain`` ABC returning
an honest no-view (every existing implementation and test double keeps
working unchanged), a real override on ``LLMSpecialistBrain`` mirroring
``LLMManagerBrain.analyze_image``'s own honesty rules, and the missing
wire in ``request_for``.

Through the real served system (``scripts.serve_ui.build_live_system``),
real FFmpeg, a real PNG and the identical governed grant mechanism
(``capabilities grant/revoke``) the rest of the closure campaign already
proved generic.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
import src.services.connections.service_registry as service_registry_module
import src.services.credentials.account_store_file as account_store_file_module
from scripts.exchange import exchange
from src.config.settings import Settings
from src.providers.base import Completion, LLMProvider
from src.providers.live_info import LiveInfoProvider, LiveInfoResult
from src.providers.live_info import utc_now as live_info_utc_now
from src.providers.translation.factory import build_translator

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sandbox(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)


def _connection_sandbox(tmp_path, monkeypatch):
    """The identical isolation
    tests/test_natural_language_live_connection_integration.py's own
    ``_isolated_paths`` fixture uses - including, critically, the two
    ``connection_factory.py`` reads *directly off their owning module*
    rather than through ``control_center``'s own copied name, so a
    service declared through the Control Center is genuinely what a
    freshly-built system selects as its active connection."""
    _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(cc, "DEFAULT_SERVICES_PATH", tmp_path / "services.json")
    monkeypatch.setattr(service_registry_module, "DEFAULT_SERVICES_PATH", tmp_path / "services.json")
    monkeypatch.setattr(account_store_file_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")


def _settings(tmp_path, **extra):
    return Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **extra,
    )


@pytest.fixture
def system(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    return su.build_live_system(_settings(tmp_path, media="local"))


def _run(system, command):
    return cc.run_command(system.centre, command, 100, system.channel, system.conversation)


def _say(system, text, to=None):
    return exchange(system.channel, system.conversation, text, addressed=to)


def _upload(system, agent_name, data, name, mime, source="conversation"):
    status, body, _ = su.route_upload(agent_name, name, mime, base64.b64encode(data).decode(), source, system)
    return status, json.loads(body)


def _png(color=(255, 0, 0)) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _mp4(tmp_path) -> bytes:
    from src.providers.media.video import resolve_ffmpeg

    out = tmp_path / "made.mp4"
    subprocess.run([
        resolve_ffmpeg(), "-y",
        "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-pix_fmt", "yuv420p",
        str(out),
    ], check=True, capture_output=True)
    return out.read_bytes()


class _VisionProbe(LLMProvider):
    """A real LLMProvider double that also exposes analyze_image - proving
    the mechanism, never claiming a live vendor call. The specialist
    counterpart of test_phase_3_files_documents_control_center.py's own
    _FakeVisionProvider."""

    def __init__(self, text: str = "I see a small green square.") -> None:
        self._text = text
        self.calls: list[tuple[bytes, str]] = []

    @property
    def name(self) -> str:
        return "closure_vision_probe"

    def complete(self, prompt, *, system=None):
        return Completion(text='{"should_retain": false, "content": null}', provider=self.name)

    def analyze_image(self, image_bytes: bytes, prompt: str, *, media_type: str = "image/png"):
        self.calls.append((image_bytes, prompt))
        return Completion(text=self._text, provider=self.name)


class TestASpecialistsOwnFullBrainNowReachesRealVisualReasoning:
    """The repaired gap: image_analysis granted to a specialist, with a
    vision-capable provider selected as that specialist's own Full Brain."""

    def test_half_brain_specialist_is_honest_not_silently_wrong(self, system):
        _run(system, "agent create iris Iris|image review|image_analysis")
        png = _png()
        _upload(system, "Iris", png, "pic.png", "image/png")
        turn = _say(system, "Iris, look at this image and tell me what you see", to="Iris")
        assert not turn.succeeded
        assert "couldn't analyze" in turn.outcome
        assert "small green square" not in turn.outcome

    def test_full_brain_specialist_gets_a_real_analysis_from_its_own_brain(self, tmp_path, monkeypatch):
        from src.providers import factory

        probe = _VisionProbe()
        monkeypatch.setitem(factory._BUILDERS, "closure_vision_probe", lambda settings, env=None, account=None: probe)
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(
            tmp_path, media="local", provider="closure_vision_probe", brain="llm", specialist_brain="llm",
        ))
        _run(system, "agent create iris Iris|image review|image_analysis")
        png = _png()
        _upload(system, "Iris", png, "pic.png", "image/png")
        turn = _say(system, "Iris, look at this image and tell me what you see", to="Iris")
        assert turn.succeeded, turn.outcome
        assert turn.outcome == "I see a small green square."
        assert probe.calls and probe.calls[0][0] == png
        # Never Alfred's own analyze_image - proof it ran as Iris, on
        # Iris's own brain, never a silent fallback to the Manager.
        iris = system.manager.registry.get("Iris")
        assert system.manager.own_skills._analyze_image is not iris.brain.analyze_image


class TestClipVideoReachesAnAuthorizedSpecialist:
    def test_a_granted_specialist_produces_a_real_distinct_clip(self, system, tmp_path):
        from src.providers.media.video import probe_duration_seconds

        _run(system, "agent create clara Clara|video editing|clip_video")
        source = _mp4(tmp_path)
        status, body = _upload(system, "Clara", source, "movie.mp4", "video/mp4")
        assert status == 200
        turn = _say(system, "Clara, clip this video from 0:00 to 0:01", to="Clara")
        assert turn.succeeded, turn.outcome
        clips = [a for a in system.runtime.artifacts.all() if a.type == "video"]
        assert len(clips) == 1
        assert clips[0].owner_agent == "Clara"
        assert body["artifact_id"] in clips[0].latest.related_artifact_ids
        clip_path = next(iter(clips[0].latest.renditions.values()))
        with open(clip_path, "rb") as handle:
            duration = probe_duration_seconds(handle.read())
        assert 0.5 <= duration <= 1.6

    def test_revoked_specialist_cannot_clip_and_nothing_clips_for_her(self, system, tmp_path):
        _run(system, "agent create clara Clara|video editing|clip_video")
        _run(system, "capabilities revoke Clara clip_video")
        source = _mp4(tmp_path)
        _upload(system, "Clara", source, "movie.mp4", "video/mp4")
        turn = _say(system, "Clara, clip this video from 0:00 to 0:01", to="Clara")
        assert not turn.succeeded
        assert "clip_video, which I'm not authorised for" in turn.outcome
        assert not [a for a in system.runtime.artifacts.all() if a.type == "video"]


class TestCreateArtifactReachesAnAuthorizedSpecialist:
    """Half-Brain-safe path: structured content given directly needs no
    model, so this is provable with no provider configured at all."""

    REQUEST = (
        "Clara, make a handout: TITLE: Garden shed plan "
        "SECTION: Materials: cedar boards and galvanised screws "
        "SECTION: Timeline: framed by the second weekend of October"
    )

    def test_a_granted_specialist_produces_a_real_pdf_artifact(self, system):
        _run(system, "agent create clara Clara|documentation|create_artifact")
        turn = _say(system, self.REQUEST, to="Clara")
        assert turn.succeeded, turn.outcome
        assert "Artifact created" in turn.outcome
        docs = [a for a in system.runtime.artifacts.all() if a.type == "document"]
        assert len(docs) == 1
        assert docs[0].owner_agent == "Clara"
        assert docs[0].latest.title == "Garden shed plan"

    def test_ungranted_specialist_is_refused_and_alfred_does_not_do_it_for_her(self, system):
        _run(system, "agent create clara Clara|documentation")
        turn = _say(system, self.REQUEST, to="Clara")
        assert not turn.succeeded
        assert "create_artifact, which I'm not authorised for" in turn.outcome
        assert not system.runtime.artifacts.all()


class TestReviseArtifactReachesAnAuthorizedSpecialistAndStaysOwnershipScoped:
    """revise_artifact is a genuinely different shape from create_artifact -
    it reads an existing artifact back, mutates it in place (same identity,
    a new version), and runs a governance check first. This is the
    ownership boundary ArtifactProductionTool's own module comment states
    ("a future second agent sharing this same ArtifactStore must not be
    able to revise another agent's artifact just by knowing its id") -
    inspected in source (``_owned``/``_existing_owned`` in
    src/agents/tools/artifact_production.py both check
    ``owner_agent == self._owner_agent``) and proven here through the real
    served system, for the first time through a granted specialist rather
    than only through Alfred."""

    CREATE = (
        "Clara, make a handout: TITLE: Garden shed plan "
        "SECTION: Materials: cedar boards and galvanised screws "
        "SECTION: Timeline: framed by the second weekend of October"
    )
    REVISE = "Clara, edit the handout: replace cedar boards with treated pine."

    def test_a_granted_specialist_revises_her_own_artifact_deterministically(self, system):
        _run(system, "agent create clara Clara|documentation|create_artifact,revise_artifact")
        created = _say(system, self.CREATE, to="Clara")
        assert created.succeeded, created.outcome

        revised = _say(system, self.REVISE, to="Clara")
        assert revised.succeeded, revised.outcome
        assert "Artifact revised" in revised.outcome

        docs = [a for a in system.runtime.artifacts.all() if a.type == "document"]
        assert len(docs) == 1
        assert docs[0].owner_agent == "Clara"
        assert docs[0].latest.version == 2
        with open(docs[0].latest.renditions["txt"], encoding="utf-8") as handle:
            text = handle.read()
        assert "treated pine" in text
        assert "cedar boards" not in text

    def test_a_second_specialist_cannot_revise_it_by_explicit_id(self, system):
        _run(system, "agent create clara Clara|documentation|create_artifact,revise_artifact")
        _run(system, "agent create nova Nova|3D modelling|revise_artifact")
        created = _say(system, self.CREATE, to="Clara")
        assert created.succeeded, created.outcome
        clara_artifact_id = [a for a in system.runtime.artifacts.all() if a.type == "document"][0].artifact_id

        turn = _say(system, f"Nova, edit artifact {clara_artifact_id}: replace cedar boards with treated pine.", to="Nova")
        assert not turn.succeeded
        assert "couldn't find that handout to revise" in turn.outcome

        docs = [a for a in system.runtime.artifacts.all() if a.type == "document"]
        assert len(docs) == 1
        assert docs[0].owner_agent == "Clara"
        assert docs[0].latest.version == 1
        with open(docs[0].latest.renditions["txt"], encoding="utf-8") as handle:
            text = handle.read()
        assert "cedar boards" in text


class TestConnectedActionReachesAnAuthorizedSpecialistAndRevocationStopsAPendingConfirmation:
    """connected_action is the last structurally distinct grantable
    capability: real consequential-action machinery (a confirmation gate,
    a governance check, a real external connection) rather than a
    document/media/vision shape already proven twice over. Verified
    through a real declared local service (127.0.0.1, never actually
    reachable unless urllib.request.urlopen is monkeypatched - the
    identical convention
    tests/test_natural_language_live_connection_integration.py already
    uses) rather than a mock that replaces the mechanism itself.

    **Genuine defect found and repaired here.**
    ``SpecialistCapabilities.__call__`` resolved a pending confirmation
    (``guided_interaction_response``, which can execute the connected
    operation on a clear "yes") *before* it ever checked whether the
    specialist was still authorised for the capability that raised it -
    a completely separate code path from the ordinary
    ``reachable(agent)``/``capability.claims()`` dispatch every other
    request goes through. Revoking ``connected_action`` (or
    ``communication``) from a specialist while a confirmation was still
    open did not stop it: the next "yes" still executed, because
    ``_execute_connected_operation`` only re-checks *tool* permission
    (always present - ``manager_tools()`` builds every tool
    unconditionally; only the *capability* layer gates who may reach
    it), never capability authorisation. Repaired by re-checking
    authorisation for the confirmation's own capability before letting
    it resolve at all; an unauthorised pending confirmation is cleared
    and honestly refused rather than executed or left dangling.
    """

    OPERATIONS = "read_item:GET:/item:open,write_item:POST:/item:auth"
    REQUEST = "Clara, check my connected account: write the item"

    def _system(self, tmp_path, monkeypatch):
        """Declares and credentials 'fixturesvc' with a throwaway build
        first (the real, confirmed-at-the-terminal-layer Control Center
        commands, exactly as a person would type them), then builds the
        real system under test - ``connection_provider`` is read once, at
        build time, so the service must already be on disk before this
        second build runs."""
        _connection_sandbox(tmp_path, monkeypatch)
        declaring = su.build_live_system(_settings(tmp_path))
        _run(declaring, f"services fixturesvc declare http://127.0.0.1:9199 {self.OPERATIONS}")
        _run(declaring, "connections fixturesvc connect testaccount genuine-secret-token")
        return su.build_live_system(_settings(tmp_path, connection_provider="fixturesvc"))

    def test_a_granted_specialist_raises_and_executes_a_real_confirmation(self, tmp_path, monkeypatch):
        import urllib.request

        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations|connected_action")

        raised = _say(system, self.REQUEST, to="Clara")
        assert raised.succeeded, raised.outcome
        assert "write_item" in raised.outcome and "(yes/no)" in raised.outcome
        assert raised.handled_by == "Clara"

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'{"id": "item-1"}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())
        confirmed = _say(system, "yes", to="Clara")
        assert confirmed.succeeded, confirmed.outcome
        assert "id" in confirmed.outcome
        # Ownership preserved: the trail attributes this to Clara, never Alfred.
        assert confirmed.handled_by == "Clara"

    def test_a_clear_no_cancels_it_without_ever_reaching_the_real_service(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations|connected_action")

        raised = _say(system, self.REQUEST, to="Clara")
        assert raised.succeeded and "(yes/no)" in raised.outcome

        # No urlopen monkeypatch: a real attempt would try to reach
        # 127.0.0.1:9199, where nothing is listening.
        cancelled = _say(system, "no, don't", to="Clara")
        assert cancelled.succeeded
        assert cancelled.outcome == "Okay, I won't write_item on fixturesvc."
        sessions = system.manager.own_skills._activity_sessions
        assert sessions.pending_confirmation("Clara") is None

    def test_revoking_mid_confirmation_stops_it_from_executing(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations|connected_action")

        raised = _say(system, self.REQUEST, to="Clara")
        assert raised.succeeded and "(yes/no)" in raised.outcome

        _run(system, "capabilities revoke Clara connected_action")

        # No urlopen monkeypatch here either: if the (buggy) old behaviour
        # were still in place, "yes" would try to reach 127.0.0.1:9199 for
        # real. A graceful, honest refusal - never a real attempted call,
        # never a silent execution - is the only way this can pass.
        confirmed = _say(system, "yes", to="Clara")
        assert not confirmed.succeeded
        assert "connected_action" in confirmed.outcome
        assert "no longer authorised" in confirmed.outcome

        # Nothing usable is left pending - not merely "the reply looked
        # like a refusal", the actual session state is gone.
        sessions = system.manager.own_skills._activity_sessions
        assert sessions.pending_confirmation("Clara") is None

        # Alfred himself is untouched by Clara's revocation - he can still
        # reach the identical connection and operation.
        alfred_raised = _say(system, "check my connected account: write the item")
        assert alfred_raised.succeeded and "(yes/no)" in alfred_raised.outcome

    def test_ungranted_specialist_is_honestly_refused_and_nothing_is_raised(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations")

        turn = _say(system, self.REQUEST, to="Clara")
        assert not turn.succeeded
        assert "connected_action, which I'm not authorised for" in turn.outcome
        sessions = system.manager.own_skills._activity_sessions
        assert sessions.pending_confirmation("Clara") is None


class _WorkingCommunicationProvider:
    """A real CommunicationProvider double that genuinely records what it
    was asked to do - proving the mechanism end-to-end without claiming a
    live vendor was ever reached. Registered through the real provider
    factory seam (``communication_factory._BUILDERS``), never swapped
    directly onto an agent's own ``ToolSet`` - so both Alfred's and every
    specialist's ``CommunicationSendTool``/``CommunicationCallTool`` (both
    built from the identical ``runtime.communication``) genuinely reach
    it, the same way a real vendor backend would."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.called: list[str] = []

    @property
    def name(self) -> str:
        return "closure-comm-probe"

    def read_messages(self, *, limit: int = 10):
        return ()

    def send_message(self, *, to: str, content: str):
        from src.providers.communication import CommunicationResult, CommunicationStatus, utc_now

        self.sent.append((to, content))
        return CommunicationResult(
            operation="send", party=to, status=CommunicationStatus.SUCCEEDED,
            source=self.name, attempted_at=utc_now(),
        )

    def place_call(self, *, to: str):
        from src.providers.communication import CommunicationResult, CommunicationStatus, utc_now

        self.called.append(to)
        return CommunicationResult(
            operation="call", party=to, status=CommunicationStatus.SUCCEEDED,
            source=self.name, attempted_at=utc_now(),
        )


class TestCommunicationReachesAnAuthorizedSpecialistAndRevocationStopsAPendingConfirmation:
    """communication shares the identical PendingConfirmation/
    ActivitySessions resolver connected_action does - 52.65's own fix
    (SpecialistCapabilities.__call__ re-checking authorisation before
    resolving a pending confirmation) already covers both, since it keys
    on "connected_action" vs "communication" generically. What had never
    been proven is communication's own, genuinely distinct execution path:
    CommunicationSendTool/CommunicationCallTool, party/content extraction,
    and a real registered CommunicationProvider - never assumed covered by
    connected_action's own proof."""

    def _system(self, tmp_path, monkeypatch):
        from src.providers import communication_factory

        provider = _WorkingCommunicationProvider()
        monkeypatch.setitem(
            communication_factory._BUILDERS, "closure_comm_probe",
            lambda settings, env=None, account=None: provider,
        )
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, communication_provider="closure_comm_probe"))
        return system, provider

    def test_a_granted_specialist_raises_and_executes_a_real_send(self, tmp_path, monkeypatch):
        system, provider = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations|communication")

        raised = _say(system, "Clara, send Kai a message saying hello", to="Clara")
        assert raised.succeeded, raised.outcome
        assert "Kai" in raised.outcome and "(yes/no)" in raised.outcome
        assert provider.sent == [], "must not send before confirmation"

        confirmed = _say(system, "yes", to="Clara")
        assert confirmed.succeeded, confirmed.outcome
        assert provider.sent == [("Kai", "saying hello")]
        # Ownership preserved: attributed to Clara, never Alfred.
        assert confirmed.handled_by == "Clara"

    def test_a_granted_specialist_places_a_real_call(self, tmp_path, monkeypatch):
        system, provider = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations|communication")

        raised = _say(system, "Clara, call Rema", to="Clara")
        assert raised.succeeded and "Rema" in raised.outcome and "(yes/no)" in raised.outcome
        assert provider.called == []

        confirmed = _say(system, "yes", to="Clara")
        assert confirmed.succeeded
        assert provider.called == ["Rema"]
        assert confirmed.handled_by == "Clara"

    def test_a_clear_no_cancels_without_ever_sending(self, tmp_path, monkeypatch):
        system, provider = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations|communication")

        _say(system, "Clara, send Kai a message saying hello", to="Clara")
        cancelled = _say(system, "no, cancel that", to="Clara")
        assert cancelled.succeeded
        assert "won't" in cancelled.outcome.lower()
        assert provider.sent == []
        sessions = system.manager.own_skills._activity_sessions
        assert sessions.pending_confirmation("Clara") is None

    def test_ungranted_specialist_is_honestly_refused_and_nothing_is_raised(self, tmp_path, monkeypatch):
        system, provider = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations")

        turn = _say(system, "Clara, send Kai a message saying hello", to="Clara")
        assert not turn.succeeded
        assert "communication, which I'm not authorised for" in turn.outcome
        assert provider.sent == []
        sessions = system.manager.own_skills._activity_sessions
        assert sessions.pending_confirmation("Clara") is None

    def test_revoking_mid_confirmation_stops_it_from_executing(self, tmp_path, monkeypatch):
        system, provider = self._system(tmp_path, monkeypatch)
        _run(system, "agent create clara Clara|operations|communication")

        raised = _say(system, "Clara, send Kai a message saying hello", to="Clara")
        assert raised.succeeded and "(yes/no)" in raised.outcome

        _run(system, "capabilities revoke Clara communication")

        confirmed = _say(system, "yes", to="Clara")
        assert not confirmed.succeeded
        assert "communication" in confirmed.outcome
        assert "no longer authorised" in confirmed.outcome
        assert provider.sent == [], "revoking mid-confirmation must prevent the real send"

        sessions = system.manager.own_skills._activity_sessions
        assert sessions.pending_confirmation("Clara") is None

        # Alfred himself is untouched by Clara's revocation - he can still
        # send through the identical provider.
        alfred_raised = _say(system, "send Kai a message saying hello")
        assert alfred_raised.succeeded and "(yes/no)" in alfred_raised.outcome
        _say(system, "yes")
        assert provider.sent == [("Kai", "saying hello")]


# ===========================================================================
# Continuation, same day: the eight remaining grantable capabilities that had
# never been exercised end to end through an authorised specialist -
# create_diagram, revise_diagram, general_assistance, image_authenticity,
# image_comparison, transcribe_media, translate and translate_media.
#
# Two genuine software defects were found doing it, both repaired here:
#
#   1. ``translate`` was unreachable whenever the agent was addressed by
#      name. ``translation_request``'s four patterns are anchored (on
#      purpose - "what's the weather in Paris" must not be a translation),
#      and nothing stripped the vocative first, so "Nova, translate 'x' into
#      Italian" matched nothing and fell through to open conversation -
#      which answered "no language model is configured" although real
#      offline packs were installed and the capability was granted. Alfred
#      addressed by name was refused identically. Repaired by applying this
#      module's own ``_strip_vocative``, which eight other anchored shapes
#      in capabilities.py already use.
#
#   2. The Control Center's trail reported no capability at all for work a
#      *specialist* did. ``observe()``'s delegated branch derived it from
#      ``_shared_words`` - the overlap between the request and the agent's
#      spec - which is an honest description of why the router chose that
#      agent and is not what it then did. Two things read that field, and
#      both were wrong: the owner saw no capability name, and
#      ``scripts/exchange.py`` gates the durable consequential-action audit
#      log on it - so a specialist granted ``communication`` really sent a
#      message and nothing at all was written to the log, while Alfred doing
#      the identical thing wrote an entry. Repaired by reading the capability
#      the handler itself reported (its result payload, as ``_invited_in``
#      already does) and, for a failed run that carries no payload, the
#      capability ``SpecialistCapabilities`` recorded that it selected - the
#      specialist-side counterpart of
#      ``ManagerSkills.selected_capability_for``, which ``_own_capability``
#      has always read for Alfred.
#
#      Found while doing so, and repaired with it: a turn that only *raised*
#      a confirmation ("Send X to Y? (yes/no)") was itself written to that
#      log as succeeded=True, against a log whose own docstring promises
#      succeeded=True means the tool ran. Every real send was double-counted
#      and claimed before the person had answered.
# ===========================================================================

ON_WINDOWS = sys.platform.startswith("win")
ARGOS_INSTALLED = build_translator(type("_TranslationChoice", (), {"translation": "argos"})()) is not None
LOCAL_MODEL = REPO_ROOT / "data" / "models" / "llm" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
# The local model tests also require llama-cpp-python to be installed.
def _llama_available() -> bool:
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False
LOCAL_MODEL_USABLE = LOCAL_MODEL.is_file() and _llama_available()


def _wav(tmp_path, sentence: str) -> bytes:
    """Real speech from Windows' own synthesiser - no network, no key, the
    identical instrument tests/test_campaign_03_file_media_interop.py already
    uses to prove real transcription."""
    path = tmp_path / "speech.wav"
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{sentence}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, timeout=120)
    return path.read_bytes()


class _TextProbe(LLMProvider):
    """A real LLMProvider double registered through the real factory seam -
    the text counterpart of ``_VisionProbe`` above, and used for exactly the
    same reason: to prove which Brain a capability actually reached, without
    a paid call. It never stands in for a capability's own work."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.prompts: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "closure_text_probe"

    def complete(self, prompt, *, system=None):
        self.prompts.append((str(prompt), str(system or "")))
        if "should_retain" in str(prompt) or "should_retain" in str(system or ""):
            return Completion(text='{"should_retain": false, "content": null}', provider=self.name)
        return Completion(text=self._text, provider=self.name)


@pytest.mark.skipif(not ARGOS_INSTALLED, reason="real offline language packs")
class TestTranslateReachesAnAuthorizedSpecialistAndSurvivesBeingAddressedByName:
    """The repaired defect, and the capability's own real offline path.

    Deterministic, so this is genuine Half Brain evidence: no reasoning
    provider is configured in any test here, and a real translation still
    comes back."""

    def test_a_granted_specialist_translates_for_real_addressed_by_name(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, translation="argos"))
        _run(system, "agent create nova Nova|languages|translate")

        turn = _say(system, "Nova, translate 'the harvest begins at dawn' into Italian", to="Nova")
        assert turn.succeeded, turn.outcome
        assert turn.handled_by == "Nova"
        assert turn.capability == "translate"
        assert "raccolto" in turn.outcome.lower()

    def test_the_manager_addressed_by_name_is_translated_too(self, tmp_path, monkeypatch):
        """The same defect, on Alfred: addressing him by name refused a
        translation he plainly does when unaddressed."""
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, translation="argos"))

        named = _say(system, "Alfred, translate 'the harvest begins at dawn' into Italian")
        plain = _say(system, "translate 'the harvest begins at dawn' into Italian")
        assert named.succeeded and plain.succeeded
        assert named.capability == plain.capability == "translate"
        assert named.outcome == plain.outcome

    def test_an_uninstalled_language_is_refused_honestly_and_never_guessed(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, translation="argos"))
        _run(system, "agent create nova Nova|languages|translate")

        turn = _say(system, "Nova, translate 'the harvest begins at dawn' into Swahili", to="Nova")
        assert not turn.succeeded
        assert "won't guess" in turn.outcome
        # Names which packs *are* installed, not only that this one is not.
        assert "Italian" in turn.outcome

    def test_turning_the_engine_off_in_the_control_center_really_stops_it(self, tmp_path, monkeypatch):
        """Provider selection affects execution, never only configuration."""
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, translation="argos"))
        _run(system, "agent create nova Nova|languages|translate")
        assert _say(system, "Nova, translate 'good morning' into Italian", to="Nova").succeeded

        _run(system, "provider translation_provider use null")
        off = _say(system, "Nova, translate 'good evening' into Italian", to="Nova")
        assert not off.succeeded and "translation_provider" in off.outcome

        _run(system, "provider translation_provider use argos")
        assert _say(system, "Nova, translate 'good evening' into Italian", to="Nova").succeeded

    def test_an_ungranted_specialist_cannot_translate(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, translation="argos"))
        _run(system, "agent create nova Nova|languages")

        turn = _say(system, "Nova, translate 'good morning' into Italian", to="Nova")
        assert not turn.succeeded
        assert "translate, which I'm not authorised for" in turn.outcome


@pytest.mark.skipif(not (ON_WINDOWS and ARGOS_INSTALLED), reason="real local speech and language packs")
class TestTranslateMediaReachesAnAuthorizedSpecialist:
    """A genuinely different path from ``translate``: real audio in, real
    recognition, real translation, a real subtitle artifact out. Proving
    ``translate`` proves none of it."""

    def _system(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        return su.build_live_system(_settings(
            tmp_path, media="local", translation="argos", video_understanding="local_whisper",
        ))

    def test_a_granted_specialist_captions_a_real_recording_into_another_language(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create nova Nova|media languages|translate_media")
        _upload(system, "Nova", _wav(tmp_path, "The harvest begins at dawn."), "note.wav", "audio/wav")

        turn = _say(system, "Nova, translate this recording into Italian with subtitles", to="Nova")
        assert turn.succeeded, turn.outcome
        assert turn.handled_by == "Nova"
        assert turn.capability == "translate_media"
        assert "into it" in turn.outcome

        subtitles = [a for a in system.runtime.artifacts.all() if "vtt" in a.latest.renditions]
        assert len(subtitles) == 1
        assert subtitles[0].owner_agent == "Nova"
        captions = Path(subtitles[0].latest.renditions["vtt"]).read_text(encoding="utf-8").lower()
        assert "raccolto" in captions

    def test_an_ungranted_specialist_cannot_and_nothing_is_produced(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create nova Nova|media languages")
        _upload(system, "Nova", _wav(tmp_path, "The harvest begins at dawn."), "note.wav", "audio/wav")

        turn = _say(system, "Nova, translate this recording into Italian with subtitles", to="Nova")
        assert not turn.succeeded
        assert "translate_media, which I'm not authorised for" in turn.outcome
        assert not [a for a in system.runtime.artifacts.all() if "vtt" in a.latest.renditions]


class TestCreateAndReviseDiagramReachAnAuthorizedSpecialist:
    """Half-Brain-safe: a request that already names its subject needs no
    model, so a real PNG comes back with no reasoning provider at all."""

    MAKE = "Nova, make a diagram of a two-stage water filtration system"
    REVISE = "Nova, revise the diagram: add a third stage with a carbon filter"

    def test_a_granted_specialist_creates_then_revises_one_real_artifact(self, system):
        _run(system, "agent create nova Nova|diagrams|create_diagram,revise_diagram")

        created = _say(system, self.MAKE, to="Nova")
        assert created.succeeded, created.outcome
        assert created.handled_by == "Nova" and created.capability == "create_diagram"
        images = [a for a in system.runtime.artifacts.all() if a.type == "image"]
        assert len(images) == 1 and images[0].owner_agent == "Nova"
        first_png = Path(images[0].latest.renditions["png"]).read_bytes()
        assert first_png.startswith(b"\x89PNG")

        revised = _say(system, self.REVISE, to="Nova")
        assert revised.succeeded, revised.outcome
        assert revised.capability == "revise_diagram"
        images = [a for a in system.runtime.artifacts.all() if a.type == "image"]
        # The same artifact identity, one real version further along.
        assert len(images) == 1 and images[0].latest.version == 2
        assert "carbon" in images[0].latest.title.lower()
        assert Path(images[0].latest.renditions["png"]).read_bytes() != first_png

    def test_a_second_specialist_cannot_revise_it_by_explicit_id(self, system):
        _run(system, "agent create nova Nova|diagrams|create_diagram,revise_diagram")
        _run(system, "agent create iris Iris|diagrams|revise_diagram")
        assert _say(system, self.MAKE, to="Nova").succeeded
        owned = [a for a in system.runtime.artifacts.all() if a.type == "image"][0]

        turn = _say(system, f"Iris, revise the diagram artifact {owned.artifact_id}: make it a single stage", to="Iris")
        assert not turn.succeeded
        assert "couldn't find that diagram to revise" in turn.outcome
        after = [a for a in system.runtime.artifacts.all() if a.type == "image"]
        assert len(after) == 1 and after[0].owner_agent == "Nova" and after[0].latest.version == 1

    def test_a_vague_request_with_no_brain_is_refused_rather_than_invented(self, system):
        _run(system, "agent create nova Nova|diagrams|create_diagram")
        turn = _say(system, "Nova, make me a diagram", to="Nova")
        assert not turn.succeeded
        assert "no reasoning capability is configured" in turn.outcome
        assert not [a for a in system.runtime.artifacts.all() if a.type == "image"]

    def test_a_vague_request_is_composed_by_the_specialists_own_brain(self, tmp_path, monkeypatch):
        from src.providers import factory

        probe = _TextProbe("A labelled cutaway of a two-stage water filter")
        monkeypatch.setitem(factory._BUILDERS, "closure_text_probe", lambda settings, env=None, account=None: probe)
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(
            tmp_path, media="local", provider="closure_text_probe", brain="llm", specialist_brain="llm",
        ))
        _run(system, "agent create nova Nova|diagrams|create_diagram")

        turn = _say(system, "Nova, make me a diagram", to="Nova")
        assert turn.succeeded, turn.outcome
        images = [a for a in system.runtime.artifacts.all() if a.type == "image"]
        assert len(images) == 1 and images[0].owner_agent == "Nova"
        assert images[0].latest.title == "A labelled cutaway of a two-stage water filter"
        # Nova's own Brain, never Alfred's: the system prompt is the specialist's.
        assert any("specialist agent" in system_prompt for _p, system_prompt in probe.prompts)

    def test_an_ungranted_specialist_creates_nothing(self, system):
        _run(system, "agent create nova Nova|diagrams")
        turn = _say(system, self.MAKE, to="Nova")
        assert not turn.succeeded
        assert "create_diagram, which I'm not authorised for" in turn.outcome
        assert not system.runtime.artifacts.all()


class TestImageAuthenticityAndComparisonReachAnAuthorizedSpecialist:
    """Both are measurement rather than reasoning - real in Half Brain, and
    neither is ever allowed to become a verdict."""

    def _jpeg(self) -> bytes:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), (200, 30, 30)).save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()

    def test_authenticity_reports_real_evidence_and_refuses_to_conclude(self, system):
        _run(system, "agent create nova Nova|images|image_authenticity")
        _upload(system, "Nova", self._jpeg(), "photo.jpg", "image/jpeg")

        turn = _say(system, "Nova, is this photo edited or AI generated?", to="Nova")
        assert turn.succeeded, turn.outcome
        assert turn.handled_by == "Nova" and turn.capability == "image_authenticity"
        assert "evidence, not a verdict" in turn.outcome
        assert "Error-level analysis" in turn.outcome
        assert "can't tell you for certain" in turn.outcome

    def test_comparison_measures_two_real_images(self, system):
        _run(system, "agent create nova Nova|images|image_comparison")
        _upload(system, "Nova", _png((10, 20, 30)), "a.png", "image/png")
        _upload(system, "Nova", _png((10, 20, 200)), "b.png", "image/png")

        turn = _say(system, "Nova, compare these two images and tell me the differences", to="Nova")
        assert turn.succeeded, turn.outcome
        assert turn.capability == "image_comparison"
        assert "measurements, not a guess" in turn.outcome
        assert "Perceptual-hash distance" in turn.outcome

    def test_neither_reaches_an_image_that_belongs_to_another_agent(self, system):
        _run(system, "agent create nova Nova|images|image_authenticity,image_comparison")
        _upload(system, "Alfred", _png(), "alfred-a.png", "image/png")
        _upload(system, "Alfred", _png((3, 4, 5)), "alfred-b.png", "image/png")

        authenticity = _say(system, "Nova, is this photo edited?", to="Nova")
        assert not authenticity.succeeded and "Which image?" in authenticity.outcome
        comparison = _say(system, "Nova, compare these two images", to="Nova")
        assert not comparison.succeeded and "I need two images" in comparison.outcome
        assert {a.owner_agent for a in system.runtime.artifacts.all()} == {"Alfred"}

    def test_ungranted_is_refused_for_both(self, system):
        _run(system, "agent create nova Nova|images")
        _upload(system, "Nova", _png(), "a.png", "image/png")
        assert "image_authenticity, which I'm not authorised for" in _say(
            system, "Nova, is this photo edited?", to="Nova").outcome
        _upload(system, "Nova", _png((3, 4, 5)), "b.png", "image/png")
        assert "image_comparison, which I'm not authorised for" in _say(
            system, "Nova, compare these two images", to="Nova").outcome


@pytest.mark.skipif(not ON_WINDOWS, reason="Windows' own speech engine")
class TestTranscribeMediaReachesAnAuthorizedSpecialist:
    def _system(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        return su.build_live_system(_settings(tmp_path, media="local", video_understanding="windows"))

    def test_a_granted_specialist_produces_a_real_transcript_and_keeps_it(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create nova Nova|media|transcribe_media")
        _upload(system, "Nova", _wav(tmp_path, "The meeting is moved to Friday afternoon."), "note.wav", "audio/wav")

        turn = _say(system, "Nova, transcribe this audio", to="Nova")
        assert turn.succeeded, turn.outcome
        assert turn.handled_by == "Nova" and turn.capability == "transcribe_media"
        assert "meeting" in turn.outcome.lower()
        kept = [a for a in system.runtime.artifacts.all() if a.latest.title.startswith("Transcript - ")]
        assert len(kept) == 1 and kept[0].owner_agent == "Nova"

    def test_an_image_is_not_a_recording(self, tmp_path, monkeypatch):
        """Modality is a real requirement, not a word: "media" does not make
        a picture transcribable."""
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create nova Nova|media|transcribe_media")
        _upload(system, "Nova", _png(), "picture.png", "image/png")

        turn = _say(system, "Nova, transcribe this recording", to="Nova")
        assert not turn.succeeded
        assert "I don't see audio or video" in turn.outcome

    def test_turning_the_recogniser_off_is_honest_rather_than_an_invented_transcript(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create nova Nova|media|transcribe_media")
        _upload(system, "Nova", _wav(tmp_path, "The meeting is moved to Friday afternoon."), "note.wav", "audio/wav")
        assert _say(system, "Nova, transcribe this audio", to="Nova").succeeded

        _run(system, "provider video_understanding_provider use null")
        turn = _say(system, "Nova, transcribe this audio", to="Nova")
        assert not turn.succeeded
        assert "rather say so than invent one" in turn.outcome

    def test_an_ungranted_specialist_cannot_transcribe(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create nova Nova|media")
        _upload(system, "Nova", _wav(tmp_path, "The meeting is moved to Friday afternoon."), "note.wav", "audio/wav")

        turn = _say(system, "Nova, transcribe this audio", to="Nova")
        assert not turn.succeeded
        assert "transcribe_media, which I'm not authorised for" in turn.outcome


class TestGeneralAssistanceReachesTheSpecialistsOwnReasoningNotAlfreds:
    """"general_assistance" must not quietly mean "the Manager can answer"."""

    ASK = "Nova, write a python function that reverses a list"

    def test_with_nothing_configured_it_refuses_honestly_rather_than_guessing(self, system):
        _run(system, "agent create nova Nova|general work|general_assistance")
        turn = _say(system, self.ASK, to="Nova")
        assert not turn.succeeded
        assert turn.handled_by == "Nova"
        assert "I won't guess at code" in turn.outcome
        assert "local_assist_provider" in turn.outcome

    @pytest.mark.skipif(not LOCAL_MODEL_USABLE, reason="local model file or llama-cpp-python not available")
    def test_half_brain_really_writes_code_through_the_local_assistant(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, media="local", local_assist="local"))
        _run(system, "agent create nova Nova|general work|general_assistance")

        turn = _say(system, self.ASK, to="Nova")
        assert turn.succeeded, turn.outcome
        assert turn.handled_by == "Nova" and turn.capability == "general_assistance"
        assert "def " in turn.outcome
        # Attributed honestly - never presented as verified reasoning.
        assert "small local model" in turn.outcome
        code = [a for a in system.runtime.artifacts.all() if a.type == "code"]
        assert len(code) == 1 and code[0].owner_agent == "Nova"

    def test_full_brain_answers_from_the_specialists_own_brain(self, tmp_path, monkeypatch):
        from src.providers import factory

        probe = _TextProbe("A heat pump moves warmth by compressing a refrigerant.")
        monkeypatch.setitem(factory._BUILDERS, "closure_text_probe", lambda settings, env=None, account=None: probe)
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(
            tmp_path, media="local", provider="closure_text_probe", brain="llm", specialist_brain="llm",
        ))
        _run(system, "agent create nova Nova|general work|general_assistance")

        turn = _say(system, "Nova, explain how a heat pump moves warmth in winter", to="Nova")
        assert turn.succeeded, turn.outcome
        assert turn.handled_by == "Nova"
        assert turn.outcome == "A heat pump moves warmth by compressing a refrigerant."
        assert any("specialist agent" in system_prompt for _p, system_prompt in probe.prompts)

    def test_an_ungranted_specialist_is_refused(self, system):
        _run(system, "agent create nova Nova|general work")
        turn = _say(system, self.ASK, to="Nova")
        assert not turn.succeeded
        assert "general_assistance, which I'm not authorised for" in turn.outcome


class TestTheTrailNamesTheCapabilityASpecialistActuallyRan:
    """Repair 2. The Control Center is a management *and assessment* surface:
    what a specialist did must be visible there, and a consequential action
    must reach the durable audit log whoever performed it."""

    def test_every_capability_a_specialist_runs_is_named_including_failures(self, system):
        _run(system, "agent create nova Nova|images|create_diagram,revise_diagram,image_comparison")

        made = _say(system, "Nova, make a diagram of a water filter", to="Nova")
        assert made.succeeded and made.capability == "create_diagram"
        revised = _say(system, "Nova, revise the diagram: add a carbon stage", to="Nova")
        assert revised.succeeded and revised.capability == "revise_diagram"
        # A failure carries no payload at all, and is still named.
        failed = _say(system, "Nova, compare these two images", to="Nova")
        assert not failed.succeeded and failed.capability == "image_comparison"

    def test_a_specialists_real_consequential_action_is_written_to_the_audit_log(self, tmp_path, monkeypatch):
        from src.providers import communication_factory
        from src.services.audit.action_log import load_action_log

        provider = _WorkingCommunicationProvider()
        monkeypatch.setitem(
            communication_factory._BUILDERS, "closure_comm_probe",
            lambda settings, env=None, account=None: provider,
        )
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, communication_provider="closure_comm_probe"))
        log = tmp_path / "action_log.json"

        def say(text, to=None):
            return exchange(system.channel, system.conversation, text, addressed=to, action_log_path=log)

        _run(system, "agent create clara Clara|operations|communication")
        raised = say("Clara, send Kai a message saying hello", to="Clara")
        assert raised.succeeded and "(yes/no)" in raised.outcome
        # Nothing was executed, so nothing is recorded yet.
        assert load_action_log(log) == ()

        confirmed = say("yes", to="Clara")
        assert confirmed.succeeded and provider.sent == [("Kai", "saying hello")]
        assert [(e.actor, e.capability, e.succeeded) for e in load_action_log(log)] == [
            ("Clara", "communication", True),
        ]

        say("send Rema a message saying hello")
        say("yes")
        assert [(e.actor, e.capability) for e in load_action_log(log)] == [
            ("Clara", "communication"), ("Alfred", "communication"),
        ]


class _RecordingLiveInfo(LiveInfoProvider):
    """A real LiveInfoProvider double registered through the actual
    ``live_info_factory._BUILDERS`` seam - it records the query it is
    genuinely handed, which is the only way to see what the capability
    actually searched for."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    @property
    def name(self) -> str:
        return "closure_search_probe"

    def fetch(self, query: str) -> LiveInfoResult:
        self.queries.append(query)
        return LiveInfoResult(
            query=query,
            content="Heat pumps move warmth by compressing a refrigerant. [source: probe]",
            source="closure_search_probe",
            retrieved_at=live_info_utc_now(),
        )


class TestWhatWasAskedIsNotWhoWasAsked:
    """``translation_request``'s repair, found a second time in
    ``general_assistance``.

    Conversation legitimately keeps the whole utterance - who is being
    spoken to is part of what was said. But when a handler takes the
    request's own words and uses them as the *thing itself* - the query to
    search for, the prompt to write code from, the title to save it under -
    the name of the agent being asked is not part of that thing.
    ``_CODE_REQUEST_VERB`` is anchored, so with a name in front of it
    nothing was stripped at all.
    """

    @pytest.mark.skipif(not LOCAL_MODEL_USABLE, reason="local model file or llama-cpp-python not available")
    def test_a_saved_code_artifact_is_titled_by_the_ask_not_the_address(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, media="local", local_assist="local"))
        _run(system, "agent create nova Nova|general work|general_assistance")

        turn = _say(system, "Nova, write a python function that reverses a list", to="Nova")
        assert turn.succeeded, turn.outcome
        code = [a for a in system.runtime.artifacts.all() if a.type == "code"]
        assert len(code) == 1 and code[0].owner_agent == "Nova"
        # Before the repair this read "Nova, write a python function that
        # reverses a list" - the agent's name saved as the title of the work.
        assert code[0].latest.title == "A python function that reverses a list"

    def test_the_search_query_carries_the_ask_not_the_address(self, tmp_path, monkeypatch):
        from src.providers import live_info_factory

        probe = _RecordingLiveInfo()
        monkeypatch.setitem(live_info_factory._BUILDERS, "closure_search_probe", lambda settings: probe)
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, live_info_provider="closure_search_probe"))
        _run(system, "agent create nova Nova|general work|general_assistance")

        turn = _say(system, "Nova, research how heat pumps work in winter", to="Nova")
        # The real provider was genuinely reached, with the ask alone.
        assert probe.queries == ["research how heat pumps work in winter"]
        # Half Brain: real evidence acquired, reasoning over it honestly refused.
        assert not turn.succeeded
        assert "found sources but couldn't properly reason" in turn.outcome

    def test_the_manager_addressed_by_name_is_treated_identically(self, tmp_path, monkeypatch):
        from src.providers import live_info_factory

        probe = _RecordingLiveInfo()
        monkeypatch.setitem(live_info_factory._BUILDERS, "closure_search_probe", lambda settings: probe)
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, live_info_provider="closure_search_probe"))

        _say(system, "Alfred, research how heat pumps work in winter")
        assert probe.queries == ["research how heat pumps work in winter"]


class TestAGrantedCapabilityThatIsSelectedByWordOverlapIsActuallyDispatchable:
    """``knowledge`` and ``live_information`` deliberately have no ``claims``
    predicate - "is this asking about something current?" has no reliable
    shape, so word overlap is how the Manager himself selects them.

    ``SpecialistCapabilities.__call__`` dispatched on ``claims`` alone, which
    made both of them undispatchable for *every* specialist: the owner could
    grant them, ``capabilities access`` reported GRANTED, and the capability
    could never run. Neither an earlier by-inspection sweep nor any test
    caught it, because both looked at the handler rather than at whether
    anything could reach the handler.
    """

    def _system(self, tmp_path, monkeypatch):
        from src.providers import live_info_factory

        probe = _RecordingLiveInfo()
        monkeypatch.setitem(live_info_factory._BUILDERS, "closure_search_probe", lambda settings: probe)
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(
            tmp_path, media="local", live_info_provider="closure_search_probe",
        ))
        return system, probe

    def test_a_granted_specialist_reaches_the_real_live_information_provider(self, tmp_path, monkeypatch):
        system, probe = self._system(tmp_path, monkeypatch)
        _run(system, "agent create nova Nova|current affairs|live_information")

        turn = _say(system, "Nova, what is happening in the news today", to="Nova")
        assert turn.handled_by == "Nova"
        assert turn.capability == "live_information"
        # The real provider was genuinely reached, as itself, with the ask.
        assert probe.queries == ["what is happening in the news today"]
        # Half Brain: real evidence acquired, synthesis honestly refused.
        assert not turn.succeeded
        assert "found current information but couldn't properly reason" in turn.outcome

    def test_alfred_and_a_granted_specialist_now_reach_it_identically(self, tmp_path, monkeypatch):
        system, probe = self._system(tmp_path, monkeypatch)
        _run(system, "agent create nova Nova|current affairs|live_information")

        alfred = _say(system, "what is happening in the news today")
        probe.queries.clear()
        nova = _say(system, "Nova, what is happening in the news today", to="Nova")
        assert alfred.capability == nova.capability == "live_information"
        assert probe.queries == ["what is happening in the news today"]

    def test_an_ungranted_specialist_never_reaches_the_provider(self, tmp_path, monkeypatch):
        system, probe = self._system(tmp_path, monkeypatch)
        _run(system, "agent create iris Iris|current affairs")

        turn = _say(system, "Iris, what is happening in the news today", to="Iris")
        assert not turn.succeeded
        assert probe.queries == []

    def test_revoking_it_stops_the_next_request(self, tmp_path, monkeypatch):
        system, probe = self._system(tmp_path, monkeypatch)
        _run(system, "agent create kai Kai|current affairs|live_information")
        assert _say(system, "Kai, what is happening in the news today", to="Kai").capability == "live_information"

        probe.queries.clear()
        _run(system, "capabilities revoke Kai live_information")
        turn = _say(system, "Kai, what is happening in the news today", to="Kai")
        assert turn.capability != "live_information"
        assert probe.queries == []

    def test_a_capability_that_is_not_scoreable_is_still_never_pulled_in_by_overlap(self, system):
        """The protection the claims-only rule existed to give, kept intact:
        almost every capability sets ``scoreable=False`` precisely because its
        declared words also occur in ordinary domain talk."""
        _run(system, "agent create nova Nova|3D modelling|create_3d_model")

        turn = _say(system, "Nova, tell me about the models you have made", to="Nova")
        assert turn.capability != "create_3d_model"
        # Nothing was built - the grant did not pull her own domain talk in.
        assert not [a for a in system.runtime.artifacts.all() if a.type == "model3d"]

    def test_the_repair_can_only_ever_affect_an_explicitly_granted_capability(self):
        """The property that bounds the change, pinned so a future capability
        cannot widen it silently.

        ``_best_scored`` only ever considers capabilities in an agent's
        *reachable* set that declare ``scoreable`` and have no ``own_path``.
        Every ``every_agent`` capability is either reached through its own
        route or is not scoreable, so for an agent granted nothing, that set
        is empty and dispatch is byte-identical to the claims-only rule this
        replaced. The entire blast radius is the two grantable capabilities
        that asked to be selected by word overlap.
        """
        from src.control_center.capability_access import EVERY_AGENT, GRANTABLE, authority_of
        from src.manager.capabilities import MANAGER_CAPABILITIES

        scoreable_without_own_route = {
            name for name, capability in MANAGER_CAPABILITIES.items()
            if authority_of(capability) == EVERY_AGENT and capability.scoreable and not capability.own_path
        }
        assert scoreable_without_own_route == set(), (
            "an every_agent capability became selectable by word overlap without its own "
            "route - that widens specialist dispatch for agents nobody granted anything"
        )

        grantable_and_scoreable = {
            name for name, capability in MANAGER_CAPABILITIES.items()
            if authority_of(capability) == GRANTABLE and capability.scoreable
        }
        assert grantable_and_scoreable == {"knowledge", "live_information"}


class _FakeResolver:
    """The two questions ``names_equipped_connection`` asks a resolver, and
    nothing else - deliberately not a ConnectionResolver, so this pins the
    rule rather than that class's construction."""

    def __init__(self, names, default_name, default_operations=("do_thing",)):
        self.names = tuple(names)
        self.default_name = default_name
        self._default_operations = tuple(default_operations)

    def get(self, name=None):
        class _Connection:
            supported_operations = self._default_operations
        return _Connection() if (name or self.default_name) == self.default_name else object()


class TestNamingAConnectionIsTheRoutingQuestion:
    """``named_connection`` answers "which *non-default* one", which is right
    for choosing between connections and wrong as a routing gate: it made the
    one connection the owner configured as default the only one they could
    not name."""

    def test_a_non_default_connection_is_named(self):
        from src.manager.capabilities import names_equipped_connection

        resolver = _FakeResolver(("clinic", "library"), "library")
        assert names_equipped_connection("check my clinic account: list the appointments", resolver)

    def test_the_default_connection_is_named_too(self):
        from src.manager.capabilities import names_equipped_connection

        resolver = _FakeResolver(("clinic", "library"), "library")
        assert names_equipped_connection("check my library account: find the book", resolver)

    def test_a_connection_that_was_never_equipped_is_not(self):
        from src.manager.capabilities import names_equipped_connection

        resolver = _FakeResolver(("clinic", "library"), "library")
        assert not names_equipped_connection("check my pharmacy account: collect it", resolver)

    def test_the_no_op_default_is_never_routed_to_by_name(self):
        """An unconfigured installation runs with the "null" connection as its
        default. It supports no operations, so naming it could not mean
        anything to do - and including it would have changed routing for every
        installation that has declared nothing at all."""
        from src.manager.capabilities import names_equipped_connection

        resolver = _FakeResolver(("null",), "null", default_operations=())
        assert not names_equipped_connection("check my null account: do it", resolver)

    def test_named_connection_itself_still_skips_the_default(self):
        """The narrower question is unchanged - ``_select_connection`` relies
        on it, and an unqualified request must still mean the default."""
        from src.manager.capabilities import named_connection

        assert named_connection("check my library account: find the book",
                                ("clinic", "library"), "library") is None
        assert named_connection("check my clinic account: list them",
                                ("clinic", "library"), "library") == "clinic"


class TestASpecialistReachesADeclaredConnectionByName:
    """The Manager had a connection-naming routing gate
    (``ManagerSkills._names_equipped_connection``); the specialist dispatcher
    did not. So a specialist genuinely granted ``connected_action`` could
    reach a connection only through the literal phrase "check my connected
    account" - never by naming the service, which is the natural way to pick
    one when several are declared. Measured against two real HTTP services:
    Alfred reached the library and the clinic by name; Rhea, granted the
    capability, reached neither."""

    def _system(self, tmp_path, monkeypatch):
        _connection_sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path))
        _run(system, "services library declare http://127.0.0.1:9401 find_book:GET:/books:open")
        _run(system, "services clinic declare http://127.0.0.1:9402 list_appointments:GET:/appointments:open")
        return su.build_live_system(_settings(tmp_path, connection_provider="library"))

    def _no_real_call(self, monkeypatch):
        import urllib.request

        class _Response:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": true}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response())

    def test_the_manager_reaches_both_the_default_and_a_non_default_by_name(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        self._no_real_call(monkeypatch)

        assert _say(system, "check my library account: find the book").capability == "connected_action"
        assert _say(system, "check my clinic account: list the appointments").capability == "connected_action"

    def test_a_granted_specialist_reaches_both_by_name(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        self._no_real_call(monkeypatch)
        _run(system, "agent create rhea Rhea|bookings|connected_action")

        by_default_name = _say(system, "Rhea, check my library account: find the book", to="Rhea")
        assert by_default_name.handled_by == "Rhea"
        assert by_default_name.capability == "connected_action"

        by_other_name = _say(system, "Rhea, check my clinic account: list the appointments", to="Rhea")
        assert by_other_name.handled_by == "Rhea"
        assert by_other_name.capability == "connected_action"

    def test_an_ungranted_specialist_reaches_neither(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        _run(system, "agent create iris Iris|bookings")

        for text in ("Iris, check my library account: find the book",
                     "Iris, check my clinic account: list the appointments",
                     "Iris, check my connected account: find the book"):
            turn = _say(system, text, to="Iris")
            assert turn.capability != "connected_action", text
            assert not turn.succeeded, text

    def test_revoking_it_stops_naming_a_connection_from_reaching_it(self, tmp_path, monkeypatch):
        system = self._system(tmp_path, monkeypatch)
        self._no_real_call(monkeypatch)
        _run(system, "agent create rhea Rhea|bookings|connected_action")
        assert _say(system, "Rhea, check my library account: find the book",
                    to="Rhea").capability == "connected_action"

        _run(system, "capabilities revoke Rhea connected_action")
        turn = _say(system, "Rhea, check my library account: find the book", to="Rhea")
        assert turn.capability != "connected_action"


class TestDisablingACapabilityStopsRealWorkNotJustRegistryMembership:
    """``test_capability_authorization_and_defined_specialists.py`` already
    pins that a disabled capability is gone from the filtered registry for
    every agent. This pins the other half - that a specialist who *was*
    granted it then actually produces nothing when asked - because "absent
    from the registry" and "produces no artifact at runtime" are the two
    facts this campaign exists to keep apart.
    """

    MAKE = "Nova, make a diagram of a two-stage water filtration system"

    def test_a_granted_specialist_builds_nothing_once_it_is_disabled_and_builds_again_once_enabled(self, system):
        _run(system, "agent create nova Nova|diagrams|create_diagram")
        assert _say(system, self.MAKE, to="Nova").succeeded
        assert len([a for a in system.runtime.artifacts.all() if a.type == "image"]) == 1

        _run(system, "capabilities disable create_diagram")
        turn = _say(system, self.MAKE, to="Nova")
        assert not turn.succeeded
        assert turn.capability != "create_diagram"
        # The real evidence: nothing new was made.
        assert len([a for a in system.runtime.artifacts.all() if a.type == "image"]) == 1
        # And the owner is told why, in the access view.
        access = _run(system, "capabilities access Nova")
        assert "create_diagram - disabled for every agent" in access

        _run(system, "capabilities enable create_diagram")
        again = _say(system, self.MAKE, to="Nova")
        assert again.succeeded and again.handled_by == "Nova"
        assert len([a for a in system.runtime.artifacts.all() if a.type == "image"]) == 2

    def test_holding_the_tool_is_not_being_authorized_for_the_capability(self, system):
        """``manager_tools`` builds every tool unconditionally, for every
        agent - so a specialist genuinely holds ``artifact_production``
        whether or not it may create anything with it. The capability grant
        is what decides, and this proves it at runtime rather than by
        reading the composition root."""
        _run(system, "agent create kai Kai|images|image_comparison")
        kai = system.manager.registry.get("Kai")
        tools = system.manager.specialist_capabilities.tools_for(kai)

        assert tools.permits("artifact_production"), "the tool is built unconditionally"
        assert system.manager.specialist_capabilities.authorised(kai) == ("image_comparison",)

        turn = _say(system, "Kai, make a diagram of a water filter", to="Kai")
        assert not turn.succeeded
        assert "create_diagram, which I'm not authorised for" in turn.outcome
        assert not [a for a in system.runtime.artifacts.all() if a.type == "image"]
