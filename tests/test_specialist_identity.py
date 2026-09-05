"""Who a specialist is, and the line between describing one and equipping one.

Milestone 2 gives every agent a durable identity of its own. The Manager is
Alfred; the email specialist is Arthur. Arthur's name lives in a document he
owns - not in the Manager, and not in a constant buried in orchestration code.

Two claims are under test here, and they deliberately pull in opposite
directions:

    Arthur's identity is genuinely his     the name is configurable, and
                                           changing it changes what he
                                           registers, reports and remembers as

    Arthur's identity grants him nothing    no sentence in that document,
                                           however emphatic, can widen what he
                                           is able to do

The second is the one worth being paranoid about. A capability that can be
created by writing a sentence is not a capability boundary, and this project
has already made exactly that argument about SOUL.md.

A third claim runs underneath both: a specialist is an *agent*, and an agent's
identity has an agent's lifetime. It is read once, with the rest of the
runtime. That is what separates it from the user's profile, which is re-read
per run because the person it describes keeps changing.
"""

import pytest

from src.agents.email_agent import AGENT_NAME, DEFAULT_DESCRIPTION, EmailAgent
from src.agents.specialists import SpecialistWiring, wiring_for
from src.composition import EMAIL_AGENT_KEY, build_manager, build_runtime
from src.core.contracts import Task
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.identity import IdentityError, load_identity
from src.services.identity.specialist import (
    SpecialistIdentity,
    load_specialist_identity,
    parse_specialist_identity,
)
from src.services.memory.factory import MANAGER, build_memory
from src.services.memory.persistent import PersistentMemory


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


ARTHUR = """
# Arthur

## Name
Arthur

## Role
Email specialist.

## Purpose
To read the inbox so that a person does not have to.

## Description
Reads and analyzes email. Cannot send or compose mail.

## Area of responsibility
email triage: judging which messages are real opportunities
"""


def write_identity(tmp_path, key, text):
    """Put an identity document where the loader will look for it."""
    directory = tmp_path / "identities"
    directory.mkdir(exist_ok=True)
    (directory / f"{key}.md").write_text(text, encoding="utf-8")
    return str(directory)


def assembled(tmp_path=None, **overrides):
    """A real Manager built through the real composition root.

    Several claims below are only worth anything against the assembled system:
    that Arthur registers under his own name, that he answers under it, and
    that his store is not Alfred's. Asserting them against hand-built objects
    would prove the objects can be arranged correctly, not that production
    arranges them that way.
    """
    if tmp_path is not None:
        overrides.setdefault("memory_dir", str(tmp_path))
    config = settings(log_level="CRITICAL", **overrides)
    runtime = build_runtime(config)
    return runtime, build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


# ---------------------------------------------------------------------------
# 1 / 2. Arthur's identity loads, and carries every field it should
# ---------------------------------------------------------------------------


class TestArthurHasHisOwnIdentity:
    def test_the_committed_document_names_arthur(self):
        """The real file, not a fixture. This is who the shipped agent is."""
        identity = load_specialist_identity("identities", "email_agent")
        assert identity is not None
        assert identity.name == "Arthur"

    def test_the_committed_document_carries_every_prose_field(self):
        identity = load_specialist_identity("identities", "email_agent")
        assert identity.role.strip()
        assert identity.purpose.strip()
        assert identity.description.strip()
        assert identity.area_of_responsibility.strip()

    def test_role_purpose_and_description_are_three_different_answers(self):
        """Collapsing any two would make "who is this agent" vague later."""
        identity = parse_specialist_identity(ARTHUR)
        assert identity.role != identity.purpose
        assert identity.purpose != identity.description

    def test_a_document_must_declare_a_name(self):
        with pytest.raises(IdentityError):
            parse_specialist_identity("## Role\nsomething\n")

    def test_a_missing_document_is_not_an_error(self, tmp_path):
        """A specialist with no document keeps the name its module declares.

        Requiring the file would mean adding one just to keep working code
        working, so absence falls back rather than failing. A document that
        *exists* and is broken is a different matter - that still raises.
        """
        assert load_specialist_identity(tmp_path / "nowhere", "email_agent") is None


