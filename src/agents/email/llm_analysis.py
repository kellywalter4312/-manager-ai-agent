"""The contract for a language model's reading of a job posting.

This is an additional layer of interpretation, not a replacement for the
deterministic pipeline. That pipeline keeps doing what it does well - detecting
opportunity emails, extracting labelled fields, matching against USER.md,
scoring and ranking - and everything it produces continues to live on
``Opportunity``.

What a model can add is the part rules genuinely cannot:

  - requirements written as prose rather than a labelled "Skills:" list, which
    is the single biggest gap in the deterministic extractor
  - a judgement about whether a posting is worth the user's time, with reasons
  - an explicit account of what it could not determine

So this contract deliberately holds none of role, company, salary, deadline,
location, band, match score, matched or missing skills, or supporting
projects. Those already have an owner; duplicating them here would create two
sources of truth that could disagree. A judgement is joined to the rest of the
domain by ``message_id`` alone.

Two properties are enforced rather than left to convention:

  - **provenance is mandatory.** Every judgement records which provider (and
    model) produced it, so a report can always distinguish a model's opinion
    from a rule-derived fact.
  - **a verdict must carry a reason.** An opinion with no rationale is not
    something this project is willing to show the user.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


def _clean_tuple(values: Iterable[str], lower: bool = False) -> tuple[str, ...]:
    """Trim, drop blanks and de-duplicate while preserving order."""
    cleaned = []
    for value in values or ():
        text = " ".join(str(value).split())
        if lower:
            text = text.lower()
        if text:
            cleaned.append(text)
    return tuple(dict.fromkeys(cleaned))


class LLMVerdict(str, Enum):
    """What the model thinks the user should do about a posting."""

    PURSUE = "pursue"        # worth applying for
    CONSIDER = "consider"    # plausible, with caveats
    SKIP = "skip"            # not worth the user's time
    UNCLEAR = "unclear"      # not enough information to say

    @property
    def label(self) -> str:
        return {
            "pursue": "WORTH PURSUING",
            "consider": "WORTH CONSIDERING",
            "skip": "NOT WORTH IT",
            "unclear": "UNCLEAR - model could not judge",
        }[self.value]

    @property
    def is_decided(self) -> bool:
        return self is not LLMVerdict.UNCLEAR


class LLMAnalysisError(Exception):
    """Raised when a judgement cannot be formed into a valid contract."""


@dataclass(frozen=True)
class LLMJudgement:
    """One model's interpretation of one message.

    Frozen for the same reason every other contract here is: an interpretation
    is a record of what a model said at a point in time, and nothing
    downstream should be able to quietly edit it.
    """

    message_id: str
    verdict: LLMVerdict
    provider: str
    rationale: str
    confidence: float = 0.0
    model: str | None = None
    summary: str = ""
    inferred_requirements: tuple[str, ...] = field(default_factory=tuple)
    inferred_seniority: str | None = None
    concerns: tuple[str, ...] = field(default_factory=tuple)
    unknowns: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not str(self.message_id).strip():
            raise LLMAnalysisError("LLMJudgement.message_id must not be empty")
        object.__setattr__(self, "message_id", str(self.message_id).strip())

        # Provenance is not optional. A judgement whose origin is unknown
        # cannot be presented honestly alongside rule-derived facts.
        if not str(self.provider).strip():
            raise LLMAnalysisError(
                "LLMJudgement.provider must name the provider that produced it"
            )
        object.__setattr__(self, "provider", str(self.provider).strip())

        # A verdict with no reason is an opinion the user cannot evaluate.
        if not str(self.rationale).strip():
            raise LLMAnalysisError(
                f"LLMJudgement for '{self.message_id}' must explain its verdict"
            )
        object.__setattr__(self, "rationale", " ".join(str(self.rationale).split()))

        if not 0.0 <= float(self.confidence) <= 1.0:
            raise LLMAnalysisError(
                f"LLMJudgement.confidence must be between 0 and 1, got {self.confidence}"
            )
        object.__setattr__(self, "confidence", float(self.confidence))

        if isinstance(self.verdict, str) and not isinstance(self.verdict, LLMVerdict):
            try:
                object.__setattr__(self, "verdict", LLMVerdict(self.verdict.strip().lower()))
            except ValueError:
                raise LLMAnalysisError(f"Unknown verdict {self.verdict!r}") from None

        model = (self.model or "").strip()
        object.__setattr__(self, "model", model or None)
        object.__setattr__(self, "summary", " ".join(str(self.summary).split()))
        object.__setattr__(
            self, "inferred_seniority",
            (self.inferred_seniority or "").strip().lower() or None,
        )
        # Requirements are lower-cased so they compare cleanly with the skills
        # already normalised elsewhere in the project.
        object.__setattr__(
            self, "inferred_requirements", _clean_tuple(self.inferred_requirements, lower=True)
        )
        object.__setattr__(self, "concerns", _clean_tuple(self.concerns))
        object.__setattr__(self, "unknowns", _clean_tuple(self.unknowns))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_decided(self) -> bool:
        """Whether the model reached a usable verdict."""
        return self.verdict.is_decided

    @property
    def is_actionable(self) -> bool:
        """Whether the model thinks this is worth the user's attention."""
        return self.verdict in (LLMVerdict.PURSUE, LLMVerdict.CONSIDER)

    def is_confident(self, threshold: float = 0.5) -> bool:
        return self.is_decided and self.confidence >= threshold

    @property
    def attribution(self) -> str:
        """How this judgement should be credited wherever it is shown."""
        return f"{self.model or self.provider}"

    def describe(self) -> str:
        """A short line safe for logs.

        Excludes ``summary`` and ``rationale``: both are model-generated prose
        about the contents of an email, and this project keeps message content
        out of log files. The same reasoning as Completion.describe().
        """
        return (
            f"{self.verdict.value} confidence={self.confidence:.2f} "
            f"via={self.attribution} requirements={len(self.inferred_requirements)} "
            f"concerns={len(self.concerns)} unknowns={len(self.unknowns)}"
        )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def unavailable(
        cls,
        message_id: str,
        provider: str,
        reason: str = "no language model interpretation was available",
        model: str | None = None,
    ) -> "LLMJudgement":
        """A valid judgement meaning "no interpretation".

        The default provider makes no model call at all, and a provider can
        fail or decline. Each of those needs a first-class, honest
        representation - otherwise the absence of an opinion gets silently
        rendered as an opinion.
        """
        return cls(
            message_id=message_id,
            verdict=LLMVerdict.UNCLEAR,
            provider=provider,
            rationale=reason,
            confidence=0.0,
            model=model,
            unknowns=("no model interpretation",),
        )


