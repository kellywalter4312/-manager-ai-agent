"""A language model's second opinion on an opportunity the rules already found.

This is an additional reasoning layer. It does not detect, extract, match or
rank - the deterministic pipeline owns all four, and keeps them. What it adds
is the part rules genuinely cannot do:

    deterministic  ->  detect  extract  match  rank
    this layer     ->  interpret  reason  concerns/unknowns  judgement

Note what this class is *not*: it does not implement ``Analyzer``. That
interface is ``analyze(messages) -> AnalysisReport`` and belongs to the
deterministic pipeline; implementing it here would make this component look
substitutable for JobOpportunityAnalyzer, which is precisely the thing it must
not be. The signature is different on purpose - one opportunity in, one
judgement out.

The provider is injected and only the LLMProvider interface is imported, so
this works identically with the null provider, a scripted test double, or a
real model. No vendor SDK appears anywhere in this file.

Privacy note: the prompt necessarily contains message-derived content - the
posting text and sender. With the default null provider nothing leaves the
machine. When a real provider is wired up, this function is the point at which
email content would travel to an external service, and that should be a
deliberate, separate decision.
"""

from __future__ import annotations

from typing import Any

from src.agents.email.llm_analysis import LLMJudgement, parse_judgement_or_unavailable
from src.providers.base import LLMProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The reviewing identity - the "give it an identity" step of the AGENT
# framework, expressed as a system prompt.
SYSTEM_PROMPT = (
    "You review job and freelance opportunities on behalf of one person and "
    "judge whether each is worth their time. "
    "Factual details have already been extracted by a separate system: do not "
    "re-extract them and do not contradict them. Your job is judgement - "
    "interpret requirements written as prose, weigh the fit against the "
    "person's demonstrated skills, name anything that should give pause, and "
    "say plainly what the posting does not tell you. "
    "Never invent a requirement, salary, deadline, company or skill that is "
    "not present in the posting. Never credit the person with a skill that is "
    "not listed as demonstrated. If you cannot judge, answer \"unclear\". "
    "Reply with a single JSON object and nothing else."
)

# The shape the parser expects. Kept beside the prompt so the two cannot drift.
RESPONSE_SCHEMA = """{
  "verdict": "pursue | consider | skip | unclear",
  "rationale": "one or two sentences explaining the verdict",
  "confidence": 0.0,
  "summary": "one sentence describing the role",
  "inferred_requirements": ["requirements stated in prose rather than a labelled list"],
  "inferred_seniority": "intern | junior | mid | senior | lead, or null",
  "concerns": ["anything that should give pause"],
  "unknowns": ["what the posting does not say"]
}"""


def _facts(opportunity: Any) -> list[str]:
    """The details the deterministic pipeline already established."""
    known = [
        ("role", getattr(opportunity, "role", None)),
        ("company", getattr(opportunity, "company", None)),
        ("source", getattr(opportunity, "source", None)),
        ("seniority", getattr(opportunity, "seniority", None)),
        ("experience", getattr(opportunity, "experience_required", None)),
        ("salary or rate", getattr(opportunity, "salary", None)),
        ("deadline", getattr(opportunity, "deadline", None)),
        ("location", getattr(opportunity, "location", None)),
        ("employment type", getattr(opportunity, "employment_type", None)),
    ]
    return [f"- {name}: {value}" for name, value in known if value]


def _candidate(profile: Any) -> list[str]:
    """What the person has actually demonstrated, and what they have not.

    Learning-tier skills are labelled as such rather than listed among the
    demonstrated ones, so the model is never handed an inflated picture to
    reason from.
    """
    if profile is None:
        return ["- no profile supplied; judge the posting on its own terms"]

    from src.services.profile.profile import SkillTier

    lines = []
    for tier, label in (
        (SkillTier.STRONG, "demonstrated (strong)"),
        (SkillTier.WORKING, "demonstrated (working level)"),
        (SkillTier.LEARNING, "currently learning - NOT a demonstrated skill"),
    ):
        skills = sorted(profile.skills_in(tier))
        if skills:
            lines.append(f"- {label}: {', '.join(skills)}")

    if profile.seniority:
        lines.append(f"- seniority: {profile.seniority}")
    if profile.targets:
        lines.append(f"- looking for: {', '.join(sorted(profile.targets))}")
    return lines or ["- no skills recorded"]


def build_interpretation_prompt(opportunity: Any, profile: Any = None) -> str:
    """Build the prompt for one opportunity.

    A pure function: no provider, no I/O. That makes what the model is asked
    fully testable on its own, which matters more here than usual - a prompt is
    the part of an LLM feature most likely to drift unnoticed.
    """
    required = getattr(opportunity, "required_skills", ()) or ()
    subject = getattr(opportunity, "subject", "") or ""
    sender = getattr(opportunity, "sender", "") or ""
    description = getattr(opportunity, "description", "") or ""

    sections = [
        "A job or freelance opportunity was found in an email. Judge whether it "
        "is worth this person's time.",
        "",
        "ALREADY EXTRACTED - treat as established fact:",
        *(_facts(opportunity) or ["- nothing could be extracted"]),
    ]

    if required:
        sections += ["", f"Requirements already identified: {', '.join(required)}"]

    sections += [
        "",
        "THE PERSON:",
        *_candidate(profile),
        "",
        "THE POSTING:",
        f"from: {sender}",
        f"subject: {subject}",
        description or "(no body text was extracted)",
        "",
        "Reply with a single JSON object in exactly this shape:",
        RESPONSE_SCHEMA,
    ]
    return "\n".join(sections)


class OpportunityInterpreter:
    """Asks a language model to judge one opportunity."""

    def __init__(self, provider: LLMProvider) -> None:
        """The provider is injected - this class never constructs one.

        Same pattern as the routers and the mailbox: the component depends on
        the interface, the composition root decides which implementation.
        """
        self._provider = provider

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def interpret(self, opportunity: Any, profile: Any = None) -> LLMJudgement:
        """Return the model's judgement, or an honest "no interpretation".

        Never raises. Three separate failures are each turned into an
        unavailable judgement that records why: the provider erroring, the
        provider returning nothing, and the provider returning something that
        cannot be read. One bad response costs that opportunity its
        interpretation - never the run.
        """
        message_id = getattr(opportunity, "message_id", "") or ""
        prompt = build_interpretation_prompt(opportunity, profile)

        try:
            completion = self._provider.complete(prompt, system=SYSTEM_PROMPT)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            logger.exception(
                "provider '%s' failed while interpreting %s",
                self._provider.name,
                message_id,
            )
            return LLMJudgement.unavailable(
                message_id=message_id,
                provider=self._provider.name,
                reason=f"the provider failed: {type(exc).__name__}: {exc}",
            )

        if completion.is_empty:
            logger.debug("provider '%s' returned no text", completion.provider)
            return LLMJudgement.unavailable(
                message_id=message_id,
                provider=completion.provider,
                reason="the model returned no text",
                model=completion.model,
            )

        # Provenance comes from the completion, never from the model's own
        # JSON - the parser enforces that.
        judgement = parse_judgement_or_unavailable(
            completion.text,
            message_id=message_id,
            provider=completion.provider,
            model=completion.model,
        )
        logger.debug("interpreted %s: %s", message_id, judgement.describe())
        return judgement
