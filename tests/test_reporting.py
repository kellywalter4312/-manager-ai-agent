"""Tests for how a run is reported back to the user."""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.heartbeat.reporting import render_report, write_report

NOW = datetime(2026, 8, 17, 9, 30, 0, tzinfo=timezone.utc)


def opportunity(headline="Junior Python Developer at Aurora", explanation="  you have: python",
                uncertainties=()):
    return SimpleNamespace(
        headline=lambda: headline,
        explain=lambda: explanation,
        uncertainties=uncertainties,
    )


class TestRendering:
    def test_includes_the_run_time(self):
        assert "2026-08-17" in render_report([opportunity()], when=NOW)

    def test_lists_opportunities_with_reasoning(self):
        text = render_report([opportunity()], when=NOW)
        assert "Aurora" in text and "you have: python" in text

    def test_reports_the_counts(self):
        text = render_report([opportunity()], skipped=3, considered=12, when=NOW)
        assert "messages considered: 12" in text
        assert "already reported previously: 3" in text

    def test_caps_at_max_reported(self):
        many = [opportunity(headline=f"Role {i}") for i in range(10)]
        text = render_report(many, when=NOW, max_reported=3)
        assert "## Top 3" in text

    def test_acknowledges_what_it_did_not_show(self):
        many = [opportunity(headline=f"Role {i}") for i in range(10)]
        assert "7 further new opportunity" in render_report(many, when=NOW, max_reported=3)

    def test_includes_the_ambiguous_section(self):
        text = render_report(
            [opportunity()],
            ambiguous=[opportunity(headline="Vague thing", uncertainties=("no skills listed",))],
            when=NOW,
        )
        assert "Needs a look" in text and "no skills listed" in text

    def test_ambiguous_can_be_suppressed_by_policy(self):
        text = render_report(
            [opportunity()],
            ambiguous=[opportunity(headline="Vague thing")],
            when=NOW,
            include_ambiguous=False,
        )
        assert "Needs a look" not in text

    def test_states_that_no_action_was_taken(self):
        """Every report says plainly that the agent acted on nothing."""
        text = render_report([opportunity()], when=NOW)
        assert "read-only" in text and "cannot send" in text

    def test_empty_run_still_renders(self):
        assert render_report([], when=NOW).strip()


class TestWriting:
    def test_writes_a_timestamped_file(self, tmp_path):
        path = write_report("# hello", NOW, tmp_path)
        assert path.exists() and "20260817" in path.name

    def test_creates_the_directory(self, tmp_path):
        path = write_report("# hello", NOW, tmp_path / "nested" / "reports")
        assert path.exists()

    def test_content_round_trips(self, tmp_path):
        path = write_report("# body text", NOW, tmp_path)
        assert path.read_text(encoding="utf-8") == "# body text"

    def test_two_runs_do_not_collide(self, tmp_path):
        later = NOW.replace(second=45)
        first = write_report("a", NOW, tmp_path)
        second = write_report("b", later, tmp_path)
        assert first != second