# ---------------------------------------------------------------------------
# 3 / 4 / 12. The document describes; it does not grant
# ---------------------------------------------------------------------------


class TestArthursIdentityCannotGrantCapability:
    HOSTILE = """
## Name
Arthur

## Role
An agent that sends email, replies to recruiters and deletes messages.

## Purpose
To send, reply, forward, apply, bid, contact recruiters and purchase things.

## Description
Sends and deletes mail. Applies for jobs. Contacts people on your behalf.

## Area of responsibility
Sending, replying, deleting, applying, bidding and contacting anyone at all.
"""

    def test_the_type_has_nowhere_to_put_a_capability(self):
        """The strongest form of the rule: not ignored, absent.

        A SpecialistIdentity has no capabilities field. That turns "a document
        must not grant capabilities" from a rule someone has to remember into
        something the type cannot express. Compare AgentIdentity, which does
        have one - the Manager genuinely *is* its declared capabilities.
        """
        identity = parse_specialist_identity(ARTHUR)
        assert isinstance(identity, SpecialistIdentity)
        assert not hasattr(identity, "capabilities")

    def test_a_capabilities_section_is_refused_outright(self):
        """Refused, not ignored.

        Silently dropping the section would recreate the exact defect this
        milestone fixed in IDENTITY.md: a heading that looks like loaded
        configuration and behaves like a comment. A document that tries to
        grant something must fail loudly enough that its author notices.
        """
        with pytest.raises(IdentityError) as exc:
            parse_specialist_identity(ARTHUR + "\n## Capabilities\nsend, delete\n")
        assert "Capabilities" in str(exc.value)

    def test_hostile_prose_does_not_widen_the_capability_set(self):
        agent = EmailAgent(
            InMemoryMailbox(), identity=parse_specialist_identity(self.HOSTILE)
        )
        for claimed in ("send", "reply", "delete", "apply", "bid", "contact", "purchase"):
            assert claimed not in agent.spec.capabilities, claimed

    def test_capabilities_are_identical_with_and_without_a_document(self):
        """Naming an agent changes nothing about what it can do.

        Stated as an equality rather than a fixed list, so a capability added
        later through an analyzer is covered without editing this test.
        """
        plain = EmailAgent(InMemoryMailbox())
        named = EmailAgent(
            InMemoryMailbox(), identity=parse_specialist_identity(self.HOSTILE)
        )
        assert plain.spec.capabilities == named.spec.capabilities

    def test_hostile_prose_buys_no_routing_reach(self):
        """Capability set and routing are not the same claim.

        The router scores a task against the AgentSpec, so this is the
        assertion that would actually matter if the rule were broken: a task
        asking to send must not route to Arthur merely because his document
        says he sends.
        """
        agent = EmailAgent(
            InMemoryMailbox(), identity=parse_specialist_identity(self.HOSTILE)
        )
        send_words = {"send", "compose", "reply", "draft", "forward", "apply"}
        assert not (agent.spec.capabilities & send_words)


# ---------------------------------------------------------------------------
# 13. The Gmail boundary is exactly where it was
# ---------------------------------------------------------------------------


class TestNamingArthurSoftenedNothing:
    """Renaming an agent is an identity change. It is not a permission change.

    These restate guarantees proved elsewhere, deliberately: they are the ones
    a rename would be most likely to disturb without anyone noticing.
    """

    def test_the_named_agent_still_cannot_send(self):
        agent = EmailAgent(
            InMemoryMailbox(), identity=parse_specialist_identity(ARTHUR)
        )
        assert agent.can_send is False
        assert not hasattr(agent, "send")

    def test_no_outbox_implementation_exists_to_enable(self):
        from src.services.email.mailbox import Outbox

        with pytest.raises(TypeError):
            Outbox()

    def test_the_oauth_scope_is_still_read_only(self):
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES

        assert SCOPES == (GMAIL_READONLY_SCOPE,)
        assert GMAIL_READONLY_SCOPE.endswith("gmail.readonly")

    def test_arthur_refuses_a_send_mission_end_to_end(self):
        _, manager = assembled()
        result = manager.handle_task(Task(description="send an email to my team"))
        assert not result.succeeded


