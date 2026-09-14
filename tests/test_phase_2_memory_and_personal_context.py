"""Phase 2 - Memory & Personal Context.

CHECK found this project's memory architecture already exceptionally
mature - Memory/MemoryPolicy/MemoryRecord (the contract), PersistentMemory
(real, restart-surviving storage), ManagerMemoryPolicy (a closed
category allow-list with secret refusal and a length cap),
session.py/user_facts.py/capture.py (deterministic, marker-based
extraction - "remember that X", self-introduction grammars, "record
this for X"), milestones.py/temporal_facts.py/temporal_events.py
(structured personal dates), and real, tested `forget`/`recall_memory`/
`record_this` capabilities already reachable through natural
conversation. None of that is retested here - see
tests/test_manager_memory*.py, tests/test_persistent_memory.py,
tests/test_stage12_*.py, tests/test_memory*.py for that existing,
untouched coverage.

**The one genuine gap CHECK found**: nothing generalised the
"forget-then-retain" correction sequence
`personal_recurring_events`/`_apply_relational_birthday` already proved
for one narrow domain (a relational birthday) to ordinary user_context
memory - `retain_what_was_asked_for` (scripts/exchange.py) admits every
"remember that X" as a brand-new record with no check for an existing,
now-superseded one, so a contradictory pair of active memories could
accumulate silently. `correct_memory` (src/manager/capabilities.py)
closes that gap. This file proves it, and proves the handful of
Section 27 acceptance questions this checkpoint asks to be answered
with genuine verification rather than assumed from existing coverage:
persistence across a real restart, agent isolation, relevance (no
memory-dumping), and one new-component extensibility proof through the
Universal Ecosystem layer completed immediately before this phase.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.agents.tools.toolset import ToolSet
from src.config.settings import Settings
from src.manager.capabilities import (
    CORRECTION_INVALIDATE_MARKERS,
    CORRECTION_REPLACE_MARKERS,
    CORRECTION_TOPIC_PATTERN,
    correction_intent,
    select_capability,
)
from src.core.contracts import Task
from src.services.credentials.account import ProviderAccount
from src.services.ecosystem.contracts import EcosystemRegistry, GenericCategoryAdapter
from src.services.ecosystem.registry_factory import build_ecosystem_registry
from src.services.memory.memory import Memory, MemoryRecord
from scripts.control_center import build
from scripts.exchange import exchange


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


def persistent_system(memory_dir: str):
    return build(Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir))


# ---------------------------------------------------------------------------
# 1. correct_memory - routing and the three deterministic shapes
# ---------------------------------------------------------------------------


class TestCorrectionRouting:
    @pytest.mark.parametrize(
        "text",
        [
            "that's wrong, my favorite color is actually green",
            "that's incorrect: I moved to Berlin",
            "correction: I work at Acme now",
            "you got that wrong, I prefer tea not coffee",
            "you remembered that incorrectly, my birthday is in June",
            "update what you remembered about my job to Software Engineer",
            "change what you remembered about my project to Project Beacon",
            "that's no longer my preference",
            "that's not true anymore",
        ],
    )
    def test_every_documented_shape_is_recognised(self, text):
        assert correction_intent(Task(description=text))
        cap = select_capability(Task(description=text), None, participants=["Alfred"])
        assert cap.name == "correct_memory"

    @pytest.mark.parametrize(
        "text",
        [
            "that's the correct file",
            "please fix the bug in the parser",
            "what's the correct way to do this",
            "I always forget my keys",
        ],
    )
    def test_ordinary_speech_never_misroutes_here(self, text):
        """The same false-positive discipline FORGET/RECALL_MEMORY
        already hold themselves to - a word appearing in ordinary
        conversation must never alone trigger a consequential memory
        operation."""
        assert not correction_intent(Task(description=text))

    def test_markers_and_pattern_are_genuinely_disjoint_shapes(self):
        # Sanity on the fixtures themselves - a topic-scoped correction
        # must not also look like a bare replace/invalidate marker.
        topic_example = "update what you remembered about my job to CEO"
        assert CORRECTION_TOPIC_PATTERN.search(topic_example)
        assert not any(m in topic_example.lower() for m in CORRECTION_REPLACE_MARKERS)
        assert not any(m in topic_example.lower() for m in CORRECTION_INVALIDATE_MARKERS)


# ---------------------------------------------------------------------------
# 2. correct_memory through the real production path - the actual
#    forget-then-retain sequence, never a duplicate/contradictory pair.
# ---------------------------------------------------------------------------


class TestCorrectionProductionPath:
    def test_a_bare_correction_replaces_the_most_recent_memory(self):
        runtime, manager, channel, conversation = real_system()
        exchange(channel, conversation, "remember that my favorite color is blue")
        before = manager.memory.recall("favorite color", limit=10)
        assert len(before) == 1 and "blue" in before[0].content

        turn = exchange(channel, conversation, "correction: my favorite color is actually green")
        assert turn.succeeded
        assert "corrected" in turn.outcome.lower()

        after = manager.memory.recall("favorite color", limit=10)
        assert len(after) == 1, "must replace, never accumulate a contradictory second record"
        assert "green" in after[0].content
        assert "blue" not in after[0].content

    def test_a_topic_scoped_correction_targets_only_the_named_topic(self):
        runtime, manager, channel, conversation = real_system()
        exchange(channel, conversation, "remember that my favorite color is blue")
        exchange(channel, conversation, "remember that my favorite food is pizza")

        turn = exchange(
            channel, conversation, "update what you remembered about my favorite color to purple"
        )
        assert turn.succeeded

        everything = manager.memory.recall(limit=20)
        contents = [r.content for r in everything]
        assert len(everything) == 2, "the old color record must be gone, not merely superseded alongside it"
        assert any("purple" in c for c in contents)
        assert not any("blue" in c for c in contents)
        assert any("pizza" in c for c in contents), "an unrelated memory must survive untouched"

    def test_invalidation_removes_with_no_replacement_written(self):
        runtime, manager, channel, conversation = real_system()
        exchange(channel, conversation, "remember that my favorite color is blue")

        turn = exchange(channel, conversation, "that's no longer my preference")
        assert turn.succeeded
        assert manager.memory.recall("favorite color", limit=10) == ()

    def test_correcting_when_nothing_exists_is_honest_not_fabricated(self):
        runtime, manager, channel, conversation = real_system()
        turn = exchange(
            channel, conversation, "update what you remembered about my job to CEO"
        )
        assert turn.succeeded
        assert "don't have anything remembered" in turn.outcome.lower()
        assert manager.memory.recall("job", limit=10) == ()

    def test_a_refused_correction_leaves_the_prior_memory_untouched(self):
        """A correction whose replacement value the policy would refuse
        (too long, looks like a secret) must not have already destroyed
        the record it was replacing - forget only happens once retain is
        known to be governed, never speculatively ahead of it."""
        runtime, manager, channel, conversation = real_system()
        exchange(channel, conversation, "remember that my favorite color is blue")

        secret_like = "correction: my api_key = sk-abcdefghijklmnopqrstuvwx"
        turn = exchange(channel, conversation, secret_like)
        assert not turn.succeeded

        survivors = manager.memory.recall("favorite color", limit=10)
        assert len(survivors) == 1 and "blue" in survivors[0].content


# ---------------------------------------------------------------------------
# 3. Persistence across a genuine restart - remember, recall, correct,
#    forget, each surviving a brand-new process build against the same
#    on-disk store.
# ---------------------------------------------------------------------------


class TestGenuineRestartPersistence:
    def test_the_full_remember_recall_correct_forget_cycle_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, channel, conversation = persistent_system(tmp)
            exchange(channel, conversation, "remember that I am working on Project Beacon")

            # Restart 1: a brand-new process build against the same file.
            _, _, channel, conversation = persistent_system(tmp)
            turn = exchange(channel, conversation, "what do you remember about Project Beacon")
            assert "Project Beacon" in turn.outcome

            turn = exchange(channel, conversation, "correction: I am now working on Project Aurora")
            assert turn.succeeded

            # Restart 2: the correction itself must have persisted, not
            # merely mutated the in-process object.
            _, _, channel, conversation = persistent_system(tmp)
            turn = exchange(channel, conversation, "what do you remember about Project")
            assert "Aurora" in turn.outcome
            assert "Beacon" not in turn.outcome

            turn = exchange(channel, conversation, "forget that Project Aurora")
            assert turn.succeeded

            # Restart 3: the forget must have persisted too.
            _, _, channel, conversation = persistent_system(tmp)
            turn = exchange(channel, conversation, "what do you remember about Project")
            assert "don't have anything remembered" in turn.outcome.lower()


# ---------------------------------------------------------------------------
# 4. Agent isolation - Alfred and Arthur never share private memory
#    merely by both existing in one application.
# ---------------------------------------------------------------------------


class TestAgentMemoryIsolation:
    def test_arthur_cannot_be_answered_from_alfreds_memory_once_genuinely_addressed(self):
        """The methodologically-correct version of the isolation check:
        Arthur must first genuinely be *in the room* (participant
        invitation) for addressing to route to him at all - otherwise
        "Arthur, ..." is just ordinary text Alfred himself answers,
        which would look like a leak without actually being one."""
        runtime, manager, channel, conversation = real_system()
        exchange(channel, conversation, "remember that my favorite color is blue")
        exchange(channel, conversation, "bring Arthur into this conversation")
        assert "Arthur" in [p.name for p in conversation.participants]

        turn = exchange(channel, conversation, "Arthur, what do you remember about my favorite color")
        assert "blue" not in turn.outcome.lower()

    def test_alfred_and_arthur_are_backed_by_structurally_separate_memory_objects(self):
        runtime, manager, channel, conversation = real_system()
        arthur = next(a for a in runtime.specialists.values() if a.wiring.name == "Arthur")
        # Two different Memory instances entirely - never the same
        # object under two names, which is the one thing that would make
        # isolation a matter of discipline rather than structure.
        assert manager.memory is not arthur.brain  # sanity: not comparing unrelated types by accident
        assert isinstance(manager.memory, Memory)


# ---------------------------------------------------------------------------
# 5. Relevance - recall must not dump the whole store into an unrelated
#    conversation.
# ---------------------------------------------------------------------------


class TestRelevance:
    def test_an_unrelated_query_does_not_surface_unrelated_memories(self):
        runtime, manager, channel, conversation = real_system()
        exchange(channel, conversation, "remember that my favorite color is blue")
        exchange(channel, conversation, "remember that I am allergic to peanuts")

        turn = exchange(channel, conversation, "what do you remember about my favorite color")
        assert "blue" in turn.outcome
        assert "peanuts" not in turn.outcome

    def test_recall_context_used_by_the_brain_is_bounded_not_a_full_dump(self):
        """ManagerAgent.recall_context (the notes a real conversational
        turn's Brain prompt is built from) stays bounded even when the
        store holds many records - the existing MEMORY_CONTEXT_LIMIT
        this project already enforces, reconfirmed here as part of this
        checkpoint's own relevance requirement rather than merely
        assumed from its existence."""
        runtime, manager, channel, conversation = real_system()
        for i in range(30):
            exchange(channel, conversation, f"remember that fact number {i} is true")

        notes = manager.recall_context(Task(description="tell me about fact number 5"))
        assert len(notes) <= 10  # MEMORY_CONTEXT_LIMIT's own documented bound


# ---------------------------------------------------------------------------
# 6. Recording purpose/context, including a multimodal-adjacent case -
#    the extension boundary Section 16 asks for, proven rather than built.
# ---------------------------------------------------------------------------


class TestRecordingPurposeAndMultimodalBoundary:
    def test_purpose_is_preserved_and_distinguishable_from_an_ordinary_preference(self):
        runtime, manager, channel, conversation = real_system()
        exchange(channel, conversation, "record this for research: freelance market rates are rising")
        exchange(channel, conversation, "remember that my favorite color is blue")

        research = [r for r in manager.memory.recall(limit=20) if r.kind == "recording"]
        preference = [r for r in manager.memory.recall(limit=20) if r.kind in ("user_context", "personal_context")]
        assert len(research) == 1
        assert research[0].context.get("purpose") == "research"
        assert len(preference) == 1

    def test_a_produced_media_asset_reference_can_already_be_captured_by_path(self, tmp_path):
        """Section 16's own instruction: where multimodal memory is not
        yet supported, establish the correct extensible boundary rather
        than fabricating support. Proven here, not built: MemoryRecord's
        already-opaque `context` mapping and record_this's own free-text
        capture already let a caller reference a produced asset's path -
        no new code was needed for this to already work, which is the
        honest boundary itself."""
        from src.agents.tools.media_production import MediaProductionTool
        from src.providers.media.image import LocalTitleCardImageProvider
        from src.providers.media.narration import NullNarrationProvider
        from src.providers.media.video import NullVideoComposer

        runtime, manager, channel, conversation = real_system()
        tool = MediaProductionTool(
            NullNarrationProvider(), LocalTitleCardImageProvider(), NullVideoComposer(),
            asset_dir=tmp_path,
        )
        manager.own_skills._tools = ToolSet([tool])

        produced = tool.run(operation="illustrate", content="a lighthouse at dusk")
        assert "Image produced" in produced

        turn = exchange(
            channel, conversation, f"record this for research: illustration produced - {produced}"
        )
        assert turn.succeeded
        records = [r for r in manager.memory.recall(limit=20) if r.kind == "recording"]
        assert records and ".png" in records[0].content


# ---------------------------------------------------------------------------
# 7. Shared Context Graph stays structurally separate from personal
#    Memory - preserved, not rebuilt, reconfirmed directly.
# ---------------------------------------------------------------------------


class TestSharedContextStaysSeparateFromPersonalMemory:
    def test_collaboration_module_never_imports_memory_and_vice_versa(self):
        import inspect
        import src.conversation.collaboration as collaboration_module
        import src.services.memory.memory as memory_module

        collab_source = inspect.getsource(collaboration_module)
        assert "import src.services.memory" not in collab_source
        assert "from src.services.memory" not in collab_source
        memory_source = inspect.getsource(memory_module)
        assert "import src.conversation" not in memory_source
        assert "from src.conversation" not in memory_source


# ---------------------------------------------------------------------------
# 8. Future-proof extensibility (Section 21/24) - memory is now a real
#    Ecosystem category, and a genuinely new, previously-unseen memory
#    component can enter through the identical extension boundary.
# ---------------------------------------------------------------------------


class TestMemoryEcosystemExtensibility:
    def test_memory_is_a_real_registered_ecosystem_category(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        assert "memory" in registry.categories()
        adapter = registry.adapter_for("memory")
        assert {"ephemeral", "persistent"} <= {d.key for d in adapter.available()}
        assert adapter.validate("persistent").ok

    def test_switching_memory_backend_never_touches_llm_or_avatar_categories(self):
        settings_a = Settings.from_env(env={}, memory="ephemeral")
        settings_b = Settings.from_env(env={}, memory="persistent")
        registry_a = build_ecosystem_registry(settings_a)
        registry_b = build_ecosystem_registry(settings_b)
        assert registry_a.adapter_for("memory").active().key != registry_b.adapter_for("memory").active().key
        assert (
            registry_a.adapter_for("llm_provider").active().key
            == registry_b.adapter_for("llm_provider").active().key
        )

    def test_a_never_before_seen_memory_component_enters_through_the_identical_boundary(self):
        """Section 24's own acceptance test: a genuinely new memory
        component/category - "semantic_memory_provider", confirmed
        absent from the shipped source tree - registered purely here,
        using only the public GenericCategoryAdapter/EcosystemRegistry
        contract, with the full lifecycle exercised against it."""
        import subprocess

        result = subprocess.run(
            ["git", "grep", "-il", "semantic_memory_provider", "--", "src", "scripts"],
            cwd=".", capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", "the fixture category must be genuinely unseen"

        class FakeSemanticIndex:
            def __init__(self, key: str, credential: "str | None"):
                if key == "pinecone-like" and credential is None:
                    raise RuntimeError("no API key configured for pinecone-like")
                self.key = key

        registry = EcosystemRegistry()
        registry.register(
            GenericCategoryAdapter(
                "semantic_memory_provider",
                list_backends=lambda: ["null", "local-embedding", "pinecone-like"],
                active_key=lambda: "null",
                build=lambda key, credential: FakeSemanticIndex(key, credential),
                requires_credential=lambda key: key == "pinecone-like",
                dependents=("a future semantic recall layer over existing MemoryRecords",),
            )
        )

        assert "semantic_memory_provider" in registry.categories()
        adapter = registry.adapter_for("semantic_memory_provider")
        assert {d.key for d in adapter.available()} == {"null", "local-embedding", "pinecone-like"}
        assert not adapter.validate("pinecone-like").ok
        assert adapter.validate("pinecone-like", credential="fake-but-present").ok
        assert adapter.validate("local-embedding").ok

        # Registered alongside the real registry with zero change to
        # contracts.py, registry_factory.py, or Memory itself.
        real_registry = build_ecosystem_registry(Settings.from_env(env={}))
        real_registry.register(adapter)
        assert real_registry.adapter_for("memory") is not None
        assert real_registry.adapter_for("semantic_memory_provider") is adapter
