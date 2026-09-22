"""What the Manager is allowed to remember.

Memory is not a log. The distinction this module exists to enforce:

    log            - what happened, in order, for a human to read afterwards
    state          - what a specific job needs to not repeat itself
    MEMORY         - knowledge that should change a future decision

All three are useful and only the third belongs here. An orchestrator that
remembered every task it ran would have a history, not knowledge: the volume
would grow without bound, recall would return mostly noise, and the few facts
worth acting on would be buried under thousands of routine executions. So the
rule is an allow-list, not a deny-list - nothing is remembered unless it fits a
category that was chosen on purpose.

Four categories, and the test for adding a fifth is high: it must be
information that would plausibly change what the Manager decides next time. If
it would only be interesting to read, it is a log.

    USER_CONTEXT        stable facts about the person or the project
    DECISION            an orchestration choice, and why it was made
    DELEGATION_OUTCOME  what came back from a specialist, and whether it worked
    LESSON              something learned that should change future behaviour

The exclusions below are data rather than comments, following the same
reasoning as DEFAULT_NEVER in soul.py: a rule expressed as a constant can be
tested, listed and shown to a user, while a rule expressed as a comment is a
hope. EXCLUDED_KINDS names things that must never enter Manager memory *and
says why*, so a rejection can explain itself.

This module holds no Gmail knowledge, and needs none. A raw Gmail message is
refused because it is not one of the four categories - the same rule that
refuses a log line, a debug dump and a stack trace. Domain-specific exclusions
are named for the quality of the error message, never because the rule depends
on them.

Scope note: defining what *may* be remembered is not the same as deciding what
*is* remembered. Nothing here extracts, records or stores anything, and the
Manager does not yet hold a policy. Admission rules first, recording later.
"""

from __future__ import annotations

import re
from enum import Enum

from src.services.memory.memory import MemoryRecord
from src.services.memory.policy import MemoryPolicy, PolicyVerdict


