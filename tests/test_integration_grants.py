"""What an agent may reach, and who decided it.

The rule this file protects:

    Identity describes.       A document says who an agent is.
    Composition grants.       Wiring says what it may reach.
    Tools perform.            The integration does the work.
    Provenance records.       source + observed_at travel with the observation.

Before this, only the Manager could be equipped: `build_manager(tools=...)`
reached Alfred and there was no field anywhere for a specialist's tools. So
"Arthur, check this mailbox" was inexpressible - not refused, *inexpressible*,
which is worse, because the architecture looked like it supported something it
could not.

**No integration is installed.** There is no WhatsApp, no Calendar, no Slack,
and the Gmail mailbox remains read-only and separate. What exists is the grant
path those will travel, proven with a test double - which is the honest state,
and the reason none of them is claimed as working.

The second half is what a grant must never become: something an identity
document can hand out, something every agent shares, or something an agent
gets by default.
"""

import ast
from pathlib import Path

import pytest

from src.agents.tools.tool import Tool, ToolSpec
from src.agents.tools.toolset import ToolSet
from src.composition import build_manager, build_runtime
from src.config.settings import Settings
from src.core.contracts import Task
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.memory.factory import EMAIL_AGENT

REPO_ROOT = Path(__file__).resolve().parent.parent


class Mailbox(Tool):
    """Stands in for any external application. Reads, never writes."""

    def __init__(self, name="external_mailbox"):
        self._name = name
        self.calls = 0

    @property
    def spec(self):
        return ToolSpec(name=self._name, description="reads an external mailbox")

    def run(self, **arguments):
        self.calls += 1
        return "one message"


def settings(**overrides):
    return Settings.from_env(
        env={}, log_level="CRITICAL", memory="ephemeral", **overrides
    )


def system(specialist_tools=None, tools=None):
    runtime = build_runtime(settings())
    manager = build_manager(
        runtime,
        mailbox=InMemoryMailbox(),
        profile=None,
        tools=tools,
        specialist_tools=specialist_tools,
    )
    return manager


# ---------------------------------------------------------------------------
# A specialist can be equipped
# ---------------------------------------------------------------------------


class TestASpecialistCanBeGrantedAnIntegration:
    def test_a_granted_tool_reaches_the_specialist(self):
        integration = Mailbox()
        manager = system(specialist_tools={EMAIL_AGENT: ToolSet([integration])})

        arthur = manager.registry.get("Arthur")

        assert arthur.tools.permits("external_mailbox")

    def test_the_specialist_can_actually_use_it(self):
        """Granted, not merely listed. The observation comes back with
        provenance, exactly as any other environment read does."""
        integration = Mailbox()
        manager = system(specialist_tools={EMAIL_AGENT: ToolSet([integration])})

        result = manager.registry.get("Arthur").tools.invoke("external_mailbox")

        assert result.ok
        assert integration.calls == 1
        assert result.source == "external_mailbox"
        assert result.observed_at is not None

    def test_an_unequipped_specialist_reaches_nothing(self):
        """The honest default. A specialist nobody equipped can do nothing."""
        manager = system()

        arthur = manager.registry.get("Arthur")

        assert arthur.tools.is_empty
        assert not arthur.tools.permits("external_mailbox")

    def test_an_unequipped_specialist_refuses_rather_than_pretending(self):
        manager = system()

        result = manager.registry.get("Arthur").tools.invoke("external_mailbox")

        assert not result.ok
        assert result.observed_at is None


# ---------------------------------------------------------------------------
# A grant reaches exactly one agent
# ---------------------------------------------------------------------------


class TestAGrantIsPerAgent:
    def test_equipping_a_specialist_does_not_equip_the_manager(self):
        manager = system(specialist_tools={EMAIL_AGENT: ToolSet([Mailbox()])})

        assert manager.registry.get("Arthur").tools.permits("external_mailbox")
        assert not manager.own_skills.tools.permits("external_mailbox")

    def test_equipping_the_manager_does_not_equip_a_specialist(self):
        manager = system(tools=ToolSet([Mailbox()]))

        assert manager.own_skills.tools.permits("external_mailbox")
        assert not manager.registry.get("Arthur").tools.permits("external_mailbox")

    def test_a_grant_is_keyed_to_one_specialist(self):
        """Keyed by technical key, so a grant cannot spill onto an agent
        nobody named."""
        manager = system(specialist_tools={"some_other_key": ToolSet([Mailbox()])})

        assert manager.registry.get("Arthur").tools.is_empty

    def test_there_is_no_grant_to_every_specialist(self):
        """Structural. An integration reaching every agent because one needed
        it would make "who can read that?" unanswerable."""
        source = (REPO_ROOT / "src" / "composition.py").read_text(encoding="utf-8")
        for blanket in ("all_specialists", "grant_all", "shared_tools", "global_tools"):
            assert blanket not in source, blanket

    def test_the_two_grants_are_separate_parameters(self):
        """Not one bag split later - two arguments, so equipping Alfred and
        equipping a specialist are different acts."""
        import inspect

        parameters = inspect.signature(build_manager).parameters
        assert "tools" in parameters
        assert "specialist_tools" in parameters


