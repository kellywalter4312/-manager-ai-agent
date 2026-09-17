"""Whole-ecosystem closure campaign 2 - Arthur's inbox and opportunities screens.

Arthur declared mailbox and opportunity surfaces long before either could be
looked at. They are now real screens: ``EmailAgent.surface_view`` answers each
with what the agent really reads and ranks, ``GET /surface/<agent>/<kind>.json``
serves it for any agent that declares and answers a surface, and the page renders
the generic item shape. Read-only throughout - the Gmail scope stays readonly and
nothing on these screens sends, replies, labels or deletes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from src.config.settings import Settings
from src.control_center.interfaces import InterfaceDescriptor, InterfaceFeature, describe_interface
from src.services.email.mailbox import MailboxError


@pytest.fixture
def system(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"),
        ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"),
        ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _get(system, path):
    status, body, content_type = su.route(path, system)
    return status, json.loads(body)


class TestArthursScreens:
    def test_both_surfaces_are_implemented_not_planned(self, system):
        arthur = describe_interface(system.manager.registry.get("Arthur"))
        assert arthur.can_render(InterfaceFeature.MAILBOX) and arthur.can_render(InterfaceFeature.OPPORTUNITIES)
        assert not arthur.planned

    def test_the_inbox_screen_lists_the_messages_the_agent_reads(self, system):
        status, data = _get(system, "/surface/Arthur/mailbox.json")
        arthur = system.manager.registry.get("Arthur")
        fetched = arthur._mailbox.fetch(limit=arthur._fetch_limit)
        assert status == 200 and data["count"] == len(fetched) == len(data["items"])
        assert [i["id"] for i in data["items"]] == [m.id for m in fetched]
        first = data["items"][0]
        assert first["title"] and first["subtitle"] == fetched[0].sender and first["meta"][0] == fetched[0].received_at.isoformat()
        assert data["can_send"] is False and "Read-only" in data["notes"][0]

    def test_the_opportunities_screen_is_the_analyzers_real_ranking(self, system):
        status, data = _get(system, "/surface/Arthur/opportunities.json")
        arthur = system.manager.registry.get("Arthur")
        messages = arthur._mailbox.fetch(limit=arthur._fetch_limit)
        reports = [a.analyze(messages) for a in arthur.analyzers]
        expected = [f.message_id for r in reports for f in getattr(r, "ranked", r.findings)]
        assert expected  # the sample inbox really holds opportunities
        assert status == 200 and [i["id"] for i in data["items"] if i["group"] == "ranked"] == expected
        assert all(i["meta"][0].startswith("score ") for i in data["items"])
        assert data["analyzers"] and data["analyzers"][0]["considered"] == len(messages)

    def test_a_mailbox_that_cannot_be_read_is_reported(self, system, monkeypatch):
        arthur = system.manager.registry.get("Arthur")

        def broken(limit=None):
            raise MailboxError("token expired")

        monkeypatch.setattr(arthur._mailbox, "fetch", broken)
        status, data = _get(system, "/surface/Arthur/mailbox.json")
        assert status == 502 and "token expired" in data["error"]

    def test_screens_nobody_declared_are_refused(self, system):
        assert _get(system, "/surface/Arthur/code.json")[0] == 404
        assert _get(system, "/surface/Alfred/mailbox.json")[0] == 404
        assert _get(system, "/surface/Nobody/mailbox.json")[0] == 404
        assert _get(system, "/surface/Arthur")[0] == 400


class TestGenericForAnyAgent:
    def test_a_future_agent_screen_is_served_with_no_change(self, system):
        from src.agents.base_agent import BaseAgent
        from src.core.contracts import AgentSpec, TaskResult

        class Cartographer(BaseAgent):
            name = "Cartographer"
            interface = InterfaceDescriptor(agent="Cartographer", display_name="Cartographer",
                                            features={"chat", "map"}, implemented={"chat", "map"})

            @property
            def spec(self):
                return AgentSpec(name="Cartographer", description="maps", capabilities=("chart",))

            def execute(self, task):
                return TaskResult.success(task, agent_name="Cartographer", output="charted")

            def surface_view(self, kind, limit=None):
                return {"title": "Routes", "items": [{"title": "North pass"}]} if kind == "map" else None

        system.manager.registry.register(Cartographer())
        status, data = _get(system, "/surface/Cartographer/map.json")
        assert status == 200 and data["items"][0]["title"] == "North pass"

    def test_the_page_renders_surfaces_generically(self):
        page = Path("ui/index.html").read_text(encoding="utf-8")
        body = page[page.index("function surfaceScreens("):page.index("function wireSurfaceScreens(")]
        for named in ("Arthur", "Alfred", '"mailbox"', '"opportunities"', "gmail"):
            assert named not in body
        assert "surface/${encodeURIComponent(agentName)}" in body and "wireSurfaceScreens();" in page

    def test_the_screens_add_no_write_path(self):
        source = Path("src/agents/email_agent.py").read_text(encoding="utf-8")
        view = source[source.index("def surface_view("):source.index("def can_send(")]
        for write in ("send(", "modify", "trash", "delete(", "insert(", "gmail.modify", "gmail.send"):
            assert write not in view
