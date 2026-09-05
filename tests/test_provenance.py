"""Telling "I observed this" apart from "I remember this".

The confusion this file exists to prevent, in one example:

    Tuesday   a tool reports Dublin = 15.6C          <- an observation
    Tuesday   the system remembers "Dublin 15.6C"    <- knowledge
    Friday    the system recalls that record         <- still Tuesday's fact

Nothing about a MemoryRecord distinguishes the third line from the first.
``created_at`` says when something was *written down*, which is not when the
world was *looked at* - and if those two are the only timestamps in play, a
three-day-old reading is indistinguishable from a fresh one.

So the observation boundary carries its own provenance. A ToolResult records
when the world was seen and what was reached for, and those travel with the
observation into whatever a caller does with it.

The second half of this file is the boundary that must not move: an
observation is still not a memory, and nothing here makes it become one.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.agents.tools.tool import Tool, ToolError, ToolResult, ToolSpec, ToolStatus
from src.agents.tools.toolset import ToolSet
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import MemoryRecord


class Thermometer(Tool):
    """A tool that reports a reading. Stands in for any live source."""

    def __init__(self, reading="15.6"):
        self._reading = reading

    @property
    def spec(self):
        return ToolSpec(
            name="thermometer",
            description="reports the current temperature somewhere",
        )

    def run(self, **arguments):
        return self._reading


class Broken(Tool):
    @property
    def spec(self):
        return ToolSpec(name="broken", description="always fails")

    def run(self, **arguments):
        raise RuntimeError("the sensor is unplugged")


# ---------------------------------------------------------------------------
# An observation knows when it was made and what it came from
# ---------------------------------------------------------------------------


class TestAnObservationCarriesItsProvenance:
    def test_a_successful_call_is_dated(self):
        before = datetime.now(timezone.utc)
        result = ToolSet([Thermometer()]).invoke("thermometer")
        after = datetime.now(timezone.utc)

        assert result.ok
        assert result.observed_at is not None
        assert before <= result.observed_at <= after
        assert result.observed_at.tzinfo is not None

    def test_a_successful_call_names_what_it_reached_for(self):
        result = ToolSet([Thermometer()]).invoke("thermometer")
        assert result.source == "thermometer"

    def test_a_source_may_be_more_specific_than_the_tool(self):
        """A tool that reaches a named external system can say so."""
        result = ToolResult.succeeded("thermometer", "15.6", source="met-eireann")
        assert result.source == "met-eireann"
        assert result.tool == "thermometer"

    def test_an_observation_is_recognisable_as_one(self):
        assert ToolSet([Thermometer()]).invoke("thermometer").is_observation is True

    def test_provenance_survives_the_toolset_boundary(self):
        """The boundary wraps every call, so it is the one place provenance
        could be dropped. It is not."""
        result = ToolSet([Thermometer()]).invoke("thermometer")
        assert result.source
        assert result.observed_at is not None
        assert "observed_at" in result.describe()
        assert "source=" in result.describe()

    def test_the_log_line_still_excludes_what_was_seen(self):
        """Provenance is not content. Where and when may be logged; the
        reading itself may not."""
        result = ToolSet([Thermometer(reading="15.6 degrees in Dublin")]).invoke(
            "thermometer"
        )
        assert "Dublin" not in result.describe()
        assert "source=" in result.describe()


# ---------------------------------------------------------------------------
# Only a successful call observed anything
# ---------------------------------------------------------------------------


class TestNothingElseIsAnObservation:
    def test_a_rejected_call_is_not_dated(self):
        """It never ran, so there is no moment at which the world was seen."""
        result = ToolSet([Thermometer()]).invoke("nonexistent")
        assert result.status is ToolStatus.REJECTED
        assert result.observed_at is None
        assert result.is_observation is False

    def test_a_failed_call_is_not_dated(self):
        """It ran and produced nothing to look at."""
        result = ToolSet([Broken()]).invoke("broken")
        assert result.status is ToolStatus.FAILED
        assert result.observed_at is None
        assert result.is_observation is False

    def test_a_failed_call_still_records_what_it_reached_for(self):
        """Useful, and different from claiming it saw something."""
        result = ToolSet([Broken()]).invoke("broken")
        assert result.source == "broken"

    def test_dating_a_non_observation_is_refused(self):
        """Structural. "We tried at 14:03" must not be readable later as "the
        world was like this at 14:03"."""
        with pytest.raises(ToolError):
            ToolResult(
                tool="thermometer",
                status=ToolStatus.FAILED,
                error="the sensor is unplugged",
                observed_at=datetime.now(timezone.utc),
            )

    def test_an_observation_time_must_be_a_time(self):
        with pytest.raises(ToolError):
            ToolResult(
                tool="thermometer",
                status=ToolStatus.SUCCEEDED,
                output="15.6",
                observed_at="tuesday",
            )


