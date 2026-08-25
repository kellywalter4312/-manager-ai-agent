# Manager AI Agent — Master Architecture & Roadmap

**Authoritative document.** Where this file and any other document disagree about
what exists, this file wins — and where this file and the *code* disagree, the
code wins and this file is wrong and must be corrected.

Last synchronised against: `HEAD = eb4fff6`, working tree with the Control
Center / channels / events / improvement work uncommitted. **3087 tests
passing.**

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
| Channel abstraction (Text / Voice) | `src/channels/channel.py` | Only `tests/` construct a Channel. No entry point routes through one. |
| Tool-using agent loop | `src/agents/loop.py` | Tests only. |
| Cognitive loop + action executor | `src/manager/cognitive_loop.py`, `src/manager/actions.py` | Tests only — and `test_reachable_deliberation.py:248` **asserts** it stays unreachable. Deliberate: Trust in Stages. |
| Event system | `src/events/*` | **Zero** imports from `src/` or `scripts/`. Nothing raises, logs, or announces an event. |
| Tool contract | `src/agents/tools/*` | The contract is reachable; **no tool is installed.** `web_search` and `code_assistant` are named as missing, not faked. |
| Persistent memory | `src/services/memory/persistent.py` | Implemented and selectable, but `MANAGER_MEMORY` defaults to `ephemeral` and **no memory file exists on disk**. Continuity is therefore not currently happening. |
| Workspace surfaces | `InterfaceFeature.WORKSPACE / CODE / FILES / SOURCES` | Enum values a specialist may declare. `render.py:138` prints one line — *"offers a dedicated workspace"*. There is no workspace object, state, or storage. |

---

## 3. Not started ❌

Nothing below exists in any form. No file, no class, no test.

Voice I/O (microphone, STT, TTS, audio playback) · per-agent `VoiceProfile` ·
voice addressing and routing · a `Conversation` object · multi-agent
conversation · real workspaces · Digital Humans / avatars · Coding Agent ·
Research Agent · security/testing specialist · context/research graph ·
project-continuity layer.

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
| C | **Shared conversation context** | ❌ | **No `Conversation` object exists.** See Gap 1. |
| D | Multi-agent conversation | ❌ | depends on C |
| E | Voice as I/O | 🟡 contract only | `channels/speech.py` |
| E | Voice as addressing/routing | ❌ | — |
| F | Agent-specific voices | ❌ | `speak(text)` has no voice parameter |
| G | Specialist workspaces | 🟡 label only | see §2 |
| H | Digital Humans | ❌ | — |
| I | Persistent continuity | 🟡 | per-agent JSON memory exists; **off by default**, and scoped per record, not per project |
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

### Gap 1 — There is no `Conversation`. **This is the keystone.**

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

### Gap 3 — No voice identity seam

`AgentIdentity` and `SpecialistIdentity` have no voice field.
`SpeechAdapter.speak(text)` has no voice parameter. **The contract as written
can only ever produce one voice**, so requirement F is currently inexpressible.

Good news: `ConversationTurn.handled_by` already records *who answered*, so
voice selection needs no new plumbing once a profile exists — registry → identity
→ profile.

### Gap 4 — Nothing composes a speech adapter

`Runtime` has no speech field; `src/composition.py` contains zero speech
references. `scripts/control_center.py` constructs `NoSpeech()` inline. Honest
today, and the one line that changes when speech is composed.

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

### Gap 7 — Continuity is per-record, not per-project

Memory is per agent, per record, day-aware. Nothing spans a project. There is no
notion of "where we started → what we planned → what we built". Also: persistent
memory is **off by default** and no memory file has ever been written.

### Gap 8 — Events cannot narrate

`src/events/` has zero production importers, so nothing can announce anything.
Requirements I and O both eventually need this.

### Gap 9 — No context graph

Absent entirely. Recorded as direction (§7), not scheduled.

---

## 6. Next implementation phases

| Phase | Work | Why this order |
|---|---|---|
| **0** | Commit the current tree (19 untracked files, 2 modified, 3087 green) in reviewable slices | 19 untracked files is a single point of loss. Nothing below should start on top of an uncommitted foundation. |
| **1** | Compose the Channel through `Runtime`; wire **one** real event emitter (`trail.observe` is the natural site) | Turns two 🟡 contracts into ✅ at the lowest possible cost. Removes Gaps 4 and 8. No new dependency. |
| **2** | **`Conversation` object**: participants, turns, shared context | The keystone (Gap 1). Everything after this is cheaper; everything before it that skips it gets rebuilt. |
| **3** | `VoiceProfile` on the identity layer + `speak(text, voice)` | Removes Gap 3. Needs **no** engine — provable with the existing recording double. Alfred and Arthur request different voices. |
| **4** | One real TTS engine behind `SpeechAdapter`. **Output only, no microphone.** | Speaking is strictly safer than listening: no always-on mic, no privacy surface, no barge-in. It also carries the requirement you care most about. |
| **5** | STT (push-to-talk), then voice addressing on top of Phase 2 | Addressing needs a conversation to route into. Doing it before Phase 2 hardcodes routing into the channel — exactly what must not happen. |

Later, in rough order: second specialist (Coding Agent) · real workspaces ·
project continuity · Digital Humans · context graph · security specialist.

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

---

## 9. Unresolved decisions

| # | Decision | Note |
|---|---|---|
| 1 | `VoiceChannel.receive()` catches only `SpeechUnavailable` | A real engine raising `OSError` would propagate. Recommend: each adapter translates its own failures. |
| 2 | Improvement deny-list omits `gmail.insert` / `gmail.labels` | The audit forbids the strings in `src/`; the deny-list does not refuse proposals containing them. Two-word fix, behaviour change. |
| 3 | Persistent memory is off by default | Continuity is currently not happening at all. |
| 4 | `README.md` `## Status` is stale | Says 2,556 tests and "no control centre". Should be trimmed to point here. |
| 5 | Local vs cloud TTS | Undecided. Affects Phase 4 and whether a second paid account is required. |
| 6 | Where `Conversation` lives | New package, or `control_center`? Affects Phase 2. |
| 7 | `ToolResult` carries no provenance | No `observed_at`, no source. An observation written to memory would lose its freshness. See §10. |
| 8 | Secret-detection is duplicated | `services/memory/secrets.py` and `manager/memory_policy.py` each carry one. Flagged in `secrets.py` itself, deliberately unconsolidated. |
| 9 | Stale docstring | `manager/memory_policy.py` says *"the Manager does not yet hold a policy"*. It does — `manager.py:192`. |

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

### 10.11 How this fits Full Brain / Half Brain

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