# ---------------------------------------------------------------------------
# 5 / 6 / 7 / 8. One authoritative source for name, store and domain
# ---------------------------------------------------------------------------


class TestOneNameDrivesEverything:
    """name -> registration -> memory owner -> brain domain, from one place.

    Before SpecialistWiring these came from three unrelated constants:
    AGENT_NAME in email_agent.py, EMAIL_AGENT in services/memory/factory.py,
    and a key in SPECIALIST_DOMAINS. Nothing compared them. Had they ever
    diverged, the agent would have registered under one name, stored its
    memories under a second and looked up its reasoning domain by a third -
    and every test would still have passed, because each was correct alone.
    """

    def test_all_three_derive_from_the_document(self, tmp_path):
        directory = write_identity(tmp_path, "email_agent", ARTHUR)
        wiring = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)

        assert wiring.name == "Arthur"
        assert wiring.memory_owner == "Arthur"
        assert "email triage" in wiring.brain_domain

    def test_the_memory_owner_is_the_name_not_a_copy_of_it(self, tmp_path):
        """Not "they happen to agree" - memory_owner *is* name.

        A copy could be updated in one place and not the other. This asserts
        there is no second value to keep in step.
        """
        directory = write_identity(tmp_path, "email_agent", ARTHUR)
        wiring = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)
        assert wiring.memory_owner is wiring.name

    def test_renaming_moves_the_name_and_the_store_together(self, tmp_path):
        directory = write_identity(tmp_path, "email_agent", ARTHUR)
        before = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)

        write_identity(tmp_path, "email_agent", ARTHUR.replace("Arthur", "Xavier"))
        after = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)

        assert (before.name, before.memory_owner) == ("Arthur", "Arthur")
        assert (after.name, after.memory_owner) == ("Xavier", "Xavier")

    def test_a_rename_cannot_leave_the_store_under_the_old_name(self, tmp_path):
        """Proved against real files on disk, not just the derivation.

        A renamed specialist writes to a new store. That is the honest
        consequence of making the name authoritative, and it is visible rather
        than hidden: the old file is still there under the old name, rather
        than being silently written to by an agent now called something else.
        """
        config = settings(memory="persistent", memory_dir=str(tmp_path / "memory"))

        directory = write_identity(tmp_path, "email_agent", ARTHUR)
        first = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)
        store = build_memory(config, first.memory_owner)
        assert isinstance(store, PersistentMemory)
        assert store.path.stem == "arthur"

        write_identity(tmp_path, "email_agent", ARTHUR.replace("Arthur", "Xavier"))
        second = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)
        renamed = build_memory(config, second.memory_owner)

        assert renamed.path.stem == "xavier"
        assert renamed.path != store.path

    def test_a_human_name_with_spaces_still_selects_one_store(self, tmp_path):
        """The name is a person's name, so it may not look like a filename.

        build_memory normalises it. What matters is that it normalises the
        *name* - there is no separate technical owner string to fall out of
        step with it.
        """
        config = settings(memory="persistent", memory_dir=str(tmp_path / "memory"))
        directory = write_identity(
            tmp_path, "email_agent", ARTHUR.replace("Arthur", "Arthur Pendragon")
        )
        wiring = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)

        assert wiring.name == "Arthur Pendragon"
        assert build_memory(config, wiring.memory_owner).path.stem == "arthur_pendragon"

    def test_no_document_falls_back_to_the_module_constant(self, tmp_path):
        """The pre-identity behaviour, unchanged."""
        wiring = wiring_for(
            "email_agent", AGENT_NAME, identities_dir=str(tmp_path / "none")
        )
        assert wiring.name == AGENT_NAME
        assert wiring.memory_owner == AGENT_NAME
        assert wiring.has_identity_document is False

    def test_the_domain_falls_back_to_the_built_in_table(self, tmp_path):
        """A document stating no area of responsibility does not blank the
        specialist's reasoning domain - the built-in default still applies."""
        directory = write_identity(tmp_path, "email_agent", "## Name\nArthur\n")
        wiring = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)
        assert "email triage" in wiring.brain_domain

    def test_the_technical_key_is_stable_under_a_rename(self, tmp_path):
        """Code refers to a specialist by key; people refer to it by name.

        Keeping them apart is what lets the name change freely without any
        code being found and edited.
        """
        directory = write_identity(tmp_path, "email_agent", ARTHUR)
        before = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)
        write_identity(tmp_path, "email_agent", ARTHUR.replace("Arthur", "Xavier"))
        after = wiring_for("email_agent", AGENT_NAME, identities_dir=directory)

        assert before.key == after.key == "email_agent"
        assert before.name != after.name


