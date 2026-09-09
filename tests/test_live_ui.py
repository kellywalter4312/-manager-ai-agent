"""The live seam is the same seam, now able to carry a message.

    ControlCenter (reads)  ->  snapshot (data)  ->  a served page
    Conversation  (record) <-  exchange()      <-  a posted message

Phase 6 changed what "live" means: `scripts/serve_ui.py` used to rebuild a
whole throwaway system per request (`export_ui.take_snapshot`); it now
holds **one** `LiveSystem` - one runtime, one Manager, one Conversation -
for the life of the process, so a live session can accumulate exactly the
way a terminal session already does. `TestTheRouteFunction` and
`TestTheServerHoldsNoSystemOfItsOwn` below are updated for that reason,
not weakened: they now prove the held system is exactly one, not zero
state rebuilt every time.

These tests call `route()`/`route_message()` directly rather than opening
a real socket. `tests/conftest.py::_no_network` makes any real `connect()`
- including to 127.0.0.1 - a hard failure for this whole suite, on
purpose: it is the guard that stops an accidental live call from reaching
a real, credentialed API and billing the account. The actual wire-level
behaviour (a browser polling and posting to a real running server) is
proven separately, by hand, outside pytest.
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

from scripts.export_ui import take_snapshot
from scripts.serve_ui import (
    DEFAULT_HOST,
    LiveSystem,
    MANAGEMENT_VERBS,
    build_live_system,
    route,
    route_command,
    route_message,
)
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.render import render_overview
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.sample_inbox import sample_inbox

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVE_UI = REPO_ROOT / "scripts" / "serve_ui.py"
EXPORT_UI = REPO_ROOT / "scripts" / "export_ui.py"


def settings():
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")


def live_system(mailbox=None, profile=None) -> LiveSystem:
    """A LiveSystem built the same way `build_live_system` does, with the
    same test doubles `tests/test_control_center.py::centre()` uses when a
    test needs a specific mailbox or profile (e.g. to force delegation)."""
    from src.composition import build_channel, build_conversation, build_manager, build_runtime

    runtime = build_runtime(settings())
    manager = build_manager(
        runtime,
        mailbox=mailbox if mailbox is not None else InMemoryMailbox(),
        profile=profile,
    )
    return LiveSystem(
        runtime=runtime,
        manager=manager,
        channel=build_channel(runtime, manager),
        conversation=build_conversation(manager),
        centre=ControlCenter(runtime, manager),
        lock=threading.Lock(),
    )


# ---------------------------------------------------------------------------
# take_snapshot(): unaffected by this milestone - still the static export's
# own assembly, still tested against its own contract.
# ---------------------------------------------------------------------------


class TestTheLiveAssemblyIsTheSameContract:
    def test_take_snapshot_returns_the_full_contract(self):
        data = take_snapshot(settings())
        for key in ("version", "agents", "conversation", "speech", "system", "history"):
            assert key in data, key

    def test_activity_and_avatar_are_populated_through_the_live_path(self):
        """Stage 17: the live path reads the same AgentPresence the
        static export does - no second, live-only source of truth."""
        data = take_snapshot(settings())
        for agent in data["agents"]:
            assert agent["activity"] == "idle"
            assert agent["avatar"]["agent_key"] == agent["name"]

    def test_the_live_path_grants_no_tools_beyond_the_static_export(self):
        tree = ast.parse(EXPORT_UI.read_text(encoding="utf-8"))
        take_snapshot_fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "take_snapshot"
        )
        call = next(
            node
            for node in ast.walk(take_snapshot_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_manager"
        )
        kwnames = {kw.arg for kw in call.keywords}
        assert "tools" not in kwnames
        assert "specialist_tools" not in kwnames


# ---------------------------------------------------------------------------
# route(): GET, reading the one held system - no socket involved
# ---------------------------------------------------------------------------


class TestTheRouteFunction:
    def test_snapshot_json_is_the_same_shape_the_static_export_uses(self):
        system = build_live_system(settings())
        status, body, content_type = route("/snapshot.json", system)
        assert status == 200
        assert content_type.startswith("application/json")
        data = json.loads(body)
        for key in ("version", "agents", "conversation", "speech", "system", "history"):
            assert key in data, key
        for agent in data["agents"]:
            assert agent["activity"] == "idle"
            assert agent["avatar"]["agent_key"] == agent["name"]

    def test_root_serves_the_rendered_shell_current_as_of_now(self):
        system = build_live_system(settings())
        status, body, content_type = route("/", system)
        assert status == 200
        assert content_type.startswith("text/html")
        page = body.decode("utf-8")
        assert "__SNAPSHOT__" not in page
        assert '"version": 1' in page or '"version":1' in page

    def test_unknown_paths_are_not_found(self):
        system = build_live_system(settings())
        status, _, _ = route("/nonexistent", system)
        assert status == 404

    def test_two_reads_serialize_independently_even_though_the_system_is_held(self):
        """The system behind these two calls is deliberately the SAME one
        (that is this milestone's whole point) - but `snapshot()` still
        builds a fresh dict/tuple structure on every call, so mutating a
        client-side copy of one response can never reach a later one."""
        system = build_live_system(settings())
        _, first_body, _ = route("/snapshot.json", system)
        first = json.loads(first_body)
        first["agents"][0]["name"] = "mutated"

        _, second_body, _ = route("/snapshot.json", system)
        second = json.loads(second_body)
        assert second["agents"][0]["name"] != "mutated"


class TestStatusAndActionsRoutes:
    """Foundation Repair #5 (reconciliation audit, 2026-09-05): the same
    questions a terminal session's `status`/`actions` commands already
    answer, now reachable through this project's second door - reusing
    the identical rendering/store, never a second JSON schema invented for
    the same facts (see route()'s own comment)."""

    def test_status_reuses_the_terminal_overview_rendering(self):
        system = build_live_system(settings())
        status, body, content_type = route("/status", system)
        assert status == 200
        assert content_type.startswith("text/plain")
        rendered = body.decode("utf-8")
        # The exact text a terminal session's own `status` command renders,
        # from the identical ControlCenter.overview() - not a paraphrase.
        assert rendered == render_overview(system.centre.overview())

    def test_actions_is_honestly_empty_before_anything_consequential_happened(self, tmp_path, monkeypatch):
        import scripts.serve_ui as serve_ui_module

        monkeypatch.setattr(serve_ui_module, "DEFAULT_ACTION_LOG_PATH", tmp_path / "action_log.json")
        system = build_live_system(settings())
        status, body, content_type = route("/actions", system)
        assert status == 200
        assert content_type.startswith("text/plain")
        assert body.decode("utf-8") == "no consequential actions recorded yet"

    def test_actions_reflects_a_real_recorded_entry(self, tmp_path, monkeypatch):
        import scripts.serve_ui as serve_ui_module
        from src.services.audit.action_log import ActionLogEntry, record_action

        log_path = tmp_path / "action_log.json"
        monkeypatch.setattr(serve_ui_module, "DEFAULT_ACTION_LOG_PATH", log_path)
        record_action(
            ActionLogEntry(
                timestamp="2026-09-05T12:00:00+00:00",
                actor="Alfred",
                capability="communication",
                succeeded=True,
            ),
            path=log_path,
        )

        system = build_live_system(settings())
        status, body, _ = route("/actions", system)
        assert status == 200
        assert "communication" in body.decode("utf-8")
        assert "Alfred" in body.decode("utf-8")


def confirm_and_run(command, system):
    """Drive the full two-step confirmation flow (Control Center repair
    item #3) for a write-classified command and return the final
    (status, result_text) - the same shape every read-only /command call
    already returns directly. Asserts the pending step actually happened
    along the way, so a write silently reverting to "execute immediately"
    would fail every caller of this helper, not just a dedicated test."""
    status, body, content_type = route_command(command, None, system)
    assert status == 200, body
    assert content_type.startswith("application/json"), "a write must describe itself first, not execute"
    payload = json.loads(body)
    assert payload["requires_confirmation"] is True
    token = payload["confirm_token"]

    status2, body2, content_type2 = route_command(command, token, system)
    assert content_type2.startswith("application/json")
    payload2 = json.loads(body2)
    assert payload2["requires_confirmation"] is False
    return status2, payload2["result"]


class TestCommandRoute:
    """Foundation Repair #5, part 2 (reconciliation audit, 2026-09-05):
    the administrative surface a terminal session already has -
    provider/credential management, connections, capabilities, voice,
    ecosystem - reachable through this project's second door. Every case
    here proves reuse, never a parallel implementation: the same
    run_command() a terminal calls, against the same real
    CredentialStore/EcosystemRegistry a terminal reads and writes.
    """

    def test_talk_is_refused_here_and_pointed_at_message(self):
        system = build_live_system(settings())
        status, body, content_type = route_command("talk hello", None, system)
        assert status == 400
        assert content_type.startswith("application/json")
        payload = json.loads(body)
        assert "/message" in payload["error"]

    def test_free_text_is_refused_the_same_way(self):
        system = build_live_system(settings())
        status, body, _ = route_command("what is the weather", None, system)
        assert status == 400

    def test_an_empty_command_is_rejected(self):
        system = build_live_system(settings())
        status, _, _ = route_command("", None, system)
        assert status == 400

    def test_a_non_string_command_is_rejected(self):
        system = build_live_system(settings())
        status, _, _ = route_command(None, None, system)
        assert status == 400

    def test_every_advertised_management_verb_is_actually_dispatchable(self, tmp_path, monkeypatch):
        """MANAGEMENT_VERBS is a claim about what this route does - proven
        here by actually calling every single one of them and confirming
        none is refused as unrecognised. Isolated exactly like
        TestCommandRouteProviderAccountManagement below: a bare `ecosystem`
        genuinely writes version-tracking state even to just list
        components (see run_ecosystem_command's own real behaviour), so
        this must not run against the real installation's files either.
        None of these bare verbs is administrative-write-classified, so
        every one still executes on the first call."""
        import scripts.control_center as control_center_module

        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
        monkeypatch.setattr(control_center_module, "DEFAULT_LEDGER_PATH", tmp_path / "ledger.json")
        system = build_live_system(settings())
        for verb in MANAGEMENT_VERBS:
            status, body, _ = route_command(verb, None, system)
            assert status == 200, (verb, body)

    def test_status_matches_the_terminal_rendering(self):
        system = build_live_system(settings())
        status, body, content_type = route_command("status", None, system)
        assert status == 200
        assert content_type.startswith("text/plain")
        assert "AGENT CONTROL CENTER" in body.decode("utf-8")

    def test_capabilities_reflects_the_real_registry(self):
        system = build_live_system(settings())
        _, body, _ = route_command("capabilities", None, system)
        text = body.decode("utf-8")
        assert "current_datetime" in text or "capabilit" in text.lower()

    def test_provider_with_no_args_lists_the_real_ecosystem(self):
        system = build_live_system(settings())
        _, body, _ = route_command("provider", None, system)
        assert "llm_provider" in body.decode("utf-8")

    def test_connections_reflects_the_real_gmail_boundary(self):
        system = build_live_system(settings())
        status, body, _ = route_command("connections", None, system)
        assert status == 200
        assert "gmail" in body.decode("utf-8").lower()

    def test_connections_management_actually_connects_a_real_service(self, tmp_path, monkeypatch):
        """The write half of connection management: additive, and
        persisted through the same CredentialStore provider accounts
        already use - proven by reading it back. Now gated by
        confirmation (repair item #3): the connect itself only takes
        effect after the second, token-bearing call."""
        import scripts.control_center as control_center_module

        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
        system = build_live_system(settings())

        status, result = confirm_and_run("connections whatsapp connect acc-1 token-value", system)
        assert status == 200
        assert "token-value" not in result

        _, listing, _ = route_command("connections", None, system)
        assert "whatsapp: connected" in listing.decode("utf-8")

    def test_voice_reports_the_real_speech_configuration(self):
        system = build_live_system(settings())
        status, body, _ = route_command("voice", None, system)
        assert status == 200

    def test_voice_management_actually_sets_a_real_preference(self):
        """The write half of voice management, not just visibility: the
        same governed preference `avatar <agent> <id>` already uses,
        applied to voice - proven by reading it back through a second,
        independent /command call. Voice preference-setting is
        deliberately NOT confirmation-gated (see classify_administrative_write's
        own docstring: cosmetic preferences, not the credential/provider/
        capability/evolution surface repair item #3 is about) - proven
        here by still executing immediately, unchanged."""
        system = build_live_system(settings())
        set_status, set_body, set_content_type = route_command("voice Arthur a-real-voice-name", None, system)
        assert set_status == 200
        assert set_content_type.startswith("text/plain")
        assert "FAILED" not in set_body.decode("utf-8")

        _, read_body, _ = route_command("voice Arthur", None, system)
        assert "a-real-voice-name" in read_body.decode("utf-8")


class TestCommandRouteProviderAccountManagement:
    """The write path: adding, selecting and revoking a real, persisted
    account through /command - the exact CredentialStore file a terminal
    session's own `provider` command reads and writes, isolated to
    tmp_path exactly the way test_independent_manageability.py's own
    provider-management tests already isolate it. Every write below now
    goes through the full two-step confirmation flow (repair item #3)."""

    def _isolated(self, tmp_path, monkeypatch):
        import scripts.control_center as control_center_module

        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")

    def test_adding_an_account_actually_persists_it(self, tmp_path, monkeypatch):
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())

        status, result = confirm_and_run(
            "provider llm_provider add anthropic my-account super-secret-key-value", system
        )
        assert status == 200

        _, listing, _ = route_command("provider llm_provider accounts", None, system)
        assert "anthropic/my-account" in listing.decode("utf-8")

    def test_the_secret_never_appears_in_the_http_response(self, tmp_path, monkeypatch):
        """The decisive check: a real, sensitive-looking credential value
        must never come back in any response body this route produces -
        across every step of the confirm flow, not just the final one."""
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())
        secret = "sk-live-do-not-leak-this-9f8e7d6c"
        command = f"provider llm_provider add anthropic my-account {secret}"

        pending_status, pending_body, _ = route_command(command, None, system)
        assert secret not in pending_body.decode("utf-8")
        token = json.loads(pending_body)["confirm_token"]

        _, executed_body, _ = route_command(command, token, system)
        assert secret not in executed_body.decode("utf-8")

        _, listing, _ = route_command("provider llm_provider accounts", None, system)
        assert secret not in listing.decode("utf-8")

        _, detail, _ = route_command("provider llm_provider", None, system)
        assert secret not in detail.decode("utf-8")

    def test_selecting_a_different_account_actually_changes_the_active_one(self, tmp_path, monkeypatch):
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())
        confirm_and_run("provider llm_provider add anthropic acc-a key-a", system)
        confirm_and_run("provider llm_provider add anthropic acc-b key-b", system)

        confirm_and_run("provider llm_provider select anthropic acc-a", system)
        _, listing, _ = route_command("provider llm_provider accounts", None, system)
        assert "[*] anthropic/acc-a" in listing.decode("utf-8")

        confirm_and_run("provider llm_provider select anthropic acc-b", system)
        _, listing2, _ = route_command("provider llm_provider accounts", None, system)
        assert "[*] anthropic/acc-b" in listing2.decode("utf-8")

    def test_revoking_an_account_actually_removes_it(self, tmp_path, monkeypatch):
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())
        confirm_and_run("provider llm_provider add anthropic temp-acc temp-key", system)
        _, before, _ = route_command("provider llm_provider accounts", None, system)
        assert "temp-acc" in before.decode("utf-8")

        confirm_and_run("provider llm_provider revoke anthropic temp-acc", system)
        _, after, _ = route_command("provider llm_provider accounts", None, system)
        assert "temp-acc" not in after.decode("utf-8")

    def test_this_route_never_touches_the_real_production_accounts_file(self, tmp_path, monkeypatch):
        """Every write test above must be isolated - this proves the
        isolation itself rather than assuming it: the real file's mtime
        (or absence) is unaffected by a write through the isolated path."""
        real_path = REPO_ROOT / "data" / "credentials" / "accounts.json"
        before = real_path.read_bytes() if real_path.exists() else None

        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())
        confirm_and_run("provider llm_provider add anthropic temp-acc temp-key", system)

        after = real_path.read_bytes() if real_path.exists() else None
        assert before == after


