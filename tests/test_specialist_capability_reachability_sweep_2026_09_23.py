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
import json
import subprocess
from pathlib import Path

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
import src.services.connections.service_registry as service_registry_module
import src.services.credentials.account_store_file as account_store_file_module
from scripts.exchange import exchange
from src.config.settings import Settings
from src.providers.base import Completion, LLMProvider

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