# ---------------------------------------------------------------------------
# Parsing a model response into the contract
#
# Guiding rule, taken from how the rest of this project treats untrusted input:
# transport damage is normalised, semantic ambiguity is rejected. Fences,
# stray prose and whitespace are wrapper noise around intact content, so they
# are stripped. An unknown verdict or a missing rationale cannot be repaired
# without deciding what the model meant, so they raise - the same reasoning
# that makes Settings reject "maybe" for a boolean and the router reject an
# agent name that is not on the roster.
# ---------------------------------------------------------------------------

# Fields the model must supply. Everything else has a documented default.
REQUIRED_FIELDS: tuple[str, ...] = ("verdict", "rationale")

# Keys a response may contain that are ignored on purpose: provenance is the
# caller's to state, never the model's to claim.
CALLER_OWNED_FIELDS: frozenset[str] = frozenset({"message_id", "provider", "model"})

_FENCED_BLOCK = re.compile(r"```[A-Za-z0-9_+-]*\s*(?P<body>.*?)\s*```", re.DOTALL)


def _extract_json_text(text: str) -> str:
    """Pull the JSON object out of a model response.

    Models routinely wrap JSON in markdown fences or a sentence of preamble.
    That is packaging, not content, so it is removed rather than treated as a
    failure.
    """
    stripped = (text or "").strip()
    if not stripped:
        raise LLMAnalysisError("the model returned an empty response")

    fenced = _FENCED_BLOCK.search(stripped)
    if fenced:
        stripped = fenced.group("body").strip()

    if not stripped.startswith("{"):
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise LLMAnalysisError(
                "the model response contains no JSON object"
            )
        stripped = stripped[start : end + 1]

    return stripped


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    """Accept a list of strings, a bare string, or nothing."""
    if value is None:
        return ()
    if isinstance(value, str):
        # A single value where a list was asked for widens losslessly.
        return (value,)
    if isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, str):
                raise LLMAnalysisError(
                    f"'{field_name}' must contain only strings, found {type(item).__name__}"
                )
        return tuple(value)
    raise LLMAnalysisError(
        f"'{field_name}' must be a list of strings, got {type(value).__name__}"
    )


