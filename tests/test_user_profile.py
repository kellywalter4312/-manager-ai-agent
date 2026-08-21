"""Tests for the user profile and its USER.md loader."""

import pytest

from src.services.profile.loader import load_profile, parse_profile
from src.services.profile.profile import (
    Project,
    ProfileError,
    SkillTier,
    UserProfile,
    normalize_seniority,
    normalize_skill,
)

SAMPLE = """
# USER

## Skills
### Strong
python, testing
### Working
javascript, node.js
### Learning
docker

## Projects
- Orchestrator - multi-agent system [skills: python, testing]
- Salon Site - static site [skills: javascript]

## Experience
seniority: junior
years: 1

## Targets
python development, automation

## Hard filters
- no unpaid work
"""


class TestNormalisation:
    def test_lowercases_and_trims(self):
        assert normalize_skill("  Python  ") == "python"

    @pytest.mark.parametrize(
        "alias,expected",
        [("JS", "javascript"), ("nodejs", "node.js"), ("py", "python"), ("API", "apis")],
    )
    def test_resolves_aliases(self, alias, expected):
        assert normalize_skill(alias) == expected

    def test_maps_seniority_synonyms(self):
        assert normalize_seniority("Entry level") == "junior"
        assert normalize_seniority("Senior Engineer") == "senior"

    def test_unknown_seniority_is_none(self):
        assert normalize_seniority("wizard") is None


class TestSkillTiers:
    def test_learning_counts_for_nothing(self):
        """The core honesty rule, encoded in the weights."""
        assert SkillTier.LEARNING.weight == 0.0
        assert SkillTier.WORKING.weight == 0.5
        assert SkillTier.STRONG.weight == 1.0

    def test_demonstrates_excludes_learning(self):
        profile = UserProfile(skills={"python": SkillTier.STRONG, "docker": SkillTier.LEARNING})
        assert profile.demonstrates("python")
        assert not profile.demonstrates("docker")

    def test_unknown_skill_is_not_demonstrated(self):
        assert not UserProfile(skills={}).demonstrates("rust")


class TestParsing:
    def test_reads_all_tiers(self):
        profile = parse_profile(SAMPLE)
        assert profile.tier_of("python") is SkillTier.STRONG
        assert profile.tier_of("javascript") is SkillTier.WORKING
        assert profile.tier_of("docker") is SkillTier.LEARNING

    def test_reads_projects_with_skills(self):
        profile = parse_profile(SAMPLE)
        assert [p.name for p in profile.projects] == ["Orchestrator", "Salon Site"]
        assert profile.projects[0].evidences("python")

    def test_reads_experience(self):
        profile = parse_profile(SAMPLE)
        assert profile.seniority == "junior" and profile.years == 1

    def test_reads_targets_and_hard_filters(self):
        profile = parse_profile(SAMPLE)
        assert "automation" in profile.targets
        assert profile.hard_filters == ("no unpaid work",)

    def test_missing_sections_are_allowed(self):
        profile = parse_profile("## Skills\n### Strong\npython\n")
        assert profile.projects == () and profile.seniority is None

    def test_rejects_skills_without_a_tier(self):
        """Guessing a tier is exactly the inflation this design avoids."""
        with pytest.raises(ProfileError) as exc:
            parse_profile("## Skills\npython\n")
        assert "tier heading" in str(exc.value)

    def test_rejects_an_unknown_tier(self):
        with pytest.raises(ProfileError):
            parse_profile("## Skills\n### Expert\npython\n")

    def test_project_names_may_use_various_dashes(self):
        profile = parse_profile("## Skills\n### Strong\npython\n## Projects\n- A — desc\n")
        assert profile.projects[0].name == "A"


class TestLoader:
    def test_missing_file_raises_with_guidance(self, tmp_path):
        with pytest.raises(ProfileError) as exc:
            load_profile(tmp_path / "nope.md")
        assert "USER.example.md" in str(exc.value)

    def test_empty_profile_raises(self, tmp_path):
        path = tmp_path / "USER.md"
        path.write_text("# USER\n", encoding="utf-8")
        with pytest.raises(ProfileError) as exc:
            load_profile(path)
        assert "no skills" in str(exc.value)

    def test_loads_a_real_file(self, tmp_path):
        path = tmp_path / "USER.md"
        path.write_text(SAMPLE, encoding="utf-8")
        assert load_profile(path).tier_of("python") is SkillTier.STRONG

    def test_the_committed_example_parses(self):
        """USER.example.md must stay valid - it is the template users copy."""
        profile = load_profile("USER.example.md")
        assert not profile.is_empty

    def test_editing_the_file_changes_the_profile(self, tmp_path):
        """Updating the profile is a file edit, not a code change."""
        path = tmp_path / "USER.md"
        path.write_text("## Skills\n### Strong\npython\n", encoding="utf-8")
        assert not load_profile(path).demonstrates("rust")
        path.write_text("## Skills\n### Strong\npython, rust\n", encoding="utf-8")
        assert load_profile(path).demonstrates("rust")


class TestEvidence:
    def test_finds_projects_supporting_skills(self):
        profile = parse_profile(SAMPLE)
        assert "Orchestrator" in profile.projects_evidencing(["python"])

    def test_returns_nothing_for_unsupported_skills(self):
        profile = parse_profile(SAMPLE)
        assert profile.projects_evidencing(["rust"]) == ()

    def test_describe_is_short_and_safe(self):
        described = parse_profile(SAMPLE).describe()
        assert "\n" not in described and "python" not in described
