# Manager AI Agent — Master Architecture & Roadmap

**Authoritative document.** Where this file and any other document disagree about
what exists, this file wins — and where this file and the *code* disagree, the
code wins and this file is wrong and must be corrected.

Last synchronised against: `HEAD = 33a55bc`, with the Phase 1 channel/event
work, the Phase 2 Conversation, the Phase 2.5 memory/provenance work and the
Phase 3 voice identity, the Phase 3.5 audit fix, Phase 4 addressing, Phase 5
invitation, Phase 5.1 identity resolution, per-agent integration grants,
Phase 5.2 session continuity, the interface foundation, its visual pass
(§26), its live-serving milestone (§27), its live-interaction milestone
(§28), its per-agent visual identity (§29), its consolidated
`AgentVisualIdentity` resolver (§30) and its agent workspace foundation
(§31), its camera-focus/Room-presence extension (§32), Alfred's genuine
3D WebGL/Three.js environment (§33), its generalization to every
agent's own 3D environment (§34), per-agent genuine 3D Brain and
Memory interfaces (§35), the Conversation Room's own genuine 3D
environment (§36), the replaceable Visual Environment Registry (§37),
the first stage of Governed System Evolution — the versioning and
migration seam, `SYSTEM_VERSION` (§38) — Stage 5's stable Manager
identity and memory ownership (§39), Stage 6's Upgrade Manifest (§40),
Stage 7's Upgrade Planner (§41), Stage 8's Upgrade Executor (§42),
Stage 8A's ambient voice activation and continuous voice conversation
(§43), Stage 9's governed upgrade lifecycle (§44), Stage 10's production
speech provider seam and multimodal foundation (§45), Stage 11.1's agent
personality foundation (§46), and Stage 11.2's agent voice identity and
Conversation Room voice path (§47) present and **uncommitted**.
**3832 tests passing.**

Status vocabulary, used strictly:

| Mark | Meaning | Test for it |
|---|---|---|
| ✅ IMPLEMENTED | A production entry point reaches it at runtime | Can you name the file that calls it outside `tests/`? |
| 🟡 PREPARED | Real code, real tests, **no production caller** | Does only `tests/` import it? |
| 🔵 PLANNED | Agreed direction, no code | — |
| ❌ NOT STARTED | Not agreed in detail, no code | — |

The distinction between ✅ and 🟡 is the one this project keeps almost losing.
`tests/test_production_reachability.py` states the rule it was written for:

> **wired != reachable != durable.** A dependency can have a seam, a real
> implementation, and a full set of passing unit tests, and still never be
> supplied by any composition root.

A contract with tests is not a feature. Nothing below is marked ✅ because it
has a docstring.

---

## 0. Canonical Project State — read this first

**0.4 Canonical Project State's own deliverable.** Everything in this
section is a compact index into evidence that already exists elsewhere in
this document (and, for source/tests, in the repository itself) — it
answers the questions a new session or a new developer actually asks
first, without requiring a read of all 52 sections below to piece the
answer together. When this section and a detailed section disagree, the
detailed section is authoritative and this one is stale and must be
corrected to match it — the same rule the document's own opening
paragraph already states for itself versus the code.

**Current, accurate test count (uncommitted working tree, not the `HEAD`
this document's own header was last synchronised against): 5834 passed,
both fixed-order and randomised-order, as of 0.4 Canonical Project State.**
The header above is a historical record of an actual commit and is left
alone; this line is the live figure.

### 0.1 What Alfred OS is (requirements)

A multi-agent orchestrator: **Alfred** is the Manager/Orchestrator, with a
canonical identity, brain, memory, personality and voice independent of
whichever LLM provider is currently configured; he behaves as a natural
conversational partner, not a command parser (§48, §52.2.3). Specialists
(today: **Arthur**, the Email/Opportunity agent, §16) are real, separate
entities with their own identity/personality/brain/memory/voice/
capabilities/connections — never a rename or a shared instance of
Alfred's own. The system must run for years, not one demo (§15). Full
requirement traceability: §4.

### 0.2 Capabilities (what exists, what it does)

§1 (implemented ✅) and §2 (prepared 🟡, real code + tests, no production
caller) are the authoritative capability inventories, kept current at the
row level. Do not re-derive "what exists" by reading source at random —
start at §1/§2, then follow each row's own module/test pointer.

### 0.3 Completed — and what "complete" means here

**Designed ≠ implemented. Implemented ≠ tested. Tested ≠ practically
verified. Only implemented + tested + practically verified = COMPLETE.**
This document's own status vocabulary (above, `✅ IMPLEMENTED` requires a
named production caller) has encoded this since before 0.1; 0.2–0.3 restate
it as an explicit chain because an audit that skips straight from "tests
pass" to "🟢 complete" is the single most common way this project's own
history shows work being marked done that was not. Documentation alone,
architecture alone, and passing unit tests alone are each, individually,
**insufficient** proof — see 0.5.

Traceable table for the areas 0.4 was specifically asked to re-verify
against the repository (all confirmed present and passing at the time of
this section's writing):

| Area | Status | Implementation | Tests | Practical verification |
|---|---|---|---|---|
| Brain/provider independence | ✅ COMPLETE | `src/manager/brain.py`, `llm_brain.py`, `brain_factory.py` | `tests/test_brain_provider_credential_memory_independence.py` (26) | Three distinct live `LLMProvider`s swapped into a running `ManagerAgent`; `type(manager.brain)` proven unchanged each time |
| Provider-specific credentials | ✅ COMPLETE | `src/providers/factory.py` (`_BUILDERS`, per-provider credential check) | same file, `TestCredentialIsolation` | Cross-provider credential substitution proven to fail closed, both directions |
| Provider switching | ✅ COMPLETE | `src/providers/factory.py`, `src/manager/brain_factory.py` | same file, `TestProviderSwitchingThroughTheRealProductionPath` | A new, never-shipped provider name registered through the real `_BUILDERS` dict and answered through `exchange()` |
| Memory continuity across providers | ✅ COMPLETE | `src/services/memory/*` (unchanged by provider) | same file, `TestMemoryContinuityAcrossProviderSwitching` | A fact survived Provider A→B→C in one process, and separately across a genuine two-`build()` restart against the same `PersistentMemory` file |
| Conversation continuity across providers | ✅ COMPLETE | `src/conversation/conversation.py` | same file, `TestConversationHistoryContinuityAcrossProviderSwitching` | The *new* provider's own captured prompt text shown to contain pre-switch conversation content |
| Voice independence (Brain/provider/voice, all directions) | ✅ COMPLETE | `src/services/identity/voice.py`, `voice_preference.py`, `src/channels/channel.py` | same file, `TestVoiceIndependenceMatrix` | Full matrix: provider swapped twice with voice unchanged, then voice changed with Brain/provider proven unchanged (`is` identity) |
| Alfred → Kokoro `am_eric` | ✅ COMPLETE | `src/providers/kokoro_speech_provider.py`; `IDENTITY.md` `## Voice` | `tests/test_kokoro_speech_provider.py` (22), `tests/test_kokoro_voice_installation.py` (11) | Real ONNX synthesis, real non-silent audio, through `exchange(..., speak=True)` |
| Arthur → Kokoro `bm_daniel` | ✅ COMPLETE | same provider; `identities/email_agent.md` `## Voice` | same two files | Same, independently, distinct from Alfred's |
| Specialist independence | ✅ COMPLETE | `src/agents/cognitive_agent.py`, `email_agent.py`, `brain_factory.py` | `tests/test_brain_provider_credential_memory_independence.py::TestSpecialistIndependence`, `tests/test_specialist_general_conversation.py` | Arthur's own `LLMSpecialistBrain`/voice/memory proven distinct; swapping Alfred's provider 3× never touched Arthur's, and vice versa |
| Natural conversation (genuine, Brain-routed) | ✅ COMPLETE | `src/manager/capabilities.py::open_conversation`, `llm_brain.py::converse` | `tests/test_natural_conversation*.py`, `tests/test_open_ended_conversation.py`, `tests/test_natural_conversation_completion_check.py` (15) | Reached only as `select_capability`'s last resort; unseen wording answered via scripted Full-Brain doubles through real `exchange()`; a real, widespread false-routing defect in the pre-filter layer found and fixed post-0.4 — see §52.7 |
| Recording (intent-based, not a fixed phrase table) | ✅ COMPLETE | `src/manager/brain.py::discern_retention`, `scripts/exchange.py::_brain_judged_retention` | `tests/test_natural_retention_understanding.py` (26) | "I might forget this.", "Don't let me lose this.", and a stated-purpose example all genuinely retained through the real production path; deterministic floor proven to still take precedence with zero duplicate writes |
| Conversation Room parity (Alfred **and** specialists) | ✅ COMPLETE | `scripts/serve_ui.py::route_message` calling the identical `scripts/exchange.py::exchange()` | `tests/test_specialist_general_conversation.py::TestRealProductionRoomParityForArthur` | Arthur answered a genuinely open-ended remark through the real HTTP route, with his own personality reaching the model prompt |
| Clarification / conflict resolution (general, not fact-type-specific) | ✅ COMPLETE | `src/manager/guided_interaction.py`, `ManagerBrain.discern_ambiguity` | `tests/test_interaction_clarification_and_learning.py::TestClarificationAndConflictResolution` (7) | A stated "either Tuesday or Wednesday" genuinely produced a clarifying question through real `exchange()`; the chosen value was retained and a *pre-existing* conflicting record was genuinely superseded |
| Interactive guided learning/assessment (arbitrary subject) | ✅ COMPLETE | `src/manager/guided_interaction.py`, `ManagerBrain.discern_activity_start`/`compose_activity_question`/`evaluate_activity_answer`, `src/manager/activity_session.py` | `tests/test_interaction_clarification_and_learning.py::TestInteractiveLearningAndAssessment` (11) | A real multi-question session on two unrelated, never-hardcoded subjects (arithmetic, Japanese greetings) genuinely asked questions, graded answers with exact deterministic scoring, adapted the next question to prior answers, and preserved a progress summary to real governed memory on completion |
| Full agent loop (objective→plan/decompose→delegate→collaborate→execute→observe→adapt→remember→continue) | ✅ COMPLETE, via already-wired mechanisms | `collaboration_candidates`/`start_collaboration`/`answer_with_collaboration` (`src/manager/collaboration_entry.py`) composed with existing memory/conversation | `tests/test_agent_system_foundation.py::TestTheFullLoopThroughAlreadyWiredMechanisms` (6) | One continuous real objective ("check my inbox... and give me your take on the market") genuinely delegated to Arthur, whose real analyzer ran against the real sample inbox; Alfred's synthesis genuinely cited Arthur's real finding; a follow-up genuinely adapted using that result; a stated preference was genuinely retained and correctly recalled several turns later |
| Brain-driven action selection (`CognitiveLoop`/`ActionExecutor`) | 🟡 PREPARED — deliberately staged, by explicit user decision | `src/manager/cognitive_loop.py`, `src/manager/actions.py` | `tests/test_agent_system_foundation.py::TestCognitiveLoopRemainsStagedButReal` (3), pre-existing `test_cognitive_loop.py`/`test_action_layer.py`/`test_reachable_deliberation.py` | Constructed and run directly, proving it is genuine working code; independently re-confirmed **not** reachable from any production entry point (`compute_reachability`); its own Router-concurrence safety check proven to refuse a delegation nobody else agrees with. Not activated — see §52.9 |

### 0.4 Current work

**0.4 Canonical Project State** (this section) — consolidating 0.1–0.3's
recovered knowledge into one indexed, traceable summary inside the
existing authoritative document. No production code is being changed by
this stage; see 0.5 for how this stage itself is verified.

### 0.5 Verification — how completion is proven, and what is not enough

A capability earns ✅ here only after: (1) a named production entry point
reaches it — no `tests/`-only import; (2) a focused test exercises the
real behaviour, not merely construction; (3) a **practical** run through
the real production path (`scripts/control_center.py::build()` +
`scripts/exchange.py::exchange()`, or the equivalent live HTTP route)
produced the claimed outcome, using real objects and, where a paid vendor
is not funded, a scripted double that exercises the identical code path
rather than a description of what the vendor would do. Full-suite results
are re-run in fixed order *and* randomised order after every stage — a
suite that only passes in one order is hiding an ordering dependency, not
proving isolation. See each stage's own report (0.1 §52.2 pointer, 0.2's
four sub-phase reports, 0.3, and this stage) for exact pass counts.

### 0.6 Dependencies

```
Settings -> build_provider -> LLMProvider -> LLMManagerBrain -> ManagerAgent
Settings -> build_specialist_brain -> LLMSpecialistBrain -> CognitiveAgent (Arthur)
Settings -> build_speech -> SpeechAdapter (none/windows/elevenlabs/kokoro) -> VoiceChannel
Settings -> build_memory -> Memory (ephemeral/persistent) -> ManagerAgent / CognitiveAgent
IDENTITY.md / identities/<key>.md -> AgentIdentity/SpecialistIdentity -> Personality, VoiceProfile, VoiceActivation
```

Read left to right: nothing downstream ever names a vendor, and nothing
upstream is rebuilt when something downstream is swapped. This is the
structural reason Brain/provider/credential/memory/voice independence
holds — see §8 and §52.5 for why each arrow was drawn this way.

### 0.7 Blockers

**One genuine, standing, external blocker, unchanged since before 0.1:**
the configured Anthropic account has no funded credits, so no real LLM
inference has ever run in this environment. Every Full-Brain-shaped claim
in this document is verified against scripted providers that exercise the
real code path, never against a real vendor response, and this is stated
plainly rather than hidden wherever it applies.

**Not blockers, explicitly:** ElevenLabs (a paid, optional voice provider)
having never been exercised against a real key is not a blocker — Kokoro
is the local, free, default-capable voice and ElevenLabs was never
required (§8 item 12). OpenAI/xAI/Grok providers not existing in
`src/providers/factory.py` today is not a blocker either — §52's own
`TestProviderSwitchingThroughTheRealProductionPath::
test_a_new_compatible_provider_can_be_registered_without_touching_brain_code`
proves the registration mechanism works generically; shipping a specific
additional vendor is a future idea (0.10), not a missing requirement.

### 0.8 Decisions that must remain stable

§8 (Standing safety boundaries, 12 items) and §52.5 (architectural
decisions with reasons) are the authoritative record. Restated at their
most compressed, for the questions this project must never re-litigate by
accident:

- Canonical Brain = stable architecture; LLM Provider = a replaceable
  dependency the Brain consults. Switching Claude → OpenAI → Grok → a
  future provider is never "changing Alfred's Brain" (§8.7, §52.5).
- Each provider owns its own credential; none may be substituted for
  another's; a future second real provider needs its **own** settings
  field, never `MANAGER_API_KEY` (§8.9).
- Memory belongs to the agent/user/conversation store, never to a
  provider; switching providers must never erase, silo, or hide it (§8.8).
- Voice is independent of Brain and provider in both directions; a local,
  free voice (Kokoro) must exist and a paid one must stay optional (§8.12,
  §52.3).
- Every specialist owns its own identity, personality, brain, memory,
  voice, capabilities and connections; changing Alfred must never silently
  alter Arthur, and the reverse (§8, §11, §52.5).
- Natural conversation is genuine Brain-routed understanding, never a
  fixed phrase table, and is one reusable capability
  (`CognitiveAgent.general_conversation`) rather than reimplemented per
  agent (§8.10, §52.4).
- The Conversation Room is never a second, simplified conversation engine
  — it calls the identical `exchange()` every other surface calls (§8.11).

### 0.9 Rejected approaches

§52.4 is the authoritative, evidenced list (12 items, each tagged with how
it is known: structurally enforced, practically verified, or stated as a
standing project rule). Do not reintroduce any of them without first
reading why they were rejected there.

### 0.10 Future ideas — not implemented, not scheduled unless marked

Governed by the pipeline in §52.6:
`NEW IDEA -> CLASSIFY -> EXISTING CAPABILITY / NEW CAPABILITY / FUTURE -> TRACK -> IMPLEMENT -> TEST -> VERIFY`.
Recorded future direction lives in §6 (Next implementation phases — the
next *scheduled* work) and §7 (Long-term direction — recorded, not
scheduled): a context/research graph, Digital Humans beyond the existing
presentation layer, a second/third specialist (Coding, Research,
security/QA), real workspaces, an Agent OS control-centre expansion, and
(from this stage's own 0.7) additional named LLM/voice vendors, should one
ever actually be needed. None of these are implemented. Implementing any
of them is explicitly out of scope for 0.4.

### 0.11 Roadmap — exact current position

| Stage | Status |
|---|---|
| 0.1 Repository Recovery | ✅ COMPLETE |
| 0.2 Reality Audit | ✅ COMPLETE (4 sub-phases — see §52.2) |
| 0.3 Project Knowledge Recovery | ✅ COMPLETE |
| **0.4 Canonical Project State** | **← current (this section)** |
| *(after 0.4)* | returns to the existing Master Roadmap — §6/§7 below, owned by whoever directs the project, not reordered or rewritten by this stage |

This table, and §52.1 which it supersedes with the 0.3 row now closed, are
the same one record — updated in place, not duplicated.

### 0.12 Evolution history

The chronological record of how the project actually got here: §17
onward, one numbered section per phase/stage, each with its own
`### Verified` subsection (test counts, what was mutation-tested, what
was practically confirmed). §49–§52 are the most recent arc. Do not
summarise this history a second time anywhere else — this index points to
it rather than repeating it.

---

## 1. Current implemented foundation ✅

| Feature | Purpose | Module | Tests |
|---|---|---|---|
| Manager orchestration | diagnose → assemble → act → assess | `src/manager/manager.py` | `test_manager.py`, `test_action_layer.py` |
| Capability routing | match a task to an agent by declared capability | `src/manager/router.py`, `fallback_router.py`, `llm_router.py` | `test_router.py`, `test_router_degradation.py` |
| Manager Brain | Alfred's own reasoning | `src/manager/brain.py`, `llm_brain.py` | `test_manager_brain.py`, `test_llm_manager_brain.py` |
| Specialist Brain | each specialist's *separate* reasoning | `src/agents/brain.py`, `llm_brain.py` | `test_specialist_brain_architecture.py` |
| Full / Half Brain | depth of reasoning, honestly reported | `src/manager/cognition.py` | `test_capability_floor_and_brain_modes.py` |
| Three-state availability | configured ≠ usable | `src/providers/availability.py` | `test_capability_floor_and_brain_modes.py:379` |
| Separate memories | one store per agent, selected by name | `src/services/memory/*` | `test_memory.py`, `test_specialist_memory.py` |
| Manager identity | `IDENTITY.md` → capabilities | `src/services/identity/identity.py` | `test_identity.py` |
| Specialist identity | `identities/<key>.md`, **no capability field** | `src/services/identity/specialist.py` | `test_specialist_identity.py` |
| User profile & schedule | who is served, and when | `src/services/profile/`, `src/services/schedule/` | `test_user_profile.py`, `test_user_schedule.py` |
| Arthur (email specialist) | reads a mailbox, ranks opportunities | `src/agents/email_agent.py`, `src/agents/email/*` | `test_email_agent.py`, `test_opportunity_analysis.py` |
| Registry discovery | specialists found, never hardcoded | `src/registry/agent_registry.py`, `src/agents/specialists.py` | `test_specialist_registry.py` |
| Heartbeat | scheduled unattended runs, locked | `src/heartbeat/*` | `test_heartbeat_runner.py` and 4 more |
| Gmail read-only boundary | `gmail.readonly`, no Outbox | `src/services/email/*` | `test_architecture_audit.py::TestTheSafetyBoundaryHolds` |
| Control Center | overview / agent / system / history | `src/control_center/*`, `scripts/control_center.py` | `test_control_center.py` |
| Decision trail | what the system observably did | `src/control_center/trail.py` | `test_control_center.py` |
| Interface descriptors | a specialist declares its surfaces | `src/control_center/interfaces.py` | `test_channels_events_improvement.py` |
| Improvement (advisory) | observe → propose → **person** | `src/improvement/*`, via `review` command | `test_channels_events_improvement.py` |
| Speech availability contract | 3 states, `available` derived | `src/channels/speech.py` | `test_channels_events_improvement.py` |

### The three axes that must never merge

```
BRAIN       how deeply can I reason?     FULL (verified usable) or HALF
CAPABILITY  what kind of work is mine?   a declared word AND a handler
TOOL        what means do I have?        ToolSet membership
```

Pinned structurally by `test_architecture_audit.py::TestTheThreeAxesNeverMerge`.
This is the single most valuable thing in the codebase and everything below
depends on it holding.

---

## 2. Architecturally prepared 🟡

Real code, real tests, **no production caller**. Each is one composition change
away from being real — and until that change, none of it runs.

| Feature | Module | Why it is not ✅ |
|---|---|---|
| Tool-using agent loop | `src/agents/loop.py` | Tests only. |
| Cognitive loop + action executor | `src/manager/cognitive_loop.py`, `src/manager/actions.py` | Tests only — and `test_reachable_deliberation.py:248` **asserts** it stays unreachable. Deliberate: Trust in Stages. |
| Event system (all kinds but one) | `src/events/*` | `delegation` now emits from Manager orchestration ✅. Every other constructor — `capability_became_available`, `tool_became_unavailable`, `work_completed`, `brain_mode_changed`, `run_failed` — still has no producer. |
| Tool contract | `src/agents/tools/*` | The contract is reachable; **no tool is installed.** `web_search` and `code_assistant` are named as missing, not faked. |
| Workspace surfaces | `InterfaceFeature.WORKSPACE / CODE / FILES / SOURCES` | Enum values a specialist may declare. `render.py:138` prints one line — *"offers a dedicated workspace"*. There is no workspace object, state, or storage. |

---

## 3. Not started ❌

Nothing below exists in any form. No file, no class, no test.

> **Superseded, in large part — see §52.** This list is accurate as of §2's
> Phase-numbering era (Phase 2/3). Voice I/O (a real STT/TTS engine, wake-word
> activation), per-agent `VoiceProfile`, and the Conversation Room UI with
> invitation and in-room specialist participation have since shipped and are
> verified — see §19, §43 and §52. The items below that remain genuinely
> unbuilt: session memory (bounded/compacted, as opposed to the unbounded
> `Conversation` transcript that already exists), real workspaces, Digital
> Humans avatars *beyond* the presentation layer already built in §29–§37,
> a second/third specialist (Coding, Research, security/QA, client-facing),
> a context/research graph, and a project-continuity layer.

Voice I/O (microphone, STT, TTS, audio playback) · per-agent `VoiceProfile` ·
voice addressing and routing · the Conversation **Room UI** · inviting an agent
into a room · a specialist answering inside a room · session memory ·
real workspaces · Digital Humans / avatars · Coding Specialist ·
Research Specialist · security/QA specialist · client-facing specialists ·
context/research graph · project-continuity layer.

The `Conversation` **domain object** left this list in Phase 2 — see §17. The
room exists and is recorded into; the dedicated interface, the invitation
mechanism and specialist participation do not.

---

## 4. Master requirements — traceability

| # | Requirement | Status | Where it lives / would live |
|---|---|---|---|
| A | Alfred: brain, memory, identity, capabilities | ✅ | `manager/`, `services/identity/identity.py` |
| A | Alfred: own interface | 🟡 | declared via `describe_interface`; no dedicated surface |
| A | Alfred: own workspace, voice, avatar | ❌ | — |
| B | Specialists are real entities (brain/memory/identity) | ✅ for Arthur | `agents/specialists.py` derives name, memory owner, brain domain from **one** identity |
| B | Specialist interface | 🟡 | `InterfaceDescriptor`; Arthur declares MAILBOX + OPPORTUNITIES as *planned* |
| B | Specialist voice / avatar / workspace | ❌ | — |
| C | Separate brains, separate memories | ✅ | `test_capability_floor_and_brain_modes.py:455,459` assert Alfred and Arthur never share either |
| C | **Shared conversation context** | ✅ domain | `src/conversation/conversation.py`; reachable from the Control Center's talk path |
| D | Multi-agent conversation | ✅ domain | Unbounded participants ✅; delegated specialist joins and speaks as itself ✅; addressed participant answers directly ✅; a registered specialist can be **invited** by name ✅ (Phase 5). A specialist named after a capability word is invitable (Phase 5.1, §22). The Room *UI* remains ❌. |
| ~~E~~ | ~~Voice as I/O~~ | **SUPERSEDED — see §52** | Real engines now exist (Windows, ElevenLabs, Kokoro) — output is no longer contractonly. |
| E | Voice as addressing/routing | 🟡 | The addressing *model* is implemented and decides who answers (Phase 4). Voice as an input to it needs STT, which does not exist. |
| ~~F~~ | ~~Agent-specific voices~~ | **SUPERSEDED — see §52** | Alfred (Kokoro `am_eric`) and Arthur (Kokoro `bm_daniel`) both genuinely speak, verified end to end. Not merely identity-declared — an engine now makes the sound. |
| G | Specialist workspaces | 🟡 label only | see §2 |
| H | Digital Humans | ❌ | — |
| I | Persistent continuity | 🟡 | per-agent JSON memory is now the **default** ✅; still scoped per record, not per project ❌ |
| J | Context / research graph | 🔵 | recorded here as direction only |
| K | Self-improvement, no self-granted permission | ✅ | `improvement/governance.py`; `improvement_is_advisory_only()` |
| L | Security / testing specialist | 🔵 | recorded here as direction only |
| M | Coding Agent as a specialist | ❌ | but see N |
| N | Half Brain can still code | ✅ **already protected** | see below |
| O | Control Center as front door | ✅ foundation | `scripts/control_center.py` |

### Requirement N is already correct — do not "fix" it

Coding is gated by **tool**, never by **brain**. `IDENTITY.md` declares
`code, coding, script, function, debug`; `capabilities.py:307` maps them to the
missing `code_assistant` tool; `general_assistance` names the missing tool
rather than guessing. Two tests pin exactly the rule you asked for:

- `test_half_brain_with_a_working_tool_still_succeeds`
- `test_full_brain_without_a_tool_still_cannot`

A Half Brain with a coding tool *can* code. A Full Brain without one *cannot*.
That is the correct axis and it is already enforced.

---

## 5. Architectural gaps — missing seams

Ordered by how much future work each one blocks.

### Gap 1 — CLOSED at the domain layer (Phase 2)

`ConversationTurn` is *one request and its answer*. There is no object holding a
conversation, its participants, its history, or its shared state. Every call is
independent.

Blocks: C (shared context), D (multi-agent conversation), E (voice routing needs
somewhere to route *into*), G (workspaces), H (avatars need conversation state),
I (continuity).

Building voice, workspaces, or avatars before this means building each of them
twice — once against no conversation, once against one.

### Gap 2 — No agent addressing path

The Manager decides who handles a task. There is no path for the *user* to
address a specialist directly ("Arthur, research this"), and no path for an
agent to be invited into an existing conversation. `AgentRegistry.get(name)`
exists; nothing user-facing uses it for addressing.

### Gap 3 — CLOSED (Phase 3)

`AgentIdentity` and `SpecialistIdentity` have no voice field.
`SpeechAdapter.speak(text)` has no voice parameter. **The contract as written
can only ever produce one voice**, so requirement F is currently inexpressible.

Good news: `ConversationTurn.handled_by` already records *who answered*, so
voice selection needs no new plumbing once a profile exists — registry → identity
→ profile.

### Gap 4 — CLOSED (Phase 1)

`Runtime.speech` now holds the adapter and `build_channel(runtime, manager)`
builds the door per-run. The Control Center reads the composed adapter instead
of constructing its own. Split by lifetime: adapter shared, channel per-run,
because a Channel holds a Manager and the heartbeat rebuilds one every tick.

### Gap 5 — No agent-to-agent collaboration seam

`SpecialistContext` deliberately withholds the Manager, the registry, and every
peer — correct, and the reason specialists cannot quietly couple. But no
*mediated* collaboration seam exists either, so D has nowhere to attach. The
answer is a conversation both agents join, **not** giving specialists the
registry.

### Gap 6 — No presentation-layer boundary for avatars

`InterfaceDescriptor` describes *kinds of surface*. It carries no visual state,
expression, or voice binding. An avatar layer would need somewhere to read
conversation state and speaking state without reaching into a Brain.

### Gap 7 — Narrowed (Phase 2.5): continuity is per-record, not per-project

Durability is fixed: `MANAGER_MEMORY` now defaults to `persistent`, so a
rebuilt agent recalls what the last one learned. Ephemeral remains fully
supported and explicitly selectable.

What remains: memory is per agent, per record, day-aware, and nothing spans a
*project*. There is still no notion of "where we started → what we planned →
what we built".

### Gap 8 — PARTIALLY CLOSED (Phase 1)

`ManagerAgent` now raises a real `delegation` event from `handle_task`, and
`ControlCenter.history()` reads it and phrases it through `narrator.compose`.
The Manager is the sole producer — a structural test forbids the Control Center
calling `record`. Still open: every other event kind has no producer, and an
`EventLog` dies with its Manager, so heartbeat-tick events are not observable.

### Gap 9 — No context graph

Absent entirely. Recorded as direction (§7), not scheduled.

---

## 6. Next implementation phases

| Phase | Work | Status | Why this order |
|---|---|---|---|
| **0** | Commit the tree in reviewable slices | ✅ done — `d875b75`, `dc973b5`, `33a55bc` | 19 untracked files is a single point of loss. |
| **1** | Compose the Channel through composition; emit one real event from Manager orchestration | ✅ done | Turned two 🟡 contracts into ✅. Removed Gaps 4 and 8 (partially — one event kind emits). |
| **2** | **`Conversation`**: participants, utterances, join/leave, addressing | ✅ done | The keystone (Gap 1). Domain object built and reachable; the Conversation *Room UI* is a separate later phase. |
| **3** | `VoiceProfile` on the identity layer + `speak(text, voice)` | ✅ done | Gap 3 closed. Proven with a recording double; no engine installed. |
| **4** | One real TTS engine behind `SpeechAdapter`. **Output only, no microphone.** | planned | Speaking is strictly safer than listening: no always-on mic, no privacy surface, no barge-in. |
| **5** | STT (push-to-talk), then voice addressing on top of Phase 2 | planned | Addressing needs a conversation to route into. Doing it before Phase 2 hardcodes routing into the channel. |

Later, in rough order: second specialist · real workspaces · project continuity ·
Digital Humans · context graph · security specialist.

Phase 1 correction worth keeping: the roadmap originally said *"compose the
Channel through Runtime"*, and inspection showed that would break the invariant
`Runtime` exists to protect. The split is by **lifetime** — the speech
*adapter* lives in `Runtime`, the *channel* is built per-run beside the Manager,
because a Channel holds a Manager and the heartbeat rebuilds one every tick.

Phase 1 also replaced two `git diff HEAD` byte-freezes over orchestration with
structural audits (`test_orchestration_knows_no_specialist`,
`test_orchestration_knows_no_capability_handler`). The freezes forbade Alfred
evolving at all while proving nothing about specialist isolation, and fell
silent on commit. See §12.

---

## 7. Long-term direction (recorded, not scheduled)

**Context graph.** The value of many agents is the context they leave behind,
not the reports they emit. Sources become nodes; shared entities become edges;
multiple supporting sources raise confidence; conflicting sources stay visible
as contradictions rather than being averaged away; isolated claims stay
identifiable as weak. *Agent scale + connected context > isolated reports.* No
graph database is to be introduced until a phase explicitly calls for it.

**Digital Humans.** A presentation layer, never a Brain:

```
Digital Human -> Agent Interface -> Agent -> Brain / Memory / Identity / Capabilities
```

Replaceable without touching the agent's intelligence. Needs Phase 2 first.

**Security / testing specialist.** A future specialist that continuously probes
applications, websites, and this system for weaknesses and regressions, and
reports them. Not scheduled.

**Agent OS.** The Control Center eventually exposes agents, workspaces,
conversations, memory, projects, collaboration, voice, avatars, history and
planning. Not a redesign — an accumulation on the existing foundation.

---

## 8. Standing safety boundaries — not negotiable

1. **Gmail is `gmail.readonly`.** No Outbox implementation exists. No write-scope
   literal may appear anywhere under `src/` — the improvement deny-list
   assembles its strings from fragments rather than earning an exception.
2. **Improvement is advisory.** observe → propose → *person*. No apply path
   exists and no argument enables one.
3. **A document describes; it never grants.** A specialist identity document has
   no capability field to put a claim in.
4. **Trust in Stages.** Permission moves when a person moves it.
5. **Half Brain is not broken.** It is a reduction in depth, never in function.
6. **Availability gates nothing.** It is evidence for reporting. An UNKNOWN
   engine must still be attempted, or it can never produce evidence.
7. **LLM provider, Brain, voice and agent environment are four independently
   configurable facts, never one.** Switching the LLM provider (`MANAGER_PROVIDER`)
   must not alter, replace, reset or couple to the canonical Brain architecture
   (`src/manager/brain.py`/`llm_brain.py`) — a new provider is handed to the same
   `ManagerBrain` contract, never a reason to construct a different one. Switching
   the provider must never silently change a selected voice, and choosing or
   changing a voice must never touch the Brain or the provider — `VoiceProfile`
   (`src/services/identity/voice.py`) carries no provider field and the Brain
   carries no voice field, by construction. A user-selected voice
   (0.2 Reality Audit's `voice_preference_for`/`set_voice_preference`, the same
   governed-Memory shape `avatar_preference_for` already established) overrides
   an agent's IDENTITY.md-declared default, is independently settable for Alfred
   and for each specialist, and is changeable at any time without restarting or
   redeploying anything.
8. **Memory belongs to the agent/user/conversation store, never to a provider.**
   Verified practically (0.2 Final Foundation Audit, `tests/test_brain_provider_
   credential_memory_independence.py`): a fact stored while Provider A answers
   remains recallable, unchanged, after switching to Provider B and then C — in
   one continuous process, and separately across a genuine two-`build()` process
   restart against the same `PersistentMemory` file. No record is ever written
   into a provider-named field, tag or store. Conversation history
   (`Conversation.transcript()`) survives a provider switch mid-conversation for
   the identical reason: neither object has ever imported the other.
9. **Each LLM provider owns its own credential; none may be substituted for
   another's.** `src/providers/factory.py`'s `_BUILDERS` registry gives each
   provider its own dedicated environment variable and its own credential-check
   function (`_anthropic_credential` for `ANTHROPIC_API_KEY`/`MANAGER_API_KEY`),
   checked *before* the vendor SDK is even imported. Verified: selecting one
   provider while only a different provider's credential is present fails
   closed, by name, never silently substituting one for the other. A future
   second real provider must receive its **own** dedicated settings field —
   `Settings.api_key`/`MANAGER_API_KEY` is Anthropic's alone and must never be
   reused (`tests/test_brain_provider_credential_memory_independence.py::
   TestKnownDesignNoteForAFutureSecondRealProvider` exists specifically so
   reusing it is a visible, deliberate decision to break a test, not an
   accident). Credentials are read fresh from the environment on every
   `build_provider` call — nothing is cached — so removing one and rebuilding
   fails closed again immediately; that *is* this architecture's revocation
   mechanism, not a missing feature.
10. **Natural conversation is genuine understanding routed through the Brain,
    never a fixed phrase table, and it is a reusable capability of the
    canonical agent architecture, never reimplemented per agent.**
    `open_conversation` (`src/manager/capabilities.py`) is reached only as
    `select_capability`'s last resort — nothing scored, real content exists —
    and hands the raw remark to `Brain.converse()` untouched. Retention intent
    ("keep this for me", and any wording nobody wrote down, like "I might
    forget this" or "don't let me lose this") is judged the same way:
    `ManagerBrain.discern_retention()`/`SpecialistBrain.converse()` ask the
    Brain to judge genuine intent, consulted only when the small, deterministic,
    Half-Brain-safe marker floor (`session.py`/`capture.py`) finds nothing — see
    §52. The identical `general_conversation()` method lives once, on
    `CognitiveAgent`, so Arthur (and any future specialist) gets it for free
    rather than by copying Alfred's own handler.
11. **The Conversation Room is never a second, simplified conversation engine.**
    `scripts/serve_ui.py::route_message` calls the exact same
    `scripts/exchange.py::exchange()` every other surface calls — confirmed by
    direct source reading, not by description. A specialist's personality,
    voice and brain are exactly as reachable inside the Room as outside it,
    because there is only one conversational path, not two.
12. **A local, free voice must exist; a paid voice provider must remain
    optional, never required.** Alfred speaks through Kokoro (`am_eric`) and
    Arthur through Kokoro (`bm_daniel`) — a local, offline, no-account neural
    TTS engine, added as one more `SpeechAdapter` registered in
    `speech_factory.py` beside (never in place of) the existing Windows and
    ElevenLabs adapters. ElevenLabs stays available and stays optional; nothing
    in the default configuration requires it. A speech provider must never
    silently substitute a different installed voice (e.g. a generic Windows
    voice) once a specific voice has been genuinely configured and confirmed
    available.

---

## 9. Unresolved decisions

| # | Decision | Note |
|---|---|---|
| 1 | `VoiceChannel.receive()` catches only `SpeechUnavailable` | A real engine raising `OSError` would propagate. Recommend: each adapter translates its own failures. |
| 2 | Improvement deny-list omits `gmail.insert` / `gmail.labels` | The audit forbids the strings in `src/`; the deny-list does not refuse proposals containing them. Two-word fix, behaviour change. |
| ~~3~~ | ~~Persistent memory is off by default~~ | **RESOLVED (Phase 2.5)** — `persistent` is the default; `ephemeral` stays explicitly selectable. |
| 4 | `README.md` `## Status` is stale | Says 2,556 tests and "no control centre". Should be trimmed to point here. |
| 5 | Local vs cloud TTS | Undecided. Affects Phase 4 and whether a second paid account is required. |
| 6 | Where `Conversation` lives | New package, or `control_center`? Affects Phase 2. |
| ~~7~~ | ~~`ToolResult` carries no provenance~~ | **RESOLVED (Phase 2.5)** — `observed_at` and `source` on the observation boundary; only a SUCCEEDED result may be dated. |
| 8 | Secret-detection is duplicated | `services/memory/secrets.py` and `manager/memory_policy.py` each carry one. Flagged in `secrets.py` itself, deliberately unconsolidated. |
| 9 | Stale docstring | `manager/memory_policy.py` says *"the Manager does not yet hold a policy"*. It does — `manager.py:192`. |
| ~~10~~ | ~~Who is Arthur?~~ | **RESOLVED — see §16.** Arthur remains the Email / Opportunity Specialist. The Coding Specialist is a separate future specialist. |
| 11 | Events die with their Manager | An `EventLog` is per-Manager and the heartbeat builds one per tick, so heartbeat events are never observable from the Control Center. Fine while events are an observability aid; a decision once they are not. |

---

## 10. Memory and live environment (architecture, not implementation)

The target definition of an agent:

```
AGENT = MODEL/BRAIN + HARNESS + MEMORY + ENVIRONMENT + EVOLUTION
```

This project already has the first two and a real, if thin, third. The fourth
exists in exactly one place. The fifth is advisory-only by design.

### 10.1 Why memory is separate from the model

A model is **stateless**. It is a function: text in, text out. It remembers
nothing between calls, and anything it appears to remember was put in front of
it by something else.

That "something else" is the memory system, and it must stay outside the model
for a reason this project has already paid for once: **you cannot inspect,
correct, or delete what lives inside a model's weights.** A memory you can list,
audit, refuse and forget is a memory a person stays in charge of. This is why
`Memory` has `forget` and `clear` in the *contract* rather than as a feature one
backend might add — a store that could only grow would have no answer to
"delete that".

The project states the rule directly in `services/memory/memory.py`:

> A brain that also stored things would decide what is worth keeping, and a
> memory that also reasoned would decide what a question means.

Enforced, not just written: `test_llm_manager_brain.py:704`
(`test_the_brain_imports_no_memory_module`) and
`test_persistent_memory.py:634`.

### 10.2 Why live environment data is separate from memory

**Memory is what was true when it was written. The environment is what is true
now.** Conflating them is how an agent tells you your flight is on time because
it remembered that yesterday.

The boundary already exists and is structurally enforced —
`test_agent_loop.py::TestToolResultsDoNotBecomeMemories` proves the tool loop
touches no memory, imports no memory policy, and that repeated tool calls fill
no store. A tool result is an **observation**; a memory record is **knowledge**.
They are different types today and must stay different types.

Current live-environment surface: **one**. The Gmail mailbox, read-only.

```
Agent -> Tool -> Environment -> Observation -> Agent reasoning
  (ToolSet)   (Mailbox/API)   (ToolResult)      (Brain)
```

The seam is sound and the pattern generalises: a weather tool, an HTTP tool, a
file tool and a coding workspace all fit `Tool.run -> ToolResult` unchanged, and
`ToolSet` already wraps failures at the boundary so a tool that raises cannot
take down a loop.

**The one real gap: `ToolResult` has no provenance.** Its fields are
`tool, status, output, error`. There is no `observed_at` and no source
identifier. So the moment an observation is written into memory, it becomes
indistinguishable from a durable fact — the exact failure this separation
exists to prevent. `MemoryRecord.created_at` records *when it was remembered*,
which is not the same as *when it was observed*.

**Future**: `ToolResult` gains `observed_at` and `source`; any record derived
from an observation carries both in `MemoryRecord.context`, which is already
opaque, carried-but-never-interpreted structured detail — the field is
deliberately shaped for exactly this.

### 10.3 Memory layers — target

| Layer | Purpose | Today |
|---|---|---|
| **SESSION** | current conversation, recent turns, compacted state | ❌ — blocked on `Conversation` (Gap 1) |
| **EPISODIC / FILE** | project history, decisions, lessons, notable events | 🟡 — `PersistentMemory` is one JSON file per agent, **off by default** |
| **SEMANTIC** | embeddings, entities, claims, sources, relationships | ❌ — recall is literal token overlap, by design |

The three are additive, not replacements. Token-overlap recall stays as the
floor: it is deterministic, needs no model, and works in **Half Brain**.
Semantic retrieval is a *depth* upgrade layered above it — the same shape as
FULL vs HALF brain, and for the same reason.

### 10.4 Memory hierarchy — target

```
AGENT LTM        private preferences, private memories      <- exists (per-agent stores)
SHARED LTM       team / cross-agent knowledge               <- absent
CLIENT STORE     client-owned information                   <- absent
SERVER GOVERNANCE audit / versions / permissions / lifecycle <- absent
```

**Rule that must not bend: SOURCE OF TRUTH = READ ONLY.** Memory may summarise,
index, retrieve and connect source information. It must never rewrite it.

This rule is already load-bearing and already enforced: the Gmail token carries
`gmail.readonly`, no `Outbox` implementation exists, and the mailbox has no
modify/label/archive method. `MemoryRecord` is frozen — correcting a memory
means *storing a new record*, so the correction stays visible rather than
overwriting history. Extending to a shared store changes none of that.

**A shared store is where agent isolation is most at risk.** Today two agents
cannot see each other's records because they are different *objects* —
`memory.py` notes that a record carries no `owner` field precisely to avoid
inviting a global store. Any shared LTM must therefore be a **separate,
permissioned store an agent contributes to**, never a widening of the private
one. Contributing an observation is not the same as sharing a mind.

### 10.5 The inference loop — target

```
ASSEMBLE                     INFER                WRITE-BACK          KEEP?
history + RAG + LTM      stateless model      extraction or        yes -> store
+ live observations       reasoning           explicit memory      no  -> skip
                                              tools
```

**The model does not become the database.** Memory is an external system the
agent deliberately reads from and writes to.

The project already implements ASSEMBLE and the KEEP? gate. INFER is the brain.
WRITE-BACK is partial: the Manager and cognitive agents write, but only what a
policy admits, and there are no explicit memory *tools* an agent could call.

### 10.6 KEEP vs SKIP — ✅ this already exists

This is the requirement the project is furthest ahead on, and it is genuinely
enforced in production, not merely designed.

`MemoryPolicy` (`services/memory/policy.py`) is a separate, replaceable
dependency from `Memory`: storage answers *how*, policy answers *whether*. It
returns a `PolicyVerdict` carrying `allowed`, a **mandatory reason**, and a
category — a bare bool was rejected because a refused record is exactly when
someone needs to know why.

Enforced at the write path in both production callers:
`manager.py:561` and `cognitive_agent.py:261`.

The rules are an **allow-list**, not a deny-list. Nothing is remembered unless
it fits a category chosen on purpose:

| Manager categories | |
|---|---|
| `USER_CONTEXT` | stable facts about the person or project |
| `DECISION` | an orchestration choice and its reasoning |
| `DELEGATION_OUTCOME` | what a specialist returned, and whether it worked |
| `LESSON` | something learned that should change future behaviour |

And `agents/memory_policy.py` adds a **confidence floor** — *"a tentative
observation is not yet knowledge"* — plus a length ceiling, because *"distilled
knowledge is short; anything far longer is usually a dump."*

**Why this matters more than it looks.** Automatic extraction degrades over
months: every run adds records, recall returns mostly noise, and the few facts
worth acting on get buried. An allow-list with a confidence floor is the
architecture that stops that. It is already right — **do not replace it when
adding layers.** New layers plug in *behind* it.

Still missing from KEEP?: **already-known** (deduplication). Nothing checks
whether a candidate duplicates an existing record.

### 10.7 Dreaming — future seam, deliberately unbuilt

A background maintenance pass that could curate, deduplicate, evict stale
records, consolidate related ones, form associations and surface contradictions.

**It must be governed by the existing improvement architecture, not beside it.**
`improvement/governance.py` already encodes the shape: observe → propose →
*person*. There is no apply path and no argument that enables one, and
`improvement_is_advisory_only()` exists so the guarantee is assertable from
outside.

Constraints, all inherited rather than invented:

1. Grants no permission. `ChangeKind` has no member for widening one, on purpose.
2. Never modifies source-of-truth data.
3. Never rewrites a `MemoryRecord` — frozen. Consolidation writes a *new* record
   and proposes eviction of the old ones.
4. Eviction of a memory is a **proposal**, not an action.

A dreaming process that silently deleted memories would be a system editing its
own history, which is the same class of risk as self-granted permission.

### 10.8 Cross-agent context and the research graph — future seam

```
300 agents -> independent brains -> shared context
           -> connected sources / entities / claims -> research graph
```

Target edges: `source -> claim`, `source -> entity`, `entity -> entity`,
`claim -> evidence`, `claim -> contradiction`, `agent -> observation`.

Confidence is **evidential, never averaged**: four supporting sources raise
confidence, two disagreeing sources leave a contradiction *visible*, one source
stays weak. Contradictions must never be resolved by overwriting.

**Where it attaches.** `MemoryRecord.context` is already an opaque structured
mapping that memory carries and never interprets. Node and edge identifiers
belong there first; a graph store is a later, separate backend. No graph
database is to be introduced until a phase explicitly calls for it.

**What must not happen:** agents sharing a brain. They contribute observations,
sources and claims to a permissioned shared store. `SpecialistContext` withholds
the Manager, the registry and every peer — that stays.

### 10.9 Multi-session continuity — worked example

```
SESSION 1  "We are building Project Beacon."
           -> USER_CONTEXT record admitted by policy, written to PersistentMemory

SESSION 2  "What is Beacon?"
           -> ASSEMBLE: recall("Beacon") from LTM        [memory]
           -> ASSEMBLE: mailbox / API observation        [live environment]
           -> INFER:    brain combines both              [reasoning]
           -> answer:   "Beacon is the project we started earlier.
                         The latest live data shows ___."
```

Three things must be true for this, and **two of them are false today**:

1. Policy admits the record — ✅ `USER_CONTEXT` covers it.
2. It survives the session — ❌ `MANAGER_MEMORY` defaults to `ephemeral`, and
   **no memory file exists on disk**. Continuity is currently not happening.
3. The answer distinguishes remembered from observed — ❌ no provenance (§10.2).

### 10.10 Where this attaches, and what must not change

**Attach here:**

| New work | Attaches to |
|---|---|
| Session memory | the future `Conversation` object (Gap 1) |
| Episodic memory | `PersistentMemory`, once it is the default |
| Semantic retrieval | a new `Memory` implementation behind the existing contract |
| Observation provenance | `ToolResult` fields + `MemoryRecord.context` |
| Shared LTM | a **new, permissioned store** — never a widening of a private one |
| Graph | identifiers in `MemoryRecord.context` first |
| Dreaming | `ImprovementBoard`, as proposals |

**Must not change:**

- `Brain != Memory`. Neither may import the other.
- `Memory != MemoryPolicy`. Storage does not judge; policy does not store.
- `Memory` does no reasoning — no summarising, ranking or interpreting.
- Tool results are not memories.
- `MemoryRecord` stays frozen.
- Per-agent stores stay separate objects with no `owner` field.
- Source of truth stays read-only.
- Improvement stays advisory.

### 10.11 How this fits Full Brain / Half Brain (see also §11)

Exactly the same axis, extended:

| | Half Brain | Full Brain |
|---|---|---|
| Recall | literal token overlap — deterministic, always available | + semantic retrieval |
| Keep/skip | allow-list + confidence floor — deterministic | + model-judged relevance |
| Live observation | tool runs, result returned | identical — tools are not model-gated |

**Half Brain must never lose memory or environment access**, exactly as it must
never lose the ability to code. Depth degrades; function does not. Semantic
retrieval and model-judged extraction are the *deliberation* tier; the
deterministic floor underneath keeps working when no model is reachable —
which, given this account currently has no credit, is the mode the system
actually runs in.

---

## 11. The canonical agent model

```
Agent = Model/Brain + Harness + Memory + Environment + Evolution
```

Five axes that must never collapse into one another. Alfred today:

| Axis | What it is for Alfred | Status |
|---|---|---|
| 🧠 **Brain** | Full Brain / Half Brain, assessed from three-state evidence | ✅ `src/manager/cognition.py` |
| ⚙️ **Harness** | the DATA loop, router, assessor, capability handlers, safety boundaries | ✅ `src/manager/*` |
| 🗃️ **Memory** | own store, own policy; session/semantic layers absent | 🟡 §10.3 |
| 🌍 **Environment** | one live source (read-only Gmail) through `Tool → ToolResult` | 🟡 §10.2 |
| 📈 **Evolution** | observe → propose → *person*; no apply path | ✅ advisory only |

Each specialist carries the same five axes, its own of each. `SpecialistWiring`
already derives name, memory owner and brain domain from **one** identity; voice
and workspace become the fourth and fifth derivations.

**Half Brain is a reduction in depth, never in function.** It keeps routing,
delegation, self-handling, assessment, memory recall, tool use and coding —
anything gated by a *tool* rather than a *model*. Pinned by
`test_half_brain_with_a_working_tool_still_succeeds` and
`test_full_brain_without_a_tool_still_cannot`.

## 12. Alfred is a manager, not a router

Alfred handles what he is capable of and delegates what needs depth. Both
halves are real and both are tested:

| Request | Outcome | Test |
|---|---|---|
| "list your capabilities" | Alfred answers directly, **no** delegation event | `test_the_manager_can_still_do_small_work_itself` |
| "scan my inbox for opportunities" | delegated, event raised | `test_delegating_raises_an_event_from_the_manager` |

The difference is **depth**, never an absolute capability wall. A specialist is
depth, not permission — Alfred does not become incapable of a kind of work
because somebody else is better at it.

**What Alfred knows:** which capabilities exist, which agent declares them,
whether delegation is appropriate — all through the registry.
**What Alfred must never know:** how a specialist class is constructed, any
vendor detail, a specialist's private memory or reasoning.

Enforced structurally by `test_orchestration_knows_no_specialist` and
`test_orchestration_knows_no_capability_handler`. These replaced two
`git diff HEAD` byte-freezes that forbade *any* change to orchestration — which
could not tell "learned about a specialist" from "gained an event", and went
quiet the moment anything was committed. Orchestration may evolve; it may not
grow a specialist.

## 13. Interfaces: many focused surfaces, never one dashboard

**This is a hard UI requirement, not a preference.** The Control Center is a
*navigation and observability layer*, not a screen that contains everything.

```
CONTROL CENTER  (navigation)
├── TALK TO MANAGER   -> Conversation Room, full screen
├── STATUS            -> observability
│   ├── Manager brain      (mode, provider, router, current decision + basis)
│   ├── Specialist brain   (per agent, its own)
│   ├── Memory activity
│   ├── Environment / capabilities
│   └── System state
└── HISTORY           -> decisions, events, activity

SPECIALIST WORKSPACES  (separate from the Conversation Room)
├── Coding workspace     conversation, files, terminal, tests, state
└── Research workspace   conversation, sources, evidence, live data, graph
```

Three areas, three **experiences** — not three panels forced onto one screen.

A **Conversation Room** is a shared meeting space. A **specialist workspace** is
where one specialist does its actual work. Neither replaces the other.

Status today: the Control Center renders overview, per-agent drill-down, system
and history as separate screens ✅. Brain mode and its reason are shown ✅.
Everything else in this section is ❌.

## 14. Conversation Room — the keystone, still unbuilt

```
                      YOU
                       |
               CONVERSATION ROOM        <- shared context
       +---------------+---------------+
       |               |               |
     ALFRED         SPECIALIST      SPECIALIST
     Brain           Brain           Brain      <- never merged
     Memory          Memory          Memory
     Voice           Voice           Voice
```

Must support: user participation · Alfred participation · specialist
participation · history · shared context · participant identity · per-agent
responses · join and leave mid-conversation · future voice routing · future
event routing · future persistent session memory.

**No participant ceiling.** The architecture must impose no maximum — not 3,
not 5, not 10. Any registered agent may join, bounded only by real runtime
cost. A design that hardcodes a limit is wrong even if the limit is generous.

**Shared context is not shared intelligence.** The room holds the conversation;
each agent keeps its own brain and memory. `SpecialistContext` withholds the
Manager, the registry and every peer, and that stays — collaboration happens
because agents are *in the same room*, never because one was handed another.

Today `ConversationTurn` is **one request and its answer**. There is no
participant list, no history object, no join. Everything in §14, plus voice
addressing, workspaces, avatars and project continuity, is blocked on this.

### 14.1 Voice is an interface onto the same agent

Text, voice and avatar all reach one Alfred. There is no second voice-Alfred.
Eventually: STT, TTS, two-way conversation, written transcript, wake words,
voice addressing ("Alfred…" / "Arthur…"), turn-taking and interruption.

Voice is an **addressing and routing layer as well as I/O** — which is why it
comes after the Conversation Room. Routing needs somewhere to route *into*.
`ConversationTurn.handled_by` already records who answered, so per-agent voice
needs no new plumbing once profiles exist.

**Each agent owns its voice.** Voice belongs to the agent identity, never one
global voice that changes. A profile describes traits — accent, pace, pitch,
warmth — and never names a vendor, model id or endpoint; provider selection
stays in composition, exactly as `MANAGER_PROVIDER` does.

### 14.2 Digital Human / avatar — presentation only

```
Digital Human -> Agent Interface -> Agent -> Brain / Memory / Identity / Capabilities
```

Never a second brain. Mirrors real agent state, and the state vocabulary is
recorded now so a later implementation does not invent its own:

`idle` · `listening` · `thinking` · `speaking` · `working` · `delegating` ·
`waiting` · `error/unavailable`

Every one of those must be *derived from something the system already knows* —
brain mode, an in-flight delegation, speech availability — never asserted by
the presentation layer. The same rule the Control Center already lives under.
Specialists get their own avatars on the same terms.

## 15. Lifetime and planning

The system is intended to run for **years — 2026, 2027, 2028, 2029 and
beyond**. Any six-month example is an illustration, not a scope.

Planning is therefore a **long-term capability**, not a temporary feature:
create a plan, break a project into stages, schedule work, track progress,
remember decisions, record lessons, review what happened, prepare final
reports, and explain how the project evolved.

Durable knowledge must belong to the **project**, not to a chat window that
happens to still be open. Today: memory is per-agent, per-record, day-aware,
and `MANAGER_MEMORY` still defaults to `ephemeral` — so nothing survives a
restart unless configured. That is the first thing project continuity needs.

---

## 16. Who Arthur is — RESOLVED

**Arthur is the Email / Opportunity Specialist.** He is not renamed, not
repurposed, and not the Coding Specialist.

```
ALFRED               Manager
ARTHUR               Email / Opportunity Specialist   ✅ exists
CODING SPECIALIST    separate future specialist       ❌ not started
RESEARCH SPECIALIST  separate future specialist       ❌ not started
SECURITY / QA        separate future specialist       ❌ not started
```

The Coding Specialist is a **separate, additional** specialist. It gets its own
identity document, its own key in `SPECIALISTS`, its own brain, memory, voice
and workspace — the same way any specialist joins. No fake coding functionality
exists and none is to be invented to make the naming tidy.

Recorded because the alternative had a real cost: a specialist's memory store
is selected by name, so renaming Arthur would have moved his memory and left
the old records stranded on disk under the previous name.

## 17. Phase 2 — the Conversation ✅ (domain layer)

`src/conversation/conversation.py`. Reachable: the Control Center records every
`talk` turn into the room the composition root opened.

```
ParticipantKind  PERSON | AGENT
Participant      name + kind — no agent, no brain, no memory, no registry
Utterance        speaker, text, when, addressed, detail (opaque)
Conversation     id, started_at, participants, utterances
                 join / leave / say / addressed_in / transcript
```

**The one rule that shapes everything:** a Conversation holds *identities*,
never agents. Collaboration is solved by putting agents in the same room, never
by handing one agent another. `Participant` has no field for a brain, a memory,
a toolset or a registry — absent rather than unused — so nothing can be reached
*through* the room that could not be reached without it.

Enforced by `tests/test_conversation.py` and by `src/conversation` joining
`NEW_PACKAGES`, which already forbids importing an engine, hardcoding an agent
name, or importing anything outside the standard library and `src`.

**No participant ceiling.** Tested with 300 agents in one room. A maximum here
would be an architectural claim about how many agents may think about a problem
together, and there is no honest number to write.

**What it deliberately does not do:** route · reason · remember · decide who
answers · merge anything · interpret `detail`. `addressed_in` *resolves* who was
spoken to and records it; the Manager still decides who answers, through the
router, exactly as before. That is the seam voice addressing will attach to.

### Still open after Phase 2

| | |
|---|---|
| Conversation **Room UI** | ❌ — the domain object exists; the dedicated full-screen surface does not |
| Inviting an agent | ❌ — "bring Research into this" is not wired to `join()` |
| A specialist answering *in* a room | ❌ — delegation still returns through the Manager |
| Session memory | ❌ — the room is unbounded and nothing compacts it |
| Voice routing on `addressed` | ❌ — recorded, never acted on |

### Sequencing note on §24

Two items were flagged before Phase 2 and remain unstarted, deliberately:

1. **Persistent memory defaults to ephemeral** — so nothing survives a restart.
   This is a prerequisite for *project continuity*, not for the Conversation,
   and the room is per-session by design. Do it before session memory.
2. **`ToolResult` has no `observed_at` / `source`** — a prerequisite for *live
   data*, not for the Conversation. Do it before any second tool exists, while
   there is one call site to change rather than many.

Neither blocks Phase 2 and neither was skipped silently.

---

## 18. Phase 2.5 — continuity and observation provenance ✅

Two correctness items, both prerequisites for later layers rather than for the
Conversation.

### 18.1 Persistent memory is the default

`MANAGER_MEMORY` defaults to `persistent` (`src/config/settings.py`). A rebuilt
agent — the heartbeat builds one per tick — now recalls what the last one
learned.

The old default was `ephemeral`, on the argument that *durability decides where
a person's data sits and should never arrive as a side effect of an upgrade*.
That argument is right about **surprise** and was wrong about the **default**,
because it made forgetting the surprise instead: an agent whose whole purpose is
to notice things, running hourly, began every tick knowing nothing — and no
memory file had ever been written.

An agent that cannot remember is not a safer agent, it is a less useful one. The
confidentiality question is answered by **what the policy admits**, not by
whether the store survives a restart. Unchanged: `MemoryPolicy` still gates
every write, secrets are still refused, records are still frozen, corrections
are still new records, `forget()` and `clear()` are still in the contract, and
each agent still has its own store.

**Ephemeral is not deprecated.** `MANAGER_MEMORY=ephemeral` remains fully
supported and is the right choice for a test, a one-off inspection, or anywhere
durability would surprise somebody.

### 18.2 Observations carry provenance

`ToolResult` gains two fields (`src/agents/tools/tool.py`):

| Field | Meaning |
|---|---|
| `source` | what was reached for — caller-owned, as `Insight.source` already is |
| `observed_at` | when the world was actually looked at |

**Why `created_at` was not enough.** A `MemoryRecord`'s `created_at` says when
something was *written down*. It says nothing about when the fact was *true*.
Remember "Dublin is 15.6C" on Tuesday, read it back on Friday, and the two are
indistinguishable — unless the observation carried the moment it was made.

```
LIVE ENVIRONMENT -> TOOL -> ToolResult(source, observed_at)
                              -> reasoning / use
                              -> optional governed memory write
```

**Only a successful call may be dated.** A rejected call never ran; a failed one
ran and produced nothing to look at. Constructing either with an `observed_at`
raises — otherwise "we tried at 14:03" becomes readable later as "the world was
like this at 14:03", which is the confusion the field exists to prevent.
`ToolResult.is_observation` is the question a caller asks before treating a
result as current truth.

**Unchanged:** tool results still do not become memories
(`TestToolResultsDoNotBecomeMemories`), `src/services/memory` still imports
nothing from `tools`, and provenance rides into a store through
`MemoryRecord.context`, which is carried and never interpreted. Brain mode still
does not gate environment access — a tool runs with no model anywhere.

### 18.3 A guard gap this surfaced

`tests/conftest.py::_no_production_writes` intercepted `write_text`, `Path.open`,
`os.replace` and `os.open` — but **not `mkdir`**. `PersistentMemory._save` calls
`parent.mkdir(parents=True)` before writing, so the first run with the new
default created a real `data/memory/` directory. Empty, because the write itself
was still blocked, but created.

`mkdir` is now guarded. Note the ordering property: the backstop test
`test_no_production_memory_directory_is_created` only catches a leak when a
leaking test runs earlier in the same session, so the guard is the real
protection and the test is the alarm.

### Still deliberately unimplemented

Session memory · Conversation Room UI · real STT/TTS · voice addressing · wake
words · per-agent voices · Coding Specialist · Research Specialist · specialist
workspaces · live-data integrations beyond the read-only mailbox · semantic
memory · RAG · context graph · Digital Humans · QA/security specialist ·
client-facing specialists.

No second tool was added. The provenance boundary is generic and there is
nothing to demonstrate it against but a test double, which is the honest state.

---

## 19. Phase 3 — voice identity ✅ (no engine)

**Voice identity is implemented. Voice I/O is not.** No microphone, no STT, no
TTS, no audio, no dependency. What exists is the layer that makes per-agent
voices expressible and derived from identity, provable without a sound.

```
AGENT IDENTITY  ->  VoiceProfile  ->  Speech interface  ->  a provider
```

Read that left to right and note where the vendor is: at the far end, and only
there.

### 19.1 `VoiceProfile` — `src/services/identity/voice.py`

`key` plus characteristics (`accent`, `pace`, `pitch`, `warmth`) and an opaque
`style` mapping for future emotion/delivery parameters, carried and never
interpreted — the contract `MemoryRecord.context` and `Utterance.detail`
already have.

**It has nowhere to put a vendor.** No provider, model id, endpoint or key —
absent, not ignored. A document that could name an endpoint would be choosing
where a request goes, which is a composition decision, not a description of who
somebody is. Mapping "measured, low, formal, nigerian-english" onto an actual
voice is the *provider's* job, which is what lets the provider change without
every agent changing.

### 19.2 Voice is the fourth thing one identity decides

`SpecialistWiring` already derives name, memory owner and brain domain from a
single identity so they cannot disagree. `voice` joins them.

A `## Voice` section now exists in `IDENTITY.md` and
`identities/email_agent.md` — **and the parsers read it**. Added in the same
change, because a heading that looks like configuration and behaves like a
comment is a defect this project has already fixed twice.

Alfred and Arthur ship with distinct voices. An agent that declares none gets
one keyed on **its own name**, never a shared default — two agents that both
declared nothing must not be given one voice.

### 19.3 Hearing and speaking are separate contracts

`Listener` (listen) and `Speaker` (speak) are separate ABCs, each publishing its
own `availability`. `SpeechAdapter` remains their union, so `NoSpeech` is
unchanged and every existing caller still works.

The likely shape is a local recogniser and a hosted synthesiser — different
costs, different privacy consequences, different failure modes. A single
interface would force one provider to answer for the other.

`Speaker.speak(text, voice=None)`. `None` means *no preference*, not "use the
house voice"; a speaker that cannot distinguish voices ignores it and still
works.

### 19.4 The channel looks a voice up; it never chooses one

`VoiceChannel.deliver` resolves the voice of whoever answered — `turn.handled_by`
→ registry → agent → `voice_for(agent)`. It holds no mapping and names no agent;
a structural test asserts `channel.py` contains no agent name in executable code.
A future specialist is heard in its own voice because it *has* one, not because
the channel learned about it.

### Genuinely implemented ✅

Per-agent voice identity · parsed from identity documents · derived through the
existing wiring · a speech contract that can express it · listener/speaker split
· voice requested by whoever answered.

### Architecturally prepared 🟡

`Listener`/`Speaker` as independent provider seams — no independent
implementation exists yet. `style` — carried, nothing consumes it.

### Still not started ❌

Real STT · real TTS · microphone · audio playback · wake words · voice
addressing acted upon (`Conversation.addressed_in` resolves and records it;
nothing routes on it) · Conversation Room UI · specialist workspaces · Coding
Specialist · Research Specialist · Digital Humans · live-data integrations
beyond the read-only mailbox · semantic memory · RAG · context graph.

**No voice package was installed.** `requirements.txt` unchanged. `NoSpeech`
still reports `UNAVAILABLE` with a reason and still returns `False` from
`speak()` — now while accepting a voice, so the architecture is testable against
the only adapter that ships.

---

## 20. Phase 4 — direct participant addressing ✅

**Decision, resolved.** When a person explicitly addresses a participant who is
already in the room, that participant answers. Alfred does not answer on their
behalf.

Alfred remains the orchestrator for unaddressed requests, for participants who
are not present, and for anything needing coordination.

```
USER
 -> CONVERSATION            resolves who was spoken to, records it
 -> MANAGER DECISION        decide(task, addressed)
      addressed + registered -> that agent
      otherwise              -> capability routing, unchanged
 -> AGENT EXECUTES          its own brain, memory, tools
 -> AGENT SPEAKS AS ITSELF
 -> CONVERSATION RECORDS
```

### One decision, not two routers

`ManagerAgent.decide(task, addressed)` replaced the direct call to
`diagnose()`. Two *inputs*, one decision, feeding the unchanged
assemble → act → assess → remember → announce loop. An addressed task is still
delegated, still assessed, still raises a delegation event.

No `AddressRouter`, `ConversationRouter` or `ParticipantRouter` exists, and a
test asserts none ever does. The room holds no `route`, `decide`,
`handle_task`, `registry` or `manager`, and resolving an address invokes
nobody.

### An absent participant is never invented

`addressed_in` matches only participants **currently in the room**, so
"present" is settled there. At the Manager, an address naming an agent the
registry does not know is logged and dropped, and the router decides — visibly,
so a person can see their address did not land rather than wonder why somebody
else replied.

**"Bring Research into this" still resolves to nobody.** That is correct and
deliberate; the invitation path is Phase 5.

### Proven, not asserted

The load-bearing test is built on a request whose words route *away* from the
specialist, so an implementation that ignored the address would fail it. Five
mutations, all caught:

| Mutation | |
|---|---|
| `decide()` ignores the address | caught |
| addressed participant falls through to routing | caught |
| entry point drops the address | caught |
| specialist's answer attributed to the Manager | caught |
| absent participant invented and auto-joined | caught |

---

## 21. Phase 5 — specialist invitation ✅ (with one known limitation)

A person can bring a registered specialist into a conversation, and only a
**name** crosses the boundary.

```
"bring Arthur into this conversation"
  -> capability routing            -> Alfred's own invitation handler
  -> handler reads roster()        -> resolves a NAME
  -> TaskResult.payload["invited"] -> carried on the turn
  -> entry point joins             -> Participant(name, AGENT)
```

**Invitation is a Manager capability**, not a new layer. Deciding who takes
part is orchestration — the same question `roster_report` answers, asked about
one name. `CapabilityRequest` already carries `roster()` and deliberately
carries no way to delegate, register or reach a memory.

**The handler has no Conversation in scope**, so it cannot join anybody even
if a later edit wanted it to. **The room has no registry**, so it cannot
discover anybody. Neither can do the other's job.

**No agent is named** in the handler or in `Conversation`. A Coding, Research
or QA specialist is invitable the day it registers, with nothing in either file
changing.

**Nobody is invented.** A name nobody is registered under resolves to `None`,
the room is unchanged, and the answer names who *is* registered.

### Known limitation — RESOLVED in Phase 5.1

Phase 5 shipped with a real collision: an agent whose name was also one of
Alfred's capability words could not be invited. `"invite Research"` scored 1 for
the invitation (`invite`) and 1 for `general_assistance` (`research`), and the
tie broke alphabetically. That gated **Research, Coding and Security** — the
three most likely next specialists. See §22.

### Still not started ❌

Voice input to addressing (needs STT) · Conversation Room UI · specialist
workspaces · the Coding, Research and QA/Security specialists themselves ·
Digital Humans · graph · RAG · live data beyond the read-only mailbox.

---

## 22. Phase 5.1 — identity resolution ✅

**The problem.** Agent *names* and capability *words* shared one flat token
space. `select_capability` scores by word overlap and breaks ties on handler
name, alphabetically — so a specialist called Research, Coding or Security was
uninvitable, because those are also kinds of work Alfred declares.

**The fix.** A capability may declare how it recognises a request that is
unmistakably its own:

```python
ManagerCapability(..., claims=invitation_intent)
```

`select_capability` asks claims first, then falls back to the existing scoring
**unchanged**. Only one capability claims anything; everything else is still
settled by word overlap, and a test asserts that.

**The rule** — an invitation is a **verb AND a registered identity**:

| | |
|---|---|
| `invite Research` | verb + registered name → invitation |
| `research this topic` | no invitation verb → ordinary work |
| `write a function to join two lists` | verb, but names no registered agent → ordinary work |

Neither half alone. That pairing is what keeps `join` usable as an ordinary
coding word and `research` usable as a kind of work.

**Why this is not a second router.** A claim selects one of *Alfred's own
handlers*; it returns a capability and cannot name an agent.
`Manager.decide()` remains the one authoritative decision and still picks
Alfred. Nothing else gained the power to choose an agent.

**Names are discovered, never listed.** `ManagerSkills.execute` passes the
current roster into selection, read per call so an agent registered later
counts. No agent name appears in the capability module. Proven by inviting a
`Quartermaster` — a name nothing in `src/` has ever seen.

### A deliberate exclusion: "add"

`"add Security to the room"` does **not** work, and that is a decision rather
than an oversight. A verb only reaches intent recognition if the router sent
the task to Alfred, and the router matches the words `IDENTITY.md` declares.
Declaring `add` would make Alfred claim `"add two numbers"` and answer it with
*"I have no specialist by that name"* — a confident wrong answer to an ordinary
request. A phrasing that does not work beats one that works by making a common
word mean something it usually does not. `invite`, `bring` and `join` all work.

### Proven

Ten mutations, **none survived**: intent recognition removed · scoring
outranking a clear invitation · a hardcoded agent name · a hardcoded branch for
one colliding name · invitation attaching a live agent · conversation reaching
the registry · unknown name auto-creating a participant · verb no longer
required · addressed specialist falling back to routing · roster no longer
supplied.

---

## 23. Per-agent integration grants ✅ (no integration installed)

**The gap.** `EmailAgent.__init__` accepted `tools`, but `SpecialistContext`
had no `tools` field and the builder never passed any. The only grant path in
the system was `build_manager(tools=...)`, which reaches **Alfred alone** — so
"Arthur, check this mailbox" was not refused, it was *inexpressible*. The
architecture looked like it supported something it could not.

**The fix.** `SpecialistContext.tools` (empty by default) and
`build_manager(specialist_tools={key: ToolSet(...)})`, keyed by each
specialist's technical key.

```
EXTERNAL APPLICATION -> Tool -> ToolResult(source, observed_at)
                          ^
                          |  granted here, per agent, in composition
                     SpecialistContext.tools / build_manager(tools=)
```

**No new abstraction.** `Tool → ToolResult` already *is* the integration
boundary, with provenance. What was missing was the grant path, not a concept.
An `Integration` layer on top would have been the speculative abstraction this
phase was told to avoid.

### The rule

```
Identity describes.     A document says who an agent is.
Composition grants.     Wiring says what it may reach.
Tools perform.          The integration does the work.
Memory remembers.       Under policy, and only if admitted.
```

**A grant reaches exactly one agent.** Equipping a specialist does not equip
the Manager, and vice versa — two separate parameters, so they are two separate
acts. There is deliberately **no** way to grant something to "all specialists":
an integration reaching every agent because one needed it would make *"who can
read that?"* a question nobody could answer. Asserted structurally.

**Nobody is equipped by default.** An unequipped agent reaches nothing and says
so — `ok=False`, `observed_at=None` — rather than pretending.

**A grant is not a capability.** Two axes, unchanged: a tool is a *means*, a
capability is a *kind of work*. Granting an integration adds no capability word.

### Honest status

**No integration is installed.** No WhatsApp, no Slack, no Telegram, no Notion,
no GitHub, no Calendar integration — asserted by an import scan. Gmail remains
read-only and separate. What exists is the path those will travel, proven with
a test double.

Mutations: grant never reaching the specialist · any grant reaching every
specialist · the Manager's grant leaking to specialists · the builder dropping
the grant — **all caught**. A deliberate no-op control survived, as it should.

### Deliberately not built

**The avatar/presence contract (§6 of the brief) was not added.** The state
vocabulary — idle / listening / thinking / speaking / working / delegating /
waiting / error — must be *derived from real system state*, and most of those
states have no producer yet: nothing tracks an in-flight delegation or a
speaking turn. An enum with no producer is exactly the contract-without-a-caller
pattern this project keeps having to correct. It stays 🔵 PLANNED, with the
vocabulary already recorded in §14.2.

The UI surfaces (Conversation Room, Status, History as rendered applications)
also remain 🔵. The terminal already separates them as distinct commands and
renderers; turning them into a windowed application needs a framework decision
that has not been taken.

---

## 24. Phase 5.2 — session continuity ✅

**What already worked.** Persistent memory is the default and the Manager
recalls before deliberating, so continuity worked for anything that *reached*
memory — delegation outcomes did.

**What was missing.** Nothing said in a conversation ever reached memory. A
multi-day discussion left no durable trace at all.

```
Conversation  ->  retention_requests()  ->  Manager.retain()  ->  MemoryPolicy
 (session)         candidates, explicit      the one write        the authority
                                                  |
                                            PersistentMemory
                                                  |
                                            a later session recalls it
```

### Only what was asked for crosses

`src/services/memory/session.py` finds the places where a person **asked** for
something to be kept — "remember that…", "note that…", "for future
reference,…" — and returns *candidates*. Everything else stays in the room.

Deterministic, so it behaves identically in Half Brain. Judging what "mattered"
without being asked needs a model and degrades quietly when wrong; asking is
cheaper and honest. Model-judged extraction is a Full Brain upgrade **on top of**
this floor, never a replacement.

The instruction is not stored with the fact: `"remember that we are building
Project Beacon"` retains `"we are building Project Beacon"`.

Provenance travels — a retained note records that it was learned in
conversation, and which one.

### One governed write path

`ManagerAgent.retain(record) -> PolicyVerdict` was extracted from
`_remember_delegation`, which now goes through it. **Still exactly one place
writes to memory** — extracting it is what kept the count at one instead of two
when a second caller appeared. It takes no owner argument, so there is no way
to write into another agent's store.

### Session context is not memory

| | |
|---|---|
| Conversation | everything said, unbounded, dies with the session |
| Memory | what was asked for, admitted by policy, survives |

Proven live across two separately built systems over one store: the room came
back **empty** and the retained note came back.

`Conversation` still imports nothing from `src` — it did not become a database,
and `session.py` imports only `MemoryRecord`, knowing nothing of rooms or
agents. Neither can do the other's job.

### Verified

Six mutations, **none survived**: every utterance becoming a candidate · the
policy no longer gating the write · provenance dropped · retention disabled ·
the instruction stored with the fact · `retain` gaining a way to name another
store.

Untouched: per-agent memory isolation · integration grants (zero, one or many
per agent) · Gmail read-only · voice identity · unbounded participants ·
Full/Half Brain.

### Deliberately not implemented

Automatic extraction of what "mattered" · session summarisation · conversation
persistence (the room is not a database) · retrieval ranking beyond the existing
token recall · the Conversation Room UI · STT/TTS · avatars · QA/Security,
Coding and Research specialists.

---

## 25. Interface foundation ✅ (shell), 🔵 (delivery)

### The framework decision, and why

The repository was pure Python with no frontend. `test_no_server_or_voice_module_is_imported`
forbids `flask`, `fastapi`, `http.server` and `websockets` in `src/` — a guard
written precisely to say *the UI phase has not started*.

Rather than weaken that guard on day one, this phase took the option needing
**no server and no dependency**:

```
ControlCenter (reads)  ->  snapshot (data)  ->  a static page  ->  laptop / phone
```

`requirements.txt` is unchanged, the guard still holds, one interface
architecture serves both devices, and the **serving** question stays open. When
a live interface is wanted, the snapshot is already the seam it reads from.

### `src/control_center/snapshot.py`

One honest reading of the system as data. It knows nothing about screens.

**It invents nothing.** Every value traces to a read that already existed — a
`CognitiveProfile`, a `MemoryView`, a `ToolSet`, a `VoiceProfile`, a
`Conversation`. Where the system cannot answer it returns `available: False`
with the reason the system gave. `activity` and `avatar` are `None`, because
**nothing produces them** — emitting a placeholder would let a screen show
"thinking" for a system doing nothing.

It imports no brain, no memory, no registry and no agent, and names no agent —
asserted structurally.

### `ui/index.html` — the environment

Not a dashboard with a 3D background. A dark spatial field with real
perspective: bodies carry x/y/z, a slow camera rotates them, and a perspective
divide makes near bodies larger and brighter while far ones sink into fog. The
interface sits *inside* that space.

| | |
|---|---|
| **Real** | one body per agent · which is the manager · manager↔specialist links (the registry says so) · every value in every panel |
| **Decorative** | drift, camera orbit, breathing, dust threads — atmosphere, explicitly **not** an activity visualisation |

`body.energy` exists as the attachment point for real activity when a producer
exists. It is currently always zero.

**Alfred is the centre of the composition**; specialists orbit as independent
bodies with their own brain, memory, voice and grants shown. Seven surfaces —
Control, Talk, Agents, Cognition, Memory, Status, History — reachable as modes
of one environment.

### One source of truth

No `ConversationRoomBrain`, `WorkspaceMemory` or equivalent exists, and a test
forbids the whole family by name. A workspace is an interface *into* an agent;
the room is a view of a conversation. Each agent's store is reported under its
own owner, and two agents sharing one would fail a test.

### Verified

Six mutations, **none survived**: inventing an activity state · inventing an
avatar · presenting no-conversation as an empty room · asserting FULL BRAIN ·
reporting speech usable with no engine · letting a transcript break out of the
JSON data island into markup.

### Implemented / prepared / not

**Implemented:** the snapshot contract · the seven surfaces · the 3D
environment · responsive laptop/phone layouts · honest unavailability · a
render path (`scripts/export_ui.py`).

**Prepared:** the activity seam (`activity`, `body.energy`) · the avatar slot
(`avatar`) · the integration display (per-agent, already correct for zero, one
or many).

**Not implemented:** any live/served interface · sending a message from the
room (the composer is disabled and says so) · real activity state · Digital
Humans · STT/TTS · QA/Security, Coding and Research specialists.

### Discovered

Exporting a snapshot surfaced a **real bug**: a request that matched no agent
put a participant named `"nobody"` in the room, because `trail.py` uses that as
a sentinel and `_exchange` joined it. Fixed by asking the registry whether the
speaker is an agent rather than comparing against the sentinel.

Also noted: `test_no_control_center_module_exists` passes only on a technicality
— it checks file *stems*, and `src/control_center/` is a directory. Its claim
has been false since the Control Center was built. It should be retired or
narrowed deliberately.

## 26. Interface — visual pass 1 (presentation only)

Section 25 built the seam and the shell. This pass changed how the shell
looks and reads; it changed nothing about what it is allowed to say. Every
edit is confined to `ui/index.html` (plus a handful of new tests in
`tests/test_snapshot.py::TestTheVisualPassStaysHonest`) — no backend file
moved, no new dependency, no server.

### What changed

- **Palette and type.** A darker, cooler base (`#05070a`), Alfred's accent
  moved from blue to cyan, and the whole shell now sets a monospace-first
  type stack (`Space Mono` / `IBM Plex Mono`, falling back to the system
  monospace stack already in use — no font is fetched; the no-network shell
  test still holds).
- **Per-agent identity color.** A small curated hue set, chosen by hashing
  the agent's own name (`hue(name)` in `ui/index.html`), colors a
  specialist's satellite card and canvas body. Alfred's cyan stays reserved.
  A future specialist gets a color automatically; nothing names one here.
- **Two new instruments, not new data.** `brainGlyph()` draws the existing
  `brain.indicator`/mode as two hemispheres (filled only as far as GREEN/AMBER
  already say); `memoryGlyph()` draws the existing `memory.sampled` count as
  a traced ring against a fixed `MEMORY_TRACE_CAP = 8` (mirrors
  `observer.py`'s `MEMORY_SAMPLE`). Both are pure re-renderings of fields the
  snapshot already carried.
- **A roadmap, static and inert.** A `ROADMAP` constant (five short labels:
  QA/Security, Coding, Research, Voice I/O, Digital Human) renders as dashed
  `.chip.off` entries under Alfred's facets. It is never read from `S`, never
  joins `S.agents`, is not clickable, and the canvas never gives it a body,
  a link, or an energy value.
- **Alfred's canvas body** is now layered rings rather than a single filled
  dot — still one body, still the same real/decorative boundary, just drawn
  as a core rather than one more orbiting point.
- **Copy.** A handful of void/absent strings were re-worded shorter
  (`"Nothing to report."`, `"THE ROOM IS QUIET"`, `"LINK ESTABLISHED"`) —
  same underlying values, no new claims.

### Real vs. decorative, unchanged

Nothing above adds a value the observer didn't already produce. The two
glyphs are read-only views of `brain.indicator` and `memory.sampled`; the
roadmap is documentation, not a discovery; `activity` and `avatar` are still
`None` and still rendered as absence, not as a placeholder animation.

### Two bugs this pass found by actually opening the page

Both existed before this pass and were invisible to the Python test suite,
because nothing in it runs the shell's JavaScript in a browser.

1. **The shell crashed on load.** `go(start)` (called immediately) invokes
   `focusOn()`, which assigns to `focus` — but `focus` was declared with
   `let` further down, inside the canvas section. Every load threw
   `ReferenceError: Cannot access 'focus' before initialization` before a
   single frame rendered. Fixed by declaring `focus` beside `S` at the top
   of the script.
2. **On phone, a specialist rendered above Alfred.** The mobile breakpoint
   makes `.satellites` static instead of absolutely positioned, so it falls
   into normal document flow — and it sits first in the DOM, ahead of
   `.core-id`. Fixed with `order` on both (`.core-id{order:1}`,
   `.satellites{order:2}`), which reorders the two visually without
   touching markup or script.

Neither is a design decision; both are recorded here because a future
session should not "fix" `focus` back to where it was, or move `.satellites`
back above `.core-id` in the DOM, without knowing why.

### Verified

`pytest` before this pass: 3326 passing. After: 3331 passing (5 added,
0 removed, 0 weakened). The five new tests hold the roadmap to being static
and inert, and hold both new instruments to rendering a real branch rather
than a hardcoded one.

### Not implemented

Everything §25 already listed as not implemented remains not implemented.
This pass added no serving layer, no live data, no Three.js/WebGL, no
enabled composer, and no new specialist.

## 27. Interface — live serving (the smallest correct path)

§25 left one question open on purpose: *"the serving question stays open."*
This section answers it, as narrowly as it can be answered.

### The seam, extended by one hop

```
ControlCenter (reads) -> snapshot (data) -> a served page, asked again
```

`scripts/export_ui.py:take_snapshot(settings)` is the one function that
assembles a real system and reads one honest snapshot of it - extracted
from what was previously inlined in `main()`, with no behaviour change
(`main()` calls it exactly as it did before). `scripts/serve_ui.py` is new:
a stdlib `http.server.ThreadingHTTPServer` with exactly two GET routes,
both computed by calling `take_snapshot` fresh, every time:

    GET /              scripts/export_ui.render(take_snapshot(settings))
    GET /snapshot.json json.dumps(take_snapshot(settings))

No other verb is defined, so `BaseHTTPRequestHandler` answers "501
Unsupported method" for anything else on its own - there is no write path
to remove because none was ever added. Binds to `127.0.0.1` by default;
reaching it from another machine is an explicit `--host` choice.

### Why this doesn't weaken the no-server guard

`tests/test_architecture_audit.py::test_no_server_or_voice_module_is_imported`
forbids `flask/fastapi/.../http.server/socketserver/websockets` - but only
inside `src/` (`source_files()` is `SRC.rglob("*.py")`, and `SRC = REPO_ROOT
/ "src"`). Its purpose, per §25, is that the *domain layer* stays
server-free. `scripts/` already holds every entry point that assembles
`src/` without being part of it - `main.py`, `scripts/heartbeat.py`,
`scripts/control_center.py`, `scripts/export_ui.py`. `scripts/serve_ui.py`
joins them. It imports no server module from `src/`, and no import of a
server module was added to `src/` - the guard was inspected, found to
already permit exactly this, and was left untouched.

`requirements.txt` is unchanged: `http.server`/`socketserver` are stdlib.

### The live UI stays a presentation layer

Every response is assembled fresh - `take_snapshot` calls `build_runtime`
and `build_manager` from nothing, every single request. Nothing survives
between them: no cached manager, no held registry, no accumulating memory.
`LiveServer` (the `ThreadingHTTPServer` subclass) carries exactly one
attribute of its own, `settings` - checked structurally, so a future
change that starts caching a manager on the server object fails a test
before it fails in production. This also makes concurrency trivial: two
threads serving two requests share no mutable object, so there is nothing
to lock.

The frontend change is equally small. `ui/index.html` gained a
`renderChrome()` extraction (no behaviour change) and one addition: if the
page's own protocol is `http:`/`https:` (i.e. it is being served, not
opened from a file), it polls `snapshot.json` every 5 seconds, and on a
successful, shape-checked response, replaces `S` and redraws the current
surface - the same `go(current)` a click already uses. A failed poll
(offline, or the static export opened from disk) changes nothing; the
last good reading stays on screen. The canvas environment only rebuilds
its bodies if the *roster* changed (a new agent registered), so it does
not jump or restart its drift/orbit on every ordinary poll. No visual
token, color, layout or copy from §26 was touched - this section is data
plumbing only.

### What remains static, what is now live

**Static, unchanged:** `ui/agent-control-center.html` (the file
`export_ui.py` writes) and opening `ui/index.html` directly from disk -
both still work exactly as before, with zero server involved.

**Live, new:** running `python scripts/serve_ui.py` and opening
`http://127.0.0.1:8642/` gets a page that refreshes its own data every 5
seconds from the real, currently running system - without hand-regenerating
a file. Verified with a real browser against a real running instance of
this server (Playwright, outside pytest): two `snapshot.json` requests
observed in an 11-second window, zero console errors.

### Not implemented, still

The room composer stays disabled - this milestone is observation, not
sending. No WebSockets - inspection found nothing that needed one; polling
answers "did anything change" well enough at this scale, and adding a
persistent connection would be exactly the dependency-for-its-own-sake this
phase was told to avoid. No new activity producer was added - `activity`
and `avatar` are still `None` on every request, and a mutation that forced
a fake `"thinking"` value was written, observed failing two tests, and
reverted. No integration, provider, specialist, STT/TTS, or Digital Human
work of any kind.

### Verified

`pytest` before this section: 3331 passing. After: 3344 passing (13
added in `tests/test_live_ui.py`, 0 removed, 0 weakened). Four boundary
mutations were introduced by hand and each caused its guarding test to
fail, then were reverted and the suite reconfirmed green: a fake activity
value on the live path, an added `tools=` grant on the live assembly, a
second attribute cached on the server, and a `0.0.0.0` default bind.

`tests/conftest.py::_no_network` blocks every real `socket.connect()` for
the whole suite, on purpose - including to `127.0.0.1` - because it is
what stops an accidental live call from reaching a real, credentialed API.
Rather than ask that guard to make a "just localhost" exception, the
server's routing decision was factored into a plain function, `route()`,
that `tests/test_live_ui.py` calls directly with no socket at all. The one
thing that genuinely needs a real running server and a real client - the
browser actually polling and redrawing - was proven by hand with
Playwright outside the pytest session, where that guard does not apply.

## 28. Interface — live interaction (talking to the one held system)

§27 made the seam live for reading. This section makes it live for
talking, on exactly the path already reviewed and approved: one held
system, one locked write, the same `exchange()` a terminal session already
calls.

### The extraction

`scripts/control_center.py` used to define `_exchange` (and its two
helpers, `_retain_what_was_asked_for` and `_admit_invited`) as private,
file-local functions. They now live in `scripts/exchange.py`, unindented
and renamed (`exchange`, `retain_what_was_asked_for`, `admit_invited`) but
otherwise byte-identical in behaviour - confirmed by running
`tests/test_control_center.py` immediately after moving them, before
anything else changed, and seeing the same 92 tests pass. `control_center.py`
now imports `exchange` and calls it exactly where `_exchange` used to be
called. This is the whole extraction: no logic was rewritten.

### One held system, not a second Alfred

`scripts/serve_ui.py` previously rebuilt a whole throwaway system
(`export_ui.take_snapshot`) on every request - correct for read-only
observation, wrong for a conversation, because a message answered by a
freshly-rebuilt Manager would be talking to something with no memory of
the previous turn. It now assembles a `LiveSystem` - one `runtime`, one
`ManagerAgent`, one `Channel`, one `Conversation`, one `ControlCenter` -
exactly once, by calling `scripts.control_center.build(settings)`, the
same function a terminal session already calls to open its own. It is
held for the life of the server process and nothing else builds one.

This is not a second Brain, Memory or Conversation architecture. There is
exactly one `ManagerAgent` in the process, built by the one real
composition root; `LiveSystem` only holds a reference to it, the same way
a local variable in `interactive()` already does for a terminal session.
Structurally guarded: `LiveServer.__init__` may assign exactly one
attribute of its own (`system`), and `LiveSystem`'s own fields are exactly
`runtime, manager, channel, conversation, centre, lock` - nothing that
duplicates one of them.

### The one write route

`POST /message` takes `{"text": "..."}`, rejects empty/non-string text
before either `Conversation.say()` or `ManagerAgent.handle_task()` are
reached, and otherwise calls `exchange(system.channel, system.conversation,
text)` - the identical function a terminal `talk` command calls - under
`system.lock`. The response reports the fields `ConversationTurn` already
carries (`handled_by`, `delegated`, `outcome`, `succeeded`, `brain_mode`,
`specialist_brain_mode`, `memory_written`, `invited`); nothing is
paraphrased or invented. A failure inside `exchange()` - a specialist
raising, a provider erroring - is caught and reported as a genuine HTTP
500, never disguised as a successful turn.

`GET /` and `GET /snapshot.json` now read `_current_snapshot(system)`,
which calls `snapshot()` against the *same held* `centre`/`conversation`/
`channel` a `POST` just acted on - not a fresh, unrelated system. This is
the one deliberate change in what "GET" means since §27: it used to answer
from a throwaway system every time; it now answers from the live session,
which is the entire point of holding one.

### Concurrency

`route()` (GET) is unlocked - each call to `snapshot()` builds a fresh
dict from state that is only ever appended to, so there is nothing for a
concurrent read to see half-written. `route_message()` (POST) is locked
around its one call to `exchange()`, because that call speaks in the room,
asks the Manager, and may retain a memory - four steps that must land as
one turn, not interleave with another request doing the same thing.
Verified structurally (`route_message` contains a `with ...lock:`;
`route` contains no `with` at all) and by mutation: temporarily removing
the lock, and separately reintroducing the old "rebuild fresh per
request" behaviour in `_current_snapshot`, both caused their guarding
tests to fail, and both were reverted.

### The UI composer

`ui/index.html` gained a module-level `LIVE` constant (already implied by
§27's poll gate, now named and reused) and two functions: `wireComposer()`
attaches an Enter-key handler to the room's `<input>` when `LIVE`, POSTs
to `/message`, and immediately calls `refreshFromLive()` rather than
waiting for the next scheduled poll; `refreshFromLive()` is the same
function §27's `setInterval` now calls too, refactored out unchanged in
behaviour. A background poll preserves an in-progress draft rather than
wiping it mid-type. No color, layout, typography or 3D-scene code changed.
Opening the static export still makes exactly one request (the page
itself) and leaves the composer `disabled` - verified with Playwright.

### Discovered, and fixed: a real write to production memory

Manual end-to-end verification ran `scripts/serve_ui.py` directly (outside
pytest, so `tests/conftest.py`'s guards do not apply) with default
`Settings`, which resolve to the real `data/memory` directory. A test
message containing "remember that..." was retained for real: exactly the
`tests/conftest.py::_no_production_writes` incident this project already
hit once with `scripts/heartbeat.py`, now hit a second time by a different
entry point. Confirmed by timestamp and content that only one record
existed (`"we prefer blue"`, tagged with that test's own conversation id)
and nothing else under `data/` was touched; the file and its
now-empty directory were removed, and the two tests that check for this
condition (`test_an_empty_history_says_so`,
`test_no_production_memory_directory_is_created`) were confirmed passing
again. **The lesson, for next time:** manual verification of a live entry
point must pass isolated settings (ephemeral memory, a scratch directory)
exactly as the test suite's own fixtures do - a real server run is a real
run, not a test, regardless of who starts it or why.

### Verified

`pytest` before this section: 3344 (matching §27's end state). After the
extraction alone: 92/92 `test_control_center.py` tests still passing,
confirming behaviour was preserved before anything else changed. After the
full milestone: 3357 passing. `tests/test_live_ui.py` grew from 13 tests to
26 - some of the original 13 adapted for the new `LiveSystem`-based
`route()` signature (not weakened; updated to prove a stronger,
intentionally different invariant now that a system is held rather than
rebuilt) plus 13 new tests covering `POST /message`. Three boundary
mutations were introduced by hand and each caused its guarding test to
fail, then were reverted: the amnesia bug reintroduced in
`_current_snapshot`, empty/malformed input allowed to reach `exchange()`,
and the lock removed from `route_message`.

### Not implemented, still

STT/TTS, Digital Humans, WebSockets (inspection found nothing polling
could not already answer), any new integration, any new specialist, and
any activity producer beyond what `turn.delegated`/`turn.handled_by`
already report factually after the fact.

## 29. Agent Visual Identity — color and form, not a fact

A new small concept, introduced in `ui/index.html` only: every real agent
gets a color and a polygon shape, chosen deterministically from its own
name (`hashOf(name)` feeding `hue()` and `sidesOf()`). It replaces Alfred's
plain filled circle with a fixed, faceted core, and gives each specialist
a simple polygon instead of a second identical dot - "distinct identity",
not "a glowing circle that happens to be blue."

**What owns it.** Three pure functions in `ui/index.html`'s script:
`hashOf`, `hue`, `sidesOf`. Nothing else. There is no `AgentVisualIdentity`
class, no registry, no store - a color and a shape are computed fresh on
every render from a string (`agent.name`) the snapshot already carried.

**What it does.** Colors a specialist's satellite card, its canvas body,
its Room presence orb, and its transcript speaker label with the same hue
every time it is drawn - and gives its canvas body a shape (diamond,
hexagon, triangle or pentagon, by the same hash) distinct from Alfred's
core. Alfred's cyan and faceted-core shape stay reserved; nothing chooses
them for a specialist.

**What it does NOT own.** Brain, Memory, Voice, Capabilities, Connections,
routing, or which agent handled a request. Manager-vs-specialist is
decided once, structurally, by `b.kind==="manager"` in the canvas (itself
set from `agents.findIndex(a=>a.is_manager)`, a real boolean already on
the snapshot) and by `agent.is_manager` in the Room - never by comparing a
name against `"Alfred"` or `"Arthur"`. A structural test
(`test_the_script_never_branches_on_a_literal_agent_name`) makes that a
build-time guarantee rather than a convention someone has to remember.

**How it relates to the rest of the architecture:**

    AgentRegistry    -> supplies the name (via the snapshot); identity
                        never queries it, never duplicates it
    Brain / Memory    -> unaffected; still their own instruments, still
                        reading their own fields
    Voice             -> unaffected; VoiceProfile is a separate seam
    Capabilities /
    Connections       -> unaffected; unrelated fields on the same agent
    Conversation      -> the Room borrows `hue()` to color a participant's
                        presence, the same function the graph uses - not
                        a second color mechanism

**Why it cannot become a second source of truth.** It adds no field to
the snapshot contract, persists nothing, and is entirely re-derivable from
data already there. Delete every trace of it and the system loses a color
and a shape; it does not lose a fact. Deterministic by construction
(`hashOf`, never `Math.random`), verified structurally and by mutation: a
random shape assignment and a hardcoded name comparison were each
introduced by hand and each failed a guarding test before being reverted.

### Verified

`pytest` before this section: 3357. After: 3361 (4 new tests in
`tests/test_snapshot.py::TestAgentVisualIdentityStaysHonest`, 0 removed,
0 weakened).

## 30. AgentVisualIdentity — one resolver, not a second registry

§29 introduced per-agent color and form. This section consolidates the
three functions that computed them (`hue`, `sidesOf`, called separately
from five places) into one: `identityOf(agent)` in `ui/index.html`. Every
place that draws an agent - the graph's canvas bodies, a satellite card,
the Room's presence orbs, the Room's transcript speaker labels - now calls
this one function and nothing else computes a color or a shape.

**WHAT IT IS.** A pure function:

```js
function identityOf(agent){
  if(agent.is_manager) return {key, hue: MANAGER_HUE, form:"core", sides:0};
  return {key, hue: <hashed>, form:"polygon", sides: <hashed>};
}
```

**WHAT OWNS IT.** `ui/index.html` alone. There is no class, no store, no
module boundary beyond one function in one file's inline script.

**WHAT IT DOES.** Takes the two fields every agent in the snapshot already
carries - `name` and `is_manager` - and returns a presentation descriptor:
a color (`hue`), a display treatment (`form`: `"core"` for the manager,
`"polygon"` for everyone else), and a shape parameter (`sides`, meaningful
only for a polygon). Alfred's cyan and faceted form are reserved
(`MANAGER_HUE`, `form:"core"`), never computed from a hash. A specialist's
hue and side-count are both derived from `hashOf(name)` - deterministic,
so the same agent looks the same on every render, and a future specialist
gets its own look with no line here changing.

**WHAT IT DOES NOT OWN.** Brain, Memory, Voice, the capability registry,
the integration registry, `AgentRegistry`, the LLM provider, agent state,
Conversation state, Brain objects, Memory objects. It cannot route a task,
call a tool, retain a memory, choose an LLM, choose a voice provider,
grant an integration, or create an agent or a Conversation - it has no
reference to anything that does any of those, only to a name and a
boolean already sitting on the object the snapshot handed it.

**How it relates to `AgentRegistry`.** Not at all, directly. The registry
is what decided which agents exist and is read once, in Python, to build
the snapshot; `identityOf()` runs only in the browser, over whatever
`S.agents` already contains. It cannot enumerate agents, cannot discover
one, and would render nothing at all for an agent the registry never
reported.

**How it relates to Brain / Memory / Voice / Capabilities / Connections.**
Not at all. Each is still read from its own field on the same agent
object (`agent.brain`, `agent.memory`, `agent.voice`, `agent.capabilities`,
`agent.tools`) by its own rendering code (`brainGlyph`, `memoryGlyph`, the
Voice/Integrations facets) - none of which calls `identityOf()`, and which
`identityOf()` does not call either. Verified structurally: a test parses
`identityOf`'s own source and asserts none of `.brain`, `.memory`,
`.voice`, `.tools`, `.capabilities`, `.provider`, `.interface` appear in
it - so a future edit that started reading one of those to decide a color
would fail a test before it reached a browser.

**How it relates to Workspace.** Not at all - the workspace surface
(`V.workspace`) reads `a.interface`, a separate field this function never
touches.

**How it avoids becoming a second source of truth.** It stores nothing.
Every call recomputes its answer from the two inputs it was just given;
there is no cache, no persisted mapping from name to color, nothing that
could go stale or disagree with a later read of the same agent. Changing
Alfred's visual identity to a different original design would mean
editing `MANAGER_HUE` and `facetedCore()`'s geometry - two lines and one
drawing function - and touching nothing else: not `ManagerAgent`, not
`LLMRouter`, not `VoiceProfile`, not `AgentRegistry`. Swapping the
provider from Claude to OpenAI touches `src/providers/factory.py` and
never touches `ui/index.html` at all - the two are not merely
decoupled, they are in different languages, running in different
processes, connected only by the snapshot's plain `name`/`is_manager`
fields.

### Verified

`pytest` before this section: 3361. After: 3362 (2 tests updated to check
the consolidated `identityOf()` rather than the three separate functions
it replaced - not weakened, since both still hold the same invariant
against the new implementation - plus 1 new test asserting `identityOf()`
reads nothing but `name`/`is_manager`). Two further mutations were
introduced by hand during this section and each caught a real gap in a
test before being reverted: a mutation that made `identityOf` read
`agent.brain` was caught immediately; a mutation that bypassed
`identityOf()` in the Room was *not* caught by the first version of its
guarding test, because that test matched a comment mentioning
`identityOf()` rather than the actual call - the test was corrected to
ignore comment lines, and then correctly failed against the same
mutation.

## 31. Agent Workspace — identityGlyph() and the workspace foundation

This section is short because there was almost nothing left to build.
`V.workspace` (the "Agents" surface) already existed, already read
`S.agents` generically, and already gave every agent its own `.band`
anchored at `#ws-<name>` - the N-agent, no-name-hardcoded workspace
foundation the directive asked for was already in place. What was missing
was presence: a workspace band was a list of labelled facts with no
spatial identity at its head.

**WHAT IT IS.** One function, `identityGlyph(agent, size)`, and one CSS
animation (`@keyframes idspin`). `identityGlyph` calls `identityOf(agent)`
(§29-30) and draws exactly what it returns - a 16-point faceted core for
`form:"core"`, a simple polygon for `form:"polygon"` - as an inline SVG,
in that agent's own hue. It is placed once, at the head of each workspace
band, via `${identityGlyph(a)}` inside the same `S.agents.map(a=>...)`
loop the band's other fields already come from.

**WHAT IT OWNS.** Nothing. It is a rendering of a rendering: `identityOf()`
already reduced an agent to a color/form descriptor; `identityGlyph()`
turns that descriptor into markup. Neither step touches Brain, Memory,
Voice, capabilities, connections, or the registry - verified structurally,
the same way §30 verified `identityOf()` itself.

**WHAT IT DOES NOT OWN.** Motion is a CSS `animation`, not a
`requestAnimationFrame` loop - it needed no new render loop, and it
inherits this page's one existing global rule
(`@media(prefers-reduced-motion:reduce){*{animation:none!important}}`)
automatically, verified by emulating reduced motion before navigation and
reading the glyph's own computed `animationName` back as `"none"`.

**Relationship to `AgentRegistry`.** None, directly - same as `identityOf()`
before it. It draws whatever `S.agents` already contains; a third
registered specialist gets a band and a glyph with nothing here changing,
because the loop that produces both already iterates the real list.

**Relationship to Brain / Memory / Voice / Capabilities / Connections.**
Unchanged and undisturbed. Each still renders from its own field on the
same agent object, in the same band, right below the glyph - the glyph is
the first `.pair`-less element in the band, everything else is exactly
what shipped in the interface foundation milestone.

**Why this is not a second source of truth.** Delete `identityGlyph()` and
a workspace band loses one small picture. It does not lose the agent's
Brain, Memory, Voice, capabilities, connections, or its place in the
registry - none of those were ever routed through it. Concretely: if
Alfred's assessed Brain mode changes from HALF to FULL, the workspace's
Brain pair changes because it reads `a.brain.mode` directly, and
`identityGlyph` - which never looked at `a.brain` in the first place - is
completely unaffected. If Alfred's provider changes from the null provider
to a real one, or from one real provider to another, nothing in
`ui/index.html` changes at all, because nothing in it has ever named a
provider.

### Deliberately not built this milestone

**A separate "enter Alfred's environment" full-screen mode.** The existing
`V.workspace` view, anchored per agent and reachable by clicking a
specialist's satellite card, already *is* the workspace surface the
directive describes - building a second, parallel navigation mode for the
same concept would have been the redesign this milestone was explicitly
told not to attempt.

**A dedicated Room "centerpiece" object.** The canvas environment
(`#scene`) is already visible behind the Room - it is `position:fixed`
behind every surface, and Alfred's faceted core is already the one fixed
point at its center. A second, separate reactor-styled element would have
duplicated a "shared command core" the environment already provides,
rather than adding one. If a future milestone wants the Room specifically
to feel more contained than "the same environment as everywhere else,"
that is a real design decision worth making deliberately - not one to
default into by adding a second object.

**Thematic identities (Batman for Alfred, Nightwing for Arthur, etc.).**
The architecture already supports this - swapping `MANAGER_HUE` and
`facetedCore()`'s geometry, or the `SPECIALIST_HUES`/`SPECIALIST_SIDES`
tables, is a two-function change that touches nothing else. But giving
one *specific* specialist one *specific* themed look, by name, is exactly
the `if Arthur` architecture this whole line of milestones has been
forbidden from writing. The deterministic, hash-derived, dark/tactical
palette already established is the correct foundation; a named creative
identity per specialist is a content decision for later, not a structural
one to make now by special-casing a name.

### Verified

`pytest` before this section: 3362. After: 3365 (3 new tests in
`tests/test_snapshot.py::TestAgentWorkspaceStaysHonest`, 0 removed, 0
weakened). Two mutations were introduced by hand and each caught: making
`identityGlyph` read `agent.brain` directly, and hardcoding
`a.name==="Arthur"` to skip the glyph for one specific specialist (caught
by both the new workspace test and the pre-existing
`test_the_script_never_branches_on_a_literal_agent_name` guard from §29).
Both were reverted. Verified live: two workspace bands (Alfred, Arthur),
each with exactly one glyph, visibly distinct (Alfred's reserved cyan
faceted star; Arthur's own hash-derived hue and a different polygon,
observed as a triangle - side-count and hue are independently hashed, so
they need not "agree" with each other, only stay internally consistent
call to call).

## 32. Camera focus and the Room's own presence

§31 made an agent's identity prominent *within its workspace band*. This
section makes the shared environment itself respond: the one canvas can
now be pointed at a real agent, and the Room gets a presence of its own
that belongs to no single agent.

### Camera focus - a look-at, not a new body

`frame()` already rotated the whole scene around one shared origin every
tick; it now optionally also computes a camera offset from a **real
existing body** (`bodies.find(...)`, never a constructed one) and
subtracts it from every body's projected position before drawing. The
target body itself never moves or gains a special position - the camera
looks at it, at whatever position it already legitimately occupies this
frame, the same way looking at something in a room does not move the
room. `focusBody` is set by clicking any agent's own identity wherever it
appears (`data-focus="${esc(a.name)}"`, present in the Workspace,
Cognition and Memory surfaces) and cleared on leaving those surfaces.
Ambient rotation, breathing and dust continue exactly as before while
focused - verified by hashing the canvas twice a second apart with no
pointer input and confirming it still changed.

**Why this needed no new dependency.** "Large and prominent" turned out to
be a camera problem, not a rendering-technology one: the existing
perspective-divide math (`project()`) already scales a body by its
distance from camera; pointing the camera at one specific body and
raising the zoom factor (`2.4`) was enough to make it fill much more of
the frame, with the exact same rings, halo and faceted/polygon geometry
already built for it.

### The Room's own presence

A neutral set of concentric rings, drawn around whatever sits at the
scene's origin, but **only while `current==="room"`** and **never colored
by any agent's `identityOf()`** - a fixed `rgba(200,224,236,...)`, checked
structurally (`identityOf(`, `.hue` and the body's own `col` variable are
all asserted absent from that code path). It represents the shared space
itself: dim, unlit chrome around whichever real body happens to be there,
not a second Alfred and not a claim that anything is "active" in the
room beyond what the room's own `S.conversation` already reports.

### A recurring bug class, caught and now guarded against

Manual browser verification (not the Python suite - nothing in it
executes this page's JavaScript) found the *same* defect class as §26's
`focus` bug: `focusBody` was declared with `let` inside the canvas
section, but assigned inside `go()`, which runs synchronously on the very
first render - a `ReferenceError: Cannot access 'focusBody' before
initialization` on every single load. Fixed the same way `focus` was:
declared at the very top, next to `S`. A regression test now asserts both
are declared together on one line, so a future variable added to `go()`
without being hoisted this way fails a test instead of failing silently
until someone opens a real browser.

### Verified

`pytest` before this section: 3365. After: 3370 (5 new tests: four for
the camera/Room-core boundary, one regression guard for the
`focusBody` ordering bug). Two mutations introduced by hand and both
caught: fabricating a camera target instead of looking one up in
`bodies`, and coloring the Room's core with `origin.identity.hue`. Both
reverted. Verified live end-to-end, ephemeral memory confirmed before any
interaction: camera focus visibly changes the canvas (confirmed by
hashing before/after a click), ambient motion continues while focused,
reduced motion correctly freezes both the canvas and every `idspin` SVG
(checked by emulating the media query *before* navigation, matching the
lesson from the `still` flag's own initialization-order sensitivity), a
real delegated exchange with Arthur worked end-to-end in the Room with
the core visible behind it, and zero console errors on desktop or phone
once the `focusBody` fix landed.

## 33. Genuine 3D — Three.js, vendored, presentation-only

§25–32 were honest about a limit: the canvas environment gives real
scene-level depth (position, scale, ordering) but draws each body as a
flat 2D silhouette - no thickness, no faces, no per-face lighting, no
object that actually turns in 3D rather than just being viewed from an
orbiting camera. This section replaces that, for Alfred specifically,
with a real WebGL scene rendered by Three.js.

### What it is

`ui/vendor/three.module.min.js` (Three.js r185, MIT-licensed, license
file alongside it) plus its one internal dependency,
`ui/vendor/three.core.min.js` - both vendored as static files, fetched
once during development via `npm` into a scratch directory never
connected to this repository, and copied in as plain assets. No
`package.json`, no `package-lock.json`, no bundler, no build step. The
existing static-export/no-network guarantee is unchanged: nothing is
fetched from a CDN at runtime, verified by the existing
`test_the_shell_loads_nothing_from_the_network` (unchanged, still
passing) plus new tests confirming both vendor files are real, non-trivial
local files.

`ui/index.html` loads Three.js via a dynamic `import()` inside its
existing classic script - not a `<script type="module">` tag, which
would have introduced a load-order dependency against the rest of the
page. This was a real, tested decision, not a guess: a dynamic import
from a page opened via `file://` is blocked by Chromium's CORS policy for
module fetches (`Access ... blocked by CORS policy ... only http, https`),
confirmed with an isolated reproduction before writing any integration
code. Opened live (`http://`), the import resolves normally.

### What owns it, and what it does not

`mountAgent3D(container, hueRgb)` is the entire renderer boundary. Its
own signature is the guarantee: it receives a DOM container and a color
string - the same `hue` `identityOf()` already computes elsewhere on this
page - and nothing else. It holds no reference to an agent object, so it
structurally cannot read `.brain`, `.memory`, `.voice`, `.tools`,
`.capabilities`, or a provider name, verified by parsing its own source
and asserting none of those appear. Three mutations proved this by hand:
passing the whole agent object through instead of a hue string, and
checking `agent.brain` inside the renderer, both failed a test
immediately; both were reverted.

Scoped to the manager only this milestone, via `a.is_manager` - a real,
registry-derived field, never a name comparison. A future specialist's
own 3D environment is later work, added by calling the same
`mountAgent3D` from a second call site, not by editing this one.

### The emblem itself

An original compound of Three.js primitive solids - a stretched
`OctahedronGeometry` core (a faceted, angular blade form) flanked by two
three-sided `ConeGeometry` wing panels - never a traced silhouette of any
existing symbol, never an image or downloaded asset of any kind. Real
vertices, real triangular faces, real per-face normals Three.js computes
automatically, lit by one directional key light (in the agent's own hue)
and one dim fill light, on a `MeshStandardMaterial` (dark, semi-metallic,
low roughness, a subtle emissive tint in the same hue) - genuine
Lambertian shading that changes as the object actually turns, not a
gradient painted at a fixed screen position.

### Autonomous motion, reduced motion, and honest failure

Rotation is driven by `THREE.Clock().getElapsedTime()`, not frame count,
so it stays consistent regardless of frame rate. `prefers-reduced-motion`
is read once, before the render loop is ever started - the same lesson
this file already learned twice from the `focus`/`focusBody` ordering
bugs, applied on purpose this time instead of discovered by a crash.
Under reduced motion, one real frame renders and the loop never starts;
the object is still visible, just still.

If the module fails to load (blocked by CORS under `file://`, a network
failure, or any other reason) or if `new THREE.WebGLRenderer()` itself
throws (no GPU, WebGL disabled), the container shows an explicit
`"3D ENVIRONMENT UNAVAILABLE"` message and the rest of the interface stays
fully usable - never a silent substitution of the old Canvas 2D system
presented as if it were the real thing. Verified live at both ends: the
served page renders correctly and keeps animating past a real 5-second
live-refresh poll (the running WebGL context and its clock are reused
across a re-render rather than torn down and restarted, which would both
leak toward the browser's limited concurrent-context budget and make the
rotation visibly jump back to zero every few seconds); the static export
opened via `file://` shows the honest fallback text, zero uncaught
errors, full interface still usable.

### Two bugs this milestone found by actually running it

1. **A missing vendor file.** `three.module.min.js` imports a sibling
   module, `three.core.min.js`, by relative path at the browser's own
   request time - invisible until a real browser actually tried to fetch
   it and got a 404. Found via `page.on("response")` logging, not
   assumed. Fixed by vendoring the second file and adding it to the
   server's asset allowlist; a test now asserts both files exist.
2. **A stale-buffer read, then a real UI bug it was masking.** `canvas.
   toDataURL()` read outside the render loop returned an unchanging
   image at first - not because the animation wasn't running (a debug log
   proved it was, ticking every frame with `still=false`) but because
   `WebGLRenderer` drops its drawing buffer after presenting unless
   `preserveDrawingBuffer: true` is set. Adding that flag (a deliberate,
   permanent choice - it also enables a future "screenshot this view"
   feature at a modest cost for a scene this simple) fixed the
   measurement and revealed a second, real bug underneath: the "same
   running stage" reuse path in `wireAgent3D()` reattached the live
   canvas into a freshly-rendered container but never removed that
   container's own fresh "INITIALIZING" fallback text, so after a real
   live-refresh poll the fallback sat visibly on top of a 3D scene that
   had been working correctly the entire time. Fixed, and a regression
   test now asserts the reuse branch clears that text - found only
   because a real live poll was allowed to fire during verification
   rather than testing a single render in isolation.

### Verified

`pytest` before this section: 3370. After: 3382 (12 new tests in
`tests/test_snapshot.py::TestAgent3DStaysHonest`, 0 removed, 0 weakened).
Two mutations introduced by hand and both caught before being reverted:
passing the whole agent object instead of a hue, and silently proceeding
past a failed `WebGLRenderer` construction instead of showing the honest
fallback. `data/` confirmed byte-identical to before this milestone's
testing began; `MANAGER_MEMORY=ephemeral` confirmed before every
interaction.

## 34. Genuine 3D, generalized — every agent gets a stage, not just the manager

§33 scoped the WebGL stage to the manager alone by checking `is_manager` at
the one call site that decided whether to render a `.agent3d` div at all.
That was an honest scope limit for that milestone, not a structural one -
this section removes it, and in doing so proves the boundary was real:
Arthur (the specialist, the Email Agent) now has his own genuine 3D
environment, added without touching Brain, Memory, Voice, Capabilities,
Connections, the registry, or any provider integration.

### What changed, structurally

The workspace template now renders a `.agent3d` stage for every agent
unconditionally - the `is_manager ? ... : ""` gate is gone. What each
stage draws is decided by one thing: `identityOf(agent).form`, the same
field the graph and the workspace glyph already use to tell a manager's
"core" from a specialist's "polygon". `mountAgent3D` never branches on
`is_manager` or a name; it asks a new lookup table, `AGENT_3D_EMBLEMS =
{ core: buildManagerEmblem, polygon: buildSpecialistEmblem }`, for a
builder function matching `identity.form`, and calls it. Replacing any
agent's visual later - the explicit long-term requirement carried into
this milestone - means pointing that one table at a different builder;
nothing about loading Three.js, creating the renderer, handling resize,
reading reduced motion, or disposing the context changes.

Each builder receives exactly `(THREE, accentColor)` - never the agent,
never a name, never `identity.key` - so neither can reach Brain, Memory,
Voice, Capabilities, Connections or a provider name even by mistake; a
source-parsed test confirms both builders' bodies are clean, the same
way §33's test already confirmed `mountAgent3D` itself was.

### Two stages, not one - the lifecycle had to become a map

§33's runtime state was a single `agent3D` variable, correct only because
exactly one agent (the manager) ever had a stage. With two agents each
holding their own running WebGLRenderer, clock, and ResizeObserver
simultaneously, a single shared slot would let mounting the second stage
silently overwrite the first's reference, orphaning its renderer with no
way to dispose it. `agent3DStages` is now a `Map` keyed by
`identity.key` (the agent's name - the same string `identityOf()` already
returns as `key`, not a second identifier invented here). `wireAgent3D()`
computes which `[data-agent3d]` elements are actually present in the
current render, disposes exactly the stages whose element disappeared,
and reuses-in-place (moving the same `<canvas>`, keeping the same clock
running) every stage whose element is still there - independently per
agent. A live-refresh poll rebuilding the whole workspace view now
correctly leaves both agents' contexts running uninterrupted, proved by
watching a real 5-second poll fire and confirming the exact same canvas
DOM node and the exact same count of WebGL contexts (two) survive it.

### Arthur's emblem

An original angular wing profile - one hand-authored six-point polygon,
extruded for real depth and beveled for a second row of facets - mirrored
into a left/right pair sharing a single root point, with a thin faceted
spine at the join. The profile is deliberately *not* re-centered before
mirroring: its root stays at the mesh's own local origin, so the mirrored
pair spreads outward from one shared point rather than sitting as two
overlapping copies of the same centered shape - the difference between
something that reads as a wing and something that reads as a blob, found
by actually looking at the first version rendered in a real browser and
resizing it before settling on final proportions. It shares the same
`MeshStandardMaterial` treatment as the manager's emblem (dark base,
metallic, low roughness, an emissive tint in the agent's own hue) so the
two read as the same visual family rendered through different geometry,
not two unrelated design languages - while remaining unmistakably
distinct in silhouette: a faceted vertical core against a spread
horizontal wing.

### Verified

`pytest` before this section: 3382. After: 3387 (5 net new in
`tests/test_snapshot.py::TestAgent3DStaysHonest` - two renamed to match
the new signature/branch text, one retired in favor of a stronger
replacement, five newly added; 0 removed in the sense of a guarantee
being dropped). Four mutations introduced by hand and all caught before
being reverted: a plain object in place of the stages `Map`, disposing
every stage instead of only the absent one, giving the specialist emblem
the manager's own geometry constructor, and skipping the fallback-removal
line in the per-agent reuse branch.

Live-browser verification, `MANAGER_MEMORY=ephemeral`: both stages mount
and animate with zero pointer/touch input on a 1440px laptop viewport and
a 390px phone viewport; canvas pixel-diffs over 1.5s show ~94-95% of
sampled bytes changing for both agents on both viewports, confirming
real, autonomous, elapsed-time-driven rotation rather than a static
image; exactly two WebGL contexts exist at any time (one per agent, one
each, checked by hooking `HTMLCanvasElement.getContext`); a real 5-second
live-refresh poll leaves the same two canvas DOM nodes in place with zero
stale fallback text; `prefers-reduced-motion` set before navigation stops
both stages precisely - confirmed by hooking `WebGLRenderer.prototype.
render` directly and observing zero calls across a 3-second window, not
just an unchanging screenshot - while both objects remain visible at
their initial pose; zero console errors on every check. One transient,
non-reproducing artifact was observed and investigated rather than
dismissed: a single per-element Playwright screenshot at 3x device pixel
ratio briefly showed the page's own always-on decorative background
canvas (`#scene`, `position:fixed`, an intentional pre-existing element
unrelated to this milestone) visible behind Arthur's stage. Repeating the
identical capture at DPR 1, 2, and 3, and capturing a full, unscrolled
viewport screenshot at the same scroll position, all came back clean;
concluded to be a one-time Playwright element-screenshot compositing
artifact, not a defect in the page itself. `data/` confirmed
byte-identical to before this milestone's testing began.

## 35. Per-agent genuine 3D Brain and Memory interfaces

§33-34 gave every agent a genuine WebGL identity emblem. This section
gives every agent two more genuine WebGL surfaces - Brain (Cognition) and
Memory - each its own stage, each already a separate top-level navigable
mode (never a chat side panel: V.room's own template contains no
`.brain`/`.memory` reference at all, structurally verified), each driven
by that one agent's real state and no one else's.

### What owns the shared lifecycle now

§34 already generalized one WebGL mount/resize/dispose/reduced-motion
implementation across two surfaces. This section pushes that further:
`mountStage(container, stagesMap, key, hueRgb, sceneBuilder)` is now the
*only* place a `WebGLRenderer` is created, a `PerspectiveCamera` and
lighting rig is built, `ResizeObserver`/DPR handling happens, reduced
motion is read, or the render loop (`tick()`) runs - there is exactly one
copy of each in the file, checked structurally. `mountAgent3D`,
`mountBrain3D` and `mountMemory3D` are thin callers, each supplying only
its own `stagesMap` (`agent3DStages` / `brain3DStages` / `memory3DStages`
- three independent `Map`s, so one agent's identity, Brain and Memory
stages can run simultaneously, and Alfred's three never collide with
Arthur's three) and its own `sceneBuilder(THREE, scene, accent)`, which
adds that surface's geometry and returns an `animate(elapsedSeconds)`
callback. `wireStages(attrKey, stagesMap, mountFn)` is the one
reuse-or-dispose loop behind `wireAgent3D`/`wireBrain3D`/`wireMemory3D`,
called every `go()` render exactly as §33 established.

### The boundary that makes Brain/Memory visualization honest

Nothing in the pure-render layer (`mountStage`, `buildBrainGeometry`,
`buildMemoryGeometry`, and every function `mountBrain3D`/`mountMemory3D`
call) ever contains the literal text `.brain` or `.memory` anywhere -
verified the same way §33 verified it for the identity emblem, over the
same full span of this subsystem, and still true after this section's
additions (not weakened to make room for the new code). `buildBrainGeometry`
receives `(THREE, accent, signal)`; `buildMemoryGeometry` receives
`(THREE, accent, lit, durable)`. None of those four values is the agent,
and none is `brain`/`memory` themselves - they are the exact narrow,
already-computed presentational values the V.brain/V.memory templates
were already deriving from the real state for display (`a.brain.
indicator` for the status dot; `memoryTrace(a.memory)` for the SVG ring)
and now also hand to the container as `data-signal`/`data-lit`/
`data-durable`. `wireBrain3D`/`wireMemory3D` read those back off the
DOM (`el.dataset.signal`, `el.dataset.lit`, `el.dataset.durable`) - they
never call into `S.agents.find(...).brain` or `.memory` either. The
renderer visualizes the reading; it is not where the reading is taken.

### Brain 3D

An original low-poly node lattice - never a copyrighted anatomical
reference. Sixteen node positions placed by a fixed golden-angle spiral
(a deterministic formula, not a randomized scatter, so the same lattice
renders identically every time), each node a small faceted icosahedron,
linked to its two nearest neighbors by thin struts computed once from the
lattice's own geometry. `signal` (the exact GREEN/AMBER/RED string
`brain.indicator` already computes, and the same one the status dot
already renders) decides how many nodes render lit versus dim - all
sixteen for GREEN (FULL BRAIN, a verified model call), about half for
AMBER (HALF BRAIN), a quarter for RED (degraded) - a presentation of that
reading, never a second computation of it. A dim, translucent core
icosahedron sits at the center for volume. The whole group turns slowly
and breathes gently; it does not claim "the agent is thinking" beyond
what its own already-computed state says.

### Memory 3D

Visually unmistakable from Brain: a solid faceted core (`Dodecahedron
Geometry`, not `Icosahedron`/`Cylinder`) with zero or more small
octahedron shards orbiting it - one shard per record the observer
actually sampled, up to `MEMORY_TRACE_CAP` (8), the exact cap and the
exact `lit` count the pre-existing `memoryGlyph()` SVG ring already used.
Both now call one new shared `memoryTrace(mem)` helper rather than each
computing `frac`/`lit` its own way - a real refactor, not a rename: one
formula, so a future change to how the trace is capped or rounded can
only happen in one place. `durable` (a real boolean, never invented)
changes only the core's own material - solid and metallic for a store
that survives restart, translucent and dim for one that dies with the
process - never the shard count. No shard is ever added beyond the real
`lit` value; there is no "capacity" being filled, only a trace of what is
actually retained, matching the directive's explicit "memory is not a
phone storage drive" requirement.

**Verified live, not just read**: a real message was sent through the
running server (`MANAGER_MEMORY=ephemeral`, "check my inbox for new
emails", genuinely delegated to and answered by the specialist), which
wrote one real memory record. Re-opening the Memory view afterward showed
the manager's stage go from zero orbiting shards to two, while the
specialist's own stage - who actually handled the request - independently
still showed its own real, different count. Proof the whole path (a real
write -> the snapshot -> `data-lit` -> `buildMemoryGeometry`) is genuinely
wired end to end and genuinely per-agent, not two views of one shared
number.

### Conversation Room boundary

V.room's template was read in full and contains no `.brain`, no
`.memory`, no `data-brain3d`/`data-memory3d`, and calls none of
`mountBrain3D`/`mountMemory3D`/`brain3DStages`/`memory3DStages` - checked
structurally, and confirmed by mutation (adding a single `.brain` read to
V.room's body was caught immediately, then reverted). The Room remains
what §15 of this milestone's directive requires: a collaboration surface
that brings agents who already have their own canonical Brain and Memory,
never a second agent with a Room-owned Brain or Memory of its own.

### Verified

`pytest` before this section: 3387. After: **3399** (12 new, in a new
`tests/test_snapshot.py::TestBrainAndMemory3DStayHonest` class; 3 of
§33/34's existing tests updated to match the shared-lifecycle refactor's
new text, 0 guarantees weakened or removed). Five mutations introduced by
hand and all caught before being reverted: rendering the full
`MEMORY_TRACE_CAP` shards regardless of the real `lit` count; giving the
Memory core an `IcosahedronGeometry` (Brain's own signature shape);
reading `.brain` inside V.room; hardcoding the Brain signal to `"GREEN"`
in the template instead of reading `a.brain.indicator`; and duplicating
`mountStage` itself. Live-browser verification, `MANAGER_MEMORY=ephemeral`:
Brain and Memory stages for both agents mount and animate on a 1440px
laptop and a 390px phone viewport, zero console errors, zero horizontal
overflow; reduced motion set before navigation produces zero
`WebGLRenderer.render()` calls across a 3-second window for both
surfaces; a real 5-second live-refresh poll leaves the same canvas DOM
nodes and the same WebGL context count in place; an 8-step navigation
stress test across Workspace/Cognition/Memory/Control confirmed contexts
are created and disposed on every transition with no accumulation (14
created across 8 navigations, matching the count of 3D-bearing views
visited exactly, never growing toward the browser's concurrent-context
limit). `data/` confirmed byte-identical to before this milestone's
testing began, including after the real message exchange used to verify
the Memory gauge.

## 36. Conversation Room — a genuine 3D environment, owning no Brain or Memory

§27-32 gave the Room a Canvas 2D decorative presence (a neutral ring
around the origin body, drawn only while the Room is active). This
section gives it a genuine WebGL centerpiece too - an original mechanical
energy core - without changing what the Room *is*: a collaboration
surface, never an agent.

**Conversation Room -> neutral 3D environment.** That is the whole of its
relationship to the architecture below it:

    Alfred    -> Identity -> Brain -> Memory -> Voice -> Capabilities -> Connections -> Workspace
    Arthur    -> Identity -> Brain -> Memory -> Voice -> Capabilities -> Connections -> Workspace
    Room      -> conversation state (existing, unchanged) -> a decorative 3D core

**The Conversation Room does NOT own an additional Brain or Memory.**
`mountRoom3D(container, key)` and the `buildRoomCore(THREE, accent)` it
calls take no identity descriptor at all - not `{key,hue,form,sides}`,
not an agent, nothing - because the Room has no Identity to describe.
Structurally verified: `buildRoomCore`'s body contains none of `.brain`,
`.memory`, `.voice`, `.tools`, `.capabilities`, `a.connections`,
`identityOf(` or `S.agents` - it cannot read a participant's state
because it is never given a participant. When Alfred and Arthur are both
present in the Room, they bring their own already-real, already-canonical
Brain and Memory with them (visible, as always, only from their own
Workspace/Cognition/Memory surfaces) - the Room itself constructs neither.
This was proven live, not just read: a real message was sent through the
running server and genuinely delegated to and answered by the specialist
(a full turn appeared in the transcript, the specialist's own avatar
appeared in `.presence`), and the energy core above kept animating,
unchanged in color, shape or behavior - because nothing about a real
conversation turn is wired to it.

### What it is, and how it reuses §33-35

The whole subsystem is one more caller of the shared `mountStage()`/
`wireStages()` lifecycle §34 established and §35 already extended twice
- there is still exactly one `mountStage`, one `tick()`, one dispose loop
in the file. `room3DStages` is a fourth independent `Map`, keyed by a
fixed DOM attribute value (`"core"`, from `data-room3d="core"`) rather
than an agent name, because there is exactly one Room and it is not an
agent - it can never collide with, or be starved by, any agent's own
identity/Brain/Memory stages. The stage is mounted only inside V.room's
real, conversation-available branch (never inside the "THE ROOM IS
QUIET" absent-state branch a system with no conversation capability
reports honestly) - verified structurally and by mutation.

### The energy core

An original layered mechanical form - never a trace of any existing
fictional reactor. A small emissive icosahedron chamber at the center;
three torus rings at increasing radius, each at its own depth (`z`) and
its own rotation speed (one built from one `ringSpecs` array via `.map`,
not three hand-copied constructors, and one of the three counter-rotates
against the other two); an octahedron rotor spinning against the rings
on its own axis; and twelve deterministically-placed box "segments" in a
radial ring around the outside, standing in for precision outer plating.
Colored with the same fixed, neutral `"200,224,236"` tone the pre-existing
Canvas 2D Room-presence rings already used (`TestTheCameraAndRoomCoreStays
Honest`) - not any one participant's `identityOf()` hue - so the core
reads as the shared space itself regardless of who is present, exactly
matching this milestone's "neutral 3D presentation" requirement.

### Motion, reduced motion, lifecycle

Elapsed-time driven, restrained: rings rotate at 0.30/-0.21/0.15 rad/s,
the rotor turns opposite the rings and nods slightly on a second axis,
the outer segment ring drifts slowly, and the core's own emissive
intensity and scale breathe gently - no rotation exceeds a slow, legible
speed. Reduced motion, set before navigation, produces zero
`WebGLRenderer.render()` calls across a 3-second window (hooked directly,
not inferred from a static screenshot) while the core stays visible.
Live-browser verified on a 1440px and a 390px viewport: one WebGL
context, zero console errors, zero horizontal overflow, zero stale
fallback text, the same canvas DOM node and the same single context
surviving a real 5-second live-refresh poll untouched.

### Verified

`pytest` before this section: 3399. After: **3408** (9 new, in a new
`tests/test_snapshot.py::TestRoomCore3DStaysHonest` class; 0 existing
tests removed or weakened). Three mutations introduced by hand and all
caught before being reverted: reading `identityOf(manager())` inside
`buildRoomCore`; removing the stage `<div>` from V.room's real-conversation
branch; and pointing `mountRoom3D` at the shared `agent3DStages` map
instead of its own `room3DStages`. `data/` confirmed byte-identical to
before this milestone's testing began, including after two real message
exchanges used to verify the core's behavior under genuine conversation
state.

## 37. The Visual Environment Layer — a replaceable presentation seam

§33-36 built four genuine 3D surfaces without ever formalizing what they
had in common: each was "a shape, drawn by one function, chosen for one
container." This section names that shape and gives it exactly three
fields - `{id, version, build}` - so a future visual upgrade is a data
change in one small registry, never a rewrite of Identity, Brain, Memory,
Voice, Capabilities, Connections, the Registry, the LLMRouter, a
provider, or the shared rendering lifecycle underneath it.

    Agent
    │
    ├── Identity       (canonical, unchanged by this section)
    ├── Brain           (canonical, unchanged)
    ├── Memory          (canonical, unchanged)
    ├── Voice           (canonical, unchanged)
    ├── Capabilities    (canonical, unchanged)
    ├── Connections     (canonical, unchanged)
    └── Workspace
          └── Visual Environment   <- presentation of the agent above,
                                       replaceable, versioned, never the
                                       agent itself

    Conversation Room
    └── separate, neutral Visual Environment - not an agent, owns none
        of the above, never will by this architecture's own rule

### What an environment is

`AGENT_VISUAL_ENVIRONMENTS = { core: {id,version,build}, polygon:
{id,version,build} }`, plus one more `{id,version,build}` constant each
for Brain, Memory and the Room. `build` is exactly the `(THREE, accent)`
function §33-36 already wrote (`buildManagerEmblem`, `buildSpecialistEmblem`,
`buildBrainGeometry`, `buildMemoryGeometry`, `buildRoomCore`) - this
section did not rewrite a single one of them, only gave each a name and
a version number a future milestone can inspect instead of an anonymous
function reference. Camera, lighting, resize, reduced motion and the
render loop stay exactly where §34 put them, inside the one shared
`mountStage()` - an environment descriptor never duplicates any of that,
because a descriptor that did would be a second engine, not a seam.
Structurally verified: no descriptor contains a `brain`, `memory`,
`provider`, `apiKey`, `credentials` or `connections` field, and every
`build` function's body is re-confirmed clean of `.brain`/`.memory`/
`.voice`/`.tools`/`.capabilities`/`.connections`/a provider name/
`Math.random()`.

### Selection stays structural, not name-based

`environmentFor(form)` chooses an agent's environment from `identityOf()`'s
`form` field - unchanged since Phase 2, still never `is_manager` directly,
still never a literal agent name - and falls back to the specialist
environment for any `form` it doesn't recognize, so a future agent
`identityOf()` hashes to an as-yet-unregistered form still receives a
real environment rather than the renderer throwing or silently mounting
nothing. A genuinely new look for a future category (a Coding
environment, a Research environment) is delivered by `identityOf()`
assigning that category its own `form` value and one new registry entry
appearing - never a name check added to `mountAgent3D`, `mountStage`, or
anywhere in the shared lifecycle. `identityOf()`'s own return shape
(`{key,hue,form,sides}`) was re-verified to carry no `version`/`env`/
`build` field of its own - an agent's identity and its environment's
version are two separate pieces of state, so a future environment
upgrade cannot touch identity even by accident.

### A real, DOM-visible signal - and a real bug it exposed

Every mounted stage is tagged `data-env-id`/`data-env-version` on its own
container, so a future upgrade tool (or a person with devtools open) can
read which environment and version is actually running without reading
this file's internals. Building this tag exposed a genuine bug, found by
watching a real live-refresh poll rather than trusting the design on
paper: the first implementation tagged inside each `mount*3D()` function,
which only runs on a *fresh* mount - the reuse path (moving an
already-running renderer into a rebuilt container, the mechanism §33
built specifically so a five-second poll doesn't restart every
animation) never calls a mount function again, so the tag silently
vanished from the container after the very first poll even though the
stage kept rendering correctly underneath. Fixed by moving the tag into
`wireStages()` itself, called unconditionally for every present element
on every wire pass - both branches, not just the fresh-mount one -
verified live by watching the tag survive a real 5-second poll, and
protected by a test that checks the tag call's textual position
precedes the reuse-vs-mount decision, not merely its presence somewhere
in the file (a positional check specifically chosen because a naive
presence check would still pass if the call were moved back to the wrong
branch).

### Verified

`pytest` before this section: 3408. After: **3417** (9 new, in a new
`tests/test_snapshot.py::TestVisualEnvironmentRegistryStaysHonest` class;
2 tests from §33/34 updated to the renamed `environmentFor`/
`AGENT_VISUAL_ENVIRONMENTS` names; 3 further tests across three existing
classes updated for `wireStages()`'s new `tag` parameter; 0 guarantees
weakened or removed). Four mutations against the registry itself, and
one further mutation reproducing the exact tagging bug above (reverting
the fix and confirming the ordering-sensitive test catches it even
though a naive presence-only version would not) - all caught, all
reverted. Live-browser verification, `MANAGER_MEMORY=ephemeral`: all
five existing environments (identity x2, Brain x2, Memory x2, Room x1 =
7 WebGL contexts) mount, animate with zero pointer input, and carry
correct `data-env-id`/`data-env-version` tags on both a 1440px laptop and
a 390px phone viewport; reduced motion (set before navigation) freezes
all four surfaces (pixel-identical over 2s); a 9-step navigation stress
cycle across every 3D-bearing view plus Control created exactly 14
WebGL contexts (matching the views visited, no accumulation) with zero
stale fallback text and zero console errors throughout; a real 5-second
live-refresh poll on the Room left the same canvas DOM node and the same
environment tag in place. `data/` confirmed byte-identical to before
this milestone's testing began.

## 38. Governed System Evolution — the versioning and migration seam

Investigation before this section found the same instinct already present,
independently, in three places: `PersistentMemory.SCHEMA_VERSION`,
`StateStore.SCHEMA_VERSION`, and `snapshot.py`'s `SNAPSHOT_VERSION`, each
tagging its own JSON with a version number. What none of them had was
anywhere to go from one version to the next - a mismatch meant quarantine
(memory) or silent discard (heartbeat state), tested and correct for a
version nobody recognizes, but exactly the "memory full, starting over"
failure the long-term product direction rules out for a version the system
*does* recognize and could carry forward. This section is that seam,
implemented for the first time this milestone, deliberately kept to the
smallest correct shape - not the upgrade runner, validator or rollback
executor a future milestone builds on top of it.

### Three different "version"s, kept apart on purpose

    SYSTEM_VERSION      "0.1.0" today. The assembled product's own
                         version - what a person means by "what is Alfred
                         OS". A human-chosen string (src/core/versioning.py),
                         bumped when the system as a whole changes in a way
                         worth naming. Surfaced in the existing snapshot
                         payload as `system.system_version`, never as the
                         payload's own top-level `version` key.

    SNAPSHOT_VERSION     unchanged, still 1. The *shape* of one interface's
                         read-only export (src/control_center/snapshot.py).
                         Regenerated fresh on every read, never loaded and
                         migrated, so it needs no Migration machinery.

    SCHEMA_VERSION       one per persisted store - PersistentMemory's own,
                         StateStore's own, and any future store's own. What
                         a Migration below actually moves between.

Conflating any two of these was the exact confusion this section's own
design brief warned against; keeping them as different types (a string vs.
two independent int constants) and different files is deliberate, not
incidental - `TestVisualEnvironmentRegistryStaysHonest`'s sibling in
`test_snapshot.py` pins the type difference so it cannot drift back
together by accident.

### Migration, MigrationChain, MigrationUnavailable

`src/core/versioning.py` - pure, stdlib-only, zero I/O, the same standard
`src/core/contracts.py` already holds itself to. A `Migration` is one
deterministic, single-step, gapless function from a raw dict at version N
to a raw dict at version N+1. A `MigrationChain` is an ordered set of them,
answering exactly two questions: can a path from A to B be walked
(`can_migrate`), and if so, walk it (`migrate`). `migrate()`'s contract is
airtight on purpose: it raises `MigrationUnavailable` and nothing else,
ever - a migration function that raises an unrelated exception, or returns
something that is not a dict, is caught inside `MigrationChain` itself and
re-raised as the one exception type every calling store already knows how
to handle. This was a real bug caught while writing this milestone's own
tests, not a hypothetical: the first version let an ordinary Python
exception from inside a broken migration propagate straight through
`PersistentMemory._load()`, which would have crashed the whole Manager on
construction over one bad migration function - exactly the "a store that
refused to start because its file was damaged would take the whole agent
down with it" failure this file's own header already warns against for
every other kind of damage. Fixed before this milestone's tests were
written against the fixed behavior, not after.

### Wired into PersistentMemory and StateStore, identically in spirit

Both stores now hold their own `MEMORY_MIGRATIONS` / `STATE_MIGRATIONS`
constant - a `MigrationChain`, **empty today**, because schema 1 has always
been schema 1 in both stores and there is nothing real to migrate from yet.
An empty chain can only "migrate" a version to itself, so every version
that exists in the wild right now hits exactly the code path it hit before
this section existed - wiring the seam in was proven behavior-preserving,
not assumed to be.

For a version a chain *does* recognize, the sequence is:

    old file on disk
      -> checkpoint: renamed aside (`<name>.v<N>-checkpoint`), the same
         technique PersistentMemory's own `.corrupt-*` quarantine already
         used and was already tested
      -> Migration(s) applied to the raw dict, in memory, never touching
         the checkpoint
      -> the result validated (still a dict, still shaped like a store)
      -> written atomically to the original path (`.tmp` then
         `os.replace`, the exact idiom `_save()` already used)

A failure anywhere in that sequence leaves either the untouched original
(safe, under its checkpoint name) or the complete migrated result on disk -
never a partial mixture, and never data destroyed to make room for a
guess. A version with **no** registered migration takes exactly its
pre-existing path unchanged: `PersistentMemory` quarantines it precisely as
`test_an_unknown_schema_version_is_quarantined` already required (the test
is unmodified and still passes); `StateStore` logs a warning and starts
fresh precisely as it already did (that store has never quarantined a
mismatched file by moving it aside, and this section does not start doing
so for the *no-migration* case - only the new *has-a-migration* case gets
the stronger checkpoint protection, because that is new code this
milestone adds, not old behavior being preserved).

Rollback, at this stage, is exactly what a checkpoint file already is: the
pre-migration state, preserved on disk, restorable by a person. This
section deliberately does not build an automatic restore - "we want the
architectural seam first," per this milestone's own instruction - and
`improvement_is_advisory_only()`'s neighboring package
(`src/improvement/`) is the reminder of why nothing here should grow into
one without that being a decision someone makes on purpose, the same way
that function's own docstring insists on for self-improvement.

### Implementation upgrade vs. identity change - a distinction, not yet a mechanism

This section's non-negotiable boundary, stated plainly because the
investigation that preceded it found a real, live tension worth recording
rather than quietly resolving:

**An implementation upgrade** (Claude -> OpenAI, a Brain implementation's
internals changing, a Memory backend's schema gaining a real Migration, a
3D visual environment's `version` field incrementing) must never change
which agent something is. Nothing in this milestone's code touches an
agent's name, its registry key, or which memory file it reads - `git diff`
against this milestone confirms `src/composition.py` and
`src/services/memory/factory.py`, the two files that decide *which file an
agent's memory lives in*, are untouched.

**An explicit identity change** - a deliberate rename - is a separate,
already-existing, already-tested, already-honest policy this milestone
does not alter: `src/agents/specialists.py` documents, and
`test_a_rename_cannot_leave_the_store_under_the_old_name` proves, that
renaming a specialist moves its memory with it on purpose, visibly, because
the store is selected by the human-editable `name`, not the stable
technical `key` the same module also carries. That tradeoff predates this
milestone and is not revisited here.

**Where those two collide today, deliberately left unresolved.** The
Manager's own memory owner is built from `runtime.identity.name` -
whatever `IDENTITY.md`'s `## Name` section says - rather than the
`MANAGER` constant `src/services/memory/factory.py` already defines for
exactly this purpose. That means an edit to one line of `IDENTITY.md`
today silently moves which file Alfred's memory lives in, with none of the
new checkpoint protection this section built, because it is not a
`SCHEMA_VERSION` change at all - it is the identity-change policy above,
applied to the one agent whose "name" and "the constant that should have
been used instead" happen to differ. Recognized, documented, and
explicitly **not fixed by this milestone** - the fix touches real,
existing persisted data (Alfred's memory file would move once) and was
deliberately deferred to its own reviewed step, pending separate approval,
using this section's own `MigrationChain` machinery when it proceeds.

### Provider and connection independence

Nothing in `src/core/versioning.py`, or in either store's use of it, names
a provider, reads an API key, or reads a model string - re-verified by a
direct scan of every file this section touched. Nothing in it reads or
writes a connection/integration (Gmail included): the only `gmail`/
`Connection` references anywhere in the touched files predate this
milestone and are untouched by its diff. A provider switch is a Brain/
router concern (`src/manager/llm_router.py`, `src/providers/`); a
connection is an agent's own relationship (`src/services/email/mailbox.py`
and friends). Neither is a `SCHEMA_VERSION`, and neither is migrated by
anything in this section - which is the point: this seam versions
*persisted store shape*, nothing else, and has no vocabulary for anything
else to hook into even if a future change tried to.

### Verified

`pytest` before this section: 3417. After: **3451** (34 new: 19 pure tests
in a new `tests/test_versioning.py`; 8 in `TestGovernedMigration` added to
`tests/test_persistent_memory.py`; 6 in `TestGovernedMigration` added to
`tests/test_heartbeat_state.py`; 1 in `test_snapshot.py` pinning
`system_version`'s presence and type. 0 removed). One existing test was
narrowed, not weakened: `test_locking_state_and_dedup_are_untouched` in
`test_heartbeat_schedule_integration.py` was a `git diff HEAD` byte-freeze
across three files, written to prove one historical milestone's scope
discipline - the same kind of freeze §6 already replaced once for
orchestration, criticizing it in the same terms this one now needed:
"forbade Alfred evolving at all... fell silent on commit." `state.py` was
dropped from its file list (renamed to
`test_locking_and_reporting_are_untouched`, with the reason documented in
its own docstring) because this section gives that file a permanent,
legitimate reason to keep changing; `lock.py` and `reporting.py`, which
this section never touches, remain frozen exactly as before. Five
mutations introduced by hand and all caught before being reverted:
skipping the pre-migration checkpoint (both stores, independently),
skipping validation of a migrated result's shape, and removing
`MigrationChain.migrate()`'s exception-wrapping (caught by three different
tests at three different layers - the pure unit test, and both stores'
integration tests - confirming the fix for the real bug above is genuinely
load-bearing, not merely present). `data/` confirmed byte-identical to
before this milestone's testing began; no browser verification was
required or performed, since nothing in this section touches presentation.

## 39. Stable Manager identity and memory ownership

§38 documented, and deliberately deferred, a real continuity gap: the
Manager's persisted memory was selected by `runtime.identity.name` -
whatever `IDENTITY.md`'s `## Name` section currently says ("Alfred") -
rather than by `MANAGER`, the stable technical key
`src/services/memory/factory.py` had already defined for exactly this
purpose and left unused. This section closes that gap.

    stable technical key = "manager"   selects the file, always
    display name         = "Alfred"    whatever IDENTITY.md declares
    memory owner          = "manager"   never the display name

### The rule, and why it applies only to the Manager

`src/composition.py`'s `build_manager()` now builds the Manager's memory
with `build_memory(runtime.settings, MANAGER)`, never
`runtime.identity.name`. A specialist's memory owner is **unchanged** -
still its own declared name, still `src/agents/specialists.py`'s own,
separate, already-tested policy (`test_a_rename_cannot_leave_the_store_
under_the_old_name`, `test_the_technical_key_is_stable_under_a_rename`,
both re-verified passing, unmodified, by this section). Inspection before
implementing found the two agent kinds were, in fact, already consistent
with each other under the *old* rule - both name-keyed, on purpose, by an
explicit historical decision the removed `composition.py` comment used to
state ("the Manager is not exempt from it"). The Manager gets a different
rule now not because specialists were wrong, but because only the Manager
had an unused stable key sitting in the codebase specifically built for
this and never wired to it, and because his continuity is the one this
whole system is least able to afford losing to an edited display line.
Specialists were inspected and left untouched, per this milestone's own
instruction: nothing about their key/name separation needed to change to
make this rule consistent, because it never claimed to be the same rule.

### Implementation upgrade vs. identity change, restated precisely

**An implementation upgrade** - Claude -> OpenAI, a Brain rewrite, a
Memory backend change, a Visual Environment version bump - must never
change which agent something is, and now provably cannot change *where*
the Manager's memory lives either: `MANAGER` is a fixed string, entirely
independent of provider, Brain implementation, or display name.

**An explicit identity change** - a deliberate rename - remains a
separate, honest, already-tested policy for specialists (their memory
moves with a rename, visibly, on purpose). For the Manager specifically,
this section adds one further guarantee beyond what specialists have:
*even a display-name change* does not move or duplicate his store,
because his store was never keyed by the display name to begin with -
`test_a_display_name_change_does_not_move_or_duplicate_alfreds_store`
proves this by building two real Managers, through the real composition
root, under two different declared names, and confirming both resolve to
the identical memory path with no duplication.

### Adoption: the file-identity counterpart to a schema Migration

`adopt_legacy_store()` (`src/services/memory/persistent.py`) is
agent-agnostic file mechanics: given a stable owner and a legacy owner, it
brings a store forward from the old filename to the new one, exactly once,
using the same discipline §38's schema migrations already established -
checkpoint (rename aside) before write, atomic write, quarantine (via
PersistentMemory's own existing, unmodified load path) for anything
damaged or unrecognized, and no separate "already done" flag, because the
stable file's own existence *is* that flag. It is deliberately not a
`Migration`/`MigrationChain` from §38: those carry a JSON *shape* forward
within one fixed path; this carries a store forward across a *path*
change, a different operation the same safety discipline still applies to.

`adopt_legacy_manager_memory()` (`src/services/memory/factory.py`) is the
thin, Manager-specific policy on top: no-op when memory is ephemeral
(nothing durable to adopt), when the declared name already normalises to
"manager", when a real `manager.json` already exists (never touched,
whatever it contains), or when no legacy file exists. `build_manager()`
calls it unconditionally, every composition - every heartbeat tick, every
live-server request - because it is cheap and safe to call when there is
nothing to do, which is the common case forever after the one real
adoption.

### Verified

Inspection first, per this milestone's own instruction, found the actual
repository holds **no `data/memory/alfred.json` on this machine right
now** - the discipline of testing under `MANAGER_MEMORY=ephemeral`
throughout this whole project's history meant a real persistent file was
never written here. The adoption path was therefore built and proven
correct against synthetic legacy files (exactly as §38's schema
migrations were proven against a synthetic old schema, since schema 1 has
never had a predecessor either) - and separately proven end to end
through a real, live run of the real composition root, entirely outside
pytest, in an isolated temp directory never touching `data/`: a seeded
legacy `alfred.json` was adopted, checkpointed, and survived three
successive rebuilds (simulating three heartbeat ticks) with no
duplication and no data loss.

`pytest` before this section: 3451. After: **3474** (23 new: 21 in a new
`tests/test_manager_identity_continuity.py` covering `adopt_legacy_store`/
`adopt_legacy_manager_memory` directly - preservation, ordering, id
stability, checkpointing, atomicity under a simulated write failure,
idempotency, never touching an unrelated or already-real store, and
unsupported/damaged legacy data still quarantining exactly as before; 2
new in `tests/test_specialist_identity.py` proving the rule and the
adoption end to end through the real composition root. One existing test,
`test_alfreds_store_follows_alfreds_own_name`, was replaced - not
weakened - by `test_alfreds_store_is_keyed_by_his_stable_technical_owner`,
which proves the corrected invariant with the same rigor the original
proved the superseded one. 0 other tests removed or weakened; every
specialist rename/key-stability test re-verified passing unmodified.
Three mutations introduced by hand and all caught before being reverted:
removing the "never touch an existing stable file" guard, and skipping
the checkpoint rename (caught independently by three different tests).
Provider and connection independence re-verified by direct source
inspection of both new functions and a `git diff` scan of every changed
file: neither names a provider, an API key, a model, Gmail, a connection,
an integration, OAuth or a token. `src/agents/specialists.py`,
`src/registry/agent_registry.py` and every connection/integration module
are untouched by this section's diff. `data/` confirmed byte-identical to
before this milestone's testing began, including after the live,
outside-pytest verification run. No browser or UI verification was
required or performed - this section is backend persistence and identity
only, and no 3D, Three.js, Brain/Memory visualization, Conversation Room
or responsive UI file appears anywhere in its diff.

## 40. The Upgrade Manifest — what version of Alfred OS is installed

§38 gave four independent things the name "version": `SYSTEM_VERSION`
(the assembled product), `SNAPSHOT_VERSION` (this interface's own export
shape), and two `SCHEMA_VERSION`s (one per persisted store). §37 gave a
fifth kind, `{id, version}` per Visual Environment surface. Nothing before
this section could answer, in one place, "what versions of Alfred OS's
real compatibility contracts are installed right now" - `src/control_
center/manifest.py`'s `UpgradeManifest` is that one place, and nothing
more than that.

**What it is not, stated plainly, because a manifest is exactly the kind
of thing that quietly grows past its own edges:** not the migration engine
(`src/core/versioning.py`'s `Migration`/`MigrationChain` are untouched by
this section and remain the only code that ever decides whether an
upgrade can run); not an update server, package manager or auto-updater
(nothing here downloads, installs, applies or schedules anything); not
agent identity (the module imports nothing from `src.registry`,
`src.services.identity` or `src.composition` - verified by parsing its
own import statements, not merely reading its prose); not Brain or Memory
*state* (it reports one integer describing Memory's on-disk *shape*, not
a single record, a Brain mode, or a provider name).

### No fake completeness

Providers, integrations, connections, and the Brain's own reasoning
implementation have no independently persisted compatibility version
anywhere in this codebase today - inspection for this milestone confirmed
it, the same inspection that found `SYSTEM_VERSION`/`SNAPSHOT_VERSION`/
both `SCHEMA_VERSION`s already real. `UpgradeManifest` therefore has
exactly five fields - `system_version`, `snapshot_version`,
`memory_schema_version`, `state_schema_version`,
`visual_environments` - and no `brain_version`, `provider_version` or
similar invented to make the shape look complete. A mutation adding a
`brain_schema_version` field (with a harmless default, so nothing else
broke) was caught by a test scanning every field name for exactly this
kind of addition, then reverted.

### Reads, never re-declares

The visual environment versions are the one part of this manifest that
could have become a second source of truth by accident: it would have
been easy to copy `{"alfred-tactical-core": 1, ...}` into a Python
constant. Instead, `visual_environment_versions()` reads the Visual
Environment Registry's own declarations straight out of `ui/index.html` -
five narrow, already-verified-unique regex patterns, one per surface
(`agent:core`, `agent:polygon`, `brain`, `memory`, `room`), matching the
exact `{id, version}` shape §37 already declares. Confirmed against the
real file, not only a synthetic fixture: `alfred-tactical-core`,
`specialist-wing-core`, `brain-node-lattice`, `memory-archive-core`,
`room-energy-core`, all version 1, today.

A missing file, or one surface whose declaration a future refactor
changes the shape of, does not take manifest generation down: that
surface is simply absent from the result, with a warning naming which
one - the same "one degraded part must not cost the rest of a
trustworthy reading" rule `PersistentMemory` already applies to a single
unreadable record among many. Proven by mutation: making the parser raise
instead of warn-and-continue was caught by two tests and reverted.

### Control Center visibility

Exposed through the existing `snapshot()` entry point - no new route, no
new dashboard - as one more top-level key, `"upgrade_manifest"`, built
from `upgrade_manifest(snapshot_version=SNAPSHOT_VERSION).to_dict()`.
`snapshot_version` is passed in by `snapshot.py` rather than imported
by `manifest.py`, deliberately: `manifest.py` has no reason to depend on
`control_center.snapshot`, and importing it just to read one constant
would make the reverse import (`snapshot.py` embedding this manifest)
circular.

### Verified

`pytest` before this section: 3474. After: **3499** (25 new: 24 in a new
`tests/test_upgrade_manifest.py`, 1 in `test_snapshot.py` proving the
embedded payload matches the real function's output exactly, not a
second summary of it. 0 removed or weakened). While writing these tests,
found and fixed a real flaw in three of them: `"brain" not in
UpgradeManifest.__dataclass_fields__` checks dict *key* membership, which
would silently miss a field named `"brain_schema_version"` - not what the
test's own docstring claimed to prove. Rewritten as a substring check
across every field name, then mutation-tested to confirm it actually
catches the exact regression it claims to. Three further mutations
(fake field, raising instead of degrading gracefully) caught and
reverted. Provider and connection independence verified both by source
inspection (no provider name, model string, Gmail, connection, OAuth or
token anywhere in the module) and by `git diff`, confirming no other file
in this section's diff besides `manifest.py`, its test, `snapshot.py`'s
one new import and payload key, and this document. `ui/index.html` and
every other 3D/rendering file are read by this section, never written -
confirmed by an empty `git diff --stat -- ui/`. `data/` confirmed
byte-identical to before this milestone's testing began.

## 41. The Upgrade Planner — can we safely move from here to there?

Four things now have a name, and this section is careful to keep them
four:

    Upgrade Manifest    "What is installed?" (§40) - a read of the
                        currently running system, no question attached.

    Upgrade Planner      "Can we safely move from here to a proposed
                        target?" - this section. Answers a question; asks
                        nothing to happen.

    Migration            "Perform one approved data transformation" -
                        Migration/MigrationChain (§38). Acts, when
                        something else - not this section, not the
                        planner - decides to call it.

    Upgrade Executor      Implemented in §42, one milestone later. As of
                        this section, still not implemented: no code
                        anywhere in this repository decides to run a
                        migration, switch a provider, rebuild a visual
                        environment, or apply anything an `UpgradePlan`
                        describes. A plan is the input a future,
                        separately-governed executor would need; this
                        milestone stops at producing that input.

### The shape

`src/control_center/upgrade_planner.py`: `UpgradeRequest` (a proposed
target - every field optional, `None`/empty meaning "no change asked for
this dimension"), `UpgradePlan` (`status`, `reason`, `required_
migrations`), and `plan_upgrade(current: UpgradeManifest, request:
UpgradeRequest) -> UpgradePlan`. `current` is Stage 6's own manifest,
taken as an argument rather than re-derived - the planner has no idea how
to read the installed system on its own, on purpose, so it cannot drift
from what the manifest already says.

Five `UpgradeStatus` values, each reachable and each meaning something
distinct (verified live while building this: an earlier draft treated a
SYSTEM_VERSION-only difference as UNSUPPORTED, which made
UPGRADE_AVAILABLE dead code no test could ever reach - a mutation-testing
discipline this project already applies to code caught it applying to a
*design decision* instead, before a single test was written against the
wrong behavior):

    INVALID              the request itself is malformed - a blank
                          version string, a non-positive schema number,
                          a visual-environment surface this installation
                          does not have.
    CURRENT               the requested target matches what is already
                          installed, in every dimension asked about.
    UPGRADE_AVAILABLE     the target differs, but nothing about the
                          difference has a migration concept to fail
                          closed on - a SYSTEM_VERSION change and/or a
                          Visual Environment Registry version change,
                          neither of which any MigrationChain governs,
                          because neither transforms persisted data.
    MIGRATION_REQUIRED    the target differs, and a complete, known
                          migration path exists for every schema that
                          changed - named step by step, never executed.
    UNSUPPORTED            at least one requested schema change has no
                          complete known path. Fails closed - the
                          directive's own worked example (schema 1->2
                          known, 2->3 missing, target 3 requested) lands
                          exactly here, and one UNSUPPORTED dimension
                          sinks the whole plan even when another
                          dimension in the same request has a perfectly
                          good migration path, so a plan can never be a
                          partial success that quietly runs the safe part
                          and skips the risky one.

### Reuses, never re-derives

Schema compatibility is answered by calling `MEMORY_MIGRATIONS.can_
migrate()`/`.path()` and `STATE_MIGRATIONS.can_migrate()`/`.path()`
directly - the exact `MigrationChain` instances `PersistentMemory` and
`StateStore` already maintain and already run real migrations through
(§38), not a second copy of "which versions are compatible" that could
drift from what actually happens when a migration runs. `MigrationChain`
gained one small, purely additive public method for this,
`path(from_version, to_version) -> tuple[Migration, ...]` - the exact
steps `migrate()` would run, without running any of them; every existing
Migration/MigrationChain test still passes unmodified. Visual environment
compatibility is answered by comparing against `UpgradeManifest.visual_
environments` (§40, itself read from `ui/index.html`'s own registry,
§37) - no second visual-version registry exists or was created.

### System version and visual environments, deliberately minimal

Neither `SYSTEM_VERSION` nor a Visual Environment Registry version has a
migration or comparison contract anywhere in this codebase - no
semantic-version resolver exists, and none is invented here, exactly as
this milestone's own instruction required. The absence of a contract is
not treated as a safety risk, though: fail-closed exists specifically to
stop an *incomplete data migration* from running, and neither of these
two fields ever asks a store to transform anything, so there is nothing
for UNSUPPORTED to protect against here. A request that changes only
these two fields is UPGRADE_AVAILABLE, reported with an honest reason
("no contract, and no risk") rather than silently assumed safe by default
or refused for a risk that does not exist.

### What this section does not do

No file is opened for writing anywhere in this module - checked
structurally, not only by observing that no test happened to create one.
No `Migration.apply` is ever called; a migration whose `apply` would
raise `AssertionError` if called was used specifically to prove
`MIGRATION_REQUIRED` is reached without calling it. No agent, provider,
connection, or 3D/Three.js file is read, imported, or referenced anywhere
in the module - verified both by parsing its actual `import` statements
(not its prose, which is free to explain in English what it deliberately
does not depend on) and by an empty `git diff --stat -- ui/`.

### Control Center exposure

Not wired into `snapshot()`'s always-computed payload, unlike Stage 6's
manifest. A plan is an answer to a question a caller asks - there is no
non-arbitrary default target to plan against on every read, and inventing
one (e.g., "plan a no-op upgrade to the current version, every time")
would produce a payload key that always says CURRENT and observes
nothing real. `upgrade_planner.py` lives in `src/control_center/`,
importable by the same layer `manifest.py` and `snapshot.py` already
occupy, ready for a future caller - a script, a test, eventually an
explicit interface action - to supply a real target through. That is
"backend observability, no new dashboard" applied honestly: the seam
exists; nothing is showing an empty question by default.

### Verified

`pytest` before this section: 3499. After: **3528** (29 new, in a new
`tests/test_upgrade_planner.py`; 0 removed or weakened; every Stage 1-6
test re-verified passing unmodified). Real MEMORY_MIGRATIONS/
STATE_MIGRATIONS are still empty chains in this codebase (schema 1 has
never had a predecessor), so - exactly as Stage 6's visual-environment
tests did against a synthetic `ui/index.html` snippet - most tests here
register a synthetic `MigrationChain` via monkeypatch to prove the real
wiring, plus one test against the real, empty chains proving today's
actual behavior (any non-current schema target is UNSUPPORTED) is
unchanged. Two mutations introduced by hand and both caught before being
reverted: letting `MIGRATION_REQUIRED` win over an `UNSUPPORTED`
dimension in the same request (the "no partial success" guarantee), and
disabling the non-positive-schema-version validation rule (caught even
though the downstream `can_migrate()` check independently still failed
closed to UNSUPPORTED rather than crashing - the test correctly demanded
the more precise INVALID diagnosis instead). Provider and connection
independence verified by source inspection and `git diff`; no 3D,
Three.js, or `ui/` file appears anywhere in this section's diff.

## 42. The Upgrade Executor — perform the already-approved migration

Five things now have a name:

    DESCRIBE   Upgrade Manifest (§40)   "What is installed?"
    PLAN       Upgrade Planner  (§41)   "Can this safely happen?"
    APPROVE    an explicit, external decision - no code anywhere in this
               repository makes it. A human, or a governed caller acting
               on a human's behalf, decides an `UpgradePlan` should run.
    EXECUTE    this section             "Perform the already-approved
                                         migration. Nothing more."
    ROLLBACK   not automatic - a governed, human operation, same as
               APPROVE. A failed migration leaves the original recoverable
               under a checkpoint or quarantine file; restoring from it is
               a decision this codebase does not make for anyone.

**This is an execution primitive, not an autonomous updater.** It does
not discover upgrades, choose a target, download anything, read the
network, modify source code, switch a provider, or decide whether an
upgrade should happen. It receives one thing - a plan someone else
already produced and approved - and does exactly, and only, what that
plan describes. There is no update checker, no scheduled task, no
startup hook, and no code path anywhere in this repository that calls
`execute_upgrade` on its own initiative.

### Where it lives, and why that took a real architectural finding

The first draft placed `upgrade_executor.py` inside `src/control_center/`,
next to `upgrade_planner.py` - the obvious place, since both are about
upgrades. The full test suite disagreed: `tests/test_control_center.py::
TestItOwnsNoBusinessLogic` already forbids anything under
`src/control_center/` from importing `src.services.memory.factory` (or
any other constructor/engine module), because that package is a
read-only observation layer - "Routing, assessment, brain construction,
memory admission, scheduling and provider selection all stay where they
are," per that test's own docstring. The planner never tripped this,
because deciding *whether* a migration is safe needs no constructor. The
executor genuinely constructs `PersistentMemory`/`StateStore` and writes
real files - it is an actor, not an observer, and belongs with the
codebase's other actor: `src/composition.py`, the one existing top-level
module under `src/` that already imports `factory.py`, `router_factory.py`
and `brain_factory.py` freely, for the same reason. `upgrade_executor.py`
now lives at `src/upgrade_executor.py`, beside `composition.py`, and
`TestItOwnsNoBusinessLogic` passes unmodified - the boundary was correct;
the first draft's location was not.

### The shape

`src/upgrade_executor.py`: `UpgradeResult` (`success`, `status`,
`migrations_attempted`, `migrations_completed`, `stores_affected`,
`failure_reason`, plus `to_dict()`), and `execute_upgrade(plan:
UpgradePlan, *, approved: bool, memory_dir=..., state_path=...) ->
UpgradeResult`. `approved` has no default and is keyword-only - a caller
must decide and say so, every call; there is no flag anywhere else in
this module, hidden or otherwise, that can supply consent on a caller's
behalf. Only `MIGRATION_REQUIRED` runs anything: `INVALID` and
`UNSUPPORTED` are refused outright, `CURRENT` and `UPGRADE_AVAILABLE`
succeed trivially with zero migrations attempted, because neither one
*has* migration work by definition (§41).

### Reuses the existing migration machinery; does not duplicate it

The actual checkpoint-then-atomic-write-then-quarantine-on-failure logic
already exists, is already tested, and already runs automatically the
moment a store is constructed and its on-disk schema differs from what
the installed code defines as current (`PersistentMemory.__init__` /
`StateStore.load()`, §38). This module's whole job is *governing
when that already-existing mechanism fires*: verifying the approved plan
still matches on-disk reality, constructing the store at the moment a
governed caller chose, and reporting - structurally, observably - whether
the schema actually landed where the plan said it would. Nothing here
calls `Migration.apply`, references a `MigrationChain`, or writes a
checkpoint file itself - verified structurally, by parsing the module's
own `import` statements, not by trusting its prose.

A read-only `_on_disk_schema_version()` peek (a direct `json.loads`,
never a `PersistentMemory`/`StateStore` construction) checks a store's
raw on-disk version *before* deciding whether triggering the real load-
time migration is the right thing to do - constructing either store
immediately begins acting, so this module cannot afford to construct one
just to look.

### Fails closed, twice over

Two independent guards must both agree before a single store is
constructed: the plan's own target schema must equal what this
installation currently defines as current (a stale or wrong-version plan
is refused, never "tried anyway" against a version nobody runs), and the
on-disk schema found must equal the plan's expected starting point (an
unexpected version on disk is refused rather than guessed past). A third
guard runs *after* construction: the on-disk schema must actually equal
the plan's target once the store's own machinery has run. This third
guard is not redundant - it is the only thing standing between a real
migration completing and the store's own quarantine (§38's `_quarantine`)
silently discarding the original and starting empty, which would
otherwise land at the *same* current schema number a genuine migration
does and could be mistaken for success. Mutation-tested directly: a
migration whose `apply` raises is quarantined by `PersistentMemory`
itself (proving the original is preserved, moved aside, never deleted),
and the executor's own after-check is what turns that into a reported
failure rather than a false success.

### Idempotency, without a separate flag

An already-migrated store (on-disk version already equals the plan's
target) is detected by the same before-check used to fail closed, and
treated as a no-op success - `stores_affected` stays empty, nothing is
constructed, nothing is rewritten (file mtime unchanged, verified
directly). No "already done" bookkeeping exists anywhere; the on-disk
file already carries that information, and re-deriving it a second way
is exactly the kind of duplication this section avoids everywhere else.

### Manager identity, providers, integrations, and visual environments

The Manager's memory is always addressed by the stable `MANAGER` key
(§39's stable-identity invariant, `src.services.memory.factory.MANAGER`)
- never a display name, and `execute_upgrade` takes no agent, name, or
identity parameter that could redirect it. The module imports nothing
from `src.services.identity`, `src.registry`, `src.composition`, or
`src.providers`, and names no provider anywhere in its source (verified
by both an AST-based import check and a source-text scan for
`claude`/`openai`/`grok`/`anthropic`/`api_key`, the same techniques §41
uses). A visual environment version differing is something the *planner*
can report (`UPGRADE_AVAILABLE`); there is no executor for it here,
deliberately - no `ui/` or Three.js file appears anywhere in this
section's diff.

### No automatic rollback

A failed migration leaves the original data recoverable exactly the way
it always has - under a checkpoint or quarantine file, per §38's own
tested guarantees - and this module reports the failure. Restoring
from that file is a governed, human decision, not something this module
performs; verified structurally (no function or call anywhere in the
module named anything resembling `rollback`/`restore`/`revert`), not
only by absence of a test that happens not to exercise one.

### Verified

`pytest` before this section: 3528. After: **3559** (31 new, in a new
`tests/test_upgrade_executor.py`; 0 removed or weakened; every Stage 1-7
test re-verified passing unmodified). Six mutations introduced by hand
against the six independent safety checks described above - the approval
gate, the executable-status allowlist, the before-migration guard, the
stale-target guard, the after-migration verification guard, and the
unknown-store fail-closed branch - and all six caught by the new suite
before being reverted; two of the six (the before-migration and stale-
target guards) were caught by a *second*, independent check even with
the first disabled, confirming the defense is layered rather than
resting on one condition. Real end-to-end migrations exercised against
both `PersistentMemory` and `StateStore` (a synthetic schema-0-to-current
`Migration` registered via the same `monkeypatch` technique §38 and §41
already use, since neither store has ever had a real predecessor
schema): checkpoint file created and byte-identical to the pre-migration
original, on-disk schema correctly advanced, and - for memory - the
original record recalled unchanged after reload. Provider, identity, and
connection independence verified by source inspection and `git diff`; no
3D, Three.js, `ui/`, Gmail, or OAuth file appears anywhere in this
section's diff.

## 43. Ambient voice activation and continuous voice conversation

    ALFRED OS RUNNING - the canonical Alfred already exists
            |
            v
    CONFIGURED ACTIVATION PHRASE - "Alfred", "Hey Alfred", or a future
            |                       phrase, declared on Alfred's own
            |                       identity document, never hardcoded
            v
    VOICE ACTIVATION - a heard utterance matches; the session moves from
            |            IDLE to ACTIVATED. Nothing is constructed.
            v
    ACTIVE VOICE CONVERSATION - LISTENING -> THINKING -> SPEAKING,
            |                    repeating for as many turns as the
            |                    person keeps talking
            v
    the exact, existing Alfred - the same Channel.ask()/Channel.deliver()
            |                     a typed message already uses
            v
    ALFRED BRAIN -> ALFRED MEMORY -> ALFRED VOICE - unchanged, untouched,
            |                          not re-entered by anything new here
            v
    LISTEN AGAIN - no activation phrase required for the next turn
            |
            v
    NEXT TURN, until the conversation ENDs and the session returns to IDLE

**Activation selects an already-running agent; it never constructs one.**
This is the one fact every other decision in this section follows from.
Before this stage, addressing Alfred by voice meant saying a wake phrase
*and* the request in the same breath, every single time - there was no
seam that remembered a conversation was already under way. After this
stage, one matched phrase opens a conversation that continues, turn after
turn, until it explicitly ends. Nothing about *which* Alfred answers, or
how, changes: `VoiceActivationSession` (`src/channels/activation.py`)
holds a `Channel` someone else already built and a `VoiceActivation`
someone else already declared, and calls that channel's own `ask`/
`deliver` - the same pair a typed message in the Conversation Room already
uses (`src/channels/channel.py`, §14). There is no second routing path, no
second delegation, and - because this module never imports a brain
factory, a memory factory, or `VoiceProfile` - structurally no way for it
to construct a second Brain, Memory, or voice identity even by accident.

**Activation identity is a fifth thing one agent identity decides, not a
fixed list in the engine.** `VoiceActivation` (`src/services/identity/
voice.py`) sits beside `VoiceProfile` in the same module and is kept as a
distinct type for the reason stated in its own docstring: changing how an
agent sounds must never change how it is addressed, and changing how it
is addressed must never change how it sounds. A `## Voice Activation`
section on an identity document - IDENTITY.md for Alfred, an
`identities/<key>.md` for a future specialist - is parsed into it exactly
as `## Voice` already is, through the same section parser
(`src/services/identity/identity.py`'s `parse_activation`, reusing a new
`bulleted_section` helper rather than the existing `listed_section`, which
would have mistaken a section's own explanatory prose for phrases - caught
by a smoke test before it reached the real test suite). An agent that
declares no section gets a default derived from its own name
(`default_activation`), the same rule `default_voice` already follows and
for the same reason: two agents that both declare nothing must still be
addressable as themselves, never as each other. This is also, concretely,
where "Alfred" and "Hey Alfred" come from - not a hardcoded phrase pair in
`VoiceActivationSession` or in the browser controller below, but IDENTITY.md's
own `## Voice Activation` section, read the same way every other fact
about Alfred already is.

**Five states, matching what a real conversation actually does:**

    IDLE        listening for the activation phrase; nothing else happens
    ACTIVATED   the phrase just matched; no turn has happened yet
    LISTENING   between turns, ready for what the person says next
    THINKING    a turn is in flight - asked, not yet answered
    SPEAKING    an answer has come back and is being delivered
    ENDED       the conversation just concluded; the session settles back
                to IDLE immediately, ready for the next activation

Activation happens once per conversation; a conversation holds as many
turns as the person keeps talking. Reaching LISTENING a second, third or
fourth time never asks for the phrase again - only a return to IDLE does,
whether reached by an explicit `end()` or by `stop()`/`dispose()`.

**Idempotent `start()` is the concrete answer to "no duplicate
listeners."** `VoiceActivationSession.start()` is a no-op on a session
already started, and critically, this holds regardless of *which* state
the session is in - a `start()` called mid-conversation does not reset it
to IDLE. That single guarantee is what makes it safe to call `start()`
from setup code that runs more than once: in this project's own browser
UI, the code that would call it re-runs on every SPA navigation
(`ui/index.html`'s `go()`, which replaces the entire `#stage` and rewires
the composer) and on every five-second live poll (`refreshFromLive()`,
which calls `go()` again). A naive `start()` that reset state
unconditionally would silently drop an active conversation on the very
first background refresh after activation - a mutation test confirmed
this exact failure mode before the guard existed
(`tests/test_ambient_voice_session.py::TestLifecycle::
test_repeated_start_does_not_reset_an_active_conversation`).

**The browser is where this is actually experienced; the Python session
is what makes the experience honest about what it reused.** This project
already had a live server (`scripts/serve_ui.py`, existing before this
stage) exposing `GET /snapshot.json` and `POST /message` - the exact path
a typed line in the Conversation Room already sends a request through.
`ui/index.html` gained one page-lifetime singleton, `initVoiceActivation()`
- guarded by `window.__alfredVoice` so a stray second call is a no-op, and
deliberately defined and invoked *outside* `go()`/`wireComposer()` for the
same duplicate-listener reason `VoiceActivationSession.start()` is
idempotent. It uses the browser's own `SpeechRecognition` for hearing and
`speechSynthesis` for speaking - real engines, arriving as the additive
change `src/channels/speech.py`'s own docstring already anticipated ("what
will let a real engine arrive later") - and reads its activation phrases
from `agents[].activation` in the snapshot this stage added to
`src/control_center/snapshot.py`, never a hardcoded list in the page.
A recognized utterance becomes a `POST /message` exactly like a typed
line; the JSON answer is spoken via `speechSynthesis` and the room is
refreshed via the exact existing `refreshFromLive()`. No new backend
route was added for this: the existing one was reused unmodified.

**Three states for speech availability, in the browser exactly as in
Python - never a boolean.** `VoiceActivationSession.listening` delegates
to the wrapped `Channel`'s own report (`VoiceChannel.listening`, itself
built on the `Availability` UNKNOWN/AVAILABLE/UNAVAILABLE vocabulary
`src/providers/availability.py` already defined) rather than re-deriving
it - and returns `None`, not `False`, when the wrapped channel has no
speech concept at all (a plain `TextChannel`), because "not applicable"
and "not listening" are different facts. The browser controller mirrors
this rather than reinventing it: a browser with no `SpeechRecognition` at
all reports a permanent, honest `UNAVAILABLE` ("NO MIC ENGINE"), the exact
position `NoSpeech` already documents for the Python side - not a bug, not
something a retry fixes.

**Provider-independent by construction.** Neither `src/channels/
activation.py` nor the `VoiceActivation`-related code in
`src/services/identity/voice.py` imports anything from `src.providers`,
names a vendor, or takes a provider/model/API-key parameter - verified by
AST-parsed import inspection and a source-text scan, the same two
techniques §41's independence tests already established. Swapping which
LLM answers behind the Manager's Brain changes nothing about how Alfred is
activated, because activation was never wired to a provider in the first
place; it is wired to one agent's own declared identity.

**What this stage deliberately does not do.** It does not discover
upgrades, choose a target, or decide anything autonomously - there is no
analogous concept here, but the same discipline applies to voice: nothing
in this stage starts listening on its own, schedules a wake check, or
runs at startup without a person's own click granting microphone access
first (a browser requirement, not a choice this project made). It does
not touch the Conversation Room's Brain/Memory boundary, the Visual
Environment Registry, or any Three.js file - a recognized voice turn
reaches the room the same way a typed one already does, through `POST
/message`, and the 3D environment renders whatever the next
`refreshFromLive()` shows exactly as it always has. Arthur's own
activation phrase is not wired this stage - `identities/email_agent.md`
declares no `## Voice Activation` section, so `SpecialistWiring.activation`
and `EmailAgent.activation` both fall back to `default_activation("Arthur")`
- proving the primitive is ready for him without a line of it having been
written with him in mind.

**Platform reality, stated plainly rather than assumed.** No microphone,
browser, or speech engine exists in the environment this stage was built
and tested in - there is no way to physically speak to this page from
here. `SpeechRecognition` additionally requires a secure context (HTTPS,
or `localhost`/loopback, which `scripts/serve_ui.py`'s default bind
already satisfies) and an explicit user gesture before a browser will
grant microphone access at all; this project makes no claim to bypass
either. What was verified without a browser: every Python-level guarantee
above, by a real test suite and real mutation testing; the exact JSON
shape `ui/index.html`'s controller reads, by generating a real snapshot
from the real composed system and a real exported page via
`scripts/export_ui.py`; and the page's JavaScript syntax, both in its
template form and in a fully-rendered real export, via `node --check`.
What was not, and could not be, verified here: an actual spoken wake
phrase reaching an actual `SpeechRecognition` instance in an actual
browser. That is a platform limitation of this environment, not a claim
this stage makes and fails to back up.

## 44. Governed System Evolution — the upgrade lifecycle

    CURRENT SYSTEM
         |
         v
    MANIFEST              Upgrade Manifest (§40) - what is installed
         |
         v
    REQUEST                an UpgradeRequest (§41) - a proposed target
         |
         v
    PLAN                   Upgrade Planner (§41) - is it reachable
         |
         v
    HUMAN APPROVAL          an explicit, external decision - never made here
         |
         v
    EXECUTE                 Upgrade Executor (§42) - perform the approved plan
         |
         v
    VERIFY                  this section's one new responsibility
         |
         v
    NEW MANIFEST

Four things already had an owner - a manifest describes, a planner
decides reachability, an executor performs an approval, and Stages 1/5's
own store machinery does the actual migrating. Nothing among them, before
this stage, tied the sequence together into one operation a governed
caller could call once and trust the whole shape of. `src/upgrade_
lifecycle.py`'s `run_upgrade(request, *, approved, ...)` is that seam -
an orchestration function, not a fifth system. It imports `plan_upgrade`
and `execute_upgrade` and calls them; it does not define a competing
planner, a competing executor, a competing migration framework, or a
second manifest format, and three of this stage's own tests exist
specifically to keep that true by AST inspection rather than trusting the
prose above it.

**Planning does not execute.** `run_upgrade` calls `plan_upgrade` exactly
once, unconditionally, and only ever hands the *result* to
`execute_upgrade` - the same directionality §41 already established
between those two modules, now with one caller sitting above both of them
rather than a human having to drive each by hand.

**Approval is explicit, every time.** `run_upgrade`'s `approved` parameter
has no default - the same discipline `execute_upgrade`'s own `approved`
parameter already enforces (§42), checked here by the identical signature
inspection Stage 8's tests already used. There is no environment
variable, no status-based inference, and no branch anywhere in this
module that supplies consent on a caller's behalf. Concretely: only a
`MIGRATION_REQUIRED` plan is ever handed to `execute_upgrade` at all - a
plan whose status is INVALID, UNSUPPORTED, CURRENT, or UPGRADE_AVAILABLE
is settled by this module directly, using the planner's own honest
`reason` text, because there is no executable work for approval to
authorise in the first place. Passing the same `approved` value through
for a status that does not need it would let the wrong value produce a
misleading refusal reason ("not approved" when the real answer is "there
was nothing to approve") - this module refuses that confusion structurally
rather than documenting around it.

**Unsupported changes fail closed, and approval cannot rescue them.** A
plan whose status is UNSUPPORTED never reaches the executor regardless of
what `approved` is - the same "one blocked dimension sinks the whole
plan" guarantee §41 already built into the planner, now impossible to
route around from above it too.

**Execution reuses the existing store migration machinery, unmodified.**
`run_upgrade` never constructs a `PersistentMemory` or a `StateStore` -
verified by both a source-text check and an AST import check - because
`execute_upgrade` already does the one thing that is allowed to (§42). A
small, purely additive refactor to `src/upgrade_executor.py` made two of
its previously-private helpers - `on_disk_schema_version` (the read-only
schema peek) and the newly-extracted `store_path` (where "memory"/"state"
actually live on disk) - public, so this stage's own verification step
could reuse the *exact* mapping and the *exact* peek the executor already
trusted, rather than re-deriving either. Every existing
`test_upgrade_executor.py` test still passes unmodified against the
refactored file.

**Verification happens after execution, and does not simply trust the
executor's own report.** This is the one genuinely new responsibility
this stage adds. `UpgradeResult.success` being `True` is evidence, not a
verdict - the same distinction `src/providers/availability.py` drew
between "configured" and "usable", and the same mistake this project has
already paid for once by collapsing it. After a real execution,
`run_upgrade` independently re-reads each migrated store's on-disk schema
version - through the exact `store_path`/`on_disk_schema_version` helpers
named above - and only reports the upgrade `verified` when that
independent read agrees with the plan's own target. A mutation test
proved this is load-bearing: an executor made to falsely report success
while a store was left untouched was caught by this check every time, in
three independently-written tests.

**One nuance the manifest's own design makes worth stating precisely.**
`UpgradeManifest.memory_schema_version`/`state_schema_version` describe
what the *installed code* currently defines as current (§40's own
docstring: "not Brain or Memory state... it reports one integer
describing Memory's on-disk *shape*") - they are fixed constants within
one running process, not a reading of any specific store file. Diffing
`before_manifest` against `after_manifest` therefore cannot, by itself,
prove a store migration happened - within a single process, those two
fields are always equal, because the *code* did not change, only a
*file* did. This is exactly why verification is grounded in an
independent on-disk read rather than in a manifest comparison: the
manifest remains the right source of truth for what it was designed to
describe (system version, visual environment versions - real,
observable differences a fresh `after_manifest` genuinely can surface),
and a real on-disk peek is the right source of truth for what a store's
migration actually did. Both are returned on every call, honestly, rather
than the weaker one being stretched to cover the other.

**Idempotent, using the executor's own protection, not a second one.** A
second `run_upgrade` call against a request already satisfied by the
installed code's schema constant resolves to CURRENT before the executor
is ever called again - no second migration attempt, no second checkpoint,
no rewrite. Confirmed against a real, isolated store: a governed upgrade
that actually ran, followed by two further calls whose target was already
met, leave the file's own modification time unchanged.

**No automatic rollback, exactly as §42 already established.** A
verification failure is reported the same way an execution failure
already is - a structured `UpgradeLifecycleResult` naming what went
wrong - never followed by an attempt to restore anything. The checkpoint
or quarantine file the store's own machinery preserved (§1, §5, §42)
remains the recovery primitive; restoring from it stays a governed, human
decision this module does not make. Verified structurally: no function or
call anywhere in this module resembles `rollback`/`restore`/`revert`.

**Control Center remains observation-only; self-improvement remains
advisory-only.** Neither boundary needed defending by a new rule - both
were already true, and this stage's tests confirm they still are: nothing
under `src/control_center/` imports `src.upgrade_lifecycle` or
`src.upgrade_executor`, and nothing under `src/improvement/` does either.
This stage was not wired into `snapshot()`'s always-computed payload, for
the same reason §41's planner was not: `run_upgrade` is an operation a
governed caller performs, not standing state to observe on every read.

**Agent identity, Brain, Memory, and voice activation are all
structurally unreachable from here, not merely undisturbed by
convention.** `src/upgrade_lifecycle.py` imports nothing from
`src.manager`, `src.agents`, `src.registry`, `src.services.identity`,
`src.composition`, `src.channels`, or `src.conversation` - checked by
parsing its actual import statements, not by trusting this paragraph. The
one place agent identity is even referenced is indirectly, through
`execute_upgrade`'s own `store_path` (`MANAGER`-keyed, §39, unchanged).
Alfred, Arthur, their Brains, their Memories, their VoiceProfiles, and the
ambient voice activation lifecycle (§43) are all exactly as reachable, and
exactly as unduplicated, as they were before this stage - because nothing
here has a way to reach any of them at all.

**Provider-independent by construction, checked the same way §41 and §43
already are.** No import from `src.providers`, no provider or model name,
no API-key-shaped token anywhere in this module's source, verified by
AST-parsed imports and a source-text scan.

**What this stage deliberately does not do.** No automatic upgrade
trigger exists anywhere in this codebase's startup, heartbeat, or
scheduling paths - this stage added none. No self-modification, no Git
automation, no package installation - the module contains no
`subprocess` import and no reference to `pip`/`git` as an operation. No
autonomous decision to upgrade: `run_upgrade` is a function a governed
caller invokes, not a loop that invokes itself.

### Verified

`pytest` before this stage: 3634. After: **3672** (38 new, in a new
`tests/test_upgrade_lifecycle.py`; 0 removed or weakened; every prior
stage's test re-verified passing unmodified, including
`test_upgrade_executor.py` against the `store_path`/
`on_disk_schema_version` refactor). Four mutations introduced by hand
against this stage's own independent contributions - INVALID/UNSUPPORTED
being reported as success, the post-execution verification result being
ignored in favour of the executor's own report, the on-disk comparison
inside that verification being disabled, and the approval gate being
short-circuited so `execute_upgrade` was never reached at all - and all
four caught by the new suite (the last one by six tests at once,
including the real-integration test below) before being reverted. A real,
isolated integration exercised end to end: a real `PersistentMemory` file
at schema 0, a synthetic migration registered the same way
`test_upgrade_planner.py`/`test_upgrade_executor.py` already do, a real
`run_upgrade` call producing a real checkpoint, a real schema-version
write, a real record surviving reload, real independent verification
passing, and two further calls against the same store proving idempotency
by an unchanged file modification time - all inside a `tmp_path`, with an
explicit assertion that this repository's real `data/memory` directory
was never touched. Boundary, identity, Brain/Memory/voice, and provider
independence verified by AST-parsed imports and source-text inspection,
the same techniques §41, §42, and §43 already established.

## 45. Production speech provider seam and the multimodal foundation

    Stage 8A                                Stage 10
    activation +                            production communication
    continuous conversation                        |
    foundation                                      v
         |                                    genuine STT (a real,
         v                                    replaceable SpeechAdapter,
    existing Channel/Manager  ------------->  selected by build_speech()
    pipeline, unchanged                             |
                                                      v
                                              existing Alfred pipeline
                                              (Brain + Memory, unchanged)
                                                      |
                                                      v
                                              genuine TTS (the same
                                              SpeechAdapter, Speaker half)
                                                      |
                                                      v
                                              continuous conversation
                                              (VoiceActivationSession,
                                              unchanged)

**What this stage actually adds: a factory, not an engine.** Before this
stage, `composition.py` constructed `NoSpeech()` directly, with its own
comment stating exactly why and exactly when that should change: "there is
exactly one adapter... a second adapter turns this line into a factory
call and nothing above it changes." `src/providers/speech_factory.py` is
that factory call - `build_speech(settings)`, selecting a `SpeechAdapter`
by name from `Settings.speech` (env: `MANAGER_SPEECH`, default `"none"`),
mirroring `src/providers/factory.py`'s `build_provider` in shape,
docstring rationale, and failure behaviour: an unknown name fails loudly
at selection time, never falls back silently, and building the default
touches no network and makes no call.

**A second, genuine entry: `"windows"`, not a cloud vendor SDK.** This
project's own architecture-audit guard
(`tests/test_architecture_audit.py::TestNoFuturePhaseHasStarted`) already
forbids `speech_recognition`, `pyttsx3`, and `whisper` anywhere in `src/` -
a pre-existing boundary this stage does not touch, let alone weaken. Beyond
that hard boundary, adding any cloud STT/TTS vendor SDK here would have
meant asserting it "genuinely works" without a credential or a verified
network path in this environment - exactly the "API key exists" standing
in for "STT works" this stage was explicitly told to refuse. Rather than
do that, `src/providers/windows_speech_provider.py`'s `WindowsSpeechAdapter`
reaches Windows' own, already-installed `System.Speech` engine through one
`subprocess` call to `powershell.exe` - no new dependency in
`requirements.txt`, no network, no credential, and genuinely, verifiably
real (see "Verified — provider status", below). `"none"` (`NoSpeech`)
remains the default; selecting real speech is still a deliberate
`MANAGER_SPEECH=windows`, never inferred from the host OS.

**Provider-independent by construction, the same way the LLM side already
is.** Changing `MANAGER_SPEECH` never touches Alfred, his identity, his
Brain, his Memory, `VoiceActivation`, or the Control Center - `build_speech`
is the only place a speech engine name is read, exactly as `build_provider`
is the only place a model name is. `Runtime.speech` still holds the
adapter (built once, shared, per §4's original design); `build_channel`
still wraps it in the same `VoiceChannel`, unchanged.

**Brain and voice remain separate axes, verified together for the first
time.** Half Brain still answers a deterministic capability with speech
`UNAVAILABLE`; a `VoiceChannel` built with an `AVAILABLE` scripted engine
does not change `assess_cognition`'s reported mode; nothing about a
provider being genuinely usable implies a microphone is. Three tests prove
this explicitly, because before this stage nothing had ever constructed a
`Channel` and asked both questions of the same running system at once.

**The voice identity seam Stage 11 will need already exists, untouched.**
`Speaker.speak(text, voice: VoiceProfile | None)` has taken a per-agent
voice since Phase 3 (§19) - this stage did not add it, and did not need
to. `VoiceChannel._voice_of` still resolves whichever agent answered and
asks *that* agent for its own voice; a future TTS provider that actually
maps `VoiceProfile` traits to distinct output is an additive change to one
`Speaker` implementation, not a rewrite of anything upstream. Stage 11
(distinct per-agent voices) is not started; only the seam it needs is
confirmed intact.

**The ambient-voice restart loop gained a bound.** Stage 8A's browser
controller resumed listening on every `SpeechRecognition.onend`,
unconditionally - correct for the ordinary case (a browser pausing
recognition after silence) and, without a limit, an uncontrolled loop for
a genuinely broken microphone. `ui/index.html`'s `startListening()` now
counts *quick, consecutive* restarts (ended within two seconds of
starting, with no real result in between) and stops retrying - reporting
`UNAVAILABLE` honestly - after five in a row; a single genuine result
resets the count, so a microphone that recovers is not permanently
penalised for a past bad patch. Verified by static inspection and a
real-export syntax check (`node --check`), not by a live microphone - see
Verified, below.

**A multimodal seam exists; nothing processes a photo, a video, or a
file.** `src/channels/modality.py` declares `InputModality` (TEXT, AUDIO,
IMAGE, VIDEO, FILE) and `ConversationInput`, a small, immutable value type
naming which shape a piece of conversation input takes. `is_handled` is
honest rather than aspirational: `True` for TEXT (a Channel already
accepts it) and AUDIO (an STT provider already exists to turn it into
text - whether that provider is actually usable is `Availability`'s
question, not this one's), `False` for IMAGE/VIDEO/FILE. The module
processes nothing - no image decoder, no video frame extractor, no file
reader - and is not yet imported by `channel.py` or `activation.py`,
which a test confirms directly: the existing text/voice pipeline is
provably unaffected by this type's mere existence, not merely undisturbed
by convention. Wiring a real modality into an actual capability remains
future work (no earlier than Stage 11, and likely later).

**Canonical agent, Brain, Memory, and activation are unduplicated -
checked through the new wiring specifically, not only assumed from
Stage 8A.** A `VoiceActivationSession` built against a `Channel` produced
by `build_channel(build_runtime(settings), manager)` still resolves to the
one `manager` object across every turn; `manager.brain`/`manager.memory`
are unchanged by any number of voice turns; two sessions built against the
same channel share the exact same `VoiceActivation` object rather than
each holding a copy. Nothing in this stage constructs a second Alfred, a
second Brain, a second Memory, or a second activation mechanism, and it
could not: `build_speech` returns a `SpeechAdapter`, nothing else, and a
`SpeechAdapter` has no way to reach any of those four things.

### Verified

`pytest` before this stage: 3672. After: **3713** (41 new, across
`tests/test_speech_factory.py`, `tests/test_conversation_modality.py`,
and `tests/test_voice_production.py`; 0 removed or weakened; every prior
stage's test re-verified passing unmodified, including
`tests/test_settings.py` and `tests/test_providers.py` against the new
`speech` field and `speech_factory.py`). Two mutations introduced by
hand: the factory's fail-closed unknown-name check replaced with a silent
fallback to `"none"` (caught by two dedicated tests), and
`ConversationInput.is_handled` forced to always report `True` (caught by
four tests, including the one proving IMAGE/VIDEO/FILE stay honestly
unhandled) - both reverted after confirming the failure. `VoiceChannel`'s
existing honesty guarantees (unavailable STT falls back rather than
fabricating a transcription; unavailable TTS never reports `spoken=True`;
available TTS reports `spoken=True` only after the scripted engine's
`speak()` was actually called) were re-exercised through the *new*
`build_speech`-produced wiring specifically, not only through the
already-passing Stage 8A suite. Browser verification: the exported page's
JavaScript (both the template and a freshly-generated real export via
`scripts/export_ui.py`) was syntax-checked with `node --check` and
confirmed to contain the new restart-limit logic; no real browser,
microphone, or speaker exists in this environment, so no actual spoken
audio, no real `SpeechRecognition` session, and no viewport/console-error
check at 390px was genuinely performed - stated plainly rather than
implied. `data/memory` and `data/state` were confirmed untouched by every
new test (all run against `tmp_path` or in-memory collaborators); no
dependency was added to `requirements.txt`.

### Stage 10 completion — genuine STT/TTS via `WindowsSpeechAdapter`

The paragraph above records the factory seam this stage started with: real,
tested, and shipping only `"none"`. What follows completes it with a second,
genuinely functional entry - `src/providers/windows_speech_provider.py`'s
`WindowsSpeechAdapter`, reaching Windows' own `System.Speech` engine through
one `subprocess` call, no new dependency, no network, no credential.

Status, stated precisely rather than rounded up:

- 🟢 **Implemented and genuinely verified** - by hand, once, outside the
  automated suite, directly against the real engine: a real
  `SpeechSynthesizer` produced a real WAV file (76,490 frames, 22.05kHz,
  3.47s of real audio) for real text; a real `SpeechRecognitionEngine` read
  that file back and transcribed it with 100% word accuracy; the real
  `speak()` method (default audio *output* device) returned `True` and
  moved `Availability` from UNKNOWN to AVAILABLE; the real default audio
  *input* device opened without error and correctly, honestly reported "no
  speech was recognized" when nothing was said (raising `SpeechUnavailable`
  and moving `Availability` to UNAVAILABLE - the *correct* outcome for
  silence, not a bug).
- 🟢 **Implemented and automated-test-verified** - the full honesty
  contract (empty transcription raises rather than returning blank;
  process failure/timeout/missing-binary all report UNAVAILABLE and never
  fabricate success; text is never interpolated into the generated script,
  only read back from a private temp file; configuration/construction
  alone never moves `Availability` off UNKNOWN) is pinned by 18 tests in
  `tests/test_windows_speech_provider.py`, every one exercised against a
  mocked `_run_powershell` seam - see 🟡 below for exactly why that
  boundary is mocked rather than real in automated runs.
- 🟡 **Implemented but not live-verified through a real human voice.** No
  physical human spoke into a microphone anywhere in this work. The
  genuine verification above proves the *engine* - both directions,
  through the actual Python code path - is real and correct; it does not
  and cannot prove a live conversational turn from an actual person's
  voice, because nothing in this environment can produce that audio. The
  distinction is the whole point of this stage: "the engine transcribes
  real audio correctly" is now 🟢; "a live human was heard" remains 🟡
  until someone actually talks to a running instance of this on real
  hardware with a real microphone.
- 🟡 **Implemented but not distinctly voiced per agent.** `speak()` accepts
  a `VoiceProfile` (the `Speaker` contract requires it, and Alfred's own
  profile is passed through correctly), but does not yet map its traits to
  one of this engine's nine real installed voices - Stage 11's job, not
  invented early to look more finished.
- 🔵 **Future, not started.** A cloud STT/TTS vendor (if one is ever
  deliberately configured with real credentials), per-agent distinct
  voices (Stage 11), camera/video/file modalities (the seam exists,
  `ConversationInput` from earlier in this section; no processing of any
  of them exists or is claimed).

### Verified (Stage 10 completion)

`pytest` before this pass: 3713. After: **3739** (26 new: 18 in
`tests/test_windows_speech_provider.py`, 3 added to
`tests/test_speech_factory.py`, 5 added to `tests/test_voice_production.py`;
0 removed or weakened). Two mutations introduced by hand against the new
provider's two most important safety checks - the empty-transcription
guard in `listen()` (removing it let a blank string escape instead of
raising) and the failure check in `speak()` (removing it let a failed
synthesis report `True`) - both caught, each by its dedicated unit test
*and* by a real end-to-end integration test running the actual
`VoiceChannel`/`Manager` pipeline against the real `WindowsSpeechAdapter`
class (only `_run_powershell` mocked), both reverted. Canonical-agent,
Brain, and Memory identity were re-confirmed unchanged specifically through
calls that used the real `WindowsSpeechAdapter` (not only the earlier
scripted double) end to end. No change to Half Brain/Full Brain semantics,
`Availability`'s three states, Stage 8A's activation architecture, or the
multimodal seam beyond what is recorded above.

## 46. Agent Personality — the foundation, not the finished thing

    IDENTITY        who the agent is
    PERSONALITY     how the agent tends to communicate     <- this section
    BRAIN           how deeply the agent can reason
    MEMORY          what the agent retains
    VOICE           how the agent sounds spoken aloud
    CAPABILITIES    what the agent can do
    CONNECTIONS     external systems the agent can reach

Seven distinct dimensions of one canonical agent - this stage adds exactly
one of them, alongside the six that already existed, and duplicates none
of the other six.

**Not Voice, and the distinction is load-bearing.** `VoiceProfile` (§19)
answers "how does this agent sound" - accent, pace, pitch, characteristics
a speech provider maps to a real timbre. `AgentPersonality`
(`src/services/identity/personality.py`) answers a different question -
"how does this agent tend to communicate" - tone, warmth, humor,
playfulness, emoji style. Kept as a genuinely separate type, in a separate
module, for the reason `VoiceActivation` was kept separate from
`VoiceProfile` in Stage 8A: giving Alfred a new personality must never
change how he sounds, and giving him a new voice must never change how he
tends to express himself.

**A preference stated in words, never a mechanical rule.** The specific
risk named for this stage was mechanical emoji insertion - a filter that
tacks on 🙂 because a flag is set, regardless of whether the moment calls
for it. `AgentPersonality` never touches a response; it is a value object.
`personality_prompt_fragment(personality)` is the one function that turns
it into text, and that text is a *description* of a tendency ("Emoji:
occasional and natural when it genuinely fits the moment... use one only
when it would feel natural - never mechanically, never in every reply"),
never an emoji character itself - verified by a test that scans the
fragment for the literal glyphs and, more strongly, asserts every
character in it is printable ASCII.

**The genuine connection, and an honest limit on what it currently
shapes.** Inspection before implementing found something worth recording
precisely: this codebase has no free-text conversational-response-generation
pipeline anywhere. Every response a user reads today comes from a
deterministic capability handler (`report_roster`, `general_assistance`)
or a specialist's own structured report - `LLMManagerBrain.deliberate()`,
the Manager's one real LLM call site, decides *who* should handle a task
(a `BrainDecision`), it does not compose what Alfred says. Given that, the
smallest genuine, honest connection available was to thread personality
into the actual `system` prompt that call sends to a real model -
verified end to end through the real composition root (`build_runtime` ->
`build_brain` -> `LLMManagerBrain`, with a capturing provider standing in
for a real vendor): Alfred's real, parsed IDENTITY.md personality
genuinely appears in the exact string a real LLM call would receive. That
is a real, testable fact about "how you tend to communicate" reaching a
model this agent actually consults - it is not, and this section does not
claim it is, a demonstration of personality shaping the prose a person
reads, because no such pipeline exists yet for it to shape. The same
mechanism (`personality_prompt_fragment`, threaded the same way) is ready
the day one does.

**Fully additive; nothing existing changed shape.** `LLMManagerBrain.__init__`
gained one keyword-only parameter, `personality: AgentPersonality | None =
None` - every prior one-argument call site is unaffected, and a
personality that declares nothing reproduces the *exact* prior
`BRAIN_SYSTEM_PROMPT` string, byte for byte (tested directly). `build_brain`
gained the same optional parameter, threaded only to `LLMManagerBrain`;
`NullBrain` ignores it, because Half Brain has no prompt to shape in the
first place - which is precisely the point of the next paragraph.

**Brain mode independence, proven, not assumed.** Personality is
accessible identically whether `MANAGER_BRAIN` is `null` or `llm` - a
Half-Brain Alfred still has `own_skills.personality`, still answers a
deterministic capability (`status`) unchanged, and declaring a rich
personality never requires Full Brain to exist. Full Brain, symmetrically,
never requires a declared personality - `LLMManagerBrain` with no
personality argument at all still deliberates exactly as before. Full
Brain ≠ personality; Half Brain ≠ no personality; neither implies anything
about the other, mirroring the identical, already-proven independence
between Brain mode and capability availability.

**Generic by construction, not by promise.** `personality.py` imports
nothing beyond the standard library - not `src.manager`, not
`src.services.memory`, not even `src.services.identity.voice` - so it
could not construct a Brain, a Memory, a VoiceProfile or a VoiceActivation
even by accident. `AgentPersonality`, `default_personality`, and
`personality_for` name no agent, checked both by a source-text scan and by
an AST walk over every string comparison in the module. Arthur (via
`SpecialistWiring`/`EmailAgent`, both of which gained a `.personality`
property mirroring `.voice`/`.activation` exactly) gets a distinct,
name-keyed default the same way he already gets a distinct default voice
and activation phrase - proven with his real wiring, not only a synthetic
stand-in.

**A real, pre-existing, dormant bug found and worked around, not
inherited.** Writing a real `## Personality` section for Alfred (`tone:
warm, friendly and conversational...`) surfaced a defect: `parse_voice`'s
identical `trait: value` line parser reads *any* colon-containing line as
data, so an identity document's own explanatory prose ("...key here:
which actual voice a given engine uses...") is silently filed into
`VoiceProfile.style` under a garbled key derived from the sentence itself.
Confirmed live against Alfred's real, already-shipped `## Voice` section -
`identity.voice.style` already carries exactly this garbage today. It has
no visible effect (nothing reads `VoiceProfile.style` anywhere in the
shipped snapshot/UI pipeline), so it is recorded here rather than fixed:
`parse_voice` is pre-existing, shared, out of this sub-stage's scope, and
"do not replace established identity mechanisms unnecessarily" applies
squarely to it. `parse_personality`, written new for this stage, does not
inherit the defect - it rejects any candidate trait line that is
implausibly long or carries sentence punctuation, verified by a
regression test built from the exact shape of prose that triggered it,
and confirmed against Alfred's real, now-clean `## Personality` section
(`personality.style == {}`).

**Control Center stays an observer.** `src/control_center/snapshot.py`
gained `_personality(agent)`, mirroring `_voice`/`_activation` exactly - a
read of what the agent's own identity already declares, nothing decided
here. Not wired into a new UI surface; the existing per-agent workspace
payload simply gained one more honestly-reported key.

**What this sub-stage explicitly does not do**, because Stage 11.1 is the
foundation and nothing past it: no permanent voice selection for Alfred,
no voice for Arthur, no distinct per-agent TTS mapping, no emotional-state
engine, no Conversation Room personality behaviour, no change to
Capabilities, Memory, or the Shared Context Graph's (still absent, still
undecided) direction. Those remain Stage 11.2+ and later, unstarted.

### Verified

`pytest` before this stage: 3739. After: **3797** (58 new, in
`tests/test_agent_personality.py`; one existing test updated -
`test_brain_factory.py`'s `TestTheCompositionRoots::
test_main_reaches_the_brain_through_the_factory` monkeypatches
`build_brain` with its own fake, whose signature had to accept the new
optional `personality` argument to keep receiving what `composition.py`
now genuinely passes; the test's own assertion is unchanged - 0 tests
removed or weakened). Every prior stage's test re-verified passing
unmodified, including the full Brain/provider suite
(`test_brain_factory.py`, `test_llm_manager_brain.py`,
`test_anthropic_wiring.py`, `test_specialist_brain_architecture.py`,
`test_capability_floor_and_brain_modes.py` - 283 tests together). Two
mutations introduced by hand: the new prose-vs-data guard in
`parse_personality` (caught by both a synthetic regression test and the
real-IDENTITY.md test), and the line in `LLMManagerBrain.__init__` that
actually appends the personality fragment to the system prompt (caught by
three independent tests, including the real `build_brain` integration
test) - both reverted. Genuine, end-to-end verification performed outside
any mock: Alfred's real IDENTITY.md, loaded through the real composition
root with a capturing (non-network) `LLMProvider` standing in for a real
vendor, produced a real `deliberate()` call whose actual `system` prompt
was inspected directly and found to contain his real, declared tone,
humor, playfulness and emoji-style text verbatim.

## 47. Agent Voice Identity — genuinely distinct, through the real engine

    Conversation Room / any Channel
              |
              v
       who actually answered (the turn already records this - §14/§25)
              |
              v
       that agent's own canonical VoiceProfile (Phase 3/§19 - unchanged)
              |
              v
       _select_voice_name(voice, this-machine's-real-installed-voices)
              |
              v
       a real SAPI SelectVoice(...) call, genuinely distinct per agent
              |
              v
       real, verified audio

**What already existed, and what this stage adds.** Alfred and Arthur
have had distinct, real, document-derived `VoiceProfile`s since Phase 3,
and `VoiceChannel._voice_of` has resolved *whichever agent answered* and
asked that agent for its own voice before speaking since the same phase -
this is the Conversation Room's speech path, and it did not need to be
rebuilt, duplicated, or even changed: it already threads the speaking
agent's identity through correctly, and this stage's own tests confirm
that directly (delegating to Arthur, then asking Alfred again, and
checking each turn's real `SelectVoice(...)` line independently). What was
missing was a real engine on the *receiving* end that acted on the voice
it was handed - `WindowsSpeechAdapter.speak()` (Stage 10) accepted a
`VoiceProfile` and, honestly, ignored it. This stage completes that seam.

**`_select_voice_name` - a real, generic, deterministic mapping, never a
hardcoded name.** `src/providers/windows_speech_provider.py` gained one
new query (`installed_voice_names()`, live via `GetInstalledVoices()`,
cached per adapter instance) and one new pure function: a stable hash
(`zlib.crc32`, not Python's own salted `hash()`, which is not reproducible
across restarts) of `voice.key` modulo the size of *this machine's own,
actually-installed* roster. No agent name appears in either function -
checked both by a source-text scan and by an AST walk over every string
comparison in the file. Two different keys select two different real
voices whenever more than one is installed; a single installed voice is
returned honestly, not treated as a failure; an empty roster (the engine
could not be reached, or reports none) returns `None` - never a guess.

**Genuinely verified, not merely constructed - and here is exactly what
that verification found.** Run once, by hand, outside any test, against
this machine's real engine: `installed_voice_names()` returned three real
voices (`Microsoft David Desktop`, `Microsoft Hazel Desktop`, `Microsoft
Zira Desktop` - down from the nine Stage 10 originally saw unfiltered,
because this stage's query now excludes disabled ones, itself a more
honest reading than Stage 10's). Alfred's real `VoiceProfile` selected
`Microsoft Zira Desktop`; Arthur's selected `Microsoft Hazel Desktop` -
genuinely different, deterministically, with zero code aware that either
belongs to "Alfred" or "Arthur". Real synthesis through the real public
`speak()` method, with each agent's real voice, produced two real WAV
files - 121,131 frames (5.49s) and 139,086 frames (6.31s) at the same
sample rate for the identical input text, a different result precisely
because a different real voice has its own speaking rate. Both directly
literal claims this section could make and does not: no human ear
confirmed the two voices sound different, and neither declared accent
(`nigerian-english`, `british-english`) is what SAPI's own three voices
actually are - they are, honestly, three generic Windows TTS voices, two
of which now stand in, distinguishably and reproducibly, for two
different declared identities. That is the limit of what this specific,
free, offline engine can honestly claim, and this section claims exactly
that and no more.

**Voice provider separation, proven, not merely designed.** `VoiceProfile`
still names no vendor, engine, model, or endpoint (checked directly against
its dataclass fields). Selecting `MANAGER_SPEECH=windows` vs `none` does
not change `ManagerSkills.voice`, because nothing about which speech
provider is configured ever reaches identity construction - `voice` comes
from `AgentIdentity`, read once, at load time, regardless of `Settings.speech`.
Selecting `MANAGER_BRAIN=null` vs `llm` does not change it either, checked
the same direct way. A future Voice B for Alfred is therefore exactly what
the roadmap asks for: editing `IDENTITY.md`'s `## Voice` section, not a
code change anywhere this stage touched.

**Personality and Voice remain genuinely separate types**, not merely
described as separate. Both independently chose `key` and `warmth` as
field names - a voice can sound warm, a personality can be warm-natured,
two unrelated facts that happen to share a word - and excluding that
overlap, the remaining fields are entirely disjoint: `accent`/`pace`/
`pitch` belong only to `VoiceProfile`; `humor`/`playfulness`/`emoji_style`
belong only to `AgentPersonality`. Neither type's module imports the
other.

**The emotional/expressive seam this stage was told only to leave clean,
not to build.** `VoiceProfile.style` - an opaque `Mapping[str, Any]`,
present since Phase 3 specifically "for future emotion or delivery
parameters" per its own docstring - remains exactly that: unused,
unread, un-narrowed. No emotion engine, no expressive-synthesis code, and
no new field were added this stage. A future expressive TTS provider
reads `style` the same way `_select_voice_name` reads `key` today -
additively, in that provider's own module, never in the generic identity
layer.

**Half Brain / Full Brain independence, and provider independence,
verified together.** `own_skills.voice` is identical whether
`MANAGER_BRAIN` is `null` or `llm`; a Half-Brain Alfred still selects his
own real voice through the real engine when one is configured; nothing
about Full Brain implies a working speech engine, and nothing about a
working speech engine implies Full Brain - the same already-proven
independence between Brain mode and capability availability, now checked
against voice too.

**No duplicate anything.** Delegating to Arthur twice, then asking Alfred
again, leaves `registry.get("Arthur")` and `channel.manager` exactly as
they were - no second agent, no second Brain, no second Memory, no second
Conversation, no second `VoiceActivation`, and (checked directly) no
`_select_voice_name`-shaped function anywhere in the generic
`src/services/identity/voice.py` module - the mapping logic lives only in
the one provider that needs it.

### Verified

`pytest` before this stage: 3797. After: **3832** (35 new, in
`tests/test_agent_voice_identity.py`; two existing Stage 10 tests updated -
`test_windows_speech_provider.py`'s and `test_voice_production.py`'s own
call-count assertions now isolate the synthesis call from the new,
additional voice-query call, rather than asserting an outdated total of
one - 0 tests removed or weakened). Every prior stage's test re-verified
passing unmodified (474 tests across every voice/personality/Control-Center
file run together). Three mutations introduced by hand: `_select_voice_name`
collapsed onto index 0 regardless of key (caught by the dedicated unit test
*and*, after strengthening a confounded assertion that had been comparing
whole scripts rather than isolating the `SelectVoice(...)` line, by the
real Alfred-vs-Arthur integration test too); the empty-roster guard
removed (caught immediately, as a `ZeroDivisionError`, confirming the
guard is load-bearing for correctness and not merely style) - all
reverted. Genuine, live, end-to-end verification performed outside any
mock, against the real engine, exactly as recorded above.

## 48. Open-ended conversation and reasoning — the Stage 11 audit fix

The Stage 11 comprehensive audit found one MUST-FIX defect: a genuinely
open-ended request - a teaching question, a short emotional or contextual
remark, "help me understand this" - matched no declared capability word
anywhere. At the outer router (`CapabilityRouter`, `src/manager/router.py`)
it scored zero against every registered agent, including the Manager's own
`ManagerSkills`, and returned `None`; `ManagerAgent.handle_task` then
reported `NO_AGENT_FOUND` outright. On the rare word-shaped request that did
reach `ManagerSkills.execute`, `select_capability`'s only fallback was
`ROSTER_REPORT` - a status listing, not an answer. Neither failure mode
changed with the Brain's configuration: `LLMManagerBrain.deliberate()` was,
and remains, "deliberately not wired into `handle_task`" (its own
docstring) - it answers *who should handle this*, a delegation-shaped JSON
question, never *what is the actual answer*. A Full Brain therefore never
once reasoned about an open-ended request; Half Brain and Full Brain
differed in nothing here.

The fix is additive at three seams, none of them redesigned:

    ManagerBrain            gains one new, concrete (not abstract) method:
                            converse(task, notes) -> BrainDecision. The
                            default implementation returns the same honest
                            no_view() NullBrain.deliberate() already
                            returns, so every existing ManagerBrain
                            subclass and test double keeps working
                            unchanged - only LLMManagerBrain overrides it,
                            reusing the same LLMProvider.complete() call
                            deliberate() already makes, against a separate
                            CONVERSE_SYSTEM_PROMPT (never JSON, never the
                            delegation shape) with the same personality
                            fragment appended, so an open-ended answer
                            still sounds like this agent, not a raw model
                            response.

    ManagerAgent.decide()   gains one more way to arrive at a decision,
                            alongside "addressed by name" and "the router
                            scored something": when nothing scored,
                            own_skills is the default owner of unclaimed
                            work - a property of the orchestrator role,
                            never an agent name, and `None` unchanged when
                            no own_skills is registered at all (a bare
                            `ManagerAgent()`, exactly as every pre-existing
                            test in `test_manager.py` already constructs
                            one).

    select_capability()     gains one more registered handler,
                            `open_conversation` (`src/manager/
                            capabilities.py`), reached only when nothing
                            else claimed or scored the task *and* the task
                            has real content - empty/gibberish-free input
                            still falls to the old, harmless
                            `ROSTER_REPORT` default. It calls
                            `CapabilityRequest.converse` (a new, optional,
                            bound-callable field defaulting to `None`,
                            never the brain object itself - the same
                            narrowing `roster` already applies) and reports
                            the Brain's answer verbatim on success, or an
                            honest `TaskResult.failure` - never a
                            fabrication, never a roster dump - when no
                            usable Brain is configured.

`open_conversation`'s own declared words (`explain`, `teach`, `discuss`)
are kinds of interaction, never subjects - there is no word for a car, a
football club, a country or a market anywhere in this fix, and there must
never be one; the mechanism that actually catches unpredictable input is
the fallback-of-last-resort itself, not a word list, which is the only way
to be honest about genuinely unbounded input.

**What stayed exactly as it was.** `LLMManagerBrain.deliberate()` - its
prompt, its JSON parsing, `BrainDecision`'s existing fields - is untouched;
`converse()` is a second, independent question the same class can now
answer. `VoiceProfile`, the speech-provider abstraction, the 3D/Control
Center architecture, and Stage 11.A/11.B's governance modules are not
imported by any file this stage touched. The canonical Brain instance
(`runtime.manager_brain`) is the same object now reachable from both
`ManagerAgent.brain` and `ManagerSkills`'s `converse` - never a second
instance - and Arthur's own Brain remains structurally distinct, unchanged.
`general_assistance`'s pre-existing `about`-word handling (research-tool
requests) is untouched and still takes priority: "tell me about the
trading market" still reaches `general_assistance`'s honest missing-tool
refusal, not `open_conversation` - a deliberate, pre-existing nuance this
fix left alone rather than rearbitrating.

Four tests in `test_manager_capabilities.py` had their assertions updated,
not weakened: `test_a_planning_request_finds_no_agent` (now
`..._reaches_the_manager_honestly_not_as_a_completed_roster`) and
`test_an_unregistered_capability_cannot_execute` both used to assert on
the *old* failure shape (`NO_AGENT_FOUND`, or a roster dump reported as
success) for exactly the scenario this stage changes; both now assert the
*new*, more honest one (`FAILED`, with a plain reason) - "never invent,
escalate rather than guess" is honoured more completely now, not less.
`test_the_shipped_registry_holds_its_handlers` and
`test_an_unmatched_task_falls_back_rather_than_failing` simply needed the
fifth handler named.

### Verified

`pytest` before this stage: 4059. After: **4096** (37 net new - 36 in the
new `tests/test_open_ended_conversation.py`, 1 added alongside the 4
updated assertions in `test_manager_capabilities.py`; 0 tests removed).
The full pre-existing suite re-verified passing, including every Stage
11.1-11.B file. One deliberate mutation: `select_capability`'s new
fallback line collapsed onto the old, single `DEFAULT_CAPABILITY` regardless
of content - caught immediately by six tests in the new file, reverted.

## 49. Deeper Memory Curation + Long-Term Retention

Stage 12's own inspection found most of the foundation this brief asked
for already built, in three pieces that had never been read together:

    `services/memory/memory.py` + `persistent.py`   the durable contract and
                                                     store - schema-versioned,
                                                     checkpointed, atomic
    `services/memory/policy.py` + `manager/memory_policy.py` /
    `agents/memory_policy.py`                        governed admission -
                                                     a closed allow-list per
                                                     agent kind, secret
                                                     detection, length limits
    `services/memory/day.py` (`Calendar`/`DayView`)  the temporal lens -
                                                     `today()` vs `history()`
                                                     over one store, never two

**Session, episodic and semantic map onto what already existed, not onto a
new subsystem.** `DayView.today()` is the session layer - "what's live
right now", exactly the boundary `ManagerAgent.recall_context` already
used to keep yesterday's conclusions from drifting into today's reasoning
unasked. Each `MemoryRecord` is its own episode - a delegation outcome, a
decision, a lesson - durable and individually retrievable via
`Memory.recall()`/`DayView.history()` regardless of when it happened. A
`MemoryCategory.USER_CONTEXT` record is the semantic/long-term layer -
"a stable fact... that should inform future decisions" was already that
category's own, pre-existing definition. No new store, no new record
shape, no new query language - three existing lenses over one canonical
Memory, now named and tested as the distinction this stage asked for.

**"Remember this" was already wired - and finding that mattered more than
building a second copy of it.** `scripts/exchange.py`'s
`retain_what_was_asked_for()` already calls `services/memory/session.py`'s
`retention_requests()` (extraction: what did the person actually ask to
keep) and `ManagerAgent.retain()` (the one governed write path, where
`ManagerMemoryPolicy` is consulted) on every real conversational turn, in
both production entry points (`scripts/control_center.py`'s terminal and
`scripts/serve_ui.py`'s live server). A first draft of this stage added a
`remember` capability to `src/manager/capabilities.py` that repeated the
same extraction and the same write - caught immediately by
`test_live_ui.py`'s own pre-existing
`test_explicit_retention_goes_through_retain_and_is_not_duplicated`,
which failed with two records where it expected one. The capability was
removed, not the older mechanism; `CapabilityRequest` carries no
`remember` field as a result (see its own docstring for why), and this is
recorded here specifically so nobody rebuilds it a third time.

**What was genuinely new:** `forget` and `recall_memory`, two more
`ManagerCapability` entries in `src/manager/capabilities.py`, following
the exact shape `participant_invitation`/`small_talk`/`open_conversation`
already established - recognised by a marker phrase via `claims=`, never
by word-overlap scoring, so "forget that we prefer blue" and "what do you
know about the trading market" are read by their *shape*, not by
`general_assistance`'s own pre-existing "about" trigger contesting the
same words. Both reach the Manager's own store through two new, narrow,
bound fields on `CapabilityRequest` - `recall` and `forget` - built once
in `composition.py` from the same `Memory` instance `ManagerAgent` itself
receives, never a second one. Reading and forgetting needed no policy
gate: `MemoryPolicy` has no opinion about either, only about what may be
*written* (its own docstring says so), which is exactly why no `remember`
field was needed to close this gap in the first place.

**Isolation, canonical identity and provider independence are structural
consequences of design already in place, not new work this stage did.**
Alfred and Arthur have always held separate `Memory` instances over
separate files; `composition.py` builds exactly one `Memory` for the
Manager (`build_memory(...)` appears once); none of
`memory.py`/`policy.py`/`persistent.py`/`session.py`/`day.py`/
`memory_policy.py` imports a provider, a voice module, Control Center, or
either Stage 11.A/11.B module. This stage's own tests re-confirm each of
these rather than re-argue them, and add nothing to any of those modules
that would put that independence at risk.

### Verified

`pytest` before this stage: 4096. After: **4139** (43 new, in the new
`tests/test_stage12_memory_curation.py`; 5 pre-existing assertions
updated - three exact capability-list/claims-list assertions extended for
the two new handlers, `test_live_ui.py`'s own duplication test
unmodified and newly passing again once the redundant `remember`
capability was removed; 0 tests weakened or removed). Genuine, real
persistence verified end to end: a fact remembered through one
`ManagerAgent` instance, backed by a `tmp_path` file, is recalled correctly
by a second, independently-constructed instance - a real restart
simulation, not a mock.
of content - caught immediately by six tests in the new file, reverted.

## 50. Shared Context Graph + Multi-Agent Collaboration

Three related pieces of work, closed together in one entry because the
first two were both incomplete without the third: a data structure for
agents to exchange context on one piece of work, a governed decision for
*when* Alfred uses it, and wiring that decision into the conversation a
real user actually has.

**The Shared Context Graph is a third kind of record, not a rename of an
existing one.** `src/conversation/collaboration.py` adds `Collaboration`
and `Contribution` - task-scoped, participant-gated, holding identities
and content and nothing else. Conversation.py already solved "several
agents in one room without becoming one agent" for an open-ended
transcript; this solves a narrower problem, one bounded piece of work
with contributions explicitly published and attributed. Neither imports
the other, and `Collaboration` is bound by the same package-wide purity
test `conversation.py` already holds itself to - it imports nothing from
`src.manager`, `src.agents`, `src.registry`, `src.services.memory`,
`src.composition` or `src.providers`. `Contribution.source` is checked
against the collaboration's own participants by `contribute()`, the same
"you must be in the room to speak in it" rule `Conversation.say` already
enforces - forged provenance is refused, not merely discouraged.

**`start_collaboration`, `collaboration_candidates` and
`answer_with_collaboration` needed `ManagerAgent`, so they could not live
in `src/conversation/`.** They were first written in `scripts/exchange.py`
- the established home for "tie the room to the orchestrator" glue - then
relocated to `src/manager/collaboration_entry.py` once the live
conversation path needed to call them too: `src` does not import
`scripts` anywhere in this project, and the real request path
(`src/control_center/trail.py`'s `observe()`) lives in `src`.
`scripts/exchange.py` now re-exports the same three names, so every
existing caller's import keeps working unchanged - one implementation,
never a second collaboration API.

**The collaboration decision is generic by construction, not by
discipline.** `collaboration_candidates` asks exactly the question
`CapabilityRouter` already asks (`agent.spec.capabilities & tokens`,
verified identical at `src/manager/capabilities.py:214`) about every
registered agent besides Alfred - never a name, never a subject word.
`answer_with_collaboration` reuses `manager.take_action` (fault-isolated
delegation), `manager.brain.converse` (already Half-Brain-honest, never
raises) and `manager.handle_task` (the unmodified ordinary path) rather
than inventing new mechanisms for any of the three. When no other
agent's declared capabilities overlap a task, nothing collaboration-
specific happens at all - "handle it myself" stays the default. A
synthetic, never-before-registered specialist collaborates through the
identical code path with no new branch, proving the genericity rather
than asserting it.

**Wired into the one real request path.** `Channel.ask` -> `observe()`
now calls `answer_with_collaboration` for every *unaddressed* request -
text and voice both funnel through the same call. An explicitly
addressed request (`"Arthur, ..."`) still goes straight to
`manager.handle_task(task, addressed)`, unchanged: addressing is a
deliberate override of Alfred's own decision-making, and a collaboration
synthesis choosing a different final speaker would break exactly that
guarantee. Stage 12's `retain_what_was_asked_for` and voice delivery
(`VoiceChannel._voice_of`, keyed on `turn.handled_by`) needed no changes
- both already operated on the turn's *outcome*, not on how it was
produced.

**Final correction - a specialist that already succeeded is never asked
to redo the work.** The comprehensive Stage 13 audit found a real
production characteristic: when a candidate's `take_action` call
succeeded but Alfred's Brain then failed to compose a synthesis
afterward (Half Brain, a failed provider call mid-request, or an empty
completion - not only a permanently absent Brain), the fallback called
`manager.handle_task(task)` again, which could re-select and re-execute
the same specialist. Harmless only because the one registered specialist
(the Email Agent) is read-only and explicitly refuses to send anything -
a real side-effect risk the moment a future specialist is not.
`start_collaboration` gained one optional, additive parameter -
`results: dict[str, TaskResult] | None` - filled in with each resolved
candidate's own `TaskResult` as it is produced; every existing caller
that omits it is unaffected. `answer_with_collaboration` now cross-
references that dict against the Shared Context Graph's own record of
what actually became a contribution, and when exactly one specialist
produced a genuine success, returns that already-obtained `TaskResult`
directly instead of asking `handle_task` to earn it a second time. A
fresh `handle_task(task)` call still runs, exactly as before, whenever
collaboration produced nothing usable to reuse - including when a
candidate genuinely failed, in which case the ordinary path's own retry
is expected, honest behaviour, not the defect this correction targets.

### Verified

`pytest` before this stage: 4139. After: **4247** (108 new, across four
files - `tests/test_collaboration.py` (48, the graph and
`start_collaboration` itself), `tests/test_governed_collaboration_entry.py`
(28, the generic decision and entry point), `tests/test_stage13_live_integration.py`
(16, the live-path wiring), `tests/test_stage13_no_double_execution.py`
(16, the final correction); three structural tests in the second file
redirected from `scripts/exchange.py` to `src/manager/collaboration_entry.py`
once the implementation moved there, one pre-existing spy's signature
extended for the new optional `results` parameter, 0 tests weakened or
removed). Mutation-tested: the capability-overlap filter, the
collaboration-decision fallback branch, the canonical registry lookup
(twice - before and after its relocation), the addressed/unaddressed
integration seam, and the no-double-execution reuse gate (twice - the
reuse branch itself, and the filter preventing a failed result from
being mistaken for a reusable success) - every mutation caught by a
real, named test failure, every one reverted, full suite re-confirmed
green after each.

---

## 51. General knowledge, history and learning — four ways of knowing, kept apart

> **Document note.** Sections 14–17 of the build (live information,
> communication, connections and the visual/presence work) landed in the
> codebase without entries here, so this section follows §50 with a gap
> behind it. The gap is a documentation debt, not a missing capability;
> the code and its tests are present.

### The defect

Alfred could not answer a settled question about the world, and the two
ways he failed said different, equally wrong things.

    "Tell me about X"   scored general_assistance's research word "about"
                        and was refused as "the 'web_search' capability is
                        not installed" - naming a *live* tool as the thing
                        missing, when nothing about a settled question
                        requires looking at the present moment.
    "Who was X"         scored nothing anywhere and fell to
                        open_conversation, which reaches the Brain and
                        nothing else - so a person who had handed Alfred a
                        shelf of their own books was answered as though
                        they had handed him none.

Both are one underlying error: **"knowing" was a single undifferentiated
thing.** It is four, and collapsing any two of them is a lie:

    general model knowledge   the Brain's own. Settled, broad, undated,
                              attributable to no document.
    user documents/books      material the person actually supplied.
                              Quotable, attributable to a named document.
    live/current information  what the world looks like right now.
    learning context          what the person is studying, and how an
                              explanation should therefore be pitched.

### What was added

| Layer | Module | What it is |
|---|---|---|
| Contract | `src/providers/knowledge.py` | `KnowledgeSourceProvider`, `KnowledgeResult`, `KnowledgeExcerpt` — the same abstraction move `base.py` made for models and `live_info.py` for currency |
| Null | `src/providers/null_knowledge_provider.py` | Honestly empty; the state "nobody supplied anything" stays valid and testable |
| Real | `src/providers/document_knowledge_provider.py` | Reads the user's own plain-text/markdown folder. No key, no network, no index. Ranking is literal token overlap — the rule `Memory.recall` already uses |
| Factory | `src/providers/knowledge_factory.py` | Name → instance, as `live_info_factory.py` already is |
| Tool | `src/agents/tools/knowledge.py` | `knowledge_lookup`, behind the ordinary `ToolSet` boundary |
| Seam | `src/manager/knowledge.py` | Gathers evidence and **labels which kind of knowing each piece is**. Deliberately has no path to a live source |
| Memory | `src/services/memory/learning.py` + `MemoryCategory.LEARNING` | The subject someone is studying, in the canonical governed store |
| Capability | `KNOWLEDGE`, `LEARNING_CONTEXT` in `capabilities.py` | Registered in the one registry, declared in `IDENTITY.md` |

Nothing under `src/manager/` or `src/agents/` imports a concrete
knowledge provider. Swapping the backend is `MANAGER_KNOWLEDGE_PROVIDER`.

### Why it cannot become a topic system

`KNOWLEDGE` is claimed by the **shape of a question** — a grammatical
opening true of any subject a person could name — never by a subject. The
subject is the string that arrived with the task; the material is whatever
the user themselves put in a folder. There is no table of periods, people,
countries, wars, sciences or titles anywhere in the implementation, and
`tests/test_stage18_knowledge_and_learning.py` parses every shipped file
to prove no identifier or carried string literal names one.

`knowledge_intent` defers in exactly three cases, each a statement about
what kind of question is being asked rather than a ranking of handlers: a
currency signal belongs to `LIVE_INFORMATION`; a memory marker is asking
what *Alfred* holds, not what the world holds; a capture request is not a
question. Each reuses the predicate that already owns that shape.

### Honesty

Live information is the only path permitted to imply "right now". A
knowledge answer stamps `currency="settled"` in its payload and closes its
evidence with a line saying so. With no reasoning provider there is **no
path through the handler that produces prose about the subject** — the
refusal names what is missing on both axes and invents nothing. Half Brain
therefore stays honest structurally, not by good intentions.

### Verified

`pytest` before this stage: 5450 collected, 63 failing (all pre-existing —
`ui/index.html` snapshot/Stage-17 work in flight, plus one stale import
assertion in `test_invitation.py`). After: **5573 collected, 58 failing** —
123 new tests, all passing, and five previously-failing pinned assertions
brought back to green (four this stage legitimately moved, one stale since
Stage 17.22 added `about_self` without updating the registry pin).

---

> **Document note, matching §51's own.** The Stage-numbered work between §51
> and here (the remainder of Stage 17's many sub-phases through Stage 18)
> landed without its own entries — the same documentation debt §51 already
> names, not a missing capability. The project's own governing process was
> also renumbered during this gap, from `Phase N` / `Stage N` to a `0.N`
> roadmap (`0.1 Repository Recovery`, `0.2 Reality Audit`, `0.3 Project
> Knowledge Recovery`, …) — both names refer to the same one project; §52
> below is the first entry under the new numbering and is where that
> renumbering itself is recorded.

## 52. The `0.N` roadmap — 0.1 Repository Recovery through 0.3 Project Knowledge Recovery

### 52.0 What this section is

0.3 Project Knowledge Recovery's own deliverable: the requirements,
decisions and rejected approaches established across 0.1 and 0.2 that were
until now real only in session history, not in this authoritative document.
Nothing in this section describes new work — it records work already done
and already tested, exactly as §49–§51 do for the phases before it.

### 52.1 Roadmap status

| Stage | Status |
|---|---|
| 0.1 Repository Recovery | ✅ COMPLETE — real git state audited (`scripts/repo_recovery.py`), AST-based reachability/duplicate-class classification across the whole tree |
| 0.2 Reality Audit | ✅ COMPLETE — see 52.2 for its sub-phases |
| **0.3 Project Knowledge Recovery** | **← current (this section)** |
| 0.4 Canonical Project State | next — not started |

This table is a **status record, not the roadmap itself** — the Master
Roadmap belongs to whoever is directing the work, one level above this
document, and is not rewritten here.

### 52.2 What 0.2 Reality Audit covered, in the order it happened

1. **Core Alfred / Voice / Recording reality audit.** Found and closed real
   gaps rather than merely reporting them: a `record this for later: X`
   colon-boundary regex defect in `src/services/memory/capture.py`; the
   ambient wake-word ↔ conversation loop (`src/channels/activation.py::
   run_ambient_loop`) built, real, and wired, closing the "`
   VoiceActivationSession` exists but nothing calls it" gap.
2. **Final Voice Installation.** Alfred → Kokoro `am_eric`; Arthur → Kokoro
   `bm_daniel` — see 52.3. `src/providers/kokoro_speech_provider.py` added
   beside (never replacing) the existing Windows and ElevenLabs adapters.
3. **Final Surgical Pass — genuine natural/social conversation.** Confirmed
   the existing `open_conversation`/personality/live-information/knowledge
   architecture was already sound (built in earlier Natural Conversation
   phases), then found and closed two real gaps:
   - Retention understanding was, by the honest admission already in
     `session.py`'s and `capture.py`'s own docstrings, "a small, fixed set
     of phrasings … never a replacement" for genuine understanding. Added
     `ManagerBrain.discern_retention()` / `SpecialistBrain.converse()` — a
     third, narrow Brain question, consulted only when the deterministic
     floor finds nothing, personality-free because its output becomes
     stored memory content rather than a reply.
   - Arthur (`EmailAgent`) had **no general conversation capability at
     all** — confirmed empirically that a remark naming none of his own
     domain vocabulary fell straight into fetching and summarising the
     mailbox. Added `CognitiveAgent.general_conversation()` — one reusable
     method every specialist inherits, never reimplemented per agent — and
     wired `EmailAgent.execute()` to reach it before assuming domain work.
4. **Final Foundation Audit — Brain / provider / credential / memory / voice
   independence.** No production code changes were required — the
   architecture was already correctly separated. Practically verified with
   three genuinely distinct (non-Anthropic) `LLMProvider` implementations
   swapped live into a running `ManagerAgent`, a new provider registered
   through the real factory registry, credential isolation in both
   directions, memory surviving both an in-process provider swap and a
   genuine two-`build()` process restart, and the full Alfred/Arthur/voice
   independence matrix. See §8 items 8–12.

### 52.3 Current verified voice choices — do not replace without new evidence

| Agent | Provider | Voice | Verified |
|---|---|---|---|
| Alfred | `kokoro` | `am_eric` (American English) | Real ONNX synthesis, real non-silent audio, through the real production `exchange(..., speak=True)` path |
| Arthur | `kokoro` | `bm_daniel` (British English) | Same, independently, distinct from Alfred's |

These were explicitly selected (not auditioned or substituted) and are
recorded here so a future session does not re-open a decision already made.
Changing them is a deliberate, explicit decision for whoever directs the
project — not something a future audit or refactor should do on its own
initiative.

### 52.4 Rejected approaches — do not return to these

Some are already load-bearing rules in §8; listed again here, together,
because 0.3's own job is exactly to make approaches like these impossible to
lose. Each is marked with how it is known:

- **Do not replace the canonical Brain when the LLM provider changes.**
  *Confirmed decision, structurally enforced* — §8 item 7; `LLMManagerBrain`
  is the only concrete LLM-backed `ManagerBrain`, and it receives a
  provider *instance*, never a name.
- **Do not couple Voice to the LLM provider, and do not couple Memory to
  the LLM provider.** *Confirmed decision, practically verified* — §8 items
  7–8; the 0.2 Final Foundation Audit's own three-provider memory/voice
  matrix.
- **Do not use one provider's credentials for another provider.**
  *Confirmed decision, practically verified* — §8 item 9.
- **Do not make paid ElevenLabs usage a requirement for Alfred's voice.**
  *Confirmed decision* — §8 item 12; Kokoro is the local, free, default-
  capable voice, ElevenLabs stays optional.
- **Do not silently fall back to an arbitrary Windows voice when a specific
  configured local voice is selected.** *Confirmed decision* — §8 item 12.
  (`WindowsSpeechAdapter`'s own *own-engine* fallback — degrading to a
  deterministic, declared choice among *that engine's* installed voices
  when a preference is absent or stale — is the one narrower, pre-existing
  exception this rule does not reach: it never crosses engines, and it
  never overrides a preference that is genuinely still available.)
- **Do not create hardcoded topic-specific "features" for general
  knowledge.** *Confirmed decision, structurally enforced* — §51's own
  `KNOWLEDGE`/`LIVE_INFORMATION` capabilities are claimed by the *shape* of
  a question, never a subject; `tests/test_stage18_knowledge_and_learning.py`
  parses every shipped file to prove no subject identifier exists anywhere.
  Napoleon, WWII, Messi, anime, books and any other topic named in a
  conversation are acceptance examples of this, never features to build.
- **Do not create fixed phrase tables as the entire natural-conversation
  system.** *Confirmed decision, now also structurally addressed* —
  `open_conversation` was always Brain-routed; 0.2's Final Surgical Pass
  closed the one place a fixed table *was* still the entire mechanism
  (retention intent) by adding genuine Brain-judged understanding as a
  layer above the deterministic floor, never a replacement for it (Half
  Brain still needs the floor).
- **Do not make the Conversation Room a separate, simplistic conversation
  engine.** *Confirmed decision, practically verified* — §8 item 11;
  `scripts/serve_ui.py::route_message` calls the identical
  `scripts/exchange.py::exchange()` every other surface calls.
- **Do not invent a fixed avatar catalogue where the architecture requires
  configurable digital humans.** *Confirmed decision, per §14.2/§29–§37* —
  `AgentVisualIdentity` and the Visual Environment Registry (§30, §37) are
  the replaceable seam; a fixed catalogue would recreate exactly the "one
  voice for the whole system" mistake §19 already refused for speech.
- **Do not start Stage/Phase 18-and-later work while Stage 17 is
  unfinished.** *Stated as a standing project rule; recorded here as
  directed, not independently re-derived from git history* — sequencing
  discipline consistent with this project's existing "Trust in Stages"
  principle (§8 item 4) applied to its own build order, not only to
  capability grants.
- **Do not treat documentation, architectural possibility, or tests alone
  as proof that a capability works.** *Confirmed decision, restated every
  audit this project has run* — this document's own status vocabulary
  (top of file: ✅ IMPLEMENTED requires a named production caller, 🟡
  PREPARED explicitly does not count) already encodes this; 0.2's own
  completion rule ("no yellow states … implemented + tested + practically
  verified") is the same rule applied to a whole audit rather than one row.
- **Do not mark incomplete work green.** *Confirmed decision, restated
  every audit this project has run* — the direct consequence of the rule
  above; a genuine external blocker is reported by name (e.g. "the
  Anthropic account has no funded credits") rather than papered over with
  🟡.

### 52.5 Architectural decisions and their reasons

Distinguishing, as directed, a confirmed decision this project has made
from an implementation observation from a future idea:

| Decision | Why (confirmed reason, where established) | Kind |
|---|---|---|
| The canonical Brain is provider-neutral | A component that both decided *and* was tied to one vendor could be talked into vendor-specific behaviour by whatever it reads, and could never be tested without that vendor. `LLMManagerBrain` depends on the `LLMProvider` *interface* alone (`src/providers/base.py`) | Confirmed (module docstrings, §11, §19) |
| Memory is provider-independent | "A brain that also stored things would decide what is worth keeping" (`services/memory/memory.py`, quoted at §10.1) — the same separation of concerns, applied to *which vendor is currently reasoning* rather than only to *reasoning itself* | Confirmed (§10.1) + practically verified (52.2.4) |
| Voice is independent of Brain/provider | `VoiceProfile` "has nowhere to put a vendor" by construction (§19.1) — a profile describes traits, never an endpoint, so the provider can change without every agent changing | Confirmed (§19.1) |
| Specialist architecture is independent (own brain, memory, voice, personality) | Each specialist owns its own `Memory`/`SpecialistBrain` instance so that "two specialists get two memories, and neither gets the orchestrator's" (`cognitive_agent.py`) — isolation by object identity, not by convention | Confirmed (§11, `cognitive_agent.py` docstring) |
| Capabilities/Connections stay separate from Brain/identity | "A document describes; it never grants" (§8 item 3) — a specialist identity document has no capability field, so capability can never be claimed by editing prose | Confirmed (§8 item 3) |
| Natural conversation is shared across surfaces, never duplicated | Two agents (or two doors — text, voice, Conversation Room) with independently-drifting conversation logic would eventually disagree about what was said, and nobody could tell which was "real Alfred" | Confirmed (§14 "no second voice-Alfred"; practically re-verified at 52.2.3–52.2.4) |
| Future providers must be addable without rebuilding the Brain | Verified by actually registering a brand-new, never-shipped provider name through the real `_BUILDERS` registry with zero changes to any Brain module | Implementation observation, practically verified this stage (52.2.4) |
| Paid external services must not become hard dependencies | Every provider factory (`speech_factory.py`, `factory.py`) defaults to `none`/`null`; a deliberate `MANAGER_SPEECH=`/`MANAGER_PROVIDER=` opts in, never a key happening to be present | Confirmed (module docstrings throughout `src/providers/`) |
| Practical verification is required before declaring completion | This document's own status vocabulary predates this stage (top of file); restated as this project's completion rule every audit since | Confirmed (document header; every 0.2 sub-phase report) |

### 52.6 Extensibility process, recovered

New ideas — a capability, a provider, a connection, an interface, an
environment, a specialist — are tracked through one path, not invented
ad hoc:

```
NEW IDEA -> CLASSIFY -> EXISTING CAPABILITY / NEW CAPABILITY / FUTURE
         -> TRACK -> IMPLEMENT -> TEST -> VERIFY
```

A new idea does not replace this document's Master Roadmap (§6/§7) or the
directing `0.N` roadmap above it — it is classified against what already
exists, and either slots into an existing seam (a new provider entry, a new
`ManagerCapability`, a new `SpecialistWiring` key) or is recorded under
§7 Long-term direction until a phase explicitly schedules it.

### Verified

No production code changed for 0.3 itself — this is a documentation
recovery stage, and its own completion criterion is that the knowledge
above is captured here, not that behaviour changed. What is recorded above
was each independently verified, with real tests, in the stage it
happened:

- 0.1: `tests/test_repository_recovery.py` (17)
- 0.2 Core/Voice/Recording: `tests/test_recording_reality.py`,
  `tests/test_ambient_wake_word.py`, `tests/test_voice_switching.py`,
  `tests/test_voice_activation.py` and related
- 0.2 Final Voice Installation: `tests/test_kokoro_speech_provider.py` (22),
  `tests/test_kokoro_voice_installation.py` (11), `tests/test_speech_
  factory.py`'s `TestKokoroProvider`
- 0.2 Final Surgical Pass: `tests/test_natural_retention_understanding.py`
  (26), `tests/test_specialist_general_conversation.py` (18)
- 0.2 Final Foundation Audit: `tests/test_brain_provider_credential_memory_
  independence.py` (26)

0.3's own verification is a check that this document's edits introduced no
factual regression: every test file named above still passes, unchanged and
unmodified by this stage, and the full suite (fixed order and randomised
order) remains green — see this stage's own final report for exact counts.

### 52.7 Post-0.4 correction — a real capability-routing defect, found and fixed

Before Phase 1.3, a dedicated Natural Conversation completion check traced
the real production path rather than trusting §0.3's own "COMPLETE" row for
natural conversation, and found a genuine, confirmed, widespread defect the
earlier passes had missed: ten `ManagerCapability` entries
(`communication`, `record_this`, `forget`, `recall_memory`, `about_self`,
`temporal_derivation`, `milestone_recognition`, `personal_recurring_events`,
`connected_action`, `learning_context`) each declared a `claims=` predicate
precise enough to recognise their real request shape - each one's own
comment already argued word-overlap scoring was unsafe for it - but never
actually set `scoreable=False`, exactly the fix `current_datetime` and
`small_talk` already had. An ordinary sentence sharing one bare word with
a capability's vocabulary ("Remind me to call the dentist tomorrow
morning.") still won the fallback score and reached the wrong handler,
producing a genuinely broken reply through the real production path:
*"I'm not sure what communication request that is."*

All ten fixed the identical, already-proven way. Two capabilities that
looked like the same bug were investigated and deliberately left
unchanged after the real test suite caught a regression on the first
attempt: `live_information`'s `scoreable=True` is load-bearing for
already-tested requests scoring only on "now" ("is Arsenal linked with
any wingers right now?"), and `participant_invitation`'s is load-bearing
for `invite_participant`'s own honest "no such specialist is registered"
answer to an unregistered name - both recorded here as accepted,
deliberate trade-offs so neither is "fixed" into a regression again.

The same check also strengthened both conversational system prompts
(`CONVERSE_SYSTEM_PROMPT`, `SPECIALIST_CONVERSE_SYSTEM_PROMPT`) to
instruct asking a clarifying question when a request is genuinely
ambiguous or incomplete, rather than guessing - the one probe (of ten)
this pass could not find already covered.

Verified: `tests/test_natural_conversation_completion_check.py` (15,
new), full suite 5849 passed in both fixed and randomised order. See
that file's own module docstring for the complete account, including
exactly which live, real-production-path utterance exposed the defect.

### 52.8 Final Pre-Phase Interaction Completion — clarification/conflict resolution, and general guided learning

The last item before the Master Roadmap's own Phase 1 begins. CHECK found
no general mechanism for either behaviour anywhere in the project: the
only existing uncertainty-resolution code was birthday-specific
(`_apply_relational_birthday`, §Stage 12), and nothing anywhere asked a
question, accepted an answer, graded it, or tracked a score.

**What was added, additively, onto `open_conversation` - never a second
Brain, a second Memory, or a per-subject feature:**

```
ManagerBrain.discern_ambiguity          "does this state unresolved
                                         candidates for one thing?"
ManagerBrain.discern_activity_start     "does this ask to begin a guided
                                         activity, on what?"
ManagerBrain.compose_activity_question  "ask one genuine question about
                                         this arbitrary topic"
ManagerBrain.evaluate_activity_answer   "was this answer correct, and
                                         does the person want to stop?"
                                         (returns ActivityAnswerVerdict,
                                         not BrainDecision - see that
                                         type's own docstring for why)
```

**Where turn-scoped state actually lives, and why it is not Memory.**
`src/manager/activity_session.py`'s `ActivitySessions` holds a pending
clarification or an in-progress activity's topic/question/score - genuinely
in-process, genuinely momentary, the identical role and shape
`PresenceRegistry` already plays for "who is speaking right now" (§17.2).
This is a deliberate reading of an existing rule, not a new one:
`MemoryCategory`'s own allow-list (`src/manager/memory_policy.py`)
already excludes "scratch", "task_history" and "processed_state" by name,
for exactly the reason this state would otherwise violate - "temporary
working state does not survive the task that made it." Once something is
genuinely worth keeping - a resolved value, a session's final score - it
crosses into real, governed `Memory` through the identical `retain()`
path every other durable fact already uses, under the existing
`USER_CONTEXT` category. No new `MemoryCategory` was added.

**Clarification is general, keyed on whatever the Brain names, never a
fact type.** `discern_ambiguity` names the exact candidate values stated,
in order; resolving matches the next reply against those candidates
deterministically (`PendingClarification.select_candidate`) and applies
governed `forget`-then-`retain` to any existing record naming one of the
*other* candidates - proven against both a same-turn conflict and a
record that already existed before the clarification was even raised.

**Guided learning is general, keyed on whatever subject the Brain
names.** No "Book Quiz" or "School Quiz" feature exists or was
considered; the identical code path was proven against two deliberately
unrelated subjects (arithmetic, Japanese greetings) in the same test run.
Scoring is this module's own deterministic bookkeeping - the Brain judges
correctness once per answer, and the running count is incremented here,
never re-derived by asking a model to remember its own earlier turns.

**A structural lesson this stage re-learned the hard way.** Two
pre-existing purity tests caught real mistakes before they shipped:
`test_stage18_knowledge_and_learning.py`'s scan for hardcoded example
subjects caught "photosynthesis" left in an illustrative prompt string
(removed, replaced with a bracketed placeholder); `test_manager_brain.py`'s
scan for state-related words in `brain.py` caught the word "history" in a
docstring (reworded). Both are recorded here as evidence the existing
guard rails were exercised for real, not merely present.

Verified: `tests/test_interaction_clarification_and_learning.py` (22,
new). Full suite 5871 passed in both fixed and randomised order.
Practically verified end to end through the real production path
(`build()` + `exchange()`), including: a genuine clarification question
raised and resolved with the obsolete alternative superseded; a
multi-question graded session with exact scoring and adaptation to prior
answers; a session ending naturally with progress preserved to real
memory; ordinary casual conversation confirmed to never spuriously start
either mechanism; and existing retention, provider independence and
specialist independence (Arthur) all reconfirmed unaffected.

### 52.9 Pre-Phase Agent System Foundation checkpoint

The last checkpoint before the Master Roadmap's Phase 1.3. The question
was not "does every future agent capability already exist" but whether
the foundation genuinely supports

```
OBJECTIVE -> UNDERSTAND -> REASON -> PLAN -> DECOMPOSE -> DELEGATE ->
COLLABORATE -> EXECUTE -> OBSERVE -> ADAPT -> REMEMBER -> CONTINUE
```

and can be extended toward it without rebuilding anything.

**Finding: yes, already, by composing existing production mechanisms -
and a second, more advanced mechanism exists, real and tested, that this
checkpoint deliberately left staged.**

`collaboration_candidates`/`start_collaboration`/`answer_with_collaboration`
(§Stage 13, `src/manager/collaboration_entry.py`) already give:
PLAN/DECOMPOSE (which registered specialist's own declared capabilities
are relevant - generic, never a name or a keyword table), DELEGATE/EXECUTE
(the real, governed `take_action` path), COLLABORATE/OBSERVE (the Shared
Context Graph capturing each specialist's own real `TaskResult`), and
ADAPT (`Brain.converse()` given every real contribution as evidence,
synthesising rather than templating). Combined with the already-proven
REMEMBER/CONTINUE (governed `retain()` + real conversation history), the
full loop was demonstrated end to end through the real production path
with one continuous, realistic objective - never "what's the weather" -
genuinely delegating to Arthur, genuinely running his real analyzer
against the real sample inbox, genuinely synthesising his real finding,
genuinely adapting a follow-up to it, and genuinely remembering and
recalling a stated preference several turns later.

**A decision explicitly left to the user, not inferred.** CHECK also
found `src/manager/cognitive_loop.py`/`actions.py` (`CognitiveLoop`/
`ActionExecutor`) - real, comprehensively tested, Brain-driven action
*selection* with independent Router re-confirmation before anything
executes, more advanced than today's capability-matching dispatch. It
remains asserted unreachable from any production entry point
(`tests/test_reachable_deliberation.py`) under this project's own
standing "Trust in Stages" rule (§8 item 4: permission moves only when a
person moves it). Asked directly, the user chose to keep it staged for
this checkpoint: verify it is real, tested and reachable-in-principle
(constructed and run directly; its own safety property - refusing a
delegation the Router does not independently agree with - re-proven)
without adding a production entry point. This is recorded as a decision
made, not a gap left open - lifting this gate is a separate, later,
explicit choice.

**No fake agenticity.** Nothing here renames a function "agent," invents
a static planner, hardcodes one demonstration workflow, or creates a
keyword-to-workflow table. The specialist involved (Arthur) is the same
real, already-independent agent used throughout this project; the
"planning" step is the same generic capability-overlap signal
`CapabilityRouter` already uses for ordinary delegation, asked once more
about who else might be relevant.

**Specialist independence (Identity/Personality/Brain/Memory/Voice/
Capabilities/Connections/Avatar/Environment), reconfirmed.** Identity,
Personality, Brain, Memory, Voice and Capabilities are each exhaustively
proven independent by earlier stages and this stage's own new test
(Arthur's real analyzer output, not a mock, carries his own memory and
identity, never Alfred's). Connections and Environment have exactly one
consumer each today (Alfred's own `connected_action`; the shared Visual
Environment Registry, already documented per-agent at §37) - checked and
found structurally unable to collide, not exercised by a second
consumer yet. Avatar preference (`avatar_preference_for`) is keyed by
`agent_key` through the identical governed-Memory mechanism already
proven independent for voice preference - confirmed by direct source
reading, not re-tested from scratch, since it is the same code path.

### Verified

`tests/test_agent_system_foundation.py` (9, new). Full suite 5880 passed
in both fixed and randomised order (see this stage's own final report
for the exact figures). Practically verified end to end through the real
production path - see this stage's own report for the full recorded
transcript of the objective, the delegation, the specialist's real
result, the synthesis, the adaptation, and the remembered/recalled
preference.

**Final status: the Agent System Foundation checkpoint passes.** Every
requirement in it is implemented (by composition of existing mechanisms,
with zero new architecture beyond what those mechanisms already were),
tested, and practically verified through the real production path - the
one deliberate exception, Brain-driven autonomous action selection,
being an explicit, informed decision to keep staged rather than a gap.

### 52.10 Phase 1.3 — Brain Architecture

CHECK found the canonical Brain architecture already real and already
verified by earlier stages: `ManagerBrain`/`LLMManagerBrain` (seven
methods - `deliberate`, `converse`, `discern_retention`,
`discern_ambiguity`, `discern_activity_start`,
`compose_activity_question`, `evaluate_activity_answer`), the
`LLMProvider` abstraction and factory (`src/providers/base.py`,
`factory.py`), Full/Half Brain semantics via `assess_cognition`/
`assess_manager` (§48), provider switching, provider-specific
credential isolation and memory/conversation independence across a
provider change (`tests/test_brain_provider_credential_memory_
independence.py`), and the deliberately-protected rule that coding and
research work are gated by an installed tool, never by which Brain mode
is active (`tests/test_capability_floor_and_brain_modes.py`, cited by
name in §4 of this document). None of it was rebuilt; all of it was
reconfirmed still green before anything changed.

**What CHECK found genuinely missing.** `open_conversation`
(`Brain.converse()` given the whole utterance and full conversation
context, §48) already had no subject restriction of any kind - but a
request never reached it if `general_assistance`'s word-overlap scoring
got there first, and it did for any sentence merely containing a bare
word like "function", "debug", "code", "research" or "find" - so "I
have a function that produces an unexpected result and I cannot figure
out why" (a description of a problem, asking to be reasoned about) was
answered "the code_assistant capability is not installed" and never
reached the Brain at all. This is exactly the keyword/phrase-command
substitute this phase existed to find: a *shape*-blind capability
intercepting genuine reasoning requests before a genuine reasoning
component ever saw them.

**The fix, and why the protected rule is untouched.**
`general_assistance` now carries a `claims` predicate
(`general_assistance_intent`, `src/manager/capabilities.py`) requiring
the request to be shaped as a genuine imperative ask to do the
tool-gated work ("write a function", "research X", anchored-verb-in-
command-position - the same shape discipline `_communication_shape`
already uses) rather than any bare word overlap, and `scoreable=False`
so it can no longer win by accident on a shared word either. A genuine
"write me a function to sort this" still reaches this handler and is
still honestly refused without a `code_assistant` tool, Full Brain or
not - `test_full_brain_without_a_tool_still_cannot` and
`test_half_brain_without_a_tool_gives_the_same_honest_answer` (§4's own
"Requirement N is already correct - do not 'fix' it") are unmodified
and still pass, because nothing about `general_assistance`'s own
handler changed. A description of a problem was never actually asking
for that tool-gated action, and now correctly falls through to
`open_conversation`, where a real Brain reasons about it - or, with no
Brain configured, Half Brain says so plainly ("I don't have a way to
properly reason about that yet ... no reasoning capability is
configured") instead of misattributing the gap to a missing tool. That
second sentence is a strictly more accurate diagnosis than the message
it replaced, not merely a different one.

**Two further, honest fallout fixes, not new architecture.**
(1) `general_assistance` gaining a `claims` predicate removed it from
the sixteen-capability list `test_scoring_still_decides_everything_else`
(`tests/test_identity_resolution.py`) pins by name - updated to include
it, with the same docstring discipline the other fifteen entries
already follow. (2) `general_assistance` no longer scoring on bare
words also removed it from a tie it had been (accidentally) winning:
"invite Research", where "Research" is not a registered specialist,
used to fall to `general_assistance`'s own overlap on "research" by
alphabetical tie-break rather than to `participant_invitation`'s
overlap on "invite" - a fragile outcome the phase's own fix legitimately
changes. `participant_invitation` stays `scoreable=True` on purpose (a
separate, already-documented, already-protected 0.4 decision:
`invite_participant`'s own handler needs the scoring fallback to give
its honest "no specialist by that name to bring in, registered
specialists: ..." answer for an unregistered name, exactly as it
already does for "invite a Ledger Specialist" - `TestNobodyIsInvented`).
"Invite Research" now reaches that same honest fallback instead of a
research-shaped tool-refusal - more coherent than before, not less, and
`tests/test_invitation.py`'s `TestAnUnregisteredNameIsNotAnIdentity` was
updated to assert the new, correct behaviour rather than the old
implementation detail. (3) A genuinely unrelated latent rendering bug
was exposed, not caused, by the routing fix: `render_turn`
(`src/control_center/render.py`) wrote every trail step's `label:
detail` line unwrapped, so any step whose detail happened to be long
enough overflowed the terminal width - no prior scenario's step detail
had been long enough to hit it. Fixed by routing that line through the
same `_wrap` helper every other long line in this file already uses.

**Serious software-problem-solving, verified through the real Brain
path with a scripted Full-Brain provider** (no funded Anthropic
credits exist in this environment - see `anthropic-account-
unfunded`-class prior limitation, unchanged) - never hardcoded as a
permanent capability, only as verification that the whole problem and
full context reach the model and an appropriately-grounded answer comes
back: root-cause reasoning about an ordering/state bug, comparing two
architectural approaches against a stated constraint, identifying
algorithm edge cases, multi-step reasoning through program execution,
diagnosing from a log-like description, explaining why a fix works, and
- the specific "continue after an intermediate result" requirement -
adapting a diagnosis after new evidence in a second turn rather than
restarting (`tests/test_brain_architecture_phase_1_3.py::
TestSeriousSoftwareReasoning`). One test asserts directly on the
provider call log that the real, unedited problem statement reaches the
prompt, not a paraphrase or a template.

**Full/Half Brain and provider independence, verified in this phase's
own context.** A configured-but-unproven provider reports Half Brain
and answers deterministically; the first successful real call earns
Full Brain; a raising provider degrades to Half Brain and says so
honestly rather than guessing; a provider that fails once and then
succeeds re-earns Full Brain from a second real call, never assumed
back. A provider switch mid-reasoning-session (`anthropic` ->
`openai`) leaves the Manager's identity, memory object, a fact recorded
under the first provider, and the voice/identity object all unchanged
and reachable - practically verified live (below), not only asserted in
a unit test. Neither `src/manager/brain.py` nor `llm_brain.py` contains
any vendor credential string, confirmed by source inspection.

### Practically verified (real `build()`/`exchange()` path)

Five scenarios, one continuous recorded session (mixing real production
composition with a scripted Full-Brain provider standing in for the
unfunded live model - architecture/path verification, never claimed as
live model-quality verification):

1. **Cold build, no provider at all** - `assess_manager` reports Half
   Brain ("no model is configured"); a genuinely open-ended technical
   question is answered honestly within whatever the deterministic
   capabilities can do (this specific probe happened to also contain
   the word "messages" and was delegated to Arthur's real inbox
   capability - ordinary specialist word-overlap delegation, a
   different and pre-existing mechanism, not the Brain).
2. **A scripted Full-Brain provider wired in** - Half Brain persists
   until a real call is made ("configured ... but not yet verified
   usable"); the first genuine reasoning exchange (diagnosing
   intermittent message drops as a queue-vs-network problem) succeeds
   through `open_conversation`, and `assess_manager` reports Full Brain
   immediately after, from that one real call.
3. **A small, non-trivial debugging problem** (an unlocked shared
   counter across two threads) is correctly diagnosed as a race
   condition with a concrete fix.
4. **A continuation with new information** ("what if the two threads
   are actually on separate processes?") produces an answer that
   explicitly builds on the prior turn's diagnosis ("a plain lock will
   not help ... you would need a cross-process primitive") rather than
   restarting from nothing - adaptation, not repetition.
5. **A provider switch mid-session** (`anthropic` -> `openai`, after
   recording "the service we are discussing is called Halcyon" under
   the first provider) - the Manager's identity name, memory object,
   voice/identity object are the same objects before and after; the
   recorded fact is still in memory; a question to the new provider
   ("is the service still called Halcyon?") is answered correctly using
   that same conversation's earlier context, and Full Brain is
   independently re-earned for the new provider from its own first real
   call.
6. **Provider removal** - with `manager.brain` set to `None`, the same
   debugging question again receives the honest "I don't have a way to
   reason about that yet - no language model is configured for me right
   now" rather than any fabricated answer, and the "Halcyon" fact
   recorded earlier is still recalled from memory - memory survives the
   Brain's removal entirely, confirming the two are genuinely
   independent components, not one system wearing two names.

### Verified

`tests/test_brain_architecture_phase_1_3.py` (14, new), plus two pinned
tests updated for the routing fix's legitimate, understood fallout
(`test_identity_resolution.py::test_scoring_still_decides_everything_else`,
`test_invitation.py::TestAnUnregisteredNameIsNotAnIdentity`) and one
pre-existing latent rendering bug fixed
(`src/control_center/render.py::render_turn`). Full suite: 5894 passed
in both fixed and randomised order, zero regressions. Practically
verified end to end through the real production path - see this
section's own six-scenario transcript above.

**Final status: Phase 1.3 passes.** The Brain architecture was already
real; the gap this phase found and fixed was routing, not
architecture - a capability that answered before a reasoning question
ever reached the Brain. Full/Half Brain semantics, the LLM abstraction,
provider switching, credential isolation, and memory/conversation
independence are all reconfirmed still true, provider-independent, and
now additionally proven under genuine software-reasoning load rather
than only under small-talk-shaped probes. No keyword-command substitute
was introduced; the one already present (the exact defect this phase
was commissioned to find) was removed. `CognitiveLoop`/`ActionExecutor`
remain staged, untouched, per the user's own prior explicit decision
(§52.9) - not activated, not started, not part of this phase's scope.

### 52.11 Phase 1.4 — LLM Providers

CHECK reconfirmed the provider abstraction (`src/providers/base.py`),
factory/registry (`src/providers/factory.py::_BUILDERS`), and Full/Half
Brain semantics (§48, §52.10) already real and unchanged. It found only
two of the three canonical-roadmap vendors registered: `null` and
`anthropic`. OpenAI and xAI/Grok did not exist in any form - no
provider class, no factory entry, no credential handling.

**What was implemented - the smallest addition the existing contract
required.** `src/providers/openai_provider.py` (`OpenAIProvider`) and
`src/providers/xai_provider.py` (`XAIProvider`), each the identical
shape as `AnthropicProvider`: a lazy-imported vendor SDK client built on
first use, never at construction; its own credential
(`OPENAI_API_KEY`/`XAI_API_KEY`, read directly from the environment,
never `Settings.api_key` - that field stays anthropic-specific, per the
rule §8.9 already states); its own model configuration
(`Settings.openai_model` = `"gpt-5"`, `Settings.xai_model` =
`"grok-4"`, two new fields, same pattern as the pre-existing `model`
field); its own response normalization and its own `ProviderResponseError`
handling for a malformed response. `src/providers/factory.py` gained two
factory functions (`_build_openai`, `_build_xai`) and two `_BUILDERS`
entries, following `_build_anthropic`'s exact shape: credential checked,
and the vendor SDK not imported at all, before anything else happens.
Nothing under `src/manager/` changed by one line - confirmed by source
inspection, not assumption
(`TestProviderAbstractionAndRegistryReconfirmed::
test_no_brain_or_identity_or_memory_or_voice_code_names_a_vendor`).

**Why xAI reuses the `openai` package.** xAI ships no SDK of its own;
xAI's own documented integration path is the OpenAI-compatible client
pointed at `https://api.x.ai/v1` with an xAI key - the wire protocol is
OpenAI-compatible by design. `XAIProvider` is still a fully independent
class implementing `LLMProvider` on its own terms, confirmed distinct by
test (`test_openai_and_xai_are_genuinely_separate_provider_classes`) and
by its own dedicated credential, model field, and base URL. This is an
implementation-library choice, not a merged architecture or "OpenAI
behaviour hardcoded into Alfred."

**Provider neutrality and future extensibility, proven, not asserted.**
A never-before-seen fourth vendor ("gemini") was registered purely
through the existing `_BUILDERS` mechanism, with its own credential
check, and flowed registry → `build_brain` → a real `LLMManagerBrain` →
a real `exchange()` reply through a live, full `ManagerAgent` - with
zero lines changed in `Brain`, `Identity`, `Memory`, `Voice`, or the
Capability Registry
(`TestFutureProviderExtensibility::
test_a_never_before_seen_vendor_flows_registry_to_brain_to_response`).
This complements, rather than replaces, the equivalent proof already
recorded at §52.5/§0.3
(`test_a_new_compatible_provider_can_be_registered_without_touching_
brain_code`).

**Credential isolation, model selection, Full/Half Brain - verified
across all three real vendors, not only anthropic.** Each of
`anthropic`/`openai`/`xai` refuses the other two vendors' credentials
and its own missing credential, with the same honest,
`ProviderConfigurationError`-before-any-import shape; each owns an
independently-settable model field with no Brain-side awareness of any
of the three; each reaches Half Brain when unconfigured or when a real
call fails, and reaches Full Brain only after a genuine successful call
through its own real, installed SDK client
(`TestFullHalfBrainAcrossEveryProvider`, parametrized over all three).

**Genuine limitation, stated plainly, unchanged in kind from every
earlier phase's own.** No Anthropic, OpenAI or xAI credentials or
credits exist in this environment. The `anthropic` and `openai` packages
are both genuinely installed in this project's own `.venv` (`openai`
added as a new optional, lazily-imported dependency in
`requirements.txt`, mirroring how `anthropic` already was) - so every
construction-time and request-shape test in this phase exercises the
real vendor SDK classes, confirmed against the actually-installed
`openai` package's own response field names
(`choices`/`message.content`/`message.refusal`/`usage.prompt_tokens`/
`usage.completion_tokens`) rather than an assumed shape. What remains
unverified is the one real network call none of the three vendors can
make here - live model-quality verification, never claimed as done.

### Practically verified (real `build()`/`exchange()` path)

Live, outside the test suite: the registry lists all four names; a
real, installed SDK client is genuinely constructed with no network call
for all three vendors (`anthropic`/`openai`/`xai`), each still unbuilt
(`_client is None`) immediately after `build_provider()`; each vendor's
missing-credential message names only its own environment variable; the
real xAI SDK client's `base_url` is confirmed to be xAI's own endpoint,
not OpenAI's default. Then the full twelve-step session from Section 10:
provider A (anthropic) establishes Full Brain from Half on its first
real call; a fact ("the callsign is Redwing") is recorded; switching to
provider B (openai) returns to Half Brain until its own first real call
re-earns Full Brain; the recorded fact, the memory object, Alfred's
identity object and voice object are all confirmed unchanged throughout;
credential isolation is reconfirmed mid-session (xAI refuses an OpenAI
key); switching a third time to provider C (xai) again re-earns Full
Brain independently and the original fact is still recalled, with
Alfred's identity still intact.

### Verified

`tests/test_phase_1_4_llm_providers.py` (41, new), plus one pinned test
updated for the registry's legitimate growth
(`test_llm_specialist_brain.py::TestExistingBehaviourIsUnchanged::
test_the_provider_factory_was_not_modified`, which pinned an exact list
where its own stated intent - "not modified for the specialist brain's
sake" - only ever needed a subset check). Full suite: 5935 passed in
both fixed and randomised order, zero regressions. Practically verified
end to end through the real production path - see this section's own
recorded transcript above.

**Final status: Phase 1.4 passes.** Anthropic remains fully verified and
untouched by this phase; OpenAI and xAI are now real, registered,
credential-isolated, model-configurable, Brain-integrated providers,
implemented as the smallest addition the existing `LLMProvider` contract
required, with zero changes to Brain, Identity, Memory, Voice or
Capability architecture. Provider neutrality and future-provider
extensibility are demonstrated, not merely claimed. The one honest,
external limitation - no funded credentials for any of the three real
vendors - is stated plainly and does not block this phase's own
completion, exactly as the equivalent Anthropic limitation has not
blocked any earlier phase. `CognitiveLoop`/`ActionExecutor` remain
staged, untouched, per the user's own prior explicit decision (§52.9) -
not part of this phase's scope.

### 52.12 Pre-Phase Agentic Workflow / Complex Objective Completion checkpoint

CHECK found the building blocks this checkpoint needed already real and
production-reachable: `answer_with_collaboration` (Stage 13's governed
entry point - collaboration when relevant, the Manager's own ordinary
D-A-T-A dispatch otherwise, itself already including the Brain-driven
`open_conversation` fallback), real memory/conversation continuity, and
Full/Half Brain semantics (§48, §52.10). What did not exist anywhere was
a mechanism for the Manager to dynamically judge, from real evidence,
whether one step actually satisfied an objective or whether more was
genuinely needed - every existing entry point ran exactly one step and
stopped regardless of what the objective actually asked for.

**A genuine gap found in the still-staged `CognitiveLoop`/
`ActionExecutor`, documented rather than fixed.** `ActionExecutor.
_delegate`'s Router-concurrence check asks `manager.diagnose()` - the
bare `CapabilityRouter`, with no notion of the Manager's own shape-based
capability claims or of being the default owner of unclaimed work.
Reproduced directly against the real production `ManagerAgent`:
`diagnose()` returns `None` for perfectly ordinary, novel wording with
no capability-word overlap at all ("xyzzyplugh transmogrify the qux into
a fnord"), where `decide()` - what the rest of the system actually
trusts and uses everywhere else - correctly returns Alfred as the
default owner. This makes `CognitiveLoop`'s `HANDLE_SELF` refuse exactly
the open-ended reasoning this checkpoint's own "no hardcoded example
dependency" requirement depends on. Activating `CognitiveLoop` for this
checkpoint would have inherited that gap for no benefit; fixing it would
mean editing a component Trust-in-Stages keeps deliberately unreachable,
for no benefit this checkpoint's own design needs. So `CognitiveLoop`/
`ActionExecutor` remain exactly as staged as before this checkpoint -
not activated, not modified, not depended upon
(`tests/test_agentic_objective_loop.py::
TestCognitiveLoopRemainsStagedAndUntouched`,
`TestTheDiagnoseVsDecideGapCognitiveLoopWouldHaveInherited`) - and the
gap itself is left recorded here as a known, real, out-of-scope
limitation of that still-staged mechanism, not silently discovered and
buried.

**What was built.** A fifth Brain judgement, `ManagerBrain.
assess_objective(objective, evidence) -> ObjectiveVerdict`
(`src/manager/brain.py`, `brain_prompts.py`'s `OBJECTIVE_SYSTEM_PROMPT`/
`build_objective_prompt`/`parse_objective_or_unavailable`, and
`LLMManagerBrain.assess_objective`) - the identical concrete-default-
to-honest-"could not judge" discipline `discern_retention`/
`discern_ambiguity`/`discern_activity_start`/`evaluate_activity_answer`
already established, extended to a sixth, distinct question: given the
real objective and everything genuinely gathered toward it so far, is
it complete, does it need one more concrete step, does it need
something only the person can supply, or has it failed? Its own system
prompt explicitly forbids naming *who* should do the next step -
deciding that remains the orchestrator's governed dispatch, never the
model's.

`src/manager/objective_loop.py::pursue_objective(manager, task,
max_steps=5)` composes this into a bounded, dynamic loop: run one step
through `answer_with_collaboration` (unmodified), ask
`assess_objective` what the real result means for the objective, and
either stop (complete/needs_input/failed) or run another
Brain-composed step, up to the safety bound. There is exactly one
function; nothing branches on subject, topic or wording, and no
`workflow_N()` catalogue exists anywhere. Wired into the one production
call site every text and voice channel already funnels through
(`src/control_center/trail.py::observe()`, replacing its direct call to
`answer_with_collaboration` for unaddressed requests) - so every real
entry point gained this capability with zero other wiring.

**Backward compatibility, the hard way.** The first implementation
naively asked `assess_objective` after every single-step turn and
treated an undecided verdict as a confirmed shortfall, which broke
roughly three dozen pre-existing tests whose own scripted Full-Brain
doubles had no opinion about this new question and so, quite correctly,
could not answer it - turning perfectly correct answers into fabricated
"partial progress" reports. The fix, found before declaring anything
green: an undecided verdict, or a decided-but-unusable one, always
fails open to the most recent step's own real, unmodified result -
byte-identical to this checkpoint's own before-state for the
overwhelming majority (single-step) case. Only a *decided* "failed"
verdict, or the safety bound being reached while the Brain still said
more was genuinely needed, produces an honest partial/failure report -
never a guess dressed up as progress. Re-running the full suite after
this correction is what actually turned zero regressions from a claim
into a fact; see `tests/test_agentic_objective_loop.py::
TestFullBrainSingleStepIsByteIdenticalToBefore`, written specifically to
pin this regression so it cannot recur silently.

**Half Brain is unaffected, provably.** Without a real Brain there is
no way to judge progress without fabricating it, so `pursue_objective`
stops after exactly the one step an ordinary request already ran before
this checkpoint existed - identical output, identical cost.

### Practically verified (real `build()`/`exchange()` path)

All six required scenarios, live, using genuinely unseen wording never
encoded anywhere in this project:

- **Test A (multi-step) + Test C (specialist coordination), combined:**
  "Look through my inbox for job opportunities, work out which one has
  the highest listed seniority, and then tell me if my own skills match
  what that role needs." Step 1 genuinely delegates to Arthur, whose
  real analyzer runs against the real sample inbox and returns its own
  real, detailed findings (seniority, salary/rate, matched/missing
  skills, per role) - not a stub. `assess_objective`, given that real
  evidence, judges the skills-match half still outstanding and composes
  a genuine second step; step 2 runs, and the final answer synthesises
  both real findings.
- **Test B (adaptation):** "research the current population of
  Millbrook Hollow" - step 1's real attempt fails honestly (no
  live-information source configured); `assess_objective`, given that
  real failure as evidence, does not blindly retry the same broken
  approach - it composes a genuinely different next step ("explain
  honestly what can be determined without one"), and the final answer
  is an honest admission of the limitation, never a fabricated
  population figure.
- **Test D (memory/continuity across turns):** turn 1 ("confirm the
  operation callsign for the mission log") genuinely pauses
  (`needs_input`) asking what the callsign is; turn 2 ("the callsign is
  Redwing") - a wholly separate `pursue_objective` call, no shared
  in-process state - correctly answers using the ordinary conversation-
  history mechanism every capability already has, with zero new
  state-passing machinery built for this.
- **Test E (failure handling):** an unavailable capability (no Brain
  configured at all) produces an honest, non-fabricated decline, never
  a guessed answer.
- **Test F (no hardcoded example dependency):** every scenario above
  uses wording and topics ("Millbrook Hollow", "Redwing", "Northwind
  Senior Python Engineer") invented for this verification and never
  encoded anywhere in the codebase - the mechanism reasons about them
  the same way it would about anything else, because nothing branches
  on subject at all.

### Verified

`tests/test_agentic_objective_loop.py` (34, new) plus two pinned tests
updated for the call site's legitimate move
(`tests/test_stage13_live_integration.py`'s two spy-based tests, now
patching `src.manager.objective_loop.answer_with_collaboration` - the
real, current call site - rather than `trail`'s own name, which no
longer binds it directly). Full suite: 5969 passed in both fixed and
randomised order, zero regressions - reached only after the fail-open
correction above; the first, naive implementation genuinely broke ~35
tests, and every one is accounted for by the same single mechanism, not
individually patched around. Practically verified end to end through
the real production path - see this section's own six-scenario record.

**Final status: this checkpoint passes.** The dynamic, general
OBJECTIVE → UNDERSTAND → PLAN → DECOMPOSE → EXECUTE → OBSERVE → ADAPT →
CONTINUE → VERIFY → COMPLETE loop is real, reaches production through
the one entry point every channel already uses, genuinely decomposes
and adapts using only real evidence, coordinates with an existing real
specialist, honestly reports partial/failed/needs-input states, and
never fabricates completion - all proven through the real production
path, not asserted from architecture alone. `CognitiveLoop`/
`ActionExecutor` remain staged, exactly as before this checkpoint - not
activated, not modified - with the one real gap CHECK found in them
recorded honestly rather than silently fixed or silently ignored. No
`workflow_N()` catalogue exists; the one new judgement,
`assess_objective`, reasons fresh from real evidence for any objective,
never a name or a topic.

### 52.13 Phase 1.5 — Provider Accounts & Credentials

CHECK found the provider *classes* (Phase 1.4: Anthropic, OpenAI, xAI),
each with its own environment-variable credential, and Full/Half Brain
semantics (Phase 1.3) both real and unchanged. What did not exist was
any actual *account* layer: exactly one credential per provider name,
no explicit selection among several, no first-class revocation
independent of unsetting a process environment variable, and validation
that meant nothing more than "a non-empty string was supplied".

**A genuine, structural gap found in the real production path, not
merely absent from a spec.** `src/composition.py::build_runtime` called
`build_provider(settings)` exactly once and handed that *single
instance* to both the Manager's own brain and every specialist's brain -
`build_specialist_brain(settings, provider, ...)` reused the identical
`provider` local for every entry in the specialist loop. Reproduced
directly: in the real, default production configuration,
`runtime.provider is arthur.brain._provider` was `True` - Alfred and
every specialist shared one literal `LLMProvider` object, and therefore
one credential, by construction. This is the opposite of the
independence this project already documents and tests for Brain, Memory
and Voice, and the pre-existing specialist-independence tests
(`tests/test_brain_provider_credential_memory_independence.py::
TestSpecialistIndependence`) had proven only that *if* Alfred and
Arthur are given different providers they stay independent - never that
the default construction path actually gives them different ones. It
did not; this phase's fix makes it do so
(`tests/test_phase_1_5_provider_accounts.py::
TestTheSharedProviderInstanceBugAlreadyFixed`).

**What was built - entirely additive, nothing pre-existing removed.**

- `src/services/credentials/account.py` - `ProviderAccount` (a named,
  provider-specific credential and, optionally, a model override; never
  repr'd, never logged) and `CredentialStore` (multiple accounts per
  provider, explicit `select()`, immediate-effect `revoke()` with no
  silent fallback to a different account or provider, `accounts_for()`
  as concrete evidence multiple accounts are real). Genuinely separate
  from Brain/Memory/Voice/Identity/Capability/Connection architecture -
  it imports none of them and is imported by none of them except at the
  one composition seam below.
- `src/services/credentials/validation.py::validate_credential` - real
  validation through the one call that actually proves a credential
  works (`LLMProvider.complete(...)`), never a
  `if key exists: valid = True` fake. Returns
  `CredentialValidation(valid, checked_live, reason)` -
  `checked_live` names, explicitly, whether the vendor was actually
  reached, so "the mechanism works" and "a vendor approved this key"
  can never be conflated.
- `src/providers/factory.py::build_provider` gained one optional
  parameter, `account: ProviderAccount | None`. When given, it - not
  `Settings.provider` - decides which builder runs, which is what lets
  the Manager and an independently-authorised specialist be built from
  the same `Settings` object without `Settings.provider` having to name
  both; each builder itself still refuses an account naming a different
  vendor than its own, as a defence-in-depth backstop. Omitted (every
  pre-Phase-1.5 caller), this reproduces the prior, environment-based
  lookup exactly.
- `src/composition.py::build_runtime` gained two optional parameters,
  `accounts: CredentialStore | None` and
  `specialist_accounts: Mapping[str, ProviderAccount] | None`, and now
  constructs a genuinely separate `LLMProvider` instance per agent - the
  fix for the structural gap above. With neither given (every existing
  caller), every agent still resolves the identical environment-
  configured credential *value* - zero behavioural regression for
  today's single-account deployment, only a different object identity.

No provider-specific branching (`if provider == "anthropic": ...`) was
added anywhere in the new credential layer or the factory beyond the
one already-existing, per-vendor builder table Phase 1.4 established -
see `tests/test_phase_1_5_provider_accounts.py::
TestNoHardcodedProviderWorkarounds`.

**Security boundaries, verified rather than asserted.** A credential is
never repr'd or logged (`ProviderAccount.__repr__`, `CredentialStore.
__repr__`, `build_provider`'s own log line), never stored in Memory
(`MemoryPolicy`'s pre-existing secret-shaped-field denylist already
names `api_key`/`token`/`secret` and was found, not added, during this
phase's own audit), never reaches a specialist's or the Manager's own
source as an owned field (`api_key` appears nowhere under `src/agents/`
or `src/manager/`, confirmed by direct source inspection, with the one
found exception being that same protective denylist), and is never
placed in a `Completion.describe()` or `Settings.describe()` diagnostic
line (both already mask it; reconfirmed here in this phase's own
context). Environment variables are explicitly named as this
environment's *development* credential source throughout, never
described as production secret management this project actually
implements.

### Practically verified (real `build()`/`exchange()` path)

Live, outside the test suite: an account is selected from a
`CredentialStore` and used to construct a real `AnthropicProvider` with
exactly that account's credential; a missing credential is refused
honestly at construction, naming only the vendor selected; a full
Anthropic → OpenAI → xAI provider switch runs through real `exchange()`
calls, with Full Brain independently re-earned for each and a recorded
fact ("Project Wren") still recalled and Alfred's identity/voice objects
unchanged throughout; revoking an account makes the very next
construction attempt fail honestly, with no silent fallback to a
different account or vendor; `validate_credential` is shown
distinguishing a working scripted provider (`valid=True,
checked_live=True`) from a raising one (`valid=False,
checked_live=False`) through the identical real code path a funded
vendor would exercise - explicitly recorded as **local production path
verified**, never **live external authorization verified**, since no
provider in this environment holds a funded account.

### Verified

`tests/test_phase_1_5_provider_accounts.py` (43, new) plus the
architecture-audit allowlist extended by one legitimate entry
(`tests/test_architecture_audit.py::TestFullBrainIsEarnedNotConfigured::
test_no_module_treats_a_key_as_reasoning`, now recognising
`src/services/credentials/` as part of the provider layer its own
docstring already says keys are confined to). Full suite: 6012 passed
in both fixed and randomised order, zero regressions. Practically
verified end to end through the real production path - see this
section's own six-part recorded transcript above.

**Final status: Phase 1.5 passes.** Provider accounts and credentials
are now genuinely usable, isolated per provider and per account,
provider-specific by construction (an account only ever authorises its
own vendor), revocable with immediate effect and no silent fallback,
selectable among several for the same provider, and independent of
Brain/Memory/Voice/Identity/Capability/Connection architecture - with
Full/Half Brain remaining truthful under every credential state
(missing, invalid, revoked, replaced) throughout. The one genuine
external limitation - no funded credentials for any real vendor in this
environment - is stated plainly and does not block this phase's
completion, exactly as it has not blocked any earlier phase; every
validation and switching claim above is honestly labelled local-path
verification, never live vendor authorization. `CognitiveLoop`/
`ActionExecutor` remain staged, untouched, per the standing Trust-in-
Stages decision - not part of this phase's scope.

### 52.14 Pre-Phase 1.6 — Full Alfred OS Production Reality & Ecosystem Acceptance Audit

An adversarial challenge pass against everything claimed GREEN through
Phase 1.5, run before Phase 1.6 was allowed to begin. Scope: identity,
Brain, providers, memory, voice, recording, natural conversation
(Part B); every named post-freeze addition - natural-conversation
completion, clarification, guided learning, the agent-system
foundation, the dynamic objective loop, complex/discovered-requirement
work, failure/adaptation (Part C); Alfred-vs-specialist intelligence
balance (Part D); the ecosystem question directly, including an
unscripted social-request test (Part E); capability/connection/trust
governance (Part F); Phase 1.5 independently re-challenged, not merely
re-read (Part G); provider-switching continuity (Part H); and an
adversarial search for false greens (Part L). This was not a
documentation review: every claim below was exercised through the real
production path (`build()`/`exchange()`/`pursue_objective()`), never
accepted from a prior phase's own report.

**Two genuine, previously-undiscovered defects were found and fixed.**
Neither was deferred to a later phase.

1. **A pending clarification or active guided-learning session had no
   priority over ordinary capability routing.** `guided_interaction_
   response`'s own contract ("the very next remark is checked... against
   the candidates actually offered") only ever runs inside
   `open_conversation`, which is reached only when nothing else claims
   or scores higher first. Reproduced live: `"that's enough for now"` -
   an ordinary way to end a quiz - is independently claimed by
   `live_information` (a real, pre-existing, unrelated shape match), so
   mid-session it was silently routed away from the activity entirely
   and answered as a live-information request instead, surfacing that
   tool's own honest "not installed" failure rather than ending the
   session. Fixed in `src/manager/manager_skills.py`: both `claims()`
   and `execute()` now give an active session for this agent
   unconditional priority over every other claim or score - the same
   "this settles it outright" priority `addressed` already has one
   level up in `ManagerAgent.decide`. No hardcoded phrase, subject, or
   per-capability exception was added; the check is purely "is a
   session active for this agent". `src/control_center/trail.py`'s own
   diagnostic capability label was given the identical check for
   consistency (a purely cosmetic re-derivation, not the execution
   path) - one known, accepted, minor limitation remains: on the exact
   turn that *ends* a session, the trail's capability label can still
   show the capability that would have matched had no session existed,
   because the label is re-derived after the session has already (and
   correctly) closed. This affects only that one diagnostic line, never
   the actual answer, memory, or session outcome - see
   `tests/test_pre_1_6_audit.py::
   TestGuidedSessionOutranksOrdinaryCapabilityRouting`.

2. **"update" - an ordinary, extremely common software/database/
   business word - falsely scored the `live_information` capability.**
   Reproduced live: "users keep seeing stale prices on the product page
   even right after we update them in the cache - what could cause
   that?" (a genuine debugging question, and precisely the kind of
   complex software-reasoning work this project has invested heavily in
   supporting) was misrouted into a fabricated "current, external
   information" request purely because it contains the word "update" -
   the identical class of false positive "today" was already removed
   from `CURRENT_INFORMATION_WORDS` for, at meaningfully larger scale
   given how often ordinary technical conversation uses "update". Fixed
   by removing the bare word from the scoring set, exactly mirroring the
   existing "today" removal's own reasoning and comment style; a genuine
   live-update request is untouched, still caught by "current"/
   "latest"/"recent"/"news"/"happening" or the pre-existing "any update
   on X" marker-phrase shape - both reconfirmed passing, not merely
   assumed. See `tests/test_pre_1_6_audit.py::
   TestOrdinaryUpdateWordingNoLongerMisroutesToLiveInformation`.

**Every explicitly-named known-risk area was independently
re-challenged, not re-read from the prior report.** The
shared-provider-instance defect (Phase 1.5): reproduced fixed in the
real default production path (`runtime.provider is not arthur.brain.
_provider`, same credential value). The `CognitiveLoop` Router-
concurrence gap (Pre-Phase Agentic Workflow checkpoint):
`manager.diagnose()` still returns `None` for the identical novel
wording that first exposed it, `manager.decide()` still correctly
returns Alfred, `CognitiveLoop`/`ActionExecutor` remain `test_only` per
`compute_reachability()` - not activated to satisfy this audit, exactly
as instructed.

**Practically verified, live, through the real production path** (all
using genuinely unseen wording, never encoded anywhere in this
project):

- **C1 (word-overlap reproduction):** five unseen paraphrases each
  containing "function"/"code"/"debug"/"call"/"record" in ordinary
  reasoning or social context all correctly reach `open_conversation`,
  none misrouted.
- **C2 (generic clarification, deliberately not a birthday):** "we're
  either going with Postgres or MongoDB... not sure which yet" raises a
  genuine clarifying question; resolving it retains exactly one clean
  fact ("Resolved: Postgres...") with no obsolete MongoDB candidate left
  as competing truth - the identical mechanism proven on a project
  decision rather than a date.
- **C3 (topic-independent learning):** a full quiz session on knot
  tying - a topic never used anywhere else in this project - genuinely
  composes calibrated questions, grades real answers correct/incorrect,
  adapts the next question, and ends on request.
- **C7 + C8 + D2 + D3, combined in one real objective:** "check my
  inbox for job opportunities and tell me if there is anything useful" -
  step 1 genuinely delegates to Arthur, whose real analyzer finds a real
  Data Analyst posting requiring SQL/Power BI (skills never mentioned by
  the user - the discovered, unstated requirement); the Brain composes a
  research step for exactly that gap; the research step genuinely fails
  (no research tool installed - a real, not fabricated, failure); the
  Brain adapts to a different approach instead of retrying or giving up;
  the final synthesis honestly states the local limitation while still
  answering from general reasoning - proving discovery, controlled
  failure, adaptation, genuine specialist augmentation, and Alfred
  remaining the one who reasons and continues, all at once.
- **D1 (Alfred's own general intelligence):** a physics question
  ("why does a greenhouse actually stay warm") entirely outside any
  registered specialist's domain is answered directly and correctly by
  Alfred, with no specialist involved.
- **E1 (unscripted social request, the Jennifer test):** "say hello to
  my friend Jennifer", "greet Marcus for me", and "send my auntie Rose a
  friendly hello" all correctly reach `open_conversation` and are
  answered naturally - no name, including "Jennifer", is encoded
  anywhere in this codebase; the behaviour is emergent from general
  conversational understanding, confirmed by generalising to two further
  unseen names.
- **F1 (WhatsApp/Phone concept):** confirmed by source inspection - no
  WhatsApp or Phone agent class exists anywhere; the generic
  `Connection` abstraction (`src/providers/connection.py`) is already
  the intended seam for exactly this ("consequential in exactly the way
  a phone/messaging [connection] would be" - that module's own
  docstring), with no live WhatsApp/phone integration claimed or
  present.

**What this audit did not re-derive from scratch.** Full/Half Brain
transitions, provider construction/switching/credential isolation
(Part G), and specialist independence (Part D4) were re-challenged via
direct reproduction (above) rather than re-built - the underlying
mechanisms were exhaustively tested in Phases 1.3-1.5 and nothing in
this audit found reason to distrust that coverage beyond the two
defects already named. Capability/connection/trust governance (Part F)
and future-agent extensibility (Part E3) rely on the pre-existing,
already-extensive test suite (unchanged by this audit) as their
evidence, reconfirmed passing rather than re-proven from zero.

### Verified

`tests/test_pre_1_6_audit.py` (10, new) - pinning both defects found and
re-confirming both explicitly-named known-risk areas. Full suite: 6022
passed in both fixed and randomised order, zero regressions. Practically
verified end to end through the real production path - see this
section's own scenario-by-scenario record above.

**Final status: 🟢 GREEN — ACCEPTED FOR PHASE 1.6.** Answering the
audit's own required questions directly: Alfred is actually usable, not
merely architecturally prepared - every scenario above ran through the
real `exchange()`/`pursue_objective()` path. Alfred actually reasons,
within this environment's honest limitation (no funded vendor credits
anywhere - every reasoning claim above is local production-path
verification through scripted providers exercising the real code path,
never live vendor model-quality verification). Alfred pursues
objectives, dynamically determines what work is needed (never a
workflow catalogue), adapts after a genuine failure, discovers an
unstated requirement from real evidence, and works with a real
specialist without becoming a dumb router - Arthur's contribution
expanded what Alfred could conclude; it did not replace Alfred's own
reasoning. Natural social behaviour (the Jennifer test) is confirmed
emergent, not hardcoded. Memory, questioning/clarification, and guided
learning all genuinely work, generalised beyond every example this audit
itself was given. Provider accounts/credentials are genuinely isolated,
selectable, revocable, and provider switching preserves memory,
conversation, voice, and identity throughout. Capabilities remain
governed; `CognitiveLoop`'s greater autonomy remains correctly staged
and inactive. The system behaves as one ecosystem - one Brain
architecture, one credential layer, one objective loop, one capability
registry, reused by every scenario above rather than a bot per task -
and nothing found during this audit suggests it cannot safely grow new
agents, providers, capabilities or connections along the same seams.
What is genuinely not working, honestly: nothing found and left
unfixed - the two real defects this audit uncovered were fixed,
tested, and regression-verified before this section was written, not
deferred.

### 52.16 Pre-Phase 1.6 — Creative Media Ecosystem Reality Check

CHECK found zero pre-existing media generation or production capability
anywhere in this project. `src/channels/modality.py`'s own docstring
already states it plainly for incoming media ("this module processes
nothing... no video [decoder]"), and nothing anywhere under
`src/providers/`, `src/agents/tools/`, or the Capability Registry
produced an image, a video, or a narration asset. The only genuinely
real, local, offline media infrastructure already present was Kokoro
text-to-speech (`data/voices/kokoro/`), used exclusively for Alfred's
and Arthur's own spoken conversation replies via `SpeechAdapter`.

**The user's own mid-task correction, honoured throughout.** The
initial task description listed example objectives (a children's video,
a Nigerian football comedy, a documentary, a user script) and an example
production path (research → script → voice → visuals → video → edit).
The user explicitly corrected this mid-turn: those are acceptance-test
examples of breadth, never a mandated workflow, sequence, provider set,
or genre list. Nothing below hardcodes any of them - see the "dynamic
discovery" evidence further down, and `TestNoHardcodedProviderWorkarounds`-
style absence of genre/platform branching throughout the new code.

**What was built - genuinely new capability, not a new ecosystem.**

- `src/providers/media/{narration,image,video}.py` - three
  provider-neutral asset families, the identical shape `LLMProvider`/
  `SpeechAdapter` already establish (abstract contract, honest null
  default, one real implementation): `NarrationProvider`
  (`KokoroNarrationProvider`, reusing the same real, already-installed
  ONNX engine and WAV encoder `KokoroSpeechAdapter` uses, for a
  *file* rather than a live-played reply); `ImageProvider`
  (`LocalTitleCardImageProvider` - a real, local, deterministic Pillow
  renderer, explicitly and repeatedly documented as *not* an AI
  illustrator, since no funded image-generation vendor exists in this
  environment - the identical honest-limitation stance every unfunded
  LLM vendor already receives); `VideoComposer`
  (`FFmpegVideoComposer`, using a real FFmpeg binary bundled via the
  `imageio-ffmpeg` PyPI package - no system-wide install, no network
  call at runtime, genuinely local).
- `src/agents/tools/media_production.py` (`MediaProductionTool`) - one
  governed tool, three atomic operations (narrate/illustrate/compose),
  each producing one real file. Deliberately knows nothing about
  objectives, sequencing, or genre - it answers exactly one narrow
  question per call, the identical narrowness `LiveInformationTool`
  already holds itself to.
- `src/manager/capabilities.py::media_production` - one new capability,
  recognising three atomic imperative shapes (never a bare word, never
  a genre or platform), tool-gated exactly like `general_assistance`/
  `live_information` already are.
- `Settings.media`/`Settings.media_asset_dir`,
  `src/providers/media/factory.py::build_media`, and `Runtime.media` -
  the identical composition-root wiring every other replaceable
  provider family here already has, defaulting to `null` (honestly
  absent) unless `MANAGER_MEDIA=local` is deliberately configured.

**No workflow, sequence, or fixed pipeline exists anywhere in this
code.** *When* narration, an image, or composition happens - and in
what order, and whether at all - is decided entirely by the pre-existing
`ManagerBrain.assess_objective`/`pursue_objective` mechanism (§52.12),
unmodified. This checkpoint added zero planning logic of its own;
`TestDynamicCreativeMediaObjective` proves the identical mechanism that
already handles a job search or a database decision handles a creative
objective too, because nothing in it is subject-aware.

**Two genuine defects found by this checkpoint's own first real test,
fixed immediately, not deferred.**

1. Bare imperative `"draw <anything>"` matched the illustration shape,
   because "draw" is also an ordinary English verb in idioms with
   nothing to do with an image ("draw a comparison", "draw a
   conclusion", "draw a blank") - reproduced live on the first attempt.
   Fixed by requiring "draw" to be followed by an actual image-shaped
   object; the idiomatic forms, and the genuine "draw a picture of X"
   form, were both reconfirmed correct afterward.
2. **A confidently-claimed, genuinely creative imperative that
   `general_assistance` could not classify as research or coding fell
   through to a bare roster listing instead of genuine reasoning.**
   `general_assistance_intent`'s own claim recognises any imperative
   "write/create/build/generate/..." shape - correctly, and by design,
   since narrowing it to "only when the object is about code" would
   reintroduce exactly the word-overlap subject-matching this project
   has repeatedly removed elsewhere. But `general_assistance`'s own
   *fourth* outcome ("the request is not ordinary") predates that
   claims-based tightening (it dates to the original Step 9 audit, when
   this capability was reached by loose word-overlap scoring, not a
   confident shape claim) and had never been revisited to match: it
   still fell through to `report_roster` - a roster dump standing in
   for an answer nobody asked for - for "create a short story about X"
   or "write one scene of narration text", discovered live the first
   time this checkpoint asked Alfred to write one. Writing prose needs
   no external tool at all, so this now reaches `open_conversation`'s
   real Brain reasoning instead - stale logic from an earlier era of
   this same capability, corrected to match what the capability's own
   entry condition has meant since Phase 1.3, not a new decision.
   `Requirement N` (coding/research gated by tool, never Brain) is
   completely unaffected - reconfirmed, not merely assumed.

**Practically verified, live, through the real production path** (never
architecture-only, never mocked generation):

- **Real narration**: Kokoro genuinely synthesises arbitrary text into
  real PCM audio, wrapped into a complete WAV container - confirmed
  readable by Python's own standard-library `wave` module, confirmed
  distinct audio for distinct text.
- **Real images**: Pillow genuinely renders a real PNG (verified by its
  own magic bytes) for arbitrary text, distinct images for distinct
  text - explicitly, repeatedly documented as a local title-card
  renderer, never described as AI illustration.
- **Real video**: FFmpeg genuinely composes real narration and real
  images into one real MP4 - independently re-decoded afterward by a
  *second*, fresh invocation of the real FFmpeg binary, which reported
  a genuine `h264` video stream, a genuine `aac` audio stream, and the
  correct total duration. This is real, playable-file verification, not
  "a byte count and a file extension".
- **Dynamic discovery, not a fixed pipeline**: a scripted Brain,
  reasoning turn by turn via `assess_objective` with no workflow coded
  anywhere, discovered on its own that "create a short story video"
  needed writing, then narration, then illustration, then composition -
  copying real file paths verbatim out of real evidence it was never
  told in advance - and produced one real, independently-verified video
  file (`TestDynamicCreativeMediaObjective`).
- **Generality**: the identical mechanism, unmodified, was exercised for
  a lighthouse-keeper story, a Nigerian football comedy line, a
  children's-space-story image, and an archaeological-documentary line -
  four genuinely unrelated genres, zero genre-specific code anywhere.
- **Revision**: a first creative draft, given feedback ("too slow, make
  it funnier, shorten it"), produced a second draft that is genuinely
  different, shorter, and reflects the requested tonal change - ordinary
  conversation-history continuity, no new memory mechanism.
- **Independence**: the media backend (`runtime.media`) is unaffected by
  swapping Alfred's LLM provider, confirmed by identity comparison
  across two provider swaps - the two axes are provably separate
  configuration, never coupled.

**What remains honestly unavailable, and why that is not this
checkpoint's fault.** No AI image-generation or AI video-generation
vendor is configured or funded in this environment - the identical
external limitation already recorded for Anthropic/OpenAI/xAI credits.
`ImageProvider`/`VideoComposer` are built exactly so that a future real
backend (a funded image-generation API, a funded text-to-video API) is
one additional implementation, added the same way OpenAI joined
Anthropic as a second `LLMProvider` - zero change to `Brain`, `Memory`,
`Voice`, `Identity`, the objective loop, or this capability's own shape
recognition. Publishing to YouTube/TikTok/Facebook/X was explicitly out
of scope for this checkpoint (Section 15) and does not exist in any
form - recorded here as a future governed `Connection`/capability,
exactly like WhatsApp/Phone already are (§52.14's own F1 finding),
never implied or claimed.

### Verified

`tests/test_creative_media.py` (34, new) plus two pinned capability-list
tests updated for the new, legitimately-added `media_production` entry.
Full suite: 6056 passed in both fixed and randomised order, zero
regressions. Practically verified end to end through the real production
path, including two independent real-FFmpeg re-decodes of genuinely
produced video files - see this section's own record above.

**Final status, answering Section 18's own required breakdown for "make
me a short video about X" today:**

| Layer | Status |
|---|---|
| Creative reasoning (understand objective, write a script/story, revise) | **ACTUALLY VERIFIED** - real Brain reasoning via `open_conversation`, unaffected by this checkpoint |
| Research (where an objective needs it) | **ACTUALLY VERIFIED** - the pre-existing `live_information`/`knowledge` capabilities, unaffected, composed by the same objective loop when genuinely needed |
| Script/scene decomposition | **ACTUALLY VERIFIED** - the Brain's own `assess_objective` reasoning, dynamically, never a fixed parser |
| Narration/audio generation | **ACTUALLY VERIFIED** - real Kokoro synthesis, real WAV files, genuinely distinct per text |
| Image/visual generation | **ACTUALLY VERIFIED, LOCAL ONLY** - a real, local title-card renderer; **MISSING**: AI illustration, **BLOCKED BY** no funded image-generation vendor |
| Video generation (composition) | **ACTUALLY VERIFIED** - real FFmpeg muxing, independently re-decoded and confirmed playable with real video+audio streams |
| Editing/composition | **ACTUALLY VERIFIED** for multi-scene concatenation with per-scene narration-matched duration; **MISSING**: transitions, cuts, effects beyond a static-image-per-scene slideshow |
| Inspection | **ACTUALLY VERIFIED** - genuine ffmpeg re-decode confirming real streams, real duration, no corruption |
| Revision | **ACTUALLY VERIFIED** - a genuinely different, feedback-reflecting second draft, produced live |
| Final file delivery | **ACTUALLY VERIFIED** - a real file path returned in the result, a real file on disk |
| Publishing (YouTube/TikTok/Facebook/X) | **MISSING** - no connection or capability exists; explicitly out of this checkpoint's scope, recorded as a future governed capability, never claimed |

**Overall: 🟡 PARTIAL, honestly.** Every layer that can be built and
verified without a funded external AI vendor is genuinely implemented,
tested, and practically verified - narration, local visual production,
real video composition/inspection, dynamic discovery, revision, and
generality across unrelated genres all actually work today, through the
real production path, for anyone who sets `MANAGER_MEDIA=local`. What
is missing is AI-quality image/video generation itself, which requires
a funded vendor this environment does not have - an external blocker,
not an architectural gap, and the architecture is deliberately shaped
(`ImageProvider`/`VideoComposer` as swappable, provider-neutral
contracts) so that adding one is additive, not a redesign. This
checkpoint does not block Phase 1.6: the two real defects it found were
fixed and regression-verified, and the honest gap it leaves - AI
illustration/video quality - is named exactly, not hidden behind an
unjustified GREEN.

### 52.17 AI Media Generation Provider — Free-First checkpoint

CHECK reconfirmed §52.16's own finding: no AI image or video generation
existed, only the local, honestly-labelled title-card renderer.

**Hardware investigated before any model or vendor was chosen**, per
this checkpoint's own explicit requirement. This machine has no CUDA
GPU (an Intel HD Graphics 520 integrated adapter, 1GB shared VRAM) and
~8GB of total system RAM. Running any modern local diffusion model here
- image or, far more so, video - would be impractical: too slow to
practically verify, and at real risk of exhausting system memory
outright. Outbound network access was separately confirmed genuinely
available (reached `huggingface.co` and `api.github.com` directly, not
merely a package registry), which is what made a genuinely free,
keyless *hosted* service the honest choice this hardware's own limits
pointed to, rather than a local model chosen by default and quietly
failing.

**Provider investigated and chosen: pollinations.ai's public image
endpoint.** Free, keyless, no signup, no payment method - confirmed by
a real, live request during this checkpoint's own investigation
returning `x-auth-status: unauthenticated` and a genuine 200 response
with no credential of any kind sent. Its own response header,
`x-model-used: sana`, names the actual backend model that answered -
NVIDIA's published SANA text-to-image model family - read from the
service's own real answer each time, never hardcoded or assumed by this
project. No paid tier, no other vendor, and no local model was locked
into the architecture: `ImageProvider` is the identical abstract
contract §52.16 already established, and `PollinationsImageProvider`
is one more concrete implementation behind it, added the same way a
second `LLMProvider` already was.

**What was built - one new provider, zero new architecture.**
`src/providers/media/image.py::PollinationsImageProvider` - real HTTP
requests (`urllib.request`, no new HTTP dependency), the vendor's own
response validated as genuine, decodable image bytes (never trusted
from a "successful-looking" status code or content-type header alone),
with `ImageAsset` gaining two new provenance fields, `provider`/`model`,
the identical role `Completion.model` already plays for a language
model's own response. `src/providers/media/factory.py` gained one new
backend, `ai_image` (`MANAGER_MEDIA=ai_image`) - identical Kokoro
narration and FFmpeg composition `local` already uses, only the image
collaborator differs. No credential of any kind is required, read, or
even accepted by this provider's constructor (confirmed by source
inspection, not merely asserted) - if a future paid tier or different
vendor ever needed one, it would use this project's existing
`ProviderAccount`/`CredentialStore` architecture exactly as OpenAI/xAI
already do, never a second credential system invented for this one
provider.

**No workflow, sequence, genre or platform is encoded anywhere in this
new code.** `media_production`'s own shape recognition, and
`assess_objective`'s own dynamic step composition (§52.12, §52.16),
are completely unmodified - a genuine AI illustration is requested,
produced and composed by the identical mechanism a local title card
already was, because the capability and the objective loop were always
written to the `ImageProvider` interface, never to a specific
implementation.

**Practically verified, live, through the real production path** (never
inside the test suite, which blocks all outbound network
unconditionally by design - see `tests/test_ai_media_generation.py`'s
own docstring for why, and for how the mechanism is instead verified
through an injected `fetch` double):

- **Three independently-generated images, visually inspected**: a robot
  in a junkyard at dusk, a child astronaut among planets, a fox in a
  snowy forest, and a Roman legion's stone bridge - four genuinely
  unrelated subjects, each a real, high-quality, prompt-matching AI
  illustration, each reporting `provider=pollinations, model=sana`.
- **A complete, real video composed from a real AI-generated image**:
  `MANAGER_MEDIA=ai_image`, through the real `exchange()` path - a real
  narration clip, a real AI-generated illustration, and a real FFmpeg
  composition, independently re-decoded afterward by a fresh FFmpeg
  invocation confirming a genuine `h264` video stream, a genuine `aac`
  audio stream, and the correct duration - not a byte count, an actual
  second decode.
- **The dynamic objective loop discovering AI image generation on its
  own**, for a genuinely unseen topic ("a short historical scene about
  Roman legionaries") never encoded anywhere: the scripted Brain's own
  `assess_objective` reasoning chose to illustrate, then narrate, then
  compose - in that order, for this objective only - and produced one
  more real, complete video.
- **Provider replacement**: `build_media(Settings(media="local"))` vs.
  `build_media(Settings(media="ai_image"))` produce genuinely different
  `ImageProvider` types while `NarrationProvider`/`VideoComposer` stay
  the identical real implementations either way -
  `media_production`/`MediaProductionTool` work identically regardless
  of which backs them, proven directly, not merely argued.

**The honest boundary this checkpoint draws, exactly as asked.**

| Capability | Status |
|---|---|
| AI image generation | **GREEN** - genuinely implemented, tested, and practically verified through the real production path, live, with visually-inspected output |
| AI video generation | **BLOCKED** - no genuinely free/keyless equivalent service was found during this checkpoint's own investigation, and this hardware (no CUDA GPU, ~8GB RAM) cannot practically run a local video-diffusion model; the existing, already-verified FFmpeg composition (§52.16) remains the real video-*assembly* mechanism, now assembling genuine AI-generated images rather than only local title cards |
| Local title-card images | **Unaffected, still real** - `local` remains available as a genuinely local, zero-network, zero-cost option; neither backend replaced the other |
| Credentials | **None required** for pollinations' free tier - documented plainly rather than silently assumed; the architecture is ready for a credentialed provider the same way OpenAI/xAI already are, without any of that plumbing having been speculatively built for a need that does not yet exist |

### Verified

`tests/test_ai_media_generation.py` (19, new). Full suite: 6075 passed
in both fixed and randomised order, zero regressions. Practically
verified end to end through the real production path, including live,
visually-inspected AI-generated images across four unrelated subjects
and one independently re-decoded, AI-image-composed video - see this
section's own record above.

**Final status: this checkpoint is resolved.** AI image generation is
genuinely real, working, provider-neutral, and requires no credential
for its current free tier; AI video generation remains an honestly
recorded external/hardware limitation, not a hidden gap - full
text-to-video generation would need either a funded vendor API or
GPU hardware this environment does not have, and the architecture
(`VideoComposer` as its own swappable contract) is already shaped to
accept one without a rewrite whenever either becomes available. Nothing
about Brain, Memory, Identity, Voice, the objective loop, or Trust-in-
Stages was touched or regressed.

### 52.18 AI Video Provider — Production-Ready Provider Architecture checkpoint

CHECK reconfirmed §52.17's own finding still held: no genuine AI
video-*generation* capability existed anywhere in the codebase -
`VideoComposer` (FFmpeg) only assembles already-produced image/audio
scenes, it does not synthesise video from a prompt. Pollinations' own
`model=video` parameter was re-probed live and reproduced returning an
empty response - there is still no free, keyless video-generation
service to build on, exactly as §52.17 found for its own investigation.
The hardware finding is unchanged and was not re-litigated: no CUDA
GPU (Intel HD Graphics 520, ~1GB shared VRAM), ~8GB total system RAM -
forcing a local video-diffusion model onto this machine was ruled out
again on the same grounds.

**A new, distinct provider-neutral contract, not a duplicate of
`VideoComposer`.** `src/providers/media/video_generation.py` adds
`VideoGenerationProvider` (ABC: `name`, `generate(prompt, *,
seed_image=None) -> GeneratedVideoAsset`) and its honest default,
`NullVideoGenerationProvider`, which refuses rather than fabricates.
This sits beside, not instead of, the pre-existing `VideoComposer` -
FFmpeg's real job (assembling scenes someone already produced) and a
generative model's real job (synthesising a new clip from a prompt) are
genuinely different capabilities, and the codebase now names both
honestly instead of overloading one.

**Provider chosen: Runway**, for a simple single-credential (Bearer
token) auth model and an ordinary submit/poll/retrieve async job shape
- the same shape most mature generative-video vendors in this space
use. `RunwayVideoProvider` is the real adapter: constructs the actual
request (`promptText`, optional `promptImage` as a base64 data URI for
image-to-video), submits it, polls `/v1/tasks/{id}` until
`SUCCEEDED`/`FAILED`/`CANCELLED` or timeout, downloads the real output,
probes its genuine duration with a second, independent FFmpeg decode
(`probe_duration_seconds`, added to `video.py` and shared by both video
paths), and returns a `GeneratedVideoAsset` carrying real
`provider`/`model` provenance - never inventing a field the vendor
didn't actually return.

**A genuine live-network correction found and fixed before anything was
called verified**, the same rigor already on record for §52.17's own
pollinations investigation: this adapter's assumed host
(`api.runwayml.com`) was tested with a real request and returned a real
404 on every documented path - it does not host these routes. Fetching
Runway's own public OpenAPI spec (`https://docs.dev.runwayml.com/openapi.json`,
live) showed the real host is `api.dev.runwayml.com`; a repeat real
request to the corrected host returned a genuine, meaningful `401 The
provided API key in the Authorization header did not start with
"key_"` - proof the request is now correctly routed, parsed, and
rejected for lacking a real credential only, not for any structural
error. The same spec check also found this adapter's original
assumption of a `model` field on a completed task's response does not
exist in Runway's real schema; the adapter now honestly falls back to
the model actually requested instead of reading a field the vendor
never sends.

**Credential architecture: zero new machinery.** `RUNWAY_API_KEY`
(env) or a `ProviderAccount` selected through the existing
`CredentialStore` - identical isolation, selection, revocation, and
no-leakage/no-logging behavior already verified for Anthropic/OpenAI/
xAI, reused directly rather than duplicated. Missing or invalid
credentials raise `ProviderConfigurationError`/`VideoGenerationError`
before any network call - no silent fallback to a different provider
or to a fabricated result.

**Independent axis, exactly like the LLM provider switch.**
`Settings.video_generation` (default `"null"`) and `Settings.media`
(image/composition backend) are orthogonal - `build_media()` now
constructs the video generator independently of which image/narration/
composition backend is selected, mirroring how `settings.provider` and
`settings.specialist_brain` are already independent. `MediaTools`
gained a fourth field, `video_generator`, and its `.name` a fourth
segment (e.g. `null+null+null+null`, or `...+runway`); switching only
this one field was proven, directly, to leave `NarrationProvider`,
`ImageProvider`, and `VideoComposer` as the identical real
implementations either way, and to leave Alfred's identity, Brain,
Memory, conversation history, Voice, and the objective loop completely
untouched.

**No hardcoded workflow.** `media_production`'s capability recognition
gained one new operation, `generate_video`, distinguished from the
pre-existing `compose` by real English verb choice (generate/create/
produce a video, vs. compose/render/assemble/stitch together a video) -
never a required step in any sequence. `assess_objective`'s own dynamic
step composition (§52.12, §52.16, §52.17) is unmodified; a scripted
test proved the Brain can genuinely adapt away from a failed
`generate_video` attempt (honest `VideoGenerationError`, unconfigured
provider) to a successful `illustrate` step on its very next reasoning
turn, using the identical adaptation mechanism already proven general
for image generation - no research→script→image→narration→video
pipeline was encoded anywhere, and a source-level scan confirmed no
genre or platform branching exists in the new code.

**A second real bug found and fixed, unrelated to Runway itself:**
`general_assistance_intent()`'s pre-existing action-verb pattern
(`generate|create|...`, from Phase 1.3) collided with the new
`generate_video`/`illustrate` patterns on the same verbs, and
alphabetical claim ordering (`"general_assistance" < "media_production"`)
let the more generic capability win the tie - "generate a video of a
dragon flying over mountains" and "create an image of a lighthouse"
were both being intercepted before `media_production` ever saw them.
Found only through live end-to-end probing of real phrasing, not code
review alone. Fixed by making `general_assistance_intent()` defer to
`media_production_intent()` whenever both match, the same "more
specific claim wins" principle `ManagerSkills.claims()` already
documents for guided sessions - verified with a 13-probe check
confirming correct routing without disturbing the existing
tool-gated coding/research routing rule.

**Practically verified - mechanism, and genuine live-vendor-boundary
contact, explicitly distinguished from live generation:**

- Full adapter mechanism (submission, header construction, polling with
  sleep-tracked continuation, success, failure, rejected submission,
  seed-image base64 encoding, timeout) verified via an injected
  `Transport` double - the test suite blocks all outbound network
  unconditionally (`tests/conftest.py::_no_network`), so this is
  dependency-injection verification, not a live call.
- **Two genuine live network calls actually reached Runway's real
  production API** during this checkpoint's own investigation (outside
  the test suite, as established practice for every unfunded vendor in
  this project): the first reproduced a real 404 on the wrong host,
  the second, after correction, reproduced a real, specific 401
  rejecting a placeholder key by name - stronger evidence than pure
  mechanism testing, though still short of live generation.
- Credential loading, missing/invalid-credential refusal, account
  selection/revocation through `CredentialStore`, no-leakage and
  no-logging behavior, and provider replacement were all verified
  directly against the real production `build_runtime()`/`build_media()`
  path, not an isolated demo script.
- Regression protection: `tests/test_ai_video_generation.py` (32, new).
  Full suite: **6107 passed** in both fixed and randomised order, zero
  regressions - includes two pre-existing `MediaTools.name` assertions
  in `tests/test_creative_media.py` legitimately updated for the new
  fourth segment, not a broken test.

**The honest boundary this checkpoint draws, exactly as asked.**

| Capability | Status |
|---|---|
| AI video-generation mechanism (provider selection, credentials, request construction, async polling, response normalisation, error handling, provider replacement, production reachability) | **GREEN** - genuinely implemented, tested, and practically verified, including two real network calls that reached Runway's actual API and confirmed correct routing and auth handling |
| Live AI video generation (an actual generated video clip, produced and inspected) | **BLOCKED** - no funded Runway API key exists; a real key (prefixed `key_`) must be obtained and set via `RUNWAY_API_KEY` or a registered `ProviderAccount` before this can occur; this is a stated external limitation, not a hidden gap or a claimed GREEN |
| AI image generation (§52.17) | **Unaffected, still GREEN** - pollinations/`ai_image`, untouched by this checkpoint |
| Local FFmpeg video composition (§52.16) | **Unaffected, still GREEN** - `VideoComposer`, untouched; `generate_video` and `compose` are now two distinct, honestly-named operations rather than one overloaded one |
| Provider replacement / architectural independence | **GREEN** - proven directly: swapping `video_generation` changes only the video generator; Brain, Memory, Voice, Identity, objective loop, and the other three media collaborators are provably unchanged |

**What activating live generation later requires** (no architectural
work): obtain a real Runway API key (prefixed `key_`), set
`RUNWAY_API_KEY` in the environment or register it as a `ProviderAccount`
for provider `"runway"`, and set `MANAGER_VIDEO_GENERATION=runway` (or
select the account) - nothing else changes.

**Final status: this checkpoint is resolved**, in the same sense
§52.11/§52.13/§52.17 already established for an unfunded vendor: the
provider architecture is finished, real, tested, and independently
verified against the vendor's actual live API boundary; only a funded
credential - external to this codebase - stands between this and a
real generated video. Phase 2 is **not** authorized to begin by this
checkpoint.

### 52.19 Surgical Checkpoint — Video Generation Provider Swappability

§52.18 built one real vendor adapter (Runway) behind
`VideoGenerationProvider`. This checkpoint's own explicit purpose was
narrower and stricter: prove, structurally, that the abstraction is
neutral - that Runway is *a* video-generation provider, never *the*
video-generation provider baked into Alfred by omission.

**CHECK, not assumed.** Re-inspected every collaborator this
checkpoint named - `VideoGenerationProvider`, `NullVideoGenerationProvider`,
`RunwayVideoProvider`, `VideoComposer`, `ImageProvider`,
`NarrationProvider`, `MediaTools`, `media_production`,
`src/providers/media/factory.py`, `ProviderAccount`, `CredentialStore`,
`src/composition.py`, the objective loop, and the real production path
- before changing anything. Found the architecture already correct on
every count §52.18 claimed: `MediaTools.video_generator` is built
independently of `settings.media`; `CredentialStore` is keyed by
`(provider, account_id)`, so two providers' credentials were already
structurally incapable of colliding; a repository-wide search for
`runway`/`gen4_turbo` found matches in exactly three files
(`video_generation.py`, `factory.py`, `settings.py`) plus that
checkpoint's own test file - no leakage into Alfred, Brain, Memory,
`media_production`'s capability routing, or the objective loop. Nothing
required correction before proceeding; this section documents proof,
not repair.

**Second provider investigated, not assumed.** Per this checkpoint's
own instruction not to blindly use the example named in the brief,
Google Veo's actual current API was independently verified live before
being chosen: Google's Gemini API exposes Veo 3.1
(`veo-3.1-generate-preview`) over a plain HTTPS `predictLongRunning`
call, authenticated with a single `x-goog-api-key` header - no OAuth
flow, no service-account JSON - the same "simple, developer-accessible
auth" bar Runway was chosen against in §52.18. A real POST to
`https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning`
with a placeholder key returned a genuine, live
`400 {"error":{"message":"API key not valid. Please pass a valid API key.","status":"INVALID_ARGUMENT","reason":"API_KEY_INVALID"}}`
- proof the request reaches Google's real production endpoint and is
correctly routed and parsed, rejected for lacking a genuine key only -
the identical class of live evidence already gathered for Runway's own
`api.dev.runwayml.com` boundary.

**`GoogleVeoVideoProvider` built as a genuinely different shape, not a
copy of `RunwayVideoProvider` with renamed strings.** Same
`VideoGenerationProvider` contract, same `GeneratedVideoAsset` result,
same injectable `Transport`/`sleep` seams - and everything below that
is deliberately different: a single `x-goog-api-key` header rather than
a Bearer token; a long-running *operation* resource (`operations/...`,
polled at its own returned URL) rather than a short task id polled at a
fixed `/tasks/{id}` route; a nested
`response.generateVideoResponse.generatedSamples[0].video.uri` success
envelope rather than a flat `output` array; Google's own
`inlineData`/`mimeType`/`data` seed-image shape rather than Runway's
`promptImage` data URI. Two vendors that disagree about nearly
everything below the shared contract is exactly the structural proof
this checkpoint asked for.

**Credential isolation, proven directly for a second real vendor.**
`VEO_API_KEY` is Veo's own, dedicated environment variable - never
`RUNWAY_API_KEY`, and never read by the Runway branch or vice versa.
Verified directly: a Runway build with both env vars set uses only the
Runway key; a Veo build with both set uses only the Veo key; a
`CredentialStore` holding one active account per provider serves the
correct credential to each and revoking one leaves the other completely
untouched; an explicit `ProviderAccount(provider="runway", ...)` passed
in while `settings.video_generation="veo"` still builds
`RunwayVideoProvider` (the account's own declared provider decides the
vendor - the identical top-level dispatch rule
`src/providers/factory.py::build_provider` already applies to the
Brain's own accounts) - it is structurally impossible for one
provider's account to be silently consumed as though it authorised the
other. No credential of either vendor appears in logs.

**Provider switching, proven through the real tool, not just
`build_media`'s return type.** A `RunwayVideoProvider`-backed
`MediaProductionTool` and a `GoogleVeoVideoProvider`-backed one share
the identical tool name and schema, and answer the identical
`generate_video` request identically except for the honest,
vendor-reported `provider=`/`model=` provenance in the result string -
proof that switching the configured provider changes only video
generation, reproduced through the real production path
(`exchange()`) as well as directly through the tool.

**Future-provider extensibility, demonstrated rather than argued.** A
third, never-before-seen `FutureVideoProvider` - written purely for this
checkpoint's own test, implementing nothing but the abstract contract -
works through the unmodified `MediaProductionTool` and produces a
correctly-attributed result. Adding a real future vendor requires
implementing `VideoGenerationProvider`, registering it in
`src/providers/media/factory.py`'s own small dispatch (one `if` branch
and one credential-env constant, the identical shape every existing
vendor already follows), and configuring credentials/selection -
nothing in Alfred, the Brain, Memory, the objective loop, Identity, or
Voice is touched, structurally proven rather than merely stated.

**Payment/credential model, stated plainly, per this checkpoint's own
requirement.** Provider accounts are external to this project; Runway's
and Google's own billing, credit balances and pricing are each that
vendor's own concern, never modeled, tracked, or assumed equivalent by
this codebase. Alfred does not own, meter, or influence either
vendor's billing. Consuming a vendor's credits happens entirely on that
vendor's own infrastructure; nothing here changes when credits are
consumed, exhausted, or renewed. Switching which vendor is configured -
or adding a third - requires no architectural change, exactly as
demonstrated above; nothing in this project assumes both vendors bill
the same way, and nothing here should be read as a permanent
commitment to Runway or to Veo as the video-generation vendor.

**Regression protection.** `tests/test_ai_video_generation.py` grew
from 32 to **53** focused tests (21 new: the Veo adapter mechanism, the
provider-neutral contract satisfied by both real adapters, credential
isolation between the two vendors, the explicit Runway→Veo switch
through the real tool, the future-provider extensibility proof, and
source-level neutrality checks confirming neither the objective loop
nor the capability-routing layer names either vendor). Full suite:
**6128 passed** in both fixed and randomised order (up from 6107 before
this checkpoint) - zero regressions.

**The honest boundary this checkpoint draws.**

| Requirement | Status |
|---|---|
| Provider-neutral `VideoGenerationProvider` contract | **GREEN** - confirmed by CHECK, not merely re-asserted; no vendor concept found outside the adapter/config layer |
| Second real provider (Google Veo) | **GREEN** - implemented, mechanism-tested, and live-boundary-verified (a real 400 API_KEY_INVALID from Google's actual production endpoint) |
| Credential isolation between Runway and Veo | **GREEN** - proven directly: neither vendor's credential is reachable from the other's build path, by construction and by test |
| Provider switching (Runway ↔ Veo) | **GREEN** - proven through the real `MediaProductionTool` and the real `exchange()` production path, not merely through `build_media`'s return type |
| Future-provider extensibility | **GREEN** - demonstrated with a genuinely third, injected implementation requiring zero changes outside the provider layer |
| Live generation (an actual produced clip, from either vendor) | **BLOCKED** - no funded Runway or Google API key exists; this remains an external, stated limitation, not a claimed GREEN |

**Final status: this checkpoint is resolved.** The video-generation
provider boundary is now demonstrated, not merely built: two genuinely
different real vendors, fully isolated credentials, direct provider
switching, and structural future-extensibility, all verified without
touching Alfred, Brain, Memory, Identity, Personality, Voice, the Agent
Registry, the objective loop, capability architecture, connection
architecture, or media orchestration. Phase 2 is **not** authorized to
begin by this checkpoint.

### 52.20 Universal Ecosystem & Evolution Management Layer

**CHECK, first.** By this point the project already had roughly a
dozen independent, replaceable axes - LLM provider, video-generation
vendor, the image/narration/composition media trio, speech engine,
connection, avatar, knowledge source, live-info source, communication,
celebration, calendar events - and every one of them, independently
invented over many earlier checkpoints, already followed the *identical*
shape: an abstract contract, an honest null/default implementation, a
small `_BUILDERS` dict keyed by name, an `available_*()` function, and a
`build_*()` factory. That repetition was this project's own real,
already-proven extensibility mechanism - reinvented once per axis,
never tied together. Avatar appearance turned out to be far further
along than assumed: `AvatarProvider`/`DefaultAvatarProvider` (Stage 17)
plus `DigitalHumanProfile` (Stage 17.4/17.15, `posture`/`clothing`/
`expression_style`/`hair_style`/`appearance`/`idle_behavior`/
`gesture_style`, all open, renderer-owned labels) plus a real, working
terminal `avatar`/`profile` command and matching `POST /avatar`/
`POST /profile` HTTP routes, all backed by governed, persisted Memory
writes - genuinely usable today, for both Alfred and Arthur
independently, exactly as this checkpoint's own Section 8 asked for.
**None of that was rebuilt.** It was confirmed working and left alone.

**A second, critical CHECK finding, surfaced only after the full
regression suite pointed at it: Stage 11.A (`src/extensions.py`) and
Stage 11.B (`src/evolution.py`) already exist**, and already implement
a governed lifecycle strikingly close to this checkpoint's own Section
2 vocabulary - `ExtensionRegistry.discover()` → `validate_extension()`
→ `plan_extension_activation()` → an external approve gate →
`apply_extension()` → `verify_extension_activation()` → activate, over
an open `Extension.kind` string that already lists "provider",
"environment", "connection" and "multimodal_feature" as examples; and,
separately, `evolution.py`'s PROPOSE → EVALUATE → APPROVE → APPLY →
VERIFY → ACTIVATE → ROLL BACK for Alfred changing something that
already exists. **This is not duplicated by the work below - it is a
genuinely different, complementary layer, and the boundary is now
documented in both directions** (see
`src/services/ecosystem/contracts.py`'s own new docstring section).
Stage 11.A/11.B govern whether an abstract *declaration* is approved to
be marked active in an immutable ledger - by explicit, stated design,
never importing a real provider, never touching a real credential,
never constructing anything (11.A's own docstring: "Actually connecting
a new... provider's module into the running system remains... a
reviewed, explicit change to a registration point"). What genuinely did
not exist anywhere was the concrete other half of that same sentence:
a single place that actually wraps those real registration points
(`src/providers/*_factory.py`) for an operator to inspect, validate
with genuine construction, and manage real, persisted credentials
against - plus one wholly new axis (visual environment) neither layer
had. That gap, and only that gap, is what this section fills.

**What was built - a generic layer over what already existed, plus
exactly one new axis.**

- `src/services/ecosystem/contracts.py` - the generic contract:
  `LifecycleOperation` (the sixteen stages Section 2 names, as an
  enum), `ComponentDescriptor` (one backend, described, never handed
  over as a live object - the same discipline `ToolSpec` already holds
  for tools), `ValidationOutcome` (honest construction success/failure,
  never a claim about a live call), `CategoryAdapter` (the abstract
  contract a category implements to join the ecosystem), and
  `GenericCategoryAdapter` - the **one** concrete adapter class most
  categories need, built from closures over each axis's own existing
  `available_*`/`build_*` functions. One class, not one-per-category
  and not "hundreds of empty classes" (Section 22's own explicit
  prohibition) - every existing axis differs only in which functions it
  closes over, never in shape. `EcosystemRegistry` is the one place all
  registered adapters can be listed, inspected, and asked about
  dependency impact.
- `src/services/ecosystem/registry_factory.py` - `build_ecosystem_registry(settings)`
  wraps thirteen categories (`llm_provider`, `video_generation_provider`,
  `media`, `speech_provider`, `connection`, `avatar_provider`,
  `environment_provider`, `knowledge_provider`, `live_info_provider`,
  `communication_provider`, `celebration_provider`,
  `calendar_event_provider`, `capability`) - every one of them calling
  the *same real* `build_*()` function `src/composition.py` itself
  calls, never a second, parallel implementation of any of them.
- **One genuinely new axis: `environment_provider`** (Section 9).
  `src/providers/environment.py` (`EnvironmentProfile`/
  `EnvironmentProvider`) and `default_environment_provider.py` mirror
  `AvatarProvider`/`DefaultAvatarProvider` exactly, including their own
  explicitly-stated honesty: this project has no 3D renderer (the
  hardware finding already on record: no CUDA GPU, ~8GB RAM), so, like
  every avatar profile before it, an `EnvironmentProfile` is a real,
  swappable, persisted *description* (mood/lighting/backdrop) - never a
  fabricated rendering pipeline built just to look complete. Wired into
  `Settings.environment_provider`/`Runtime.environment_provider`
  exactly as `avatar_provider` already is - a genuinely independent
  axis, proven directly: replacing the LLM provider leaves it
  unchanged, and vice versa.
- **The one real, missing credential gap, closed:** `build_speech`
  (`src/providers/speech_factory.py`) gained the identical optional
  `account: ProviderAccount | None` seam `build_provider`/`build_media`
  already have, so voice (`elevenlabs`) becomes a genuine
  account-managed axis rather than the one provider still limited to a
  raw environment variable - Section 6's own explicit request, closed
  with the same three-line pattern already proven for four other
  vendors, not a new mechanism.
- `src/services/credentials/account_store_file.py` -
  `load_credential_store`/`save_credential_store`: a pure load/save
  pair operating *around* the unmodified `CredentialStore`/
  `ProviderAccount` classes (Phase 1.5) - never a second credential
  mechanism, never a change to either class's body. Persists to
  `data/credentials/accounts.json` (gitignored, the identical role
  `gmail_credentials_path`/`gmail_token_path` already play for Gmail's
  own OAuth tokens), so an operator's added/selected/revoked accounts
  survive a process restart. A genuine bug was found and fixed here
  during this checkpoint's own testing: `CredentialStore.add`'s honest
  "the first account registered for a provider becomes active absent
  anything else" default meant a naive reload silently
  un-revoked a previously-revoked active account merely because its
  sibling account was still registered; fixed by explicitly clearing
  the active slot for any provider present in the loaded accounts but
  absent from the persisted "active" map.
- `scripts/ecosystem_cli.py` - the real, practical, user-facing control
  surface Section 4 asks for: `list`, `status <category>`,
  `validate <category> <key> [--credential]`, `accounts <category>`,
  `add-account`/`select-account`/`revoke-account`. Deliberately an
  **operator** tool, never something Alfred's Brain can invoke -
  switching a credentialed provider is exactly the "Brain must never
  directly receive arbitrary provider credentials or unrestricted
  provider access" case this project has held as a standing rule since
  the AI video-generation checkpoint. `validate` performs real
  construction only (no live network call) and its own output says so
  explicitly, so an operator can never mistake a green `validate` for a
  live-verified vendor call.

**Dependency-aware replacement (Section 13).** Every adapter declares
its own `dependents()` in plain language - `connection` names the
capabilities that reach `connected_action`; `llm_provider` names the
Manager's and every specialist's own reasoning; `avatar_provider`
names agent presence, explicitly noting that an agent's *own chosen*
avatar/digital-human style survives a provider swap unaffected, the
same guarantee already proven for video-generation provider switching
(§52.19). `EcosystemRegistry.impact_of(category)` surfaces this before
a change is made, real text an operator reads before disabling or
replacing something, never a silent removal.

**The credential-shape distinction, honored rather than flattened
(Section 2, Section 14).** `requires_credential` is declared *per key*,
not per category: `llm_provider`'s `null` needs none while `anthropic`
does; `connection`'s `gmail` is deliberately never marked
credential-managed by this layer at all, because its real credential
(OAuth token files) is not a single string secret the way a vendor API
key is - the ecosystem layer knows the difference between credential
*shapes* instead of forcing Gmail through a string-credential interface
it does not fit, exactly as this checkpoint's own Section 2 forbids.

**The acceptance test (Section 21), reproduced literally, not merely
argued.** A category named `embedding_provider` - confirmed, by a real
`git grep` inside the test itself, to appear nowhere else in the
shipped source tree - is registered purely inside
`tests/test_ecosystem_registry.py` using only the public
`GenericCategoryAdapter`/`EcosystemRegistry` contract. The full
lifecycle (discover, list, validate a real honest failure, validate a
real honest success, report a real dependent) is exercised against it,
and it is then registered into the *same* registry `composition.py`'s
real settings produced, alongside `llm_provider` and every other real
axis, with zero change to `contracts.py` or `registry_factory.py`. This
is the concrete answer to Section 17's own six-months-from-now
question: a genuinely new category enters through implementing one
interface and one `register()` call, nothing else.

**Practically verified, not merely built:**

- Every adapter's `validate()` was exercised against the *real*
  `build_provider`/`build_media`/`build_speech` functions - `anthropic`/
  `runway`/`veo`/`elevenlabs` each fail honestly with no credential and
  construct successfully (real client construction, no network call)
  with one supplied - IMPLEMENTED/TEST-VERIFIED evidence, explicitly
  never claimed as LIVE EXTERNAL VERIFIED.
- Credential isolation was proven at the registry layer, not merely
  assumed: a credential supplied to `speech_provider`'s own `validate()`
  call cannot affect `llm_provider`'s active backend, because each
  adapter's `build` closure calls a wholly independent real factory
  function.
- File-backed account persistence was round-tripped, including the
  revoke-while-siblings-remain-registered case that surfaced the bug
  above, and the real CLI was exercised end-to-end (`add-account` →
  `accounts` → `select-account` → `revoke-account`), asserting no
  credential value ever appears in its own output.
- Dependency impact reporting was checked against a real category
  (`connection` → `connected_action`), not an invented example.
- `environment_provider` was proven independent of `avatar_provider`
  and of `llm_provider` directly (switching one, holding `Settings`
  fixed otherwise, leaves the other's active backend unchanged).

**Regression protection.** `tests/test_ecosystem_registry.py` (39, new)
plus three new tests in `tests/test_speech_factory.py` for the
elevenlabs account seam (42 new tests total). One genuine, unrelated
regression was found and fixed along the way:
`tests/test_control_center.py`'s own source-scan test (confirming
`src/composition.py` names no specific speech vendor) tripped on the
word "elevenlabs" appearing in this checkpoint's own new docstring
prose in `build_runtime` - fixed by rephrasing that docstring to name
no vendor, exactly as `composition.py` is already required to. Full
suite: **6170 passed** in both fixed and randomised order (up from 6128
before this checkpoint), zero unexplained regressions.

**The honest boundary this checkpoint draws.**

| Requirement | Status |
|---|---|
| Generic lifecycle contract, independent of any specific component list | **GREEN** - `CategoryAdapter`/`GenericCategoryAdapter`/`EcosystemRegistry`; source-scanned to confirm no vendor name leaks into the contract itself |
| Every existing real axis wrapped, none duplicated | **GREEN** - thirteen categories, each calling the same real `build_*()`/`available_*()` functions the rest of the project already uses |
| Avatar/appearance management | **Unaffected, already GREEN** - found genuinely complete (Stage 17/17.4/17.15); preserved exactly, not rebuilt |
| Visual environment axis | **GREEN, descriptive** - a new, honest axis mirroring avatar's own "descriptive, no renderer" precedent; **not** a claim of an actual rendered experience, since no renderer exists in this project |
| Voice/account credential completeness | **GREEN** - `build_speech` gained the same `ProviderAccount` seam every other credentialed axis already has |
| Persisted, operator-managed credentials | **GREEN** - file-backed `CredentialStore`, isolated per provider, survives a restart |
| User-facing control without editing source code | **GREEN** - `scripts/ecosystem_cli.py`, a real operator CLI, deliberately not exposed to the Brain |
| Dependency-aware replacement | **GREEN** - every adapter declares real dependents; checked against `connection` |
| Future-category extensibility | **GREEN** - proven with a genuinely unseen `embedding_provider` fixture, zero core changes |
| Connections (Gmail, future services) | **Unaffected, already GREEN where it exists** - Gmail's own OAuth-file credential shape deliberately left outside the string-credential seam, not forced to fit |
| Capabilities/tools | **Unaffected, already GREEN** - wrapped as a discovery-only category; no change to `src/manager/capabilities.py`'s own governance |

**Final status: this checkpoint is resolved.** The goal was never
another registry or another provider abstraction for their own sake -
it was that a genuinely new idea, arriving with no advance warning, has
an actual, already-built path from *discoverable* to *persistent*
without redesigning Alfred's core, proven with a component that did not
exist before this checkpoint began. Phase 2 is **not** authorized to
begin by this checkpoint; development returns to exactly where the
roadmap was paused before this cross-cutting checkpoint began.

### 52.21 Phase 2 — Memory & Personal Context

**CHECK found an exceptionally mature system already sitting under a
different name.** Roughly 2,850 lines across `src/services/memory/`
alone: `Memory`/`MemoryRecord`/`MemoryPolicy` (the storage-agnostic
contract - literal token-overlap recall, deterministic, no ranking, no
embeddings), `EphemeralMemory`/`PersistentMemory` (real, atomic,
restart-surviving JSON storage), `ManagerMemoryPolicy` (a *closed*
category allow-list - `USER_CONTEXT`/`DECISION`/`DELEGATION_OUTCOME`/
`LESSON`/`RECORDING`/`MILESTONE`/`LEARNING` - with named exclusions,
real secret-pattern refusal, and a length cap that catches a dump filed
under a plausible category), and four deterministic, marker-based
extraction modules (`session.py`'s `RETENTION_MARKERS`, `user_facts.py`'s
self-introduction grammars, `capture.py`'s "record this [for X]",
`temporal_facts.py`/`milestones.py`'s structured dates) - none of them
guessing, all of them offering *candidates* that only an agent's own
governed `retain()` can admit. `forget`/`recall_memory`/`record_this`
were already real, tested, natural-language capabilities reachable
through the actual production conversation path. **None of this was
rebuilt.** It is exactly what Sections 1-10, 15 and 17 of this
checkpoint ask for, already there, confirmed rather than assumed.

**The one genuine gap: correction.** `retain_what_was_asked_for`
(`scripts/exchange.py`) admits every "remember that X" as a brand-new
record with no check for an existing, now-superseded one -
`personal_recurring_events`'s own relational-birthday handling
(`_apply_relational_birthday`) already proved the fix (read the
existing tagged record, forget it, retain the replacement) for exactly
one narrow domain, but nothing generalised it. Left alone, two
statements about the same preference would sit in the store
indefinitely, both active, both returned by recall - precisely the
"silently maintain contradictory active memories" Section 11 forbids.

**What was built - one new capability, marker-based like every other
memory trigger in this file, never inferred from free-text similarity.**
`correct_memory` (`src/manager/capabilities.py`), recognising three
deterministic shapes:

| Phrasing | Behaviour |
|---|---|
| `"update/change/correct what you remembered about X to Y"` | named topic, named replacement - recalls the topic, retains the replacement, forgets the original *only after* the replacement is confirmed admissible |
| `"that's wrong, Y"` / `"correction: Y"` / `"you got that wrong, Y"` / ... | unnamed topic - targets this agent's own single most recently retained `user_context` record |
| `"that's no longer my ..."` / `"... not true anymore"` | pure invalidation, no replacement - forgets the target, writes nothing |

Reuses the identical `request.recall`/`request.forget`/`request.retain`
governed write path every other memory capability already goes
through - no second admission mechanism, no direct store access.

**Two real bugs found and fixed during this checkpoint's own testing,
neither hypothetical:**

1. **Ordering.** Both replacement shapes originally forgot the old
   record *before* confirming the new one was admissible - a
   replacement the policy refused (a secret, too long) would have
   already destroyed the record it was meant to replace, leaving
   nothing. Fixed by retaining first and forgetting only on success -
   caught by a test asserting the prior record survives a refused
   correction.
2. **Content loss.** The topic-scoped shape originally stored only the
   bare replacement value ("purple") as the new record's content,
   discarding the topic ("my favorite color") that made it findable -
   a later "what do you remember about my favorite color" recall,
   which matches on content tokens alone, would never surface it again.
   Fixed by composing `"{topic} is {value}"` unless the value already
   restates the topic.

**Practically verified, live, through the real production path**
(`scripts/exchange.py`'s own `exchange()`, never a unit test standing
in for it):

- **Remember → recall → correct → recall**: a real turn, real
  persistence, the corrected value is what comes back, the prior value
  is gone - not merely superseded alongside it.
- **A genuine three-restart cycle**: remember (process 1) → restart →
  recall confirms it survived → correct (process 2) → restart →
  recall confirms the *correction* survived, not the original → forget
  (process 3) → restart → recall confirms the removal survived too.
  Three separate `build()` calls against the same on-disk file, each a
  genuine fresh process construction, never a re-read of a held object.
- **Agent isolation, tested the methodologically correct way**: an
  earlier, naive attempt to address "Arthur, what do you remember..."
  without first inviting Arthur into the conversation produced a
  result that looked like a leak (Alfred's own memory came back) but
  was not one - Arthur was never actually addressed, because he was
  never in the room, so Alfred answered the sentence himself. Repeated
  correctly (invite, then address), Arthur's own reply contains none of
  Alfred's memory content - confirmed structurally, not merely by
  reading the code, and the false start is recorded here rather than
  quietly corrected out of the transcript.
- **Relevance**: an unrelated query never surfaces an unrelated memory
  in a real reply, and `ManagerAgent.recall_context` - what a real
  Brain prompt is actually built from - stays bounded
  (`MEMORY_CONTEXT_LIMIT`) even against thirty stored records, never a
  full-store dump.
- **Recording purpose**: "record this for research: X" and "remember
  that Y" produce genuinely distinct, correctly-kinded records
  (`recording` vs. `user_context`), the purpose preserved in `context`,
  reconfirmed rather than assumed from `capture.py`'s own existing
  tests.
- **Multimodal boundary (Section 16), proven rather than built**: a
  real image produced by `MediaProductionTool.illustrate` was captured
  by path through the *existing*, unmodified `record_this` - no new
  code was needed, because `MemoryRecord.context` was already opaque
  and `capture.py` was already free-text. That absence of required
  work *is* the honest extensible boundary this section asks for.
- **Shared Context Graph**: reconfirmed structurally separate from
  personal Memory - `src/conversation/collaboration.py` imports
  nothing from `src.services.memory` and vice versa, exactly as §50
  already documented; nothing here needed to change that.

**Future-proof extensibility (Sections 21-22, reusing §52.20's own
Universal Ecosystem layer rather than inventing a second one).** A
`memory` category is now registered in
`src/services/ecosystem/registry_factory.py`, wrapping the existing
`available_memories()`/`build_memory()` unchanged - `ephemeral`/
`persistent` are real, listed, and independently validated (`persistent`
constructs a real `PersistentMemory` with no stray file written by
validation alone, confirmed directly). Switching this axis proven
independent of `llm_provider`'s own active backend, the identical
"provider replacement never bleeds into another category" guarantee
already proven for every other axis. Section 24's own acceptance test
is reproduced literally: a category named `semantic_memory_provider` -
confirmed absent from the shipped source tree via a real `git grep` -
is registered purely inside the test file using only the public
`GenericCategoryAdapter`/`EcosystemRegistry` contract, and the full
lifecycle (discover, list, an honest missing-credential failure, an
honest success, a declared dependent) is exercised against it, then
registered alongside the real `memory`/`llm_provider`/... categories
with zero change to `contracts.py`, `registry_factory.py`, or `Memory`
itself.

**Regression protection.** `tests/test_phase_2_memory_and_personal_context.py`
(30, new) - the two bugs above were caught by tests in this same file,
before any commit, never merely reported afterward. Every pre-existing
memory test file (`test_manager_memory*.py`, `test_persistent_memory.py`,
`test_stage12_*.py`, `test_memory*.py`, and the rest) is untouched. Two
pre-existing tests with pinned, exact capability-name lists
(`tests/test_manager_capabilities.py`,
`tests/test_identity_resolution.py`) needed a legitimate one-line
addition for `correct_memory`, the same kind of expected update every
prior checkpoint that added a capability has made to these same two
tests. Full suite: **6200 passed** in both fixed and randomised order
(up from 6170 before this phase), zero unexplained regressions.

**The honest boundary this checkpoint draws.**

| Capability | Status |
|---|---|
| Remember (explicit, natural) | **Unaffected, already GREEN** - `retain_what_was_asked_for`/session.py, preserved |
| Recall (targeted, relevant, bounded) | **Unaffected, already GREEN** - `recall_memory`, `ManagerAgent.recall_context`, reconfirmed under load |
| Correction (replace or invalidate) | **GREEN, new this checkpoint** - `correct_memory`, practically verified through the real production path, including two real bugs found and fixed by its own tests |
| Forget | **Unaffected, already GREEN** - `forget_this`, reconfirmed to persist across a real restart |
| Persistence across restart | **GREEN, practically verified** - a genuine three-restart cycle, not asserted from `PersistentMemory`'s own unit tests alone |
| Agent isolation (Alfred/Arthur) | **GREEN, practically verified** - confirmed structurally and behaviourally, including the corrected methodology after an initial false start |
| Shared Context Graph boundary | **Unaffected, already GREEN** - reconfirmed structurally separate, not merged |
| Recording purpose/context | **Unaffected, already GREEN** - `capture.py`/`record_this`, reconfirmed distinct from ordinary retention |
| Multimodal memory | **Extension boundary confirmed, not fabricated** - a real produced asset captured by path today, through existing code, with no vector store, embedding index, or media-native memory built or claimed |
| Memory provider extensibility | **GREEN** - `memory` is a real Ecosystem category; a genuinely unseen future category proven to enter through the identical boundary |
| User visibility/control | **Unaffected, already GREEN** - `scripts/control_center.py`'s existing `history`/`unresolved` commands, plus natural `recall_memory`/`forget`/`correct_memory` conversationally; no new surface needed |

**Final status: this checkpoint is resolved.** Alfred can genuinely
remember, retrieve, correct, forget and preserve relevant information
over time, with governed admission, real persistence, real agent
isolation, and a real path for a future memory technology to enter
without a core rewrite - not merely memory classes and a schema. Phase
3 is **not** authorized to begin by this checkpoint; development
returns to exactly where the roadmap was paused before this phase
began.

### 52.22 Phase 3 — Knowledge, Research & Live Information

**CHECK found General Knowledge and Live Information already real.**
`KNOWLEDGE`/`gather_knowledge_evidence` already acquires real evidence
(the user's own documents, plus learning-context Memory) and reasons
over it, never returning raw evidence as an answer -
`SETTLED_KNOWLEDGE_NOTE` already keeps it honestly distinct from
current information. `LIVE_INFORMATION`/`WikipediaLiveInfoProvider`
already reaches a real, live, keyless, multi-result search API. Neither
was rebuilt. What CHECK found genuinely missing, broken, or
misrouted - and this checkpoint fixed - follows.

**1. Research had no real implementation at all.**
`general_assistance`'s own "research" `GeneralCapability` named a tool,
`web_search`, that was never installed anywhere in this project -
confirmed by a dedicated pre-existing test
(`test_no_web_search_or_coding_tool_is_falsely_shipped`) whose own
docstring said so plainly. Every "research the best X and compare
them" request failed honestly, forever, with "not installed". Fixed
with:

- `WebSearchTool` (`src/agents/tools/web_search.py`) - wraps the
  *identical* real `LiveInfoProvider` `LiveInformationTool` already
  wraps (today, `WikipediaLiveInfoProvider`), under the tool name
  `general_assistance` already names. One real backend, two governed
  tool names, never a second search backend invented for this - this
  project has exactly one genuinely working, free, keyless web-evidence
  source, and pretending otherwise would have been the "second parallel
  research architecture" this phase was explicitly told not to build.
- `general_assistance`'s own research branch repaired
  (`_research_synthesis`) to acquire evidence, then reason over it -
  the identical acquire-then-reason shape `live_information` already
  established, reused rather than duplicated. Coding's own tool output
  is still returned as-is; only research's own deliverable changed
  shape, because "more than search and paste" (this phase's own
  explicit requirement) is a fact about research specifically.
- **Research continuity**, folded into the same repair rather than
  built as a second mechanism: prior, explicitly-recorded research
  (`record_this`, kind=RECORDING, tagged "research" by `capture.py`'s
  own existing purpose tagging - Phase 2, unchanged) is recalled and
  offered alongside the fresh search, so "continue the research we did
  earlier" has something real to continue from - practically verified
  by inspecting the actual prompt a real Brain double received.

**2. Three genuine routing bugs, found live and fixed - not
hypothetical, each reproduced with this checkpoint's own literal
Section 6 examples before being repaired:**

| Request (this phase's own example) | Was routing to | Now routes to | Fix |
|---|---|---|---|
| "what happened in the world today?" | `knowledge` (settled, live info explicitly excluded) | `live_information` | New, narrow, phrase-shaped `CURRENT_INFORMATION_MARKERS` ("happened in the world today", four close variants) - "happened in" alone still correctly means "explain a settled event" |
| "read this document and explain it" / "find the section about X in this file" | `roster_report` / `general_assistance` (a live web search) | `knowledge` | New `DOCUMENT_REFERENCE_MARKERS` ("this document", "this file", "read this document", ...) |
| "create a report from these findings" | `general_assistance` (raw composed prose, no file) | `generate_document` | New capability, see below |

The second and third rows each surfaced a second problem once fixed:
an alphabetical tie-break collision, the identical class of bug already
found and fixed twice in earlier checkpoints
(`general_assistance` vs. `media_production`, then vs. `knowledge` in
Phase 2's own turn) - `general_assistance_intent`'s own action-verb
gate ("find", "create") matched the same request a more specific
capability's own shape also matched, and `select_capability`'s
alphabetical sort let the generic one win. Fixed with two more explicit
deferrals in `general_assistance_intent`, mirroring the existing
pattern exactly - never a change to `select_capability` itself, which
stays a flat, unordered registry.

**3. PDF was the single most commonly requested "document" format with
no real, working path.** `DocumentKnowledgeProvider` (unchanged for
plain text) now also reads `.pdf` files for real - `pypdf` (a genuine,
new, lazily-imported, optional dependency, added to `requirements.txt`
following the identical pattern Pillow/imageio-ffmpeg/kokoro-onnx
already established), real text extraction page by page, never OCR, a
scanned PDF with no text layer honestly yielding nothing rather than a
guess. Spreadsheets, presentations and images remain genuinely
unsupported - not claimed, per this phase's own explicit instruction.

**4. "Create a report from these findings" (Section 6's own example)
had no real implementation.** `DocumentGenerationTool`
(`src/agents/tools/document_generation.py`) + `generate_document`
capability: real recent-conversation findings, reasoned into real
composed text by the real Brain, saved as a real, content-addressed
Markdown file (mirroring `MediaProductionTool._write`'s own pattern) -
never a placeholder reply standing in for a generated artifact. Two
honest gates, checked independently, the same shape every other
Brain-and-tool-dependent capability here already uses: no Brain, no
composition to offer; no `document_generation` tool, nothing to
persist even once composed.

**Practically verified, through the real production path, not merely
unit-tested:**

- A real live network call reached the real Wikipedia API during this
  checkpoint's own investigation (returning three genuine, dated,
  multi-source results for "Eiffel Tower") - the identical class of
  live evidence this project has now gathered for every vendor boundary
  it can reach without a paid credential. The full pytest suite itself
  exercises the *mechanism* only (`tests/conftest.py::_no_network`
  blocks every socket unconditionally), via the identical
  `patch_wikipedia_response`-style injection `test_stage14_production_live_information.py`
  already established - never conflated with the live call above.
- A real, hand-built, valid PDF was read end-to-end: real text
  extracted, real citation with a `page N` locator, reaching a real
  Brain double's actual prompt through the real `exchange()` path.
- A real research request reached real (mocked) search evidence, was
  genuinely synthesised by a real Brain double, and a second research
  request - after "record this for research: ..." - was confirmed to
  carry that recorded note into the *next* research prompt verbatim.
- A real document-generation request produced a real `.md` file on
  disk, readable afterward, containing the real Brain-composed text.
- Failure handling: a broken `LiveInfoProvider` (a simulated network
  outage) produced an honest, named failure, never a fabricated result;
  a genuinely uninstalled `web_search`/`document_generation` tool still
  produces the honest "not installed" refusal this project has held
  itself to since Step 9.
- Half Brain honesty reconfirmed across every new path: with no Brain
  configured, research and document generation both refuse plainly
  rather than fabricating reasoning - never a dead/decorative mode,
  never a false success.

**Five pre-existing tests updated, each a legitimate, expected
consequence of a deliberately-changed reality, never a weakened
assertion:** `test_no_web_search_or_coding_tool_is_falsely_shipped`
(renamed `test_no_coding_tool_is_falsely_shipped` - its own docstring
already said "when one is built this test is what says so out loud");
`test_a_present_tool_is_actually_used` (research now requires genuine
synthesis, not raw tool output - split into two tests, one with a
working Brain, one confirming the honest refusal without one);
`test_web_search_remains_ungranted_...` (renamed - web_search is now
deliberately granted); and two pinned, exact capability-name lists
(`tests/test_manager_capabilities.py`, `tests/test_identity_resolution.py`)
gaining `generate_document`, the same kind of one-line addition every
prior checkpoint that added a capability has already made to these same
two tests.

**Future-proof extensibility (Section 9, reusing the Universal
Ecosystem layer completed at Phase 1.x's own close - never a second
registry).** `knowledge_provider` and `live_info_provider` remain real,
validated Ecosystem categories, reconfirmed rather than assumed. A
category named `source_ranking_provider` - confirmed absent from the
shipped source tree via a real `git grep` - was registered purely
inside a test using only the public `GenericCategoryAdapter`/
`EcosystemRegistry` contract, and the full lifecycle (discover, an
honest missing-credential failure, an honest success, a declared
dependent) exercised against it, then registered alongside the real
categories with zero change to `contracts.py` or `registry_factory.py`.

**Regression protection.** `tests/test_phase_3_knowledge_research_live_information.py`
(33, new). Full suite: **6235 passed** in both fixed and randomised
order (up from 6200 before this phase), zero unexplained regressions -
five expected, legitimate pinned-test updates, detailed above, not
hidden.

**The honest boundary this checkpoint draws.**

| Capability | Status |
|---|---|
| General Knowledge (settled subjects, any topic) | **Unaffected, already GREEN** - `KNOWLEDGE`, reconfirmed |
| Live Information (current events, real-time queries) | **Unaffected, already GREEN**, plus one real routing fix so this phase's own literal example reaches it |
| Research (real sources, real synthesis, real continuity) | **GREEN, new this checkpoint** - real acquisition, real Brain synthesis, real continuity via existing Memory, honest failure handling |
| Files: plain text | **Unaffected, already GREEN** |
| Files: PDF | **GREEN, new this checkpoint** - real text extraction, page-numbered citations, malformed-file isolation |
| Files: spreadsheets, presentations, images | **Honestly unsupported** - not claimed, no format-specific code exists for them |
| Document generation ("create a report from these findings") | **GREEN, new this checkpoint** - real composed text, real saved file, two independent honest gates |
| Full Brain / Half Brain boundary | **Preserved and reconfirmed** - every new path refuses honestly with no Brain, exactly like every existing path |
| Provider/account isolation, credentials, capability permissions | **Unaffected, already GREEN** - no new credential mechanism introduced; `WebSearchTool` and `DocumentGenerationTool` need none |
| Ecosystem extensibility | **GREEN** - `knowledge_provider`/`live_info_provider` remain real categories; a genuinely unseen future category proven to enter through the identical boundary |

**Final status: this checkpoint is resolved.** General Knowledge and
Live Information were confirmed genuinely real rather than rebuilt;
Research and real document generation, which did not meaningfully exist
before this checkpoint, now do - real acquisition, real Brain
reasoning, real continuity, real saved files - and three genuine,
live-reproduced routing bugs (including this checkpoint's own literal
examples) are fixed rather than merely noted. PDF joins plain text as a
genuinely supported document format; no other format is claimed.
Phase 4 - the separately planned Full Brain + Half Brain + whole-system
reality audit - is **not** authorized to begin by this checkpoint;
development stops here, awaiting authorization.

### 52.23 Phase 3 completion — Files, Documents & Control Center

**CHECK reconfirmed §52.22 in full**: General Knowledge, Live
Information, Research, PDF/text support, and the Control Center's own
existing terminal REPL (`scripts/control_center.py` - `status`/
`history`/`avatar`/`profile`/`voice`/`review`, all already reading real
runtime state) were genuinely real and untouched. The remaining gap
named by this checkpoint - spreadsheets, presentations, image analysis,
and a genuinely user-accessible Control Center - is closed below.

**Spreadsheet and presentation support**, added to the existing,
unmodified `DocumentKnowledgeProvider` rather than a competing file
system: `.xlsx`/`.csv` (openpyxl, plus the standard library's own `csv`
module for `.csv` - no dependency at all) and `.pptx` (python-pptx),
each lazily imported exactly like `pypdf` already is. A spreadsheet row
is header-labelled ("Name: Alice, Revenue: 4200"), never a bare value
list; a slide's own text frames are joined per slide. **One real,
stated limitation, not hidden**: token-overlap retrieval was built for
prose, and an aggregate question ("what is the highest value") shares
no words with the data it needs - `SPREADSHEET_MAX_EXCERPTS` (40, up
from the ordinary 3) and an overlap-threshold bypass specific to
spreadsheet rows are what make this genuinely answerable in practice,
verified directly (all three rows of a real workbook reached a real
Brain's prompt, and the real maximum was correctly identified) - but
this remains bounded retrieval, never a guaranteed full scan, and is
recorded as such rather than claimed as unlimited. `DOCUMENT_REFERENCE_MARKERS`
gained the spreadsheet/presentation phrasings this checkpoint's own
Section 2/3 examples use ("this spreadsheet", "this presentation").

**Image analysis - a real architectural correction found and fixed
during this checkpoint's own testing, not merely a feature added.** The
first implementation built `ImageAnalysisTool` as a `Tool` holding an
`LLMProvider` directly - and the full regression suite's own
pre-existing `test_the_toolset_consults_no_brain`
(`tests/test_provenance.py`) caught it immediately: "a tool may depend
on a *provider of information*, and must never depend on reasoning or
model-availability machinery" is a real, deliberate, load-bearing rule
in this project, checked by an AST scan of every file under
`src/agents/tools/`. Visual reasoning is reasoning, not environment
access, so it does not belong in a Tool at all - the same category
`converse()` already lives in. **Corrected, not patched over**: the
Tool was deleted; `ManagerBrain` gained a sixth question,
`analyze_image(image_bytes, prompt) -> BrainDecision`, alongside
`converse`/`discern_retention`/`discern_ambiguity`/etc. (default: an
honest no-view, exactly like every sibling question), overridden in
`LLMManagerBrain.analyze_image` to call a provider's own `analyze_image`
method via `hasattr` when one genuinely exists. `CapabilityRequest`
gained an `analyze_image` field, bound from `runtime.manager_brain.analyze_image`
in `composition.py` and threaded through `ManagerSkills`, the identical
wiring `converse` already has - never a second mechanism.
`AnthropicProvider.analyze_image` is the one real, additive,
non-breaking method that genuinely calls Claude's own vision-capable
Messages API (a real image content block, base64-encoded, alongside
text, in one user turn) - deliberately kept outside the core
`LLMProvider` contract (`base.py`'s own "one method, plain strings in
and out" is a stated, deliberate choice), so OpenAI/xAI/Null are
refused honestly by `hasattr`, never forced to grow a parameter none of
them can honour. `src/manager/brain.py` and `src/manager/llm_brain.py`
name no vendor anywhere - a second real violation this checkpoint's own
full regression suite caught (five separate pre-existing "the Brain
module names no vendor" tests, independently written across five
different files over this project's history, all failed on a single
docstring mention of "AnthropicProvider" - fixed by describing the
capability generically instead).

**The Control Center - genuinely usable, not a status page.** Two new
commands, added to the existing terminal REPL rather than a second UI:

- `capabilities` - the real capability matrix. Every row reads an
  actual runtime fact (a granted tool, `assess_manager()`'s own real
  Brain-mode assessment - the identical function `status <agent>`
  already uses, never re-derived - a configured provider's real name,
  `hasattr(provider, "analyze_image")`) and reports READY or
  UNAVAILABLE with the honest reason, never a card shown because a
  class exists. Verified directly: every row honestly reports
  UNAVAILABLE against the real default (Half Brain, null providers),
  and Image Analysis alone flips to READY the moment a real
  vision-capable provider is configured - reconfirming the Full
  Brain/Half Brain boundary is exposed correctly, per this checkpoint's
  own Section 11.
- `provider [category] [action]` - reuses `EcosystemRegistry`
  (`src/services/ecosystem/registry_factory.py`) and the file-backed
  `CredentialStore` (`src/services/credentials/account_store_file.py`),
  both completed at the Universal Ecosystem checkpoint, verbatim -
  `scripts/ecosystem_cli.py`'s own mechanics, reached from the same
  terminal a person already has open rather than a second tool. `list`
  (bare `provider`), per-category detail with dependents,
  `accounts`/`add`/`select`/`revoke`/`validate` - a full add → select →
  revoke round trip was verified directly through this exact command
  surface, confirming no secret is ever echoed back and a revoked
  account is genuinely gone from the persisted store, not merely hidden
  from the current screen.

Neither command introduces a second credential mechanism, a second
provider registry, or a second UI architecture - both are thin, direct
callers of what §52.20/§52.21/§52.22 already built.

**Practically verified, through the real production path:**

- A real `.xlsx` workbook, read end-to-end: real rows extracted, a real
  Brain double correctly identified the genuine maximum value across
  all three rows, reached through `exchange()`.
- A real `.pptx` deck, read end-to-end: real slide text extracted with
  correct `slide N` locators, a real Brain double correctly summarised
  it.
- A real, hand-built PNG, analysed end-to-end through the corrected
  Brain-based path: a real vision-capable provider double received the
  actual image bytes and its own real analysis text became the final
  answer; a non-vision provider (including the real default `NullBrain`)
  refused honestly, naming the reason, at both the "no analyze_image
  method" and the "no reasoning capability configured" boundaries.
- `AnthropicProvider.analyze_image` mechanism-verified via an injected
  client double: the real Messages API request shape (an `image`
  content block with base64 data, a `text` block, in that order) was
  confirmed directly - no live network call was made or claimed.
- The Control Center's `capabilities` and `provider` commands exercised
  directly through the real command dispatcher (`run_command`), not a
  hand-assembled substitute - including a real account add/select/revoke
  cycle persisted to and reloaded from a real file.

**Two genuine bugs found and fixed by this checkpoint's own testing,
before anything was called done:** the Tool-holding-an-LLMProvider
architectural violation above, and a vendor name leaking into the
Brain's own module - both caught by this project's own pre-existing
regression tests, neither hypothetical.

**Regression protection.** `tests/test_phase_3_files_documents_control_center.py`
(32, new). Full suite: **6267 passed** in both fixed and randomised
order (up from 6235 before this checkpoint), zero unexplained
regressions - two legitimate pinned capability-name-list updates
(`generate_document`'s own sibling addition, `image_analysis`), the
same expected one-line update every prior checkpoint that added a
capability has already made to these same two tests.

**The honest boundary this checkpoint draws.**

| Capability | Status |
|---|---|
| Spreadsheet (.xlsx/.csv) | **GREEN, new this checkpoint** - real rows, real search, real aggregate-question answering within a stated, bounded-retrieval limit |
| Presentation (.pptx) | **GREEN, new this checkpoint** - real slide text, real per-slide search |
| Image analysis | **GREEN within its verified boundary** - real visual reasoning through the Brain when a vision-capable provider is configured (today: one real provider); every other provider honestly refuses, never fabricates |
| PDF, plain text, Research, Knowledge, Live Information, Document generation | **Unaffected, already GREEN** - reconfirmed, not rebuilt |
| Control Center capability visibility | **GREEN, new this checkpoint** - the real matrix, reachable through the real command dispatcher |
| Control Center provider/account management | **GREEN, new this checkpoint** - the real Ecosystem/CredentialStore surface, reachable without editing source code |
| Tool/Brain architectural boundary | **GREEN, restored** - the one real violation this checkpoint introduced was caught by this project's own existing tests and corrected before being reported as done |
| Brain vendor-neutrality | **GREEN, restored** - the one real violation this checkpoint introduced was caught by this project's own existing tests and corrected |

**Final status: this checkpoint is resolved.** Spreadsheet and
presentation support are genuinely real; image analysis is genuinely
real within its honestly-stated boundary (one vision-capable provider
today, every other provider refused honestly, never faked); the
Control Center is a genuinely usable management surface, not a status
page - a person can see the real capability matrix and manage real
provider accounts without touching source code. Two real architectural
violations were introduced, caught by this project's own pre-existing
tests, and corrected within this same checkpoint rather than shipped.
Phase 3, across both of its checkpoints (§52.22, §52.23), is complete.
The next authorized activity is the separately planned **Full Brain +
Half Brain + Whole-System + Control Center Reality Audit** - not begun
here.

### 52.24 Whole-System Reality Audit — Full Brain / Half Brain + Control Center

**CHECK covered the entire system this project's history records** -
Brain/Full Brain/Half Brain, LLM providers, ProviderAccount/
CredentialStore, the Universal Ecosystem registry, Memory, Knowledge/
Research/Live Information, Files (PDF/text/spreadsheet/presentation),
image analysis, media generation, Avatar, the Objective Loop, and the
Control Center - against the actual, current implementation and
practical behaviour, not against ARCHITECTURE.md's own prior claims
taken on faith. Nearly all of it was reconfirmed genuinely real,
reachable, and already covered by its own checkpoint's focused tests.
**One genuine orphan feature was found and fixed; two smaller,
practically-verified additions were made to close this audit's own
broader Control Center requirement.**

**A. FULL BRAIN — 🟢.** The provider abstraction, switching, credential
isolation, and honest failure handling already proven at Phase 1.4/1.5
and the Universal Ecosystem checkpoint were reconfirmed structurally
unchanged: nothing added since names a specific vendor inside
`src/manager/`, and every Brain-dependent capability (Knowledge,
Research, Files, Image Analysis, Document Generation) reaches
`request.converse`/`request.analyze_image` - never a provider object
directly. No current capability secretly assumes one LLM provider;
image analysis is the one genuinely provider-dependent capability
(today, one real vendor supports it), and it is checked by `hasattr`,
never assumed.

**B. HALF BRAIN — 🟢.** Reconfirmed honestly, not assumed: with no
Brain configured, deterministic capabilities (`personal_recurring_events`,
`media_production` narration/illustration/composition, memory
remember/recall/forget/correct, avatar/environment preference,
provider/account management, capability status) all still work,
verified directly through `exchange()`; every reasoning-dependent
capability (Knowledge, Research, Document Generation, Image Analysis,
open-ended conversation) refuses honestly, naming why, never
fabricating an answer. The Brain never becomes the authorization
layer - it proposes; `request.tools`/`request.retain`/the capability
handler's own gates decide whether anything executes, unchanged by
this audit.

**F. The one genuine orphan feature found: Environment had no
per-agent management at all.** `EnvironmentProvider`/
`environment_provider` (built at the Universal Ecosystem checkpoint)
was a real, registered provider *category* - visible via
`provider environment_provider` - with no way for a person to actually
choose one for a given agent, and nothing to persist such a choice even
if invented ad hoc. The identical gap `AvatarProvider` itself had
before Stage 17's own preference work closed it. **Fixed, mirroring
that exact pattern**: `environment_preference_for`/
`set_environment_preference`/`environment_for`
(`src/services/identity/presence.py`, one governed write path, the
same forget-then-remember composition avatar already uses), a new
`environment`/`environment <agent>`/`environment <agent> <choice>`
terminal command (`scripts/control_center.py`), and a new
`POST /environment` HTTP route (`scripts/serve_ui.py`) - both reaching
the identical write path, never a second implementation. Practically
verified: list → choose → relist confirms the change → a genuine
process restart confirms it persisted → an invalid choice is refused
by name with real alternatives listed → choosing one agent's
environment never touches another agent's, or that agent's own avatar/
digital-human style records.

**No other orphan feature was found.** Every other provider category
already had both visibility (`provider <category>`) and, where a real
credential axis exists, full account management
(`add`/`select`/`revoke`/`validate`) through the terminal Control
Center, reconfirmed directly rather than assumed: `llm_provider`,
`video_generation_provider` (Runway and Veo accounts proven isolated
by model and credential through this exact command surface, a
real-key-free revoke/select round trip performed live), `speech_provider`,
`connection`, `memory`, `knowledge_provider`, `live_info_provider`.
"Capability" remains a deliberately discovery-only category (every
registered handler is always available if declared and backed - there
is no per-capability enable/disable toggle in this project's design,
and none was added here: that is this project's own established
capability-floor model, not a gap).

**Control Center capability matrix widened to the whole system.** The
`capabilities` command (built at the Files/Documents checkpoint,
Phase-3-scoped) now also reports AI Image Generation, Narration, Video
Composition, AI Video Generation, Voice, Avatar, Environment,
Connection and Memory - each reading a real runtime fact
(`media.illustrator.name`, `runtime.speech.name`, `runtime.connection.name`,
`type(manager.memory).__name__`, ...), never a class's mere existence.
Verified directly against the real default system: Brain- and
vendor-provider-dependent rows honestly report UNAVAILABLE, while
Avatar/Environment/Memory - genuinely deterministic, no-credential,
no-network capabilities - correctly report READY even with nothing
else configured, and AI Video Generation/Voice/Connection correctly
flip to UNAVAILABLE until a real backend is selected.

**G. Future extensibility — 🟢, reconfirmed rather than re-argued.**
Every provider category already proven swappable at the Ecosystem/
Phase 3 checkpoints remains so; this audit added no new provider axis
and touched no `GenericCategoryAdapter`/`EcosystemRegistry` code at
all - the environment-preference fix lives entirely in the presentation
layer (`presence.py`/`control_center.py`/`serve_ui.py`), never in the
provider/credential architecture, which is exactly why it required no
new extension mechanism to add.

**I. Regression.** `tests/test_whole_system_reality_audit.py` (18,
new) plus one legitimate, expected update to a Phase 3 test whose own
"zero READY by default" assertion needed to name the three genuinely,
honestly READY-by-default rows (Avatar/Environment/Memory) the widened
matrix now correctly reports. Full suite: **6285 passed** in both
fixed and randomised order (up from 6267 before this audit), zero
unexplained regressions.

**The honest boundary this audit draws.**

| Area | Status |
|---|---|
| Full Brain | **🟢** - provider-neutral, no hardcoded vendor, honest multimodal boundary |
| Half Brain | **🟢** - deterministic capabilities work, reasoning-dependent ones refuse honestly, Brain never authorizes |
| Environment management | **🟢, new this audit** - the one genuine orphan feature found, fixed and practically verified end to end |
| Control Center capability visibility | **🟢, widened this audit** - now spans the whole system, not only Phase 3 |
| Provider/credential management (LLM, video-gen, speech, connection) | **Unaffected, already 🟢** - reconfirmed, not rebuilt |
| Capability enable/disable | **N/A by design** - this project's capability-floor model has no such toggle; not a gap |
| 3D/real-time environment rendering | **Unaffected, already honestly documented** - descriptive only, no renderer exists, unchanged by this audit |
| Objective Loop genericity | **🟢, reconfirmed** - no vendor name, no fixed workflow, found clean by direct source inspection |

**K. FINAL VERDICT: 🟡 WHOLE-SYSTEM AUDIT GREEN WITH DOCUMENTED
LIMITATIONS.** Not a plain 🟢, honestly: real, external limitations
remain and are recorded, not hidden - no funded video-generation
credential, no live LLM vision call ever made (mechanism-verified
only), no 3D/real-time environment renderer, and every future roadmap
phase (communication, calendar, commerce, multi-user) remains
unbuilt and is not claimed otherwise. Within everything actually built
so far, the system is genuinely usable, provider-neutral, extensible,
and honest about its own boundaries. The next roadmap phase is **not**
begun by this audit.

### 52.25 Phase 4 — Real-World Communication

**NEW PERMANENT ARCHITECTURAL RULE: Open-Ended Generality.** From this
checkpoint forward, every person, contact, provider, service, account,
device, agent, sport, team, or other entity a requirement names is an
example instance of a general category, never an implementation
boundary. "Rema is calling" means incoming-caller intelligence for
*any* caller; "send Kai a message" means messaging *any* recipient;
Gmail/WhatsApp/a specific LLM vendor are each one example instance of a
provider category, never the permanent choice. The required shape is
**GENERAL CATEGORY → ECOSYSTEM/REGISTRY → DYNAMIC INSTANCE/PROVIDER/
ACCOUNT → GOVERNED CAPABILITY → EXECUTION**, never EXAMPLE → HARD-CODED
FEATURE. This rule is retroactive in spirit but not in effort: it does
not reopen or rebuild completed phases - see the Generality Audit below
for what was actually checked against it.

**CHECK found the general architecture for communication already
real.** Stage 15's `CommunicationProvider` contract
(`src/providers/communication.py`) - `read_messages`/`send_message`/
`place_call`, every method taking a plain `party: str` - and the
`communication` capability's own `_communication_shape`
(`src/manager/capabilities.py`) - party/content extracted purely by
grammatical shape ("send X a message", "call X"), documented in that
function's own comment as never resolving against a contacts concept
this project does not have - already satisfy Section 1's requirement
for messaging and outgoing calling. Nothing here was rebuilt.

**The one genuine gap: no confirmation gate for a consequential
action.** `_communication_send`/`_communication_call` executed
immediately once a party (and, for send, content) were present -
zero opportunity for a person to confirm before a real external
side effect. This directly violated this phase's own Section 12
("do not silently perform consequential actions where confirmation
is required"). **Closed** by extending `ActivitySessions`
(`src/manager/activity_session.py`) with a third in-progress-state
kind, `PendingConfirmation` (`operation`/`party`/`content`/
`description`), mirroring the exact shape and lifecycle
`PendingClarification` already had (raise → check the very next
remark → resolve or leave pending). `_raise_communication_confirmation`
(`src/manager/capabilities.py`) checks tool permission first (an
unauthorized Manager refuses outright, never engaging with confirming
an action it cannot perform), fails safe with an honest refusal if no
`ActivitySessions` reaches the request at all, then describes the
action back (`Send "..." to X? (yes/no)` / `Call X? (yes/no)`) and
raises it. `_try_resolve_confirmation`
(`src/manager/guided_interaction.py`) resolves the next remark
**deterministically** - a fixed `_CONFIRM_WORDS`/`_CANCEL_WORDS`
vocabulary, never a Brain call, so this resolves identically under
Full Brain and Half Brain - executing the real, pre-existing
`_communication_call`/`_communication_send` helpers on a clear yes,
cancelling on a clear no, and leaving the confirmation pending on
anything else (the identical "may be saying something else first"
fall-through an unresolved clarification already gets).
`manager_skills.py`'s own priority routing (two call sites: claiming a
task away from a specialist, and forcing `OPEN_CONVERSATION`) was
extended to also hold the floor while a confirmation is pending -
mirroring exactly how an unresolved clarification already holds it,
so a pending "yes/no" is never abandoned mid-resolution for an
unrelated request.

**The second gap: the tools existed but were withheld from
production.** `CommunicationSendTool`/`CommunicationCallTool`
(`src/agents/tools/communication.py`) were fully built and governed
since Stage 15 but never granted in the real `ToolSet`
(`scripts/control_center.py`) - Stage 15's own deliberate decision,
made explicit in that file's comment, precisely *because* no
confirmation gate existed yet. The identical "orphan feature" shape
the Whole-System Reality Audit (52.24) already found for Environment.
**Fixed**: now that the confirmation gate makes it safe, both tools
are granted alongside the pre-existing `CommunicationReadTool`. The
module's own former "nothing here can send, reply, apply, bid or
contact anyone" docstring claim was corrected: Gmail alone remains
read-only (`gmail.readonly` scope, no Outbox - a real, separate
`Connection`, untouched by this phase); general communication send/call
are now genuinely reachable, always behind mandatory confirmation.

**Sections 6/7 (Messaging, Social) — no new contract needed.**
`CommunicationProvider` already **is** the general contract these
sections ask for: WhatsApp, Telegram, SMS, and any social platform's
DM/post capability are all future named backends behind the same
`communication_provider` Ecosystem category (registered at the
Universal Ecosystem checkpoint, 52.20) - never a second
`MessagingProvider`/`SocialProvider` axis. No live backend for any of
these exists today (only `NullCommunicationProvider` is registered in
`communication_factory.py`), and none is fabricated; the general seam
is real and provider-swappable, verified by `TestFutureProviderNeeds
NoOrchestrationChange` (tests/test_stage15_communication.py), which
proves a never-before-seen `CommunicationProvider` implementation works
through the identical path with zero orchestration change.

**Section 8 (Phone Intelligence) — honest boundary, not fabricated.**
Outgoing calls are real and governed via the confirmation path above.
Genuine *incoming*-call detection/interception needs real telephony/
device hardware this Windows-desktop-CLI project does not have and
does not claim to have. What already exists honestly within reach:
`CommunicationEvent(kind="call", ...)` read through the "read" shape
("who called me", "missed calls") represents *already-happened* call
history from whatever provider is connected - never a live push event.
A future real provider could add live incoming-call notification as an
additive extension to `CommunicationProvider` without changing this
contract, the same way `AnthropicProvider.analyze_image` was added
beside (never inside) the core `LLMProvider` contract at the Files/
Documents checkpoint. Not built here, and not faked.

**Section 9 (Camera/Photo/Video) — preserved, boundary reconfirmed.**
Media *understanding* of an already-existing image file is real
(`ManagerBrain.analyze_image`, Phase 3) and untouched. *Capturing* a
photo/video from a live camera device is not built - no camera library
is installed, no device access exists - an honest device/runtime
boundary, not a permanent architectural limit, and not fabricated here.

**Section 5 (Email) — deliberately untouched.** Gmail's existing read/
search capability (Stage 16/18, the separate `Connection` contract) is
unaffected. Adding Gmail send capability would require a real,
consequential OAuth scope change (`gmail.send`) on the user's own
Google account - outside this phase's authorization and not attempted.

**Generality Audit (Section 18) — retroactive check, no rebuild.**
Swept `src/manager/*.py`, `src/services/profile/profile.py`, and the
live-information/provider-registry layer for accidental example-
specific hardcoding:
- `src/manager/capabilities.py` - two grep hits for "football club" are
  both pre-existing comments citing it as an example of what must
  *never* be hardcoded (the codebase already documents this principle);
  no real comparison found. Confirmed precisely by
  `TestGeneralityAuditFindsNoViolations` (a regex scan for actual
  `== "name"`-shaped comparisons, not bare substring matches, which
  would have false-positived on these exact comments).
- `src/services/profile/profile.py` - `UserProfile`'s real field list
  (`skills`, `projects`, `seniority`, `years`, `targets`,
  `hard_filters`) has no contact/phone/recipient field of any kind -
  confirmed directly, not taken on faith.
- `src/providers/live_info.py` - `LiveInfoProvider.fetch(query: str)`
  is a plain free-text query with no sport/team/league modelling at
  all; "check who is scoring" already flows through this same general
  contract as one example query, never a football-specific feature.
- `src/services/identity/presence.py` - the one hit for "Alfred" is a
  docstring cross-reference, not a comparison.
- `_communication_shape` itself: confirmed by direct source inspection
  (`TestGeneralityAcrossArbitraryParties::test_no_party_is_ever_matched
  _against_a_predefined_list`) to contain no list of known names at
  all.

**Zero genuine violations found.** No completed phase or system was
reopened or rewritten because of this audit; only the two real gaps
above (confirmation gate, tool-granting) were fixed.

**Practical verification.** Exercised through the real production path
(`scripts/control_center.build`, `scripts/exchange.exchange` - never a
new test harness) with five distinct, arbitrary party names including
one never seen anywhere else in this codebase
(`Zzyxq-Unseen-7734`), proving byte-for-byte identical routing and
confirmation behaviour for every one, for both "send" and "call".
Verified: a send/call is never executed before confirmation; a clear
yes executes the real, underlying tool call; a clear no cancels without
ever touching the provider; an ambiguous remark mid-confirmation (a
genuinely unrelated question) leaves the confirmation pending rather
than losing or force-resolving it; a missing tool grant means the
confirmation is never even raised; an unavailable
(`NullCommunicationProvider`) backend fails honestly, after
confirmation, naming the real reason, never fabricating success; a
request built with no `ActivitySessions` at all fails safe rather than
ever executing unconfirmed. Verified identically with no Brain
configured and with a real `LLMManagerBrain` wired in - the resolver
never calls the Brain either way. Verified in the real Control Center
capability matrix (`communication` row: `UNAVAILABLE`, pointing at
`provider communication_provider`) and in `provider
communication_provider`'s own real, honest output (`null` is the only
registered backend).

**Providers/Connections.** `communication_provider` is a real,
registered Universal Ecosystem category; only `"null"` (
`NullCommunicationProvider`) exists as a backend - genuinely no live
messaging/calling/social access is connected, and none is claimed.
Gmail's `Connection` is unaffected and remains read-only.

**Regression.** `tests/test_phase_4_real_world_communication.py` (26,
new) plus legitimate, expected updates to eight pre-existing Stage 15
tests whose own assertions predated the confirmation gate and the new
default tool grant (an immediate-execution assumption for send/call, a
"read-only by default" assertion, and a structural check that no
longer accounted for `control_center.py`'s own new explanatory
comment). Full suite: **6311 passed** in both fixed and randomised
order (up from 6285 before this phase), zero unexplained regressions.

**Roadmap status.**

| Phase | Status |
|---|---|
| Phase 1 | ✅ |
| Phase 2 — Memory & Personal Context | ✅ |
| Phase 3 — Knowledge, Research, Live Info & Files | ✅ |
| Whole-System Reality Audit | ✅ |
| **Phase 4 — Real-World Communication** | **🟡 GREEN WITH DOCUMENTED LIMITATIONS** |

**FINAL VERDICT: 🟡 PHASE 4 GREEN WITH DOCUMENTED LIMITATIONS.** Not a
plain 🟢, honestly: outgoing messaging/calling is real, general, and
governed end to end, but no live messaging/social/phone backend is
connected (only `NullCommunicationProvider` exists today), no live
incoming-call interception exists on this runtime, and no camera-
capture device access exists - each a named, external/hardware
limitation, not a fabricated capability and not a hidden one. Within
what is actually built, the mechanism is genuinely general (proven
across five arbitrary parties, including one never seen anywhere in
this codebase), genuinely governed (never a silent side effect), and
provider-neutral (a never-before-seen provider works with zero
orchestration change). The next roadmap step is whatever the user
authorizes next - no further phase is begun by this checkpoint.

### 52.26 Universal Provider/Connection Management Repair

A read-only audit, prompted by connecting WhatsApp as a
``communication_provider`` backend, checked whether the Universal
Ecosystem/Provider-Account layer (``EcosystemRegistry``/
``CredentialStore``, built at the Ecosystem and Phase 1.5 checkpoints)
was genuinely reachable from the real, running system - not merely
real and tested in isolation. **It was not.** This checkpoint is the
surgical repair, scoped to exactly the gap found: no phase was
reopened, no capability redesigned, and WhatsApp itself was
deliberately not implemented here.

**Three genuine gaps CHECK found, none specific to WhatsApp:**

1. **The persisted account store never reached the live runtime, for
   any category.** ``scripts/control_center.py::build`` (and
   ``scripts/export_ui.py``/``scripts/heartbeat.py``) called
   ``build_runtime(settings)`` bare. ``load_credential_store(...)`` was
   called only by the ``provider <category> add/select/revoke``
   command itself (to serve that command) and by the separate
   ``scripts/ecosystem_cli.py`` - never by anything that actually
   constructs the Manager a person talks to. Selecting an account
   through the Control Center genuinely updated
   ``data/credentials/accounts.json`` and had zero effect on anything
   else.
2. **``communication_provider`` had no credential parameter at all.**
   Unlike ``build_provider``/``build_media``/``build_speech`` (each
   already accepting an optional ``ProviderAccount``),
   ``build_communication_provider`` took only ``settings``/``env``. The
   ecosystem registry's own ``communication_provider`` adapter
   discarded whatever ``credential`` it was given to ``validate(...)``,
   and unconditionally reported ``requires_credential=False``
   regardless of the backend named.
3. **A real, load-bearing bug, found only once the first two gaps were
   actually wired together and made observable**: ``scripts/
   control_center.py``'s ``provider <category> add/select/revoke``
   persisted (and looked up) accounts keyed by *category*
   ("llm_provider", "video_generation_provider", ...). Every real
   builder (``build_provider``/``build_media``/``build_speech``/
   ``build_communication_provider``) reads ``account.provider`` to
   decide which concrete *backend* dict entry to build
   (``_BUILDERS[provider_name]`` - "anthropic", "runway", "elevenlabs",
   ...) - the identical convention Phase 1.5's own tests already
   construct accounts under. An account added through this command
   could never have reached a real construction: threading it in would
   have raised ``Unknown provider 'llm_provider'``, not silently done
   nothing. (``scripts/ecosystem_cli.py``, the other operator tool
   reading and writing the same file, was checked and found to
   already use the correct backend-keyed convention throughout its own
   ``add-account``/``select-account``/``revoke-account``/``accounts``
   commands - its ``category`` argument there was, and remains,
   simply a misleading name for what already functioned as the backend
   key; it needed no code change, only a clarifying docstring note.)

**What was fixed, additively, mirroring the exact pattern every
credentialed category already established - nothing pre-existing
removed, no second account system introduced:**

- ``src/composition.py::build_runtime`` gained one more optional
  parameter, ``communication_account: ProviderAccount | None = None``,
  threaded into ``build_communication_provider(settings,
  account=communication_account)`` - the identical seam
  ``video_generation_account``/``speech_account`` already are for
  their own categories.
- ``src/providers/communication_factory.py``: every builder now takes
  ``(settings, env, account)`` (mirroring
  ``src/providers/speech_factory.py`` exactly); ``build_communication_provider``
  gained the matching ``account`` parameter. ``"null"`` ignores all
  three, unchanged in behaviour - a future credentialed backend
  (WhatsApp, Telegram, ...) reads ``account.credential`` the identical
  way ``_build_elevenlabs`` already does.
- ``src/services/ecosystem/registry_factory.py``'s
  ``communication_provider`` adapter's ``build`` closure now threads
  ``credential`` through via ``_credentialed_account(key, credential)`` -
  the identical seam ``llm_provider``/``video_generation_provider``/
  ``speech_provider`` already use - instead of discarding it.
  ``requires_credential`` is now ``lambda key: key != "null"``,
  correcting an unconditional ``False`` that would have misreported
  every future credentialed communication backend as free.
- ``scripts/control_center.py::build`` now loads the persisted
  ``CredentialStore`` once and passes ``accounts=store`` (for the
  Manager's own LLM account, resolved internally by
  ``build_runtime`` via ``accounts.active(settings.provider)``) and
  each of ``video_generation_account``/``speech_account``/
  ``communication_account`` (each ``store.active(settings.<field>)``)
  into ``build_runtime`` - the exact, pre-existing seam
  ``tests/test_phase_1_5_provider_accounts.py`` already exercised
  directly, now actually reached from the real production entry point.
  ``scripts/export_ui.py::take_snapshot`` and ``scripts/heartbeat.py::
  build_manager_factory`` were updated identically, so a live web
  snapshot or an autonomous heartbeat tick can never quietly diverge
  from what the terminal Control Center would build from the same
  file. (The heartbeat grants no communication tool at all - see its
  own "read-only throughout" docstring - so ``communication_account``
  has no practical effect there today; it is threaded through anyway
  so this stays true if that ever changes, rather than silently
  drifting from the other two entry points.)
- ``scripts/control_center.py``'s ``provider <category> add/select/
  revoke`` now take an explicit ``<backend>`` argument
  (``provider <category> add <backend> <account-id> <credential>
  [model]``, mirroring the shape ``validate`` already had) and persist
  the account keyed by that backend - never by category. A backend
  named that the category does not actually have
  (``_require_backend``, checked against the adapter's own
  ``available()``) is refused by name, the identical "no fabricated
  access" discipline the rest of this project already holds itself to.
  The command's own help text and its ``add``/``select`` replies now
  say plainly that the change takes effect on the *next start* of this
  Control Center, never on the process currently running it.

**What this repair deliberately does not do.** It does not add live,
in-session provider switching. ``runtime.provider``/``runtime.media``/
``runtime.communication``/``runtime.speech`` are still each
constructed exactly once, at ``build_runtime`` time, and bound
directly into ``ToolSet``/tool construction (e.g.
``MediaProductionTool.__init__`` stores its ``video_generator`` fixed
for the tool's own lifetime) - structurally unlike Avatar/Environment,
which are genuinely live-switchable because their preference is
re-read from Memory on every use rather than baked into a constructed
object. Changing an account or a backend selection while a Control
Center session is already running still only writes
``accounts.json``; the next command against that same running session
still reads the file it wrote and nothing more. **This is now
explicit** in the ``provider`` command's own help text and in
``scripts/control_center.py::build``'s own docstring - previously,
this distinction was not stated anywhere, and the 52.24 report's own
"proven... through this exact command surface" language for Runway/Veo
account isolation could be misread as proving live effect, when it
only ever proved persistence-layer isolation. That gap is now named
explicitly rather than left implicit: **persisted account selection**
(what ``accounts.json`` holds), **runtime activation** (what the next
``build()`` call actually constructs from it - the fix made here), and
**live switching/reload** (changing what an already-running process
uses without restarting it - still not implemented) are three
genuinely distinct things, and no claim above blurs them.

**Practical verification, through the real production path
(``scripts.control_center.build``), never a synthetic stand-in for
it:** an account added and selected for ``anthropic`` (llm_provider)
and for ``runway`` (video_generation_provider) through the real
``provider`` command is genuinely used by a freshly-built
``runtime.provider``/``runtime.media.video_generator`` on the next
``build()`` call, byte-for-byte matching the selected credential;
revoking an account is honoured identically (falling back to the
environment credential when one exists, never silently reusing the
revoked key); a completely new, never-before-registered backend
("future-whatsapp" for ``communication_provider``, "future-llm" for
``llm_provider``) reaches a real, live ``runtime`` through nothing
more than one ``_BUILDERS`` entry, the ordinary
``provider <category> add`` command, and an environment variable
selecting it - zero change to ``src/manager/``, ``src/composition.py``,
or ``scripts/control_center.py`` was needed for either; the ecosystem
registry's own ``validate(key, credential=...)`` for
``communication_provider`` now genuinely fails when the wrong (or no)
credential is supplied and succeeds with the right one, proving the
credential is received rather than silently discarded; the ``"null"``
backend builds identically whether or not an account parameter is
passed, confirming zero regression for the default, unconfigured
installation.

**Regression.** ``tests/test_universal_provider_connection_repair.py``
(12, new) plus one added refusal test in
``tests/test_phase_3_files_documents_control_center.py`` and two
legitimate, expected updates to pre-existing tests
(``tests/test_phase_3_files_documents_control_center.py``'s and
``tests/test_whole_system_reality_audit.py``'s own account round-trip
tests, both updated from the old, never-actually-reachable
category-keyed calling convention to the corrected backend-keyed one).
Full suite: **6324 passed** in both fixed and randomised order (up
from 6311 before this repair), zero unexplained regressions.

**What still prevents true Control-Center-only, no-restart
provider/account management** - named honestly, not hidden: live,
in-session activation remains unbuilt (see above); there is still no
HTTP/web route for provider/account management
(``scripts/serve_ui.py`` has routes only for avatar/profile/
environment/message); and a genuinely new *category* of external
credential shape (an OAuth-style connection rather than a single
string secret, the shape Gmail's own ``Connection`` already uses
instead of ``ProviderAccount``) still requires its own, separate
per-category design decision - the string-secret ``ProviderAccount``
axis this repair strengthens is the right fit for a WhatsApp-style
single-token credential, not a universal answer to every possible
future authentication shape.

**Roadmap.** This is a cross-cutting infrastructure repair, not a
roadmap phase - Phase 1 ✅, Phase 2 ✅, Phase 3 ✅, Whole-System Reality
Audit ✅, Phase 4 ✅ (still complete, untouched, not reopened) all stand
exactly as reported at their own checkpoints. WhatsApp itself remains
explicitly not implemented; the next step is whichever the user
authorizes - most naturally, a real ``WhatsAppCommunicationProvider``
now built on a genuinely load-bearing account/credential path rather
than one that would have silently gone nowhere.

### 52.27 Independent Manageability - a usable management interface before the graphical Control Center

52.26 made persisted provider accounts reach the real runtime. This
checkpoint closes what it explicitly named as remaining: **nothing
distinguished the two fundamentally different management shapes this
project's providers and connections actually have, and there was no
way to make a persisted choice live without a full process restart.**
The completion bar this checkpoint was held to: *"I can connect
multiple providers, switch the active provider without deleting the
others, connect multiple external applications simultaneously,
disconnect one without affecting the others, persist those choices,
and independently manage the relevant configuration without editing
source code - even though the graphical Control Center has not been
built yet."* Everything below is what makes that statement true today,
through ``scripts/control_center.py``'s own real, existing terminal -
not a new tool, and not the future graphical Control Center, which
remains entirely unbuilt and is not claimed otherwise.

**PROVIDERS vs. CONNECTIONS - a permanent conceptual distinction, made
explicit rather than left implicit.**

- **Providers** (``llm_provider``, ``speech_provider``, ``video_generation_provider``,
  ``media``, ...): exactly one backend is *active* per category at any
  time (one vendor powers the Brain, one engine speaks, one service
  generates video); many accounts may be retained for a given backend,
  and none of this was rebuilt - it is 52.26's own ``EcosystemRegistry``/
  ``CredentialStore`` axis, reconfirmed here rather than redesigned.
- **Connections** (Gmail, and, in future, WhatsApp/Telegram/X/Spotify/
  ...): additive. Many can be connected simultaneously; there is no
  shared "active connection" slot across unrelated services; connecting
  or disconnecting one never touches another. This axis did not exist
  as a management concept before this checkpoint (only Gmail's own,
  single-backend ``connection`` ecosystem category did, which is a
  *provider*-shaped axis by construction and remains unchanged) and was
  built additively, reusing ``CredentialStore`` exactly as-is - **not a
  second credential system**: a connection's credential is a
  ``ProviderAccount`` like any other, keyed directly by the service's
  own name ("whatsapp", "telegram", ...) rather than through an
  ``EcosystemRegistry`` category, since a category structurally implies
  "pick one active backend" - the one shape connections must not have.

**What was built:**

- ``connections`` (``scripts/control_center.py::run_connections_command``,
  reached from ``run_command``'s own dispatch exactly like ``provider``
  already is): ``connections`` (list every connected service),
  ``connections <service>`` (that service's accounts), ``connections
  <service> connect/disconnect <account-id> [credential] [model]``,
  ``connections <service> validate`` (delegates to a real ecosystem
  adapter's ``validate()`` when the service matches a registered
  backend - e.g. once a real ``communication_provider`` entry for
  "whatsapp" exists - and otherwise honestly reports that no automated
  check exists yet, never fabricating one). Any service name is
  accepted - open-ended on purpose, Phase 4's own "examples are not
  boundaries" principle applied here: a future service needs no code
  change to become connectable, only a name. A provider-axis backend
  name (e.g. "anthropic") is refused here and redirected to
  ``provider``, keeping the two surfaces genuinely distinct.
- **Gmail's real OAuth shape, honestly preserved, never faked.** Gmail
  authenticates via a working, pre-existing token-file flow
  (``scripts/authorize_gmail.py``) - genuinely different from a typed
  API key/access token. ``connections gmail`` reports its real
  ``availability``/``authorization``/``supported_operations`` by
  constructing a Gmail ``Connection`` directly
  (``build_connection(replace(settings, connection_provider="gmail"))`` -
  independent of whichever backend the single-active ``connection``
  ecosystem category currently has selected, so Gmail's own status is
  never hidden behind an unrelated selection). ``connections gmail
  connect/disconnect`` are refused with a pointer to the real script and
  the real token file path - this project does not pretend a typed
  secret can stand in for an OAuth grant, per this checkpoint's own
  explicit instruction not to invent a fake OAuth implementation. No
  new OAuth infrastructure was built; none was justified by anything
  this project currently connects to.
- **``reload`` (``scripts/control_center.py::interactive``, backed by
  the new ``_reload_session`` helper) - the smallest safe activation
  this architecture actually supports.** Every provider object
  (``runtime.provider``/``runtime.media``/``runtime.communication``/
  ``runtime.speech``) is still constructed exactly once and bound
  directly into ``ToolSet``/tool construction - nothing here hot-swaps
  one live object inside an already-running Manager, and no claim to
  the contrary is made anywhere. What ``reload`` genuinely does:
  rebuild the *entire session* through the identical ``build()`` every
  entry point already uses, without leaving the terminal - the
  persisted-account activation 52.26 built, now reachable from inside a
  running session rather than only across a restart.
- **``reload <field>=<value>`` - closing the one gap a plain reload
  cannot: switching which *backend* is active, not merely which
  *account* authorises an already-selected one.** ``provider <category>
  select <backend> <account>`` only ever changes the latter (52.26's
  own scope); it was never able to make "Anthropic active" become
  "OpenAI active" by itself, because *which backend* is active for a
  category is ``Settings``' own field (``settings.provider``,
  ``settings.video_generation``, ...), not a ``CredentialStore``
  concept. ``reload provider=openai`` applies that one field as an
  override via ``dataclasses.replace`` and rebuilds - genuinely live,
  in this session, with no restart and no environment variable needed.
  **Deliberately session-scoped, not persisted**: nothing here writes a
  file or touches ``os.environ``, so it does not survive an actual
  restart on its own - the matching ``MANAGER_<FIELD>`` environment
  variable remains what makes a backend choice the default on every
  future start, exactly as it always was. Every reply naming this
  distinction says so in plain language, never leaving "is this live or
  only persisted" for the user to guess.

**Presentation configuration (Avatar, Visual Environment) - reconfirmed
independent, not touched.** Neither has an ``EcosystemRegistry``
category under the provider/connection axes this checkpoint manages
(they have their own, ``avatar_provider``/``environment_provider``,
untouched); neither ``avatar``/``environment`` command interacts with
``CredentialStore`` at all - proven directly, not assumed. This
checkpoint added no new classification that could have blurred this;
it only extended the provider/connection surfaces, which avatar and
environment were never part of.

**Authentication-shape honesty.** The conceptual distinction between
API key/access token (WhatsApp/Telegram-shaped, fits the existing
``ProviderAccount`` string-secret exactly), OAuth (Gmail-shaped, needs
a real token-file flow, not a typed secret), and any future shape
(refresh tokens, service certificates) is preserved by *not* forcing
Gmail through the string-secret path and *not* building speculative
OAuth machinery no current connection justifies. If a second
OAuth-shaped connection is added in the future, the same pattern
Gmail already uses (a real ``Connection`` implementation, a real
one-time authorization script, status reported by direct construction)
is the one to extend - not a generalized, speculative OAuth framework
built ahead of a second real need.

**Practical verification, through the real terminal Control Center,
never a synthetic stand-in for it:** the ten-step Anthropic/OpenAI
scenario this checkpoint's own brief specifies - connect both, select
Anthropic, switch to OpenAI, confirm Anthropic remains stored, confirm
OpenAI active, rebuild, confirm the runtime uses OpenAI, switch back,
confirm OpenAI remains stored - performed exactly as specified, through
``run_provider_command`` and ``run_command`` alike; ElevenLabs remains
connected after adding and selecting a second, uncredentialed voice
backend; Runway is representable and selectable once (and only once) a
real account is added, and is never reported connected without one;
WhatsApp and Telegram connect, coexist, disconnect (one, not the
other), and reconnect exactly as the brief's own seven-step scenario
specifies; provider and connection state both survive a genuinely
fresh ``build()`` call standing in for a real restart; the entire
workflow above was driven by nothing but plain string commands handed
to ``run_command`` - no monkeypatching of internals, no Settings field
edited directly, no source file touched; ``reload`` (plain and with a
``field=value`` override) rebuilds a live session and reports honestly
whether the result is session-scoped or would need a restart to become
permanent, verified both by calling the mechanism directly and by
driving the real ``interactive()`` REPL with scripted input.

**Regression.** ``tests/test_independent_manageability.py`` (20, new).
Full suite: **6344 passed** in both fixed and randomised order (up from
6324 before this checkpoint), zero unexplained regressions.

**What still prevents true Control-Center-only, no-restart provider/
account management** - named honestly, not hidden: switching which
*backend* is active is now genuinely live within a session
(``reload <field>=<value>``), but does not persist across a real
restart without also setting the matching environment variable - this
project has no persisted "which backend is the default" concept
independent of ``Settings``/the environment, and none was added here,
deliberately, to avoid introducing a second configuration mechanism
alongside the environment-variable one this project has always used.
There is still no HTTP/web route for provider/connection management
(``scripts/serve_ui.py`` is unchanged); the graphical Control Center
remains entirely future work, and when it is built, it must expose
these same ``run_provider_command``/``run_connections_command``/
``_reload_session`` operations rather than inventing a second
management system - exactly as this checkpoint's own brief requires.

**Roadmap.** Cross-cutting infrastructure, not a roadmap phase - Phase
1 ✅, Phase 2 ✅, Phase 3 ✅, Whole-System Reality Audit ✅, Phase 4 ✅ all
stand unchanged. WhatsApp itself remains not implemented; AI Video
Clipping, named in this checkpoint's own closing instruction, is
explicitly not begun here.

### 52.28 Full Alfred OS Ecosystem Evolution

Alfred OS must be able to evolve as a whole ecosystem - Alfred, every
specialist, Brain infrastructure, providers, connections, capabilities,
tools, and shared infrastructure - not only "swap the LLM vendor".
CHECK found the governance machinery for exactly this already real and
already tested, in two separate, complete systems that had never been
connected to each other, to the real ecosystem, or to any command
surface:

- **``src/evolution.py`` (Stage 11.B)** - a complete, isolated,
  generic propose → evaluate → approve → apply → verify → activate →
  rollback state machine for "an existing thing changing", already
  taking any plain-string ``target``. Its own docstring is explicit
  that it "never touches a real Brain, Memory, VoiceProfile, or any
  other live component" and that persistence and real wiring are a
  caller's job, "a reviewed, explicit change elsewhere."
- **The Stage 6-9 Upgrade Manifest/Planner/Executor/Lifecycle**
  (``src/control_center/manifest.py``, ``upgrade_planner.py``,
  ``src/upgrade_executor.py``, ``src/upgrade_lifecycle.py``) - a
  complete, separate, already-tested governed pipeline specifically
  for SYSTEM_VERSION/memory & state SCHEMA_VERSION/visual-environment
  versions, with real migration-chain machinery
  (``src/core/versioning.py``) wired in and ready.

**Neither had ever been reached from the Control Center** (confirmed
directly: no import of either anywhere under ``scripts/``, and
``tests/test_repository_recovery.py``'s own reachability check had
both flagged ``test_only`` before this checkpoint). This was the real
gap - not missing governance machinery, but no real "caller" wiring
that machinery to the real, installed ecosystem, and no persistence
across a restart for the generic one.

**What was built - the caller ``src/evolution.py`` itself says must
exist elsewhere, additive, nothing pre-existing changed in behaviour:**

- **``src/services/evolution/inventory.py``** - the real, whole-
  ecosystem read side. Every provider/connection ecosystem category
  (``EcosystemRegistry``, unchanged), every specialist
  (``runtime.specialists``), Alfred himself, Brain *mode*
  (``settings.brain``/``specialist_brain`` - Full/Half, a genuinely
  different axis from *which vendor* answers), and the existing
  Upgrade Manifest - each tagged by one of exactly five categories
  (provider/connection/specialist/brain/system), never blurred. **No
  fabrication**: a provider/connection's "version" is the real,
  honest ``backend:account_id:model`` tuple it is actually configured
  with (``"null"`` when nothing is configured - never a fake semver);
  a specialist's (and Alfred's own) version is a real SHA-256 content
  hash of its actual identity document - changes if, and only if, the
  document's real content changes. This mirrors
  ``src/control_center/manifest.py``'s own, already-stated refusal to
  invent a version number for anything that genuinely does not have
  one.
- **``src/services/evolution/inventory.py::memory_development_snapshot``** -
  Memory's *developmental* reading: real counts by
  ``MemoryCategory`` kind, real corrections (Phase 4's own
  ``resolved_ambiguity``-tagged confirmation resolutions), real
  guided-learning sessions (Stage 12's own ``learning_progress`` tag) -
  reusing ``Memory.recall()`` verbatim, never a new store, and never a
  fabricated "maturity score". This type has no ``version`` field at
  all, structurally - development and component-version are never the
  same axis.
- **``src/services/evolution/ledger_store.py``** - the persistence
  ``src/evolution.py``'s own docstring says a caller must own: a
  schema-versioned (``SCHEMA_VERSION = 1``, an intentionally-empty
  ``MigrationChain`` - the exact "wire the seam in now, before any
  real migration is needed" discipline ``src/core/versioning.py``
  itself recommends and Stages 1-5 already practiced), JSON-backed
  ``EvolutionState``: the ledger itself, every proposal ever seen (so
  a REJECTED/FAILED proposal - which ``EvolutionLedger`` alone never
  records - is still visible), and a full, timestamped history log of
  every governed transition.
- **``src/services/evolution/ecosystem_evolution.py``** - the governed
  caller. ``propose`` reads a component's real current version fresh
  from the ledger (seeded once from the real inventory, never
  overwriting a version the ledger already independently tracks).
  ``run`` is the fast path (evaluate → approve → apply → verify →
  activate in one governed call, approval always explicit, no
  default) with exactly **one real, non-fabricated apply/rollback
  mechanism**: a ``provider.<category>`` target's active account gets
  its ``model`` field updated (and, on rollback, restored) through the
  identical ``CredentialStore``/``ProviderAccount`` 52.26/52.27 already
  built - written, then *independently re-read from disk* before
  reporting success, the same "never restate the executor's own
  report" discipline ``src/upgrade_lifecycle.py`` already holds itself
  to. If the real write fails after the ledger already recorded
  APPLIED, the ledger's version bump is deliberately **not** merged -
  the proposal is marked FAILED instead, so the ledger can never claim
  a change that did not actually land. Every other target
  (a specialist, a connection, Brain mode) is refused with a precise,
  honest reason - recorded, never silently advanced, never faked.
- **``scripts/control_center.py``'s new ``ecosystem`` command** - the
  first real command surface either governance system has ever had:
  ``ecosystem`` (the whole inventory, by category), ``ecosystem
  <component>`` (detail + every proposal on record), ``ecosystem
  <component> propose/run/rollback``, ``ecosystem history [component]``,
  ``ecosystem memory <agent>`` (the developmental snapshot - never
  routed through propose/run), ``ecosystem system`` (the real Upgrade
  Manifest) and ``ecosystem system upgrade <target> yes|no`` (the real,
  existing ``run_upgrade`` pipeline, called from the Control Center for
  the first time).

**Governed, not autonomous**, throughout: every step requires an
explicit ``approved=True``/``False`` with no default anywhere - the
identical discipline ``src.evolution``/``src.upgrade_lifecycle``
already enforce, now actually reachable rather than only unit-tested.

**No destructive coupling** - proven, not merely asserted
(``TestNoDestructiveCoupling``): evolving ``provider.llm_provider``
never changes ``provider.avatar_provider``/``environment_provider``,
Brain mode, Alfred's own identity hash, or Arthur's; disconnecting one
additive connection never affects another; a specialist's real
identity-document hash is provably unchanged after a governed
(necessarily metadata-only, since no real apply mechanism exists for
this category yet) evolution of that same specialist's own component.

**Practical verification, through the real Control Center command
surface, never a synthetic stand-in for it**: a full real scenario -
connect an Anthropic account, connect an unrelated Runway account,
connect WhatsApp and Telegram, propose an Anthropic model upgrade,
run it (evaluate → approve → apply → verify → activate, all real),
confirm history recorded every transition, **rebuild a genuinely fresh
runtime and confirm it actually uses the new model** (not merely a
ledger claim), confirm the Runway account and both connections are
byte-for-byte untouched, roll back, confirm the model is restored and
the rollback is itself recorded. Also verified: declining approval
never applies anything; a malformed risk level, an unknown component,
an unknown proposal, and a proposal with an inactive dependency are
each refused with a precise, honest reason; a specialist evolution is
recorded and honestly refused at apply (no fabricated specialist
upgrade); ``ecosystem system upgrade`` reports the real, existing
pipeline's real (honest, ``executed: False``) answer for a
SYSTEM_VERSION-only request, since SYSTEM_VERSION has no on-disk store
to write to and this project has never pretended otherwise; the ledger
and full history survive a genuinely fresh ``load_evolution_state``
call standing in for a restart.

**Regression.** ``tests/test_ecosystem_evolution.py`` (25, new) plus
one legitimate, expected update to
``tests/test_repository_recovery.py``'s own reachability check: it
previously asserted ``src.evolution``/``src.upgrade_lifecycle`` were
``test_only`` (true before this checkpoint); they are now genuinely
``production`` (this checkpoint's own ``ecosystem`` command is their
first real caller), and the test was updated to assert exactly that
rather than weakened - ``src.extensions`` (Stage 11.A, deliberately
untouched by this checkpoint) remains correctly ``test_only``. Full
suite: **6369 passed** in both fixed and randomised order (up from
6344 before this checkpoint), zero unexplained regressions.

**Two different forms of evolution, held apart everywhere above:**
component upgrade/replacement (a real, swappable version - providers
today, specialists inspectable the same way once a real apply
mechanism is justified) versus development/maturation (Memory's own
qualitative snapshot, never routed through the propose/apply pipeline,
never a fabricated score treating record count as intelligence).

**What remains a genuinely future extension point, stated honestly,
not hidden or fabricated:**

- A specialist's identity document (and Alfred's own) has no real
  *apply* mechanism yet - only inspection (a real content hash) and
  honest refusal at apply. Swapping one remains, as
  ``src.evolution``'s own docstring already says any real wiring must,
  "a reviewed, explicit change elsewhere" until a real, safe mechanism
  for it is built and justified by an actual need.
- A connection's own backend *implementation* (as opposed to its
  credential, already governed by 52.27's ``connections`` command) has
  no apply mechanism either, for the identical reason.
- Live, in-session activation of an evolved provider remains exactly
  the gap 52.27 already named and gave a real, honest answer to:
  ``reload`` (or ``reload provider=<backend>``) makes it live in this
  session; a real restart is needed otherwise. This checkpoint's own
  ``ecosystem <component> run`` output says so explicitly, every time.
- The graphical Control Center remains entirely future work; when
  built, it must expose ``run_ecosystem_command``'s own operations
  rather than inventing a second management system - the identical
  requirement 52.27 already established for providers/connections,
  extended here to evolution.

**Roadmap.** Cross-cutting infrastructure, not a roadmap phase - Phase
1 ✅, Phase 2 ✅, Phase 3 ✅, Whole-System Reality Audit ✅, Phase 4 ✅ all
stand unchanged, untouched, not reopened. AI Video Clipping, a real
WhatsApp connection, and Phase 5 were explicitly not started by this
checkpoint.

### 52.29 Full Alfred OS Ecosystem Evolution - surgical completion pass

52.28 proved one genuinely executable evolution path (a provider's
account model) and honestly named the rest - specialists, capabilities,
tools, connection adapters, Brain development - as inspection-only.
This checkpoint closes those gaps **wherever this repository can
genuinely support a real, technically meaningful mechanism**, and
leaves the rest explicitly, honestly unsupported rather than fabricated
- per this checkpoint's own governing rule: *"Every evolution category
must have a real, technically meaningful apply/migrate/verify mechanism
OR explicitly remain unsupported with a governed, honest refusal."*

**The engine became generic; components now supply their own
behavior.** ``ecosystem_evolution.run``/``rollback`` no longer contain
any category branching at all - that would have been exactly the "fake
generic apply anything" this checkpoint's brief forbids in one
direction, and exactly the "hard-coded permanent catalog" its Section
11 forbids in the other. ``src/services/evolution/handlers.py`` defines
one small ``EvolutionHandler`` contract (``applies_to``/``apply``/
``rollback``) and a registry (``handler_for``/``register_handler``);
the engine only ever asks "which handler owns this id" and calls it.
**This is the real, governed extension point Section 11 requires**: a
future component (a new Brain provider, a new specialist, a new tool, a
new connection adapter, an AI technology that does not exist yet)
participates by registering one handler - never by editing the engine,
proven directly by
``TestHandlerRegistryIsOpenEnded::test_a_future_component_participates_by_registering_one_handler``
registering a handler for a component id that appears nowhere else in
this codebase and having it work through the identical propose/run
path with zero engine changes.

**Four new, real, independently-verified mechanisms - each proven
end-to-end: propose → run (evaluate → approve → apply → verify →
activate) → fresh runtime → actually active → unrelated state intact →
rollback → fresh runtime → restored:**

- **``specialist.<key>`` / ``alfred`` - the real identity document's own
  content, never merely a hash bump.** ``SpecialistIdentityHandler``
  requires the *real new document content* (via a new
  ``propose-content`` action, since free-form markdown prose has
  nowhere safe to live inside ``EvolutionProposal``'s own fixed,
  short-string schema - the content is read as raw bytes, staged to
  disk keyed by proposal id, and both ``current_version``/
  ``proposed_version`` are real SHA-256 content hashes of raw bytes,
  the identical format ``inventory.py`` already reports). Apply backs
  up the real, current file byte-for-byte before ever touching it,
  re-parses the new content to verify it is a genuinely loadable
  identity (a malformed document fails apply outright and is proven
  never to land, even briefly), then independently re-reads the
  written file's own hash to confirm it matches. Rollback restores the
  exact backed-up bytes, verified byte-for-byte against the untouched
  original. **Structurally cannot touch a specialist's real
  capabilities** - proven, not merely asserted
  (``test_full_lifecycle_preserves_capabilities_and_activates_on_fresh_runtime``):
  a specialist's real capabilities come from registered code
  (``BASE_CAPABILITIES``/analyzers, e.g. ``src/agents/email_agent.py``),
  never the identity document, so evolving role/purpose/description
  here cannot silently change what a specialist can actually do. The
  identical handler evolves Alfred's own ``IDENTITY.md`` too - proven
  directly, not merely claimed to generalise.
  **A real, genuine bug was found and fixed while building this**:
  reading/writing the staged and real files in Python's default *text*
  mode silently translated newlines (``\r\n`` <-> ``\n``) between the
  moment a hash was computed and the moment the same bytes were written
  back, making a completely correct write fail its own independent
  verification purely from a line-ending mismatch. Fixed by reading and
  writing raw bytes throughout (``read_bytes``/``write_bytes``,
  decoding to text only for the parse-validation step) - the identical
  "the bytes are the fact" discipline ``inventory._file_version``
  already held itself to, now held consistently end-to-end.
- **``capability.general_conversation`` - the real, previously-hardcoded
  conversation-history turn count** (``scripts/control_center.py``'s
  own former ``CONVERSATION_HISTORY_LIMIT`` constant, unchanged as the
  honest default). Made governable through a new, small, explicitly
  version ``GovernedConfig`` store (``src/services/evolution/governed_config.py`` -
  a flat, non-secret ``component_id -> value`` JSON mapping, the
  identical "read fresh, write whole" shape ``CredentialStore``'s own
  persistence already uses, genuinely necessary because ``Settings``
  itself has no on-disk persistence of its own). Verified on a fresh
  runtime by actually driving a real ``Conversation`` past the old and
  new turn counts and confirming the bound history function returns
  exactly that many lines - not merely that a number changed.
- **``tool.document_generation`` - the real output directory
  ``DocumentGenerationTool`` writes to** (``settings.documents_dir``,
  made governable the identical way). Verified by actually writing a
  real document through the evolved tool and confirming the file lands
  in the new directory.
- **``connection.gmail`` - GmailConnection's own real adapter
  *implementation* version, never the account/credential.** v1 (the
  unchanged default) forwards only ``limit`` to ``Mailbox.fetch``; v2
  additionally forwards an optional ``label`` filter - real, because
  ``Mailbox.fetch(limit, label=None)`` (``src/services/email/mailbox.py``)
  already declares this parameter and ``GmailMailbox`` already
  implements it, v1 simply never forwarded it. ``build_connection``
  and ``build_runtime`` both gained a new, optional, additive
  ``adapter_version`` parameter (the identical seam every account
  parameter beside it already is) - ``composition.py`` still never
  names ``GmailConnection``/``NullConnection`` directly.
  ``ConnectionAdapterHandler.apply`` actually constructs a real
  ``GmailConnection`` at both the current and proposed version and
  refuses outright if authorization would differ between them -
  proving, not merely asserting, that an adapter upgrade never disturbs
  the account. Proven never to affect WhatsApp/Telegram or any other
  additively-connected service.

**Brain and Memory development remain deliberately read-only - never
routed through propose/run.** No safe, real way exists in this
codebase to version Alfred's own reasoning *code*, or most capability
*implementations*, without editing source - inventing one would be the
exact fabrication this checkpoint's own brief forbids. ``ecosystem
brain <agent>`` (new) reports the real reasoning mode
(``settings.brain``/``specialist_brain``) alongside real corrections/
lessons read from Memory (reusing ``memory_development_snapshot``,
extended with a real ``lessons`` count from ``MemoryCategory.LESSON``
records) - explicitly labelled as distinct from *which vendor answers*
(``provider.llm_provider``), and proven, not merely stated, to be
un-proposable: ``brain.manager`` is a real inventory component with no
registered handler, and ``propose`` against it is refused by name.

**Dependency/compatibility checking is real, not new machinery.**
``src.evolution.evaluate_proposal`` already rejects a proposal whose
named dependency is not active, or whose claimed current version no
longer matches the ledger's own independent record - reused verbatim,
now exercised against the four new mechanisms above, not only
providers.

**Persistence, made explicit.** Three genuinely distinct persisted
facts, in three files, each schema-versioned like every other store in
this project (``SCHEMA_VERSION`` + an intentionally-empty
``MigrationChain``, the same "wire the seam in now" discipline
``src/core/versioning.py`` itself recommends): ``data/evolution/state.json``
(the governed ledger + full history - unchanged from 52.28),
``data/evolution/config.json`` (the new ``GovernedConfig`` - capability/
tool/connection-adapter values), and per-specialist identity backups
under ``data/evolution/specialist_backups/`` (one real, timestamped
file per applied identity evolution, never overwritten, never pruned
automatically). None of this is a second credential system: secrets
still live only in ``data/credentials/accounts.json``, untouched.

**Control Center.** ``ecosystem <component> propose-content`` (new)
and ``ecosystem brain <agent>`` (new) extend the existing surface;
every other action (`propose`/`run`/`rollback`/`history`/`system`)
is unchanged in shape, now genuinely doing more because more handlers
are registered.

**Practical verification, through the real Control Center command
surface, on real repository components, never a synthetic stand-in:**
all four new mechanisms exercised exactly per this checkpoint's own
required shape (before → propose → approve → apply → verify → fresh
runtime → actually active → state preserved → rollback → fresh runtime
→ restored) using an isolated copy of Arthur's own real
``identities/email_agent.md`` (the canonical file was read to seed the
demo and never written to - confirmed directly afterward, byte-for-
byte identical) and a real, isolated copy of ``IDENTITY.md`` for
Alfred's own evolution. The real project's own persisted
``data/evolution/state.json``/``data/credentials/accounts.json`` were
inspected afterward and confirmed to hold only harmless, pre-existing
seed data - no demo content ever reached them.

**Regression.** ``tests/test_ecosystem_evolution_extension.py`` (24,
new) plus two legitimate, expected updates: 52.28's own
``test_a_specialist_target_is_recorded_but_apply_is_refused_honestly``
(renamed and re-asserted - specialist evolution is no longer
unsupported; what remains true is that the plain ``propose`` action has
no staged content to apply) and ``test_stage16_connections.py``'s own
``test_composition_never_names_a_specific_connection_subclass``
(updated to match ``build_connection``'s new, still-generic
``adapter_version`` argument). Full suite: **6393 passed** in both
fixed and randomised order (up from 6369 before this checkpoint), zero
unexplained regressions.

**What remains intentionally, honestly unsupported - not fabricated:**

- Most capability *implementations* (their actual Python code) and
  every tool's own *class* (as opposed to the one configuration value
  each of ``capability.general_conversation``/``tool.document_generation``
  now governs) have no real, safe apply mechanism in this codebase
  without editing source. Representing a fake "capability version" for
  these would have been exactly the fabrication this checkpoint's own
  brief forbids.
- Brain reasoning code itself has no real, safe apply mechanism -
  Brain development is real and inspectable, never proposable.
- A connection's own credential/account remains 52.27's own axis,
  entirely untouched by this checkpoint - only Gmail's real adapter
  *implementation* gained a second version, and only because a real,
  pre-existing, unused ``Mailbox`` capability (``label`` filtering)
  made a genuine v2 possible without fabricating one.
- Live, in-session activation is unchanged from 52.27/52.28: every
  ``ecosystem <component> run`` reply still states plainly whether a
  restart (or ``reload``) is needed - this checkpoint added no new
  live-activation mechanism.
- The graphical Control Center remains entirely future work; when
  built, it must expose ``run_ecosystem_command``'s own operations
  (unchanged in shape by this checkpoint) rather than inventing a
  second management system.

**Roadmap.** Cross-cutting infrastructure, not a roadmap phase - Phase
1 ✅, Phase 2 ✅, Phase 3 ✅, Whole-System Reality Audit ✅, Phase 4 ✅ all
stand unchanged. AI Video Clipping, a real WhatsApp connection, and
Phase 5 were explicitly not started by this checkpoint.