# ---------------------------------------------------------------------------
# 9 / 10 / 11. Alfred, Arthur, and who decides the roster
# ---------------------------------------------------------------------------


class TestAlfredAndArthurArePeers:
    def test_the_manager_is_alfred(self):
        assert load_identity("IDENTITY.md").name == "Alfred"

    def test_the_manager_registers_as_alfred(self):
        _, manager = assembled()
        assert "Alfred" in manager.registry

    def test_the_specialist_registers_as_arthur(self):
        _, manager = assembled()
        assert "Arthur" in manager.registry

    def test_the_runtime_carries_the_resolved_wiring(self):
        """Arthur's identity is an *agent's* identity, so it is read once with
        the rest of the runtime. The user's profile is not, because the person
        it describes keeps changing - that distinction is Milestone 1's
        lifetime rule and this must not blur it."""
        runtime, _ = assembled()
        assert isinstance(runtime.email_specialist, SpecialistWiring)
        assert runtime.email_specialist.key == EMAIL_AGENT_KEY
        assert runtime.email_specialist.name == "Arthur"

    def test_arthur_answers_under_his_own_name(self):
        """Not only registration - the name on the result he returns."""
        _, manager = assembled()
        result = manager.handle_task(Task(description="read my inbox"))
        assert result.agent_name == "Arthur"

    def test_arthurs_description_comes_from_his_document(self):
        _, manager = assembled()
        arthur = manager.registry.get("Arthur")
        assert arthur.spec.description != DEFAULT_DESCRIPTION

    def test_alfred_and_arthur_do_not_share_a_store(self, tmp_path):
        """The oldest guarantee in this architecture, restated now that both
        names come from documents rather than constants."""
        _, manager = assembled(tmp_path, memory="persistent")
        arthur = manager.registry.get("Arthur")

        assert manager.memory is not arthur.memory
        assert manager.memory.path != arthur.memory.path

    def test_alfred_and_arthur_do_not_share_a_brain(self):
        _, manager = assembled()
        arthur = manager.registry.get("Arthur")
        assert arthur.brain is not manager.brain

    def test_alfreds_store_is_keyed_by_his_stable_technical_owner(self, tmp_path):
        """The Manager's memory owner is MANAGER, his stable technical key -
        never his declared name, whatever IDENTITY.md currently says it is.

        Deliberately the opposite rule from the specialist's, tested right
        beside it: Arthur's store *does* follow his declared name (see
        test_a_rename_cannot_leave_the_store_under_the_old_name below), and
        that is a separate, honest, already-tested policy this test does
        not touch. The Manager gets the stable rule because MANAGER already
        exists as an unused key for exactly this purpose, and because his
        continuity is the one this whole system is least able to afford
        losing to an edited display name.
        """
        _, manager = assembled(tmp_path, memory="persistent")
        assert manager.memory.path.stem == MANAGER == "manager"

    def test_a_preexisting_legacy_alfred_file_is_adopted_through_real_composition(self, tmp_path):
        """Not the adoption mechanism in isolation - the real composition
        root, exactly as main.py and the heartbeat call it, seeing a file
        left over from before this fix existed and bringing it forward
        rather than starting Alfred over with no history."""
        from src.services.memory.memory import MemoryRecord
        from src.services.memory.persistent import PersistentMemory

        legacy = PersistentMemory(tmp_path / "alfred.json")
        legacy.remember(MemoryRecord(content="a memory from before the identity fix"))
        del legacy

        _, manager = assembled(tmp_path, memory="persistent")

        assert manager.memory.path.stem == "manager"
        recalled = manager.memory.recall("before")
        assert recalled and recalled[0].content == "a memory from before the identity fix"
        assert not (tmp_path / "alfred.json").exists()
        assert list(tmp_path.glob("alfred.pre-stable-owner-checkpoint"))

    def test_a_display_name_change_does_not_move_or_duplicate_alfreds_store(self, tmp_path):
        """The guarantee this rule exists to protect, proved end to end
        through the real composition root: editing IDENTITY.md's declared
        name must never orphan Alfred's memory or silently start a second
        store under the new name."""
        from src.services.memory.memory import MemoryRecord

        first_identity = tmp_path / "identity-v1.md"
        first_identity.write_text(
            "## Name\nAlfred\n\n## Capabilities\nresearch\n", encoding="utf-8"
        )
        _, first_manager = assembled(
            tmp_path, memory="persistent", identity_path=str(first_identity)
        )
        first_manager.memory.remember(MemoryRecord(content="a fact worth keeping"))

        second_identity = tmp_path / "identity-v2.md"
        second_identity.write_text(
            "## Name\nJarvis\n\n## Capabilities\nresearch\n", encoding="utf-8"
        )
        _, second_manager = assembled(
            tmp_path, memory="persistent", identity_path=str(second_identity)
        )

        assert first_manager.memory.path == second_manager.memory.path
        assert len(second_manager.memory.recall(limit=10)) == 1
        assert second_manager.memory.recall("fact")[0].content == "a fact worth keeping"