class TestAdministrativeConfirmation:
    """Control Center repair item #3 (2026-09-07): a write-classified
    command must never execute on its first call, must require the exact
    token issued for that exact command, must genuinely change nothing
    when never confirmed, and must be enforced here - server-side - not
    only by a UI that could be skipped with a raw request."""

    def _isolated(self, tmp_path, monkeypatch):
        import scripts.control_center as control_center_module

        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")

    def test_the_first_call_never_executes(self, tmp_path, monkeypatch):
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())

        route_command("provider llm_provider add anthropic never-should-exist secret", None, system)

        _, listing, _ = route_command("provider llm_provider accounts", None, system)
        assert "never-should-exist" not in listing.decode("utf-8")

    def test_the_description_names_what_would_happen(self, tmp_path, monkeypatch):
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())

        _, body, _ = route_command("provider llm_provider select anthropic acc-a", None, system)
        payload = json.loads(body)
        assert "anthropic/acc-a" in payload["description"]
        assert "llm_provider" in payload["description"]

    def test_an_invalid_token_is_refused_not_executed(self, tmp_path, monkeypatch):
        """The exact scenario item #3 names explicitly: the governance
        boundary must hold even against a direct call that never went
        through any confirm dialog at all."""
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())

        status, body, content_type = route_command(
            "provider llm_provider add anthropic never-should-exist secret",
            "not-a-real-token",
            system,
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["requires_confirmation"] is True  # refused execution, asked again

        _, listing, _ = route_command("provider llm_provider accounts", None, system)
        assert "never-should-exist" not in listing.decode("utf-8")

    def test_a_token_issued_for_a_different_command_does_not_work(self, tmp_path, monkeypatch):
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())

        _, body_a, _ = route_command("provider llm_provider add anthropic acc-a key-a", None, system)
        token_for_a = json.loads(body_a)["confirm_token"]

        # The token from adding acc-a must not authorise adding acc-b.
        status, body_b, _ = route_command(
            "provider llm_provider add anthropic acc-b key-b", token_for_a, system
        )
        payload_b = json.loads(body_b)
        assert payload_b["requires_confirmation"] is True

        _, listing, _ = route_command("provider llm_provider accounts", None, system)
        assert "acc-b" not in listing.decode("utf-8")

    def test_a_token_can_only_be_used_once(self, tmp_path, monkeypatch):
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())
        command = "provider llm_provider add anthropic once-only key"

        _, body, _ = route_command(command, None, system)
        token = json.loads(body)["confirm_token"]

        first = route_command(command, token, system)
        assert json.loads(first[1])["requires_confirmation"] is False

        # Reusing the same token for the identical command must be
        # refused, not silently re-executed (which would double-add).
        second_status, second_body, _ = route_command(command, token, system)
        second_payload = json.loads(second_body)
        assert second_payload["requires_confirmation"] is True

    def test_never_confirming_leaves_the_system_provably_unchanged(self, tmp_path, monkeypatch):
        """Cancellation, in this stateless protocol, is simply never
        sending the second call - proven here by taking every other
        action available and confirming the declined write still never
        happened."""
        self._isolated(tmp_path, monkeypatch)
        system = build_live_system(settings())

        route_command("provider llm_provider add anthropic abandoned secret", None, system)
        route_command("status", None, system)
        route_command("capabilities", None, system)

        _, listing, _ = route_command("provider llm_provider accounts", None, system)
        assert "abandoned" not in listing.decode("utf-8")

    def test_capability_disable_is_also_confirmation_gated(self, tmp_path, monkeypatch):
        """Proves repair items #1 and #3 compose: capability enable/
        disable is one of the administrative writes item #3 names."""
        import scripts.control_center as control_center_module

        monkeypatch.setattr(control_center_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
        system = build_live_system(settings())

        route_command("capabilities disable current_datetime", None, system)
        _, listing, _ = route_command("capabilities list", None, system)
        assert "[disabled" not in listing.decode("utf-8")  # never executed - no token supplied

        status, result = confirm_and_run("capabilities disable current_datetime", system)
        assert "now disabled" in result
        _, listing2, _ = route_command("capabilities list", None, system)
        assert "[disabled" in listing2.decode("utf-8")

    def test_read_only_verbs_are_never_confirmation_gated(self):
        """The scope boundary stated once, proven here: nothing about
        this repair item slows down or blocks an ordinary read."""
        system = build_live_system(settings())
        for command in ("status", "capabilities", "provider", "connections", "voice", "actions"):
            status, body, content_type = route_command(command, None, system)
            assert status == 200
            assert content_type.startswith("text/plain")


# ---------------------------------------------------------------------------
# route_message(): POST /message - validated, locked, honestly reported
# ---------------------------------------------------------------------------


class TestPostMessage:
    def test_empty_text_is_rejected_before_touching_the_conversation(self):
        system = live_system()
        before = len(system.conversation.utterances)
        status, body, _ = route_message("", system)
        assert status == 400
        assert len(system.conversation.utterances) == before

    def test_missing_text_is_rejected(self):
        system = live_system()
        status, _, _ = route_message(None, system)
        assert status == 400

    def test_non_string_text_is_rejected(self):
        system = live_system()
        status, _, _ = route_message(42, system)
        assert status == 400

    def test_a_successful_message_produces_a_real_turn(self):
        system = live_system()
        status, body, content_type = route_message("what can you do?", system)
        assert status == 200
        assert content_type.startswith("application/json")
        turn = json.loads(body)
        for key in (
            "handled_by", "delegated", "outcome", "succeeded",
            "brain_mode", "specialist_brain_mode", "memory_written", "invited",
        ):
            assert key in turn, key

    def test_get_after_post_reflects_the_same_conversation(self):
        """The regression this milestone must never reintroduce: a GET
        right after a POST must see what the POST just said, because both
        read the one held Conversation rather than two different ones."""
        system = live_system()
        route_message("what can you do?", system)

        _, body, _ = route("/snapshot.json", system)
        data = json.loads(body)
        assert len(data["conversation"]["utterances"]) == len(system.conversation.utterances)
        assert (
            data["conversation"]["utterances"][-1]["text"]
            == system.conversation.utterances[-1].text
        )

    def test_two_sequential_posts_accumulate_in_the_same_room(self):
        system = live_system()
        route_message("first message", system)
        after_first = len(system.conversation.utterances)
        route_message("second message", system)
        after_second = len(system.conversation.utterances)

        assert after_second > after_first
        said = [u.text for u in system.conversation.utterances]
        assert "first message" in said
        assert "second message" in said

    def test_half_brain_stays_honest_through_a_live_message(self):
        """No provider is configured for this settings object, so the
        response must say HALF BRAIN - never a fabricated FULL BRAIN."""
        system = live_system()
        _, body, _ = route_message("what can you do?", system)
        turn = json.loads(body)
        assert turn["brain_mode"] == "HALF BRAIN"

    def test_activity_stays_idle_after_a_live_message_no_state_evidence_yet(self):
        """A reply happened, but nothing in this path yet hands
        presence_for() any evidence of SPEAKING/LISTENING (see
        src/services/identity/presence.py's own docstring on how
        state is only ever accepted, never computed) - so this must
        keep reading IDLE rather than a guessed richer state, while
        the avatar itself must still resolve to a real profile."""
        system = live_system()
        route_message("what can you do?", system)
        _, body, _ = route("/snapshot.json", system)
        data = json.loads(body)
        for agent in data["agents"]:
            assert agent["activity"] == "idle"
            assert agent["avatar"]["agent_key"] == agent["name"]

    def test_explicit_retention_goes_through_retain_and_is_not_duplicated(self):
        system = live_system()
        before = len(system.manager.memory.recall(limit=50))

        route_message("remember that we prefer blue", system)
        after_one = len(system.manager.memory.recall(limit=50))
        assert after_one == before + 1

        route_message("what can you do?", system)  # no retention marker
        after_two = len(system.manager.memory.recall(limit=50))
        assert after_two == after_one, "an ordinary message must not become durable memory"

    def test_a_delegated_specialist_remains_the_speaker(self):
        """Arthur must answer as Arthur - the Manager must never appear as
        the author of words a specialist produced."""
        from src.services.profile.loader import parse_profile

        system = live_system(
            mailbox=sample_inbox(),
            profile=parse_profile("## Skills\n### Strong\npython\n"),
        )
        alfred = system.manager.own_skills.name

        _, body, _ = route_message("scan my inbox for opportunities", system)
        turn = json.loads(body)

        assert turn["handled_by"] == "Arthur"
        assert system.conversation.utterances[-1].speaker == "Arthur"
        assert [u.text for u in system.conversation.said_by(alfred)] == []


# ---------------------------------------------------------------------------
# The server holds exactly one system, and exactly one write path
# ---------------------------------------------------------------------------


class TestTheServerHoldsNoSystemOfItsOwn:
    def test_serve_ui_imports_no_domain_module_directly(self):
        """It reaches the system through assemble_system()/exchange()/
        snapshot() only - never its own copy of the manager, the registry,
        or a brain."""
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
        for banned in (
            "src.manager.manager",
            "src.services.memory",
            "src.registry",
            "src.agents",
            "src.providers",
            "src.conversation",
        ):
            assert not any(m.startswith(banned) for m in imported), (banned, imported)

    def test_the_server_stores_exactly_one_system(self):
        """`LiveServer.__init__` may only ever set one attribute of its
        own: the one `LiveSystem` every request reads or acts on. Holding a
        second reference alongside it - a cached manager, a spare
        conversation - would be exactly the duplicate this architecture
        forbids."""
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        live_server = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "LiveServer"
        )
        init = next(
            node
            for node in live_server.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        self_assignments = [
            target.attr
            for stmt in init.body
            if isinstance(stmt, ast.Assign)
            for target in stmt.targets
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ]
        assert self_assignments == ["system"]

    def test_live_system_holds_no_second_conversation_or_manager(self):
        """The dataclass itself: exactly the fields a held session needs,
        nothing that duplicates one of them."""
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        cls = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "LiveSystem"
        )
        fields = [n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)]
        assert fields == ["runtime", "manager", "channel", "conversation", "centre", "lock"]

    def test_route_message_is_guarded_by_a_lock(self):
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "route_message"
        )
        with_nodes = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
        assert with_nodes, "route_message must guard its mutating call with a lock"
        locked = any(
            isinstance(item.context_expr, ast.Attribute) and item.context_expr.attr == "lock"
            for w in with_nodes
            for item in w.items
        )
        assert locked

    def test_route_is_not_locked(self):
        """Read-only GETs must not be serialised behind the same lock -
        the architecture does not require it (see module docstring), and
        locking a read would make every poll wait behind every other."""
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "route"
        )
        assert not [n for n in ast.walk(fn) if isinstance(n, ast.With)]

    def test_the_handler_defines_exactly_one_write_method(self):
        """`do_POST` now exists, deliberately - `/message` is the one
        sanctioned write path. `do_PUT`/`do_DELETE`/`do_PATCH` must not:
        nothing else was added."""
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        handler = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "SnapshotHandler"
        )
        methods = {n.name for n in handler.body if isinstance(n, ast.FunctionDef)}
        assert "do_POST" in methods
        for write_verb in ("do_PUT", "do_DELETE", "do_PATCH"):
            assert write_verb not in methods

    def test_no_second_brain_or_memory_class_exists_here_either(self):
        forbidden = (
            "ConversationRoomBrain", "ConversationRoomMemory", "WorkspaceBrain",
            "WorkspaceMemory", "UIBrain", "UIMemory", "RoomMemory", "RoomBrain",
        )
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name not in forbidden, node.name

    def test_the_server_names_no_agent(self):
        source = SERVE_UI.read_text(encoding="utf-8").lower()
        for name in ("arthur", "alfred"):
            assert name not in source, name

    def test_the_server_binds_to_loopback_by_default(self):
        assert DEFAULT_HOST == "127.0.0.1"