def _confidence(value: Any) -> float:
    """Accept a number, or a string that is unambiguously a number."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        # True would silently become 1.0 - a fabricated certainty.
        raise LLMAnalysisError("'confidence' must be a number, got a boolean")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise LLMAnalysisError(
                f"'confidence' must be a number between 0 and 1, got {value!r}"
            ) from None
    raise LLMAnalysisError(
        f"'confidence' must be a number, got {type(value).__name__}"
    )


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise LLMAnalysisError(
        f"'{field_name}' must be text, got {type(value).__name__}"
    )


def parse_judgement(
    response: str,
    *,
    message_id: str,
    provider: str,
    model: str | None = None,
) -> LLMJudgement:
    """Turn a model's response into a judgement, or raise LLMAnalysisError.

    Provenance comes from the caller and only from the caller. If a response
    names a provider or a message of its own, those keys are ignored: a model
    must not be able to assert which message it read or which system produced
    it.
    """
    payload = _extract_json_text(response)

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LLMAnalysisError(f"the model response is not valid JSON: {exc.msg}") from None

    if not isinstance(data, Mapping):
        raise LLMAnalysisError(
            f"expected a JSON object, got {type(data).__name__}"
        )

    missing = [key for key in REQUIRED_FIELDS if key not in data or data[key] is None]
    if missing:
        raise LLMAnalysisError(
            f"the model response is missing required field(s): {', '.join(missing)}"
        )

    verdict = data["verdict"]
    if not isinstance(verdict, str):
        raise LLMAnalysisError(
            f"'verdict' must be text, got {type(verdict).__name__}"
        )

    rationale = data["rationale"]
    if not isinstance(rationale, str):
        raise LLMAnalysisError(
            f"'rationale' must be text, got {type(rationale).__name__}"
        )

    # Unknown verdicts, blank rationales and out-of-range confidence are all
    # rejected by the contract itself, so validation lives in one place.
    return LLMJudgement(
        message_id=message_id,
        provider=provider,
        model=model,
        verdict=verdict,
        rationale=rationale,
        confidence=_confidence(data.get("confidence")),
        summary=_optional_text(data.get("summary"), "summary") or "",
        inferred_requirements=_string_tuple(
            data.get("inferred_requirements"), "inferred_requirements"
        ),
        inferred_seniority=_optional_text(
            data.get("inferred_seniority"), "inferred_seniority"
        ),
        concerns=_string_tuple(data.get("concerns"), "concerns"),
        unknowns=_string_tuple(data.get("unknowns"), "unknowns"),
    )


def parse_judgement_or_unavailable(
    response: str,
    *,
    message_id: str,
    provider: str,
    model: str | None = None,
) -> LLMJudgement:
    """Parse a response, degrading to "no interpretation" instead of raising.

    The strict parser is the right tool when a caller wants to know a response
    was unusable. This one is for the analysis loop: one malformed reply
    should cost that message its interpretation, not abandon the run - the
    same fault-isolation rule the Manager applies to a failing agent. The
    reason is preserved so the failure is visible rather than silent.
    """
    try:
        return parse_judgement(
            response, message_id=message_id, provider=provider, model=model
        )
    except LLMAnalysisError as exc:
        return LLMJudgement.unavailable(
            message_id=message_id,
            provider=provider,
            reason=f"could not read the model response: {exc}",
            model=model,
        )