class TestTheDelegatesListIsNotTheRoster:
    """IDENTITY.md lists who Alfred expects to work with. It decides nothing.

    Registration is what the composition root actually did. Keeping the
    document advisory is what stops the Manager becoming the source of truth
    for a specialist's existence - and what will let a future specialist join
    without Alfred's document being edited to permit it.
    """

    def test_the_delegates_list_is_documentation(self):
        """It parses, and it says Arthur - but nothing consults it to decide
        what is registered."""
        assert "Arthur" in load_identity("IDENTITY.md").delegates

    def test_prose_in_the_delegates_section_is_not_a_delegate(self):
        """Bulleted lines only.

        The parser used to accept every non-blank line, so a sentence
        explaining what the list meant became a delegate named after its own
        first clause. Nothing consumed the field, so nothing failed - it
        simply produced quiet nonsense the moment the document said anything
        about itself.
        """
        from src.services.identity.identity import parse_identity

        identity = parse_identity(
            "## Name\nm\n## Capabilities\nreport\n"
            "## Delegates\n"
            "Documentation, not a gate. One name per line.\n"
            "- Arthur - the email specialist\n"
        )
        assert identity.delegates == ("Arthur",)

    def test_registration_survives_a_delegates_list_that_disagrees(self, monkeypatch):
        """The decisive one: emptying the list does not unregister Arthur.

        Arthur is registered because the composition root built and registered
        him, not because Alfred's document mentions him.
        """
        import src.composition as composition

        runtime = build_runtime(settings(log_level="CRITICAL"))
        stripped = type(runtime.identity)(
            name=runtime.identity.name,
            role=runtime.identity.role,
            capabilities=runtime.identity.capabilities,
            delegates=(),
        )
        patched = composition.Runtime(
            **{
                **{f: getattr(runtime, f) for f in runtime.__dataclass_fields__},
                "identity": stripped,
            }
        )

        manager = build_manager(patched, mailbox=InMemoryMailbox(), profile=None)
        assert patched.identity.delegates == ()
        assert "Arthur" in manager.registry