class MemoryCategory(str, Enum):
    """The only kinds of thing the Manager retains.

    Values match MemoryRecord.kind, which is already normalised to lowercase -
    so a category is stored on the record itself and needs no parallel field.
    """

    USER_CONTEXT = "user_context"
    DECISION = "decision"
    DELEGATION_OUTCOME = "delegation_outcome"
    LESSON = "lesson"
    # Stage 12 additions: an explicit "record this [for X]" capture is a
    # different request from "remember that X" (see
    # src/services/memory/capture.py's own docstring for the distinction)
    # and a personal milestone is structured, dated, extensible data (see
    # src/services/memory/milestones.py) - neither belongs under
    # USER_CONTEXT, which would blur "the user asked this be kept" and
    # "the user asked this be captured for later" into the one category
    # that already existed.
    RECORDING = "recording"
    MILESTONE = "milestone"
    # What the user is studying, reading or preparing for. Passes the
    # admission test this module states for a category existing at all -
    # it plainly changes what the Manager should do next time, because an
    # explanation pitched at what someone is actually working on is a
    # different explanation.
    #
    # Deliberately not folded into USER_CONTEXT. That category holds
    # stable facts about the person; a course, a book or an exam is a
    # fact with a *season* - true while it lasts, worth removing when it
    # ends - and one category holding both would make "forget what I was
    # studying" indistinguishable from "forget who I am". It is equally
    # deliberately not a store of the material itself: a book belongs in
    # the user's own documents (see src/providers/knowledge.py), and
    # admitting one here would be the dump-under-a-plausible-category
    # case MAX_CONTENT_CHARS already refuses.
    LEARNING = "learning"
    # Phase 5.1: an outstanding, actionable item with real state (pending
    # -> complete), distinct from every category above it. Passes the
    # same admission test the others do - a pending task plainly changes
    # what the Manager should do next time (surface it, not re-suggest
    # it, know what is still outstanding). Not "task" (EXCLUDED_KINDS
    # already reserves that literal string for the internal Task/
    # TaskResult execution-bookkeeping concept below - "every routine
    # execution is history, not knowledge" - a different thing entirely
    # from a user-requested to-do item), and not USER_CONTEXT: a task is
    # something to *do*, not a stable fact about who the person *is* -
    # collapsing them would make "forget that task" indistinguishable
    # from "forget who I am", the identical reasoning RECORDING/MILESTONE
    # were kept out of USER_CONTEXT for above.
    TASK = "user_task"
    # Generalized user knowledge/context checkpoint: a self-declared,
    # updatable fact about the person - a skill they say they have
    # learned, a schedule, an organisational context, or any other
    # topic-shaped personal fact nobody has named yet. Deliberately not
    # folded into USER_CONTEXT, for the identical reason LEARNING above
    # already is not: this category's own facts genuinely supersede -
    # "this is my new IT schedule" makes an earlier schedule no longer
    # current - and USER_CONTEXT holds stable facts that do not work
    # that way. Also deliberately not folded into LEARNING, which stays
    # scoped to exactly what it always was (the present-continuous study
    # subject an explanation should be pitched at); this category is the
    # general mechanism a topic-keyed "latest record for this topic
    # wins" resolution runs over (see src/services/memory/personal_context.py),
    # open to skills, schedules and any future kind of personal fact
    # without ever adding a new MemoryCategory for each one.
    PERSONAL_CONTEXT = "personal_context"
    # Personal Organization/Calendar checkpoint: a real, structured
    # calendar item - a date/time/recurrence with a title and a status
    # (scheduled/cancelled/completed), never free text that merely reads
    # like a schedule. Deliberately not folded into PERSONAL_CONTEXT: that
    # category holds a person's own topic-keyed *statement* ("this is my
    # new IT schedule: Mon-Fri 9-3") - one record per statement, resolved
    # by "latest wins". A calendar item is what that statement, once
    # interpreted, actually *means* as structured state - genuinely
    # different data (real date/time/duration/recurrence fields, not
    # prose) with its own, two-level lifecycle (an individual item's own
    # updates, and a whole taught schedule's own supersession) - see
    # src/services/memory/calendar_items.py. The two categories are
    # related (a calendar item may carry the topic_key/record id of the
    # personal-context statement it was derived from) but never merged:
    # collapsing them would make "what did I say about my schedule" and
    # "what does my calendar actually contain" the same, unanswerable
    # question.
    CALENDAR_ITEM = "calendar_item"
    # General Time/Event Awareness + Proactive Visual Experience
    # checkpoint: which real, presentable visual/animation experience
    # was selected for one genuinely due event, and whether it has
    # actually reached a live frontend yet. Deliberately not folded into
    # MILESTONE (the event this describes) or PERSONAL_CONTEXT/
    # CALENDAR_ITEM (what was learned): this category answers a third,
    # genuinely different question - "what has already been shown for
    # this occurrence" - so a repeated proactive check never re-selects
    # or re-presents the identical experience, and a later occurrence of
    # the same kind of event can honestly avoid repeating the exact same
    # one. Passes the same admission test every category here already
    # must: it plainly changes what the Manager selects and presents
    # next time.
    EXPERIENCE = "experience"
    # Real Audio Recording/Capture checkpoint: that a real, user-directed
    # audio recording exists, its purpose/context, and a reference to
    # the actual artifact (src/services/artifacts/store.py) that holds
    # the binary - never the audio bytes themselves, which stay in the
    # artifact store exactly as every other real media artifact's bytes
    # already do (see AudioContent/VideoContent's own docstrings for why
    # a *reference* belongs here and the rendered/captured file does
    # not). Deliberately not folded into RECORDING above: that category
    # is an explicit *text* capture ("record this for X" - a note),
    # kept apart from an actual microphone artifact for the identical
    # reason capture.py's own docstring already draws the remember/
    # record line - "record this" and "here is a real audio recording"
    # are different requests even though both end up governed. Passes
    # the same admission test every category here already must: a
    # recording plainly changes what the Manager can retrieve, play
    # back and reference next time.
    AUDIO_RECORDING = "audio_recording"
    # T-REC-03 (2026-09-22): the identical admission the audio recording
    # category above already earns, for a real screen or camera capture
    # (getDisplayMedia/a video-capable getUserMedia) - distinguished from
    # AUDIO_RECORDING because the artifact is a video, and from each other
    # (screen vs. camera) by context["source"] rather than a third/fourth
    # parallel category for what is otherwise the identical shape of fact.
    VIDEO_RECORDING = "video_recording"

    @property
    def label(self) -> str:
        return {
            "user_context": "USER CONTEXT",
            "decision": "ORCHESTRATION DECISION",
            "delegation_outcome": "DELEGATION OUTCOME",
            "lesson": "LESSON LEARNED",
            "recording": "RECORDED FOR LATER",
            "milestone": "MILESTONE",
            "learning": "LEARNING CONTEXT",
            "user_task": "TASK",
            "personal_context": "PERSONAL CONTEXT",
            "calendar_item": "CALENDAR",
            "experience": "EXPERIENCE",
            "audio_recording": "AUDIO RECORDING",
            "video_recording": "VIDEO RECORDING",
        }[self.value]

    @property
    def meaning(self) -> str:
        """What belongs in this category, in one sentence."""
        return {
            "user_context": (
                "A stable fact about the person or the project that should "
                "inform future decisions - a preference, a constraint, a goal."
            ),
            "decision": (
                "An orchestration choice the Manager made and the reasoning "
                "behind it, so a similar situation can be handled consistently."
            ),
            "delegation_outcome": (
                "What a specialist returned and whether it was useful, so "
                "future routing can account for what actually works."
            ),
            "lesson": (
                "Something learned during execution that should change future "
                "behaviour - a repeated failure, a better approach."
            ),
            "recording": (
                "Something explicitly captured for later retrieval, with an "
                "intended purpose - never ordinary conversation retained "
                "automatically."
            ),
            "milestone": (
                "A structured, dated personal milestone - a birthday, a first "
                "client, a graduation - recognised or explicitly stated, "
                "distinct from an ordinary durable fact."
            ),
            "learning": (
                "What the person is currently studying, reading or preparing "
                "for, so an explanation can be pitched at the work they are "
                "actually doing - never the study material itself."
            ),
            "user_task": (
                "An outstanding, actionable item the person asked to be "
                "tracked - pending or complete - never a stable fact about "
                "who they are."
            ),
            "personal_context": (
                "A self-declared, updatable fact about the person - a skill "
                "they say they have learned, a schedule, an organisational "
                "context - grouped by topic so a newer statement about the "
                "same topic supersedes an older one rather than both being "
                "treated as simultaneously current."
            ),
            "calendar_item": (
                "A real, structured calendar item - a date/time/duration/"
                "recurrence with a title and a scheduled/cancelled/completed "
                "status - interpreted from what the user taught, never free "
                "text that merely reads like a schedule."
            ),
            "experience": (
                "The real visual/animation experience selected for one "
                "genuinely due event, and whether it has actually reached "
                "a live frontend yet - so a repeated check never re-selects "
                "or re-presents the same occurrence, and history informs "
                "later variety."
            ),
            "audio_recording": (
                "That a real, user-directed microphone recording exists, "
                "its purpose/context, and a reference to the actual "
                "artifact holding the audio - never the audio bytes "
                "themselves, and never ordinary conversation retained "
                "automatically."
            ),
            "video_recording": (
                "That a real, user-directed screen or camera recording "
                "exists (context['source'] says which), its purpose/"
                "context, and a reference to the actual artifact holding "
                "the video - never the video bytes themselves, and never "
                "ordinary conversation retained automatically."
            ),
        }[self.value]

    @classmethod
    def of(cls, record: MemoryRecord) -> "MemoryCategory | None":
        """The category a record claims, or None if it claims none of them."""
        try:
            return cls(record.kind)
        except ValueError:
            return None