# ---------------------------------------------------------------------------
# Observed is not remembered
# ---------------------------------------------------------------------------


class TestObservationAndMemoryStayDistinct:
    def test_observing_writes_no_memory(self):
        """The boundary that already existed, re-checked now that observations
        carry something a store would find attractive."""
        memory = EphemeralMemory()
        ToolSet([Thermometer()]).invoke("thermometer")
        assert len(memory.recall(limit=50)) == 0

    def test_when_it_was_seen_and_when_it_was_written_are_different_facts(self):
        """The worked example. A reading taken on Tuesday and written down on
        Friday must still say Tuesday."""
        tuesday = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
        friday = tuesday + timedelta(days=3)

        observation = ToolResult.succeeded(
            "thermometer", "15.6", source="met-eireann", observed_at=tuesday
        )
        remembered = MemoryRecord(
            content="Dublin was 15.6C",
            kind="user_context",
            created_at=friday,
            context={
                "observed_at": observation.observed_at.isoformat(),
                "source": observation.source,
            },
        )

        assert remembered.created_at == friday
        assert remembered.context["observed_at"] == tuesday.isoformat()
        assert remembered.created_at != observation.observed_at

    def test_a_record_can_carry_provenance_without_memory_understanding_it(self):
        """``context`` is opaque - carried, never interpreted - which is how
        provenance rides into a store that knows nothing about tools."""
        observation = ToolResult.succeeded("thermometer", "15.6", source="met-eireann")
        memory = EphemeralMemory()

        memory.remember(
            MemoryRecord(
                content="Dublin was 15.6C",
                kind="user_context",
                context={
                    "observed_at": observation.observed_at.isoformat(),
                    "source": observation.source,
                },
            )
        )

        recalled = memory.recall("Dublin", limit=1)[0]
        assert recalled.context["source"] == "met-eireann"

    def test_memory_gained_no_knowledge_of_tools(self):
        """Structural. Provenance travelling into memory must not make memory
        depend on the observation boundary."""
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "src" / "services" / "memory"
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "tools" not in node.module, (path.name, node.module)


# ---------------------------------------------------------------------------
# Brain mode does not gate the environment
# ---------------------------------------------------------------------------


class TestHalfBrainStillReachesTheEnvironment:
    def test_a_tool_runs_with_no_model_anywhere(self):
        """Environment access is a tool question, never a brain one. Half Brain
        is reduced reasoning depth, not reduced reach."""
        from src.manager.cognition import CognitiveMode, assess_cognition

        assert assess_cognition(None).mode is CognitiveMode.HALF_BRAIN

        result = ToolSet([Thermometer()]).invoke("thermometer")
        assert result.ok
        assert result.is_observation

    def test_the_toolset_consults_no_brain(self):
        """Environment access is a tool question, never a brain one - so a
        tool may depend on a *provider of information*, and must never
        depend on reasoning or model-availability machinery.

        Narrowed in Stage 14, not weakened: ``src/providers/live_info.py``
        is a provider in the ``LLMProvider`` sense of the word - a narrow,
        swappable source interface - but of current information, never of
        model reasoning, and living in ``src/providers/`` alongside the
        LLM providers is organisation, not a reasoning dependency. The
        properties this test actually guards - no brain, no cognition
        module, no *model* provider - are checked by name below, rather
        than by a substring that also happens to match the module Stage
        14's own live-information tool legitimately depends on.
        """
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "src" / "agents" / "tools"
        banned_modules = {
            "src.providers.base",
            "src.providers.factory",
            "src.providers.null_provider",
            "src.providers.anthropic_provider",
        }
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for banned in ("brain", "cognition"):
                        assert banned not in node.module, (path.name, node.module)
                    assert node.module not in banned_modules, (path.name, node.module)