class TestPrivateHttpsIsOptIn:
    """Private device-access checkpoint (2026-09-09): `--https` must never
    change what happens when it is omitted. These are structural (AST)
    checks in the same spirit as the class above, for the same reason -
    `tests/conftest.py::_no_network` blocks real `connect()`/DNS/
    `SSLContext.wrap_socket` for this whole suite, so `main()`'s actual
    wire-level behaviour (both with and without `--https`) is proven
    separately, by hand, outside pytest - see `tests/test_dev_tls.py`'s own
    module docstring and the live verification in this checkpoint's own
    commit message."""

    def _main_function(self):
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        return next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )

    def test_dev_tls_is_never_imported_at_module_level(self):
        """The optional 'cryptography' dependency dev_tls.py needs must
        never be imported just by importing serve_ui.py itself - a
        plain-HTTP run (still the default) must not even attempt it."""
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        top_level_imports = []
        for node in tree.body:  # module top level only, not inside main()
            if isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.append(node.module)
            elif isinstance(node, ast.Import):
                top_level_imports += [alias.name for alias in node.names]
        assert not any("dev_tls" in m for m in top_level_imports)

    def test_dev_tls_is_imported_only_inside_an_https_branch(self):
        """The lazy import must live inside a real `if` - never at the
        top of main(), which would attempt it on every plain-HTTP run
        too."""
        main_fn = self._main_function()
        import_nodes = [
            n for n in ast.walk(main_fn)
            if (isinstance(n, ast.ImportFrom) and n.module and "dev_tls" in n.module)
        ]
        assert import_nodes, "main() must import dev_tls somewhere for --https to work"

        # Every such import must have an ast.If somewhere among its
        # ancestors within main() - i.e. it is conditional, not top-level
        # in the function body.
        def ancestors_within(root, target):
            for node in ast.walk(root):
                for child in ast.iter_child_nodes(node):
                    if child is target:
                        yield node

        for imp in import_nodes:
            found_if = False
            # Walk the whole function tree looking for an If node whose
            # subtree contains this import.
            for node in ast.walk(main_fn):
                if isinstance(node, ast.If) and imp in ast.walk(node):
                    found_if = True
                    break
            assert found_if, "the dev_tls import must be gated behind an if, not unconditional"

    def test_https_flag_exists_and_defaults_to_off(self):
        tree = ast.parse(SERVE_UI.read_text(encoding="utf-8"))
        main_fn = self._main_function()
        calls = [
            n for n in ast.walk(main_fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument"
        ]
        https_calls = [
            c for c in calls
            if c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == "--https"
        ]
        assert https_calls, "main() must define a --https flag"
        kwargs = {kw.arg: kw.value for kw in https_calls[0].keywords}
        assert "action" in kwargs and getattr(kwargs["action"], "value", None) == "store_true"

    def test_tls_unavailable_is_caught_and_reported_not_silently_swallowed(self):
        """A missing 'cryptography' package (or any other real TLS setup
        failure) must fail loudly, the same honest-refusal shape the
        pre-existing configuration guard already uses - never a silent
        fallback to plain HTTP, which would claim --https had been
        honoured while actually handing back a microphone-refusing
        origin."""
        main_fn = self._main_function()
        handlers = [n for n in ast.walk(main_fn) if isinstance(n, ast.ExceptHandler)]
        names = []
        for h in handlers:
            if h.type is None:
                continue
            if isinstance(h.type, ast.Name):
                names.append(h.type.id)
            elif isinstance(h.type, ast.Tuple):
                names += [e.id for e in h.type.elts if isinstance(e, ast.Name)]
        assert "TLSUnavailable" in names

    def test_serving_still_binds_to_whatever_host_was_asked_for(self):
        """--https must not silently override --host to something else -
        the same flexible, already-real bind override the prior audit
        confirmed exists (test_the_server_binds_to_loopback_by_default
        above), now proven to remain untouched by the TLS branch: no
        assignment to args.host anywhere in main()."""
        main_fn = self._main_function()
        assigned_names = {
            target.attr
            for node in ast.walk(main_fn)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
            and target.value.id == "args"
        }
        assert "host" not in assigned_names