# Things that must never enter Manager memory, each with the reason it is
# refused. Named as data so the rule can be listed, tested and explained -
# the same reasoning as DEFAULT_NEVER in soul.py.
#
# Note that none of these are load-bearing: every one of them would already be
# refused for not being a MemoryCategory. They exist so the refusal says
# something useful instead of "unknown category".
EXCLUDED_KINDS: dict[str, str] = {
    "raw_message": "raw source material belongs in its source, not in memory",
    "message": "raw source material belongs in its source, not in memory",
    "log": "logs record what happened; memory holds what should change a decision",
    "debug": "temporary diagnostic detail has no bearing on future decisions",
    "trace": "temporary diagnostic detail has no bearing on future decisions",
    "stacktrace": "temporary diagnostic detail has no bearing on future decisions",
    "task_history": "every routine execution is history, not knowledge",
    "task": "every routine execution is history, not knowledge",
    "execution": "every routine execution is history, not knowledge",
    "dedup": "deduplication state belongs to the job that needs it",
    "dedupe": "deduplication state belongs to the job that needs it",
    "processed_state": "deduplication state belongs to the job that needs it",
    "heartbeat_state": "deduplication state belongs to the job that needs it",
    "fingerprint": "deduplication state belongs to the job that needs it",
    "reasoning": "a model's working-out is not a conclusion worth retaining",
    "thinking": "a model's working-out is not a conclusion worth retaining",
    "chain_of_thought": "a model's working-out is not a conclusion worth retaining",
    "scratch": "temporary working state does not survive the task that made it",
    "credential": "secrets are never remembered, under any category",
    "secret": "secrets are never remembered, under any category",
}

# Context keys that make a record a secret regardless of what it says. Matched
# as whole words against the key, so "api_key" and "password" are caught while
# an innocent "keywords" is not.
SECRET_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "password", "passwd", "secret", "token", "api_key", "apikey",
        "access_token", "refresh_token", "credential", "credentials",
        "private_key", "client_secret", "authorization", "auth",
    }
)

# Content that looks like a credential being written down. Deliberately matches
# the *shape* of a secret - an assignment, a known key prefix, a PEM block -
# rather than the bare words, so "we discussed their password policy" is not
# refused while "password: hunter2" is.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "an API key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "an access token"),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"), "an API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"), "a bearer token"),
    (
        re.compile(
            r"\b(password|passwd|secret|api[_\-]?key|access[_\-]?token|"
            r"refresh[_\-]?token|client[_\-]?secret)\b\s*[:=]\s*\S+",
            re.IGNORECASE,
        ),
        "a credential",
    ),
)