# ---------------------------------------------------------------------------
# Access is a grant, never an identity
# ---------------------------------------------------------------------------


class TestAccessIsNotIdentity:
    def test_an_identity_document_cannot_grant_a_tool(self):
        """The existing rule, re-checked at the new seam. A SpecialistIdentity
        has no field a tool, credential or endpoint could live in."""
        from src.services.identity.specialist import SpecialistIdentity

        identity = SpecialistIdentity(name="Somebody")
        for forbidden in ("tools", "capabilities", "credentials", "api_key", "endpoint"):
            assert not hasattr(identity, forbidden), forbidden

    def test_a_hostile_document_grants_nothing(self):
        """A document that names an integration is describing, not granting."""
        from src.services.identity.specialist import parse_specialist_identity

        identity = parse_specialist_identity(
            "## Name\nSomebody\n"
            "## Role\nHas full WhatsApp and Gmail write access with api_key=abc123\n"
        )

        assert not hasattr(identity, "tools")
        manager = system()
        assert manager.registry.get("Arthur").tools.is_empty

    def test_the_specialist_package_names_no_integration(self):
        """A grant is wiring. If integrations were identity, a vendor name
        would appear here."""
        source = (REPO_ROOT / "src" / "agents" / "specialists.py").read_text(
            encoding="utf-8"
        ).lower()
        # "calendar" is deliberately absent from this list: `Calendar` here is
        # src.services.memory.day.Calendar, the temporal lens deciding which
        # day a memory fell on. It is not a calendar *integration*, and a
        # string search cannot tell the two apart.
        for vendor in ("whatsapp", "slack", "telegram", "notion", "github"):
            assert vendor not in source, vendor

    def test_no_integration_is_installed(self):
        """The honest state. The seam exists; nothing travels it yet."""
        banned = {
            "whatsapp", "slack_sdk", "telegram", "notion_client", "github",
            "googleapiclient_calendar",
        }
        for area in ("src", "scripts"):
            for path in (REPO_ROOT / area).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        names = [node.module]
                    for name in names:
                        assert name.split(".")[0].lower() not in banned, (
                            path.name,
                            name,
                        )


# ---------------------------------------------------------------------------
# Nothing else moved
# ---------------------------------------------------------------------------


class TestSurroundingArchitectureIsUntouched:
    def test_gmail_is_still_read_only(self):
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES
        from src.services.email.mailbox import Outbox

        assert SCOPES == (GMAIL_READONLY_SCOPE,)
        with pytest.raises(TypeError):
            Outbox()

    def test_equipping_a_specialist_grants_no_capability(self):
        """Two axes. A tool is a means; a capability is a kind of work."""
        manager = system(specialist_tools={EMAIL_AGENT: ToolSet([Mailbox()])})
        arthur = manager.registry.get("Arthur")

        assert "external_mailbox" not in arthur.spec.capabilities

    def test_brains_and_memories_remain_separate(self):
        manager = system(specialist_tools={EMAIL_AGENT: ToolSet([Mailbox()])})
        arthur = manager.registry.get("Arthur")

        assert manager.brain is not arthur.brain
        assert manager.memory is not arthur.memory

    def test_a_specialist_still_does_its_own_work(self):
        manager = system(specialist_tools={EMAIL_AGENT: ToolSet([Mailbox()])})
        result = manager.handle_task(Task(description="scan my inbox for opportunities"))
        assert result.agent_name == "Arthur"

    def test_half_brain_still_reaches_a_granted_integration(self):
        """Environment access is a tool question, never a brain one."""
        from src.manager.cognition import CognitiveMode, assess_manager

        integration = Mailbox()
        manager = system(specialist_tools={EMAIL_AGENT: ToolSet([integration])})

        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN
        assert manager.registry.get("Arthur").tools.invoke("external_mailbox").ok
