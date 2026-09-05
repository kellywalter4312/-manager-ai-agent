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
from scripts.serve_ui import DEFAULT_HOST, LiveSystem, build_live_system, route, route_message
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
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