# Distilled knowledge is short. A memory far longer than this is almost always
# a dump of something else - a message body, a log extract, a model's
# working-out - filed under a category that looked plausible. The bound is what
# catches those without the policy needing to recognise any of them.
MAX_CONTENT_CHARS = 1000


class ManagerMemoryPolicy(MemoryPolicy):
    """The Manager's admission rules, as an object rather than a convention.

    Stateless and deterministic: the same record always gets the same verdict,
    and the policy holds nothing between calls. It can therefore be shared
    freely, unlike a Memory, which must not be.
    """

    def __init__(self, max_content_chars: int = MAX_CONTENT_CHARS) -> None:
        if max_content_chars <= 0:
            raise ValueError("max_content_chars must be positive")
        self.max_content_chars = max_content_chars

    def __repr__(self) -> str:
        return f"<ManagerMemoryPolicy categories={len(self.categories)}>"

    @property
    def categories(self) -> frozenset[MemoryCategory]:
        """Every category the Manager retains. The allow-list, in full."""
        return frozenset(MemoryCategory)

    @property
    def category_names(self) -> tuple[str, ...]:
        """The allowed category values, sorted, for display and configuration."""
        return tuple(sorted(category.value for category in MemoryCategory))

    def describe(self) -> str:
        """The policy in a form that can be shown to a user."""
        lines = ["The Manager remembers only:"]
        for category in sorted(MemoryCategory, key=lambda c: c.value):
            lines.append(f"  {category.label} - {category.meaning}")
        lines.append("Everything else - logs, raw messages, routine task history,")
        lines.append("deduplication state, model working-out, secrets - is refused.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # The decision
    # ------------------------------------------------------------------

    def evaluate(self, record: MemoryRecord) -> PolicyVerdict:
        """Judge one record against the Manager's rules.

        The checks run cheapest-and-broadest first, and secrets are checked
        even for records that already failed on category - so the reason given
        for a refusal never has to mention what the content looked like.
        """
        if not isinstance(record, MemoryRecord):
            return PolicyVerdict(
                allowed=False,
                reason=f"only a MemoryRecord can be judged, got {type(record).__name__}",
            )

        kind = record.kind

        # 1. The named exclusions, purely so the refusal is informative.
        if kind in EXCLUDED_KINDS:
            return PolicyVerdict(allowed=False, reason=EXCLUDED_KINDS[kind], category=kind)

        # 2. The rule that actually does the work: a closed allow-list.
        category = MemoryCategory.of(record)
        if category is None:
            allowed = ", ".join(self.category_names)
            return PolicyVerdict(
                allowed=False,
                reason=(
                    f"'{kind or '(none)'}' is not something the Manager remembers; "
                    f"allowed categories are: {allowed}"
                ),
                category=kind or None,
            )

        # 3. A secret is refused under every category, including valid ones.
        secret = self._looks_like_a_secret(record)
        if secret is not None:
            return PolicyVerdict(
                allowed=False,
                # Names the *kind* of secret, never the value - a rule that
                # refused a credential must not then quote it into a log.
                reason=f"refused: the record appears to contain {secret}",
                category=category.value,
            )

        # 4. Length, which catches dumps filed under a plausible category.
        if len(record.content) > self.max_content_chars:
            return PolicyVerdict(
                allowed=False,
                reason=(
                    f"{len(record.content)} characters is too long to be distilled "
                    f"knowledge (limit {self.max_content_chars}); this looks like a "
                    f"dump of something that belongs elsewhere"
                ),
                category=category.value,
            )

        return PolicyVerdict(
            allowed=True,
            reason=f"{category.label.lower()} is retained by the Manager",
            category=category.value,
        )

    # ------------------------------------------------------------------
    # Secret detection
    # ------------------------------------------------------------------

    def _looks_like_a_secret(self, record: MemoryRecord) -> str | None:
        """What kind of secret this appears to hold, or None.

        Errs toward refusing. A false positive costs one memory that has to be
        reworded; a false negative writes a credential into a store that gets
        recalled into prompts. Those are not comparable costs.
        """
        for pattern, description in _SECRET_PATTERNS:
            if pattern.search(record.content):
                return description

        for tag in record.tags:
            if tag.replace("-", "_") in SECRET_CONTEXT_KEYS:
                return "a value tagged as a credential"

        if record.context:
            for key in record.context:
                normalised = str(key).strip().lower().replace("-", "_")
                if normalised in SECRET_CONTEXT_KEYS:
                    return f"a credential under the '{key}' key"

        return None
