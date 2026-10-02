# Alfred: current state (handoff snapshot, 2026-10-02)

This file says what Alfred **is** on `main` today. It is not a roadmap and
not a promise. The full history is in `ARCHITECTURE.md`, which is
chronological, and the full requirement record is in `REQUIREMENT_LEDGER.md`.
Where they and this file disagree about the present, this file was written
last and from evidence re-checked on 2026-10-02.

## State vocabulary

Every claim below uses exactly one of these words, and they are not
interchangeable:

| State | Meaning |
|---|---|
| DISCOVERED | someone named it (a conversation, an example) |
| RECORDED | it has a row in `REQUIREMENT_LEDGER.md` |
| ARCHITECTURALLY SUPPORTED | an open registry or seam would admit it without a code change to core, but no concrete instance exists |
| IMPLEMENTED | code exists and its own tests pass |
| INTEGRATED | production composition reaches it from a real door (Control Center, served page, CLI, conversation) |
| RUNTIME VERIFIED | exercised at a real door with real local components (a real browser, a real local model, a real local HTTP server) |
| PRODUCTION VERIFIED | used for real against real external services or devices by the owner |

"Not currently implemented" is a factual state, not a defect.

## What Alfred is

Alfred is a provider-independent agent-orchestration system written in Python.
It is an evolving ecosystem, not a chatbot. A Manager agent (Alfred) receives
requests, decides who handles them, delegates to specialists or handles them
itself, assesses the result and reports. Brain (model), memory, identity,
capabilities, connections and tools are separate, replaceable axes:

```
Agent = Brain + Harness + Memory + Environment + Evolution
```

Snapshot of `main` (`f8892f5` plus this documentation): about 277 source modules
and 77,700 lines of Python under `src/`, 265 test files.

**Test verification of this snapshot, stated exactly.** On 2026-10-02 the
full suite was started on clean main. It reached 38% with 0 failures and 2
skips, then it was stopped because the 8 GB development machine was critically
low on memory. **No complete full-suite pass of main was obtained on
2026-10-02.** The stopped process's `exit=1` came from the stop, not from a
test failure. The last complete run on record is the Phase 11 closure-point
run, made before `f8892f5` was committed (2026-09-30: 8679 passed, 2 skipped;
ledger P11X). That is a recorded result, not one re-run today.

## Branches

| Branch | What it holds |
|---|---|
| `main` | Phases 1-11 of the canonical roadmap, committed and verified (Phase 11 = `f8892f5`, 2026-09-30), plus this documentation |
| `gm-mechanisms-wip` (`84eaa0f`) | **Preserved work in progress, not verified Phase 11 work, not merged.** The owner-approved general-mechanism build: GM sequence steps 1-6 are wired and covered by their own tests; GM-05 spatial and GM-12 capture exist as code only, with no imports and no tests. Exact-tree suite on 2026-10-02: 8830 passed, **6 failed**, 2 skipped. All 132 GM tests pass. The 6 failures are known architecture/integration issues (GM-05/GM-12 unwired, a hardcoded `'Alfred'` in the capture pipeline, the GM-06 live-view implementation) and are deliberately not fixed. See `GM_WORK_IN_PROGRESS.md` on that branch |

## Phase 11

**Closed and committed** (`f8892f5`). The last recorded closure issue, the
drift of `capability.general_conversation-1`, was resolved on 2026-09-28
through the governed rollback (ledger P11-G-13). Re-checked 2026-10-02:
production `data/evolution/config.json` holds `capability.general_conversation
= "6"` and nothing else, byte-identical (md5 `d2dad4e9...`) to the state
recorded after the 2026-09-30 residue removal (P11X-02). No production
mutation was needed or made in this handoff.

Open owner decisions carried from Phase 11, unchanged: P11X-05 / P11Z-11
(make the suite hermetic by carrying owner-state paths on `Settings`), and
`propose()` reading the ledger's version (an unchanged-version proposal is
recorded and only rejected at run time).

## Brain modes

### Half Brain (no model) - verified 2026-10-02

Half Brain is the default operating mode (`llm_provider = null`), not a
failure state. It was re-verified on 2026-10-02 at the one-shot CLI door
(`scripts/control_center.py talk ...`). Each request ran as a fresh process
from a clean copy of `main` with throwaway `data/`:

| Request | Result | Mode |
|---|---|---|
| "what can you do?" | roster and capability report | available locally / deterministically |
| "remember that my favourite colour is teal", then in a new process "what is my favourite colour?" | stored, then recalled after a process restart | available locally / deterministically |
| "what time is it?" | answered from the machine clock | available locally / deterministically |
| "scan my inbox for job opportunities" | delegated Alfred -> Arthur (email specialist), 3 ranked matches from the built-in sample inbox | available locally / deterministically |
| "write a short document titled Kiln Safety with three points ..." | document artifact v1 created, with JSON and PDF | available locally / deterministically |
| "translate good morning into French" | refused honestly: offline translation (`argos`) not enabled and no model available | needs the offline engine switched on, or Full Brain |
| "what is 17 times 23?" | refused honestly: no deterministic arithmetic; it falls to knowledge and then "would rather say so than guess" | requires Full Brain |
| "explain why the sky is blue", "create a web page with a button that counts clicks" | refused honestly with "no reasoning capability is configured" | requires Full Brain |
| "what providers are available?", "switch to the local model" (as free text) | refused honestly; provider switching is a Control Center command (`provider llm_provider use local`), not a conversation request | Control Center command |

Nothing in Half Brain fabricated success. Two cosmetic observations were
recorded and **not** changed: a "nothing matches" result from
`knowledge_lookup` is logged as an ERROR with a full traceback on the
console, and the arithmetic refusal says it "found 1 relevant passage" even
though the documents source reported no match.

### Full Brain

**Not re-verified today (2026-10-02).** Nothing below was re-run in this
handoff.

Recorded earlier: the local model (`llm_provider = local`: qwen2.5-1.5b-instruct
q4_k_m through llama.cpp, model file under the gitignored `data/models/`) was
exercised at real doors during the Phase 10 and Phase 11 gates and closure
passes (ARCHITECTURE §54.1, §54.4; ledger P10, P11-G, P11F). Several Full-Brain-only
defects were found and repaired that way. It is a small CPU model, about 20–40 s
per call on this machine, and it is no substitute for a frontier model. Full
Brain is reported only after a real reasoning call succeeds; until then the
system reports Half Brain.

Vendor providers (`anthropic`, `openai`, `xai`) are implemented behind the
same provider contract, but **no real vendor inference has ever run**. The
Anthropic key is valid and the account is unfunded (`400 credit balance too
low`, 2026-08-20). No OpenAI or xAI credential has been exercised. The suite
blocks sockets and scrubs keys, so it cannot spend money.

## Memory - verified

Persistent per-owner memory, written atomically, day-aware ("New Dawn":
records become history because the clock moved, with no rollover job),
remember / recall / correct / forget, kept separate from brain, identity,
voice, provider and capabilities. Re-verified on 2026-10-02 in Half Brain
across a process restart (above). Recorded earlier and covered by the suite:
memory history, correction and forget (P11F-06/07), and memory backend as a
deployment setting (pre-Phase-10 closure).

## Other mechanisms on main

These states are **as recorded** in the ledger and ARCHITECTURE.md. They were
not re-run in this handoff, except where the Half Brain section above says so.

| Mechanism | State | Evidence |
|---|---|---|
| Provider switching (brain, speech, translation, image, knowledge, ...) | INTEGRATED, RUNTIME VERIFIED (local); vendor providers never exercised for real | Control Center `provider` axes; session-preserving live rebind (L-01); ledger section V |
| Agent architecture: Alfred, the Arthur email specialist, owner-created specialists as data (`agent create`), delegation, discussions, shared context, persisted sessions | INTEGRATED, RUNTIME VERIFIED | Phases 9-10; ledger P10 |
| Capabilities and governance: grantable / every-agent / manager-only, owner grants, audited admin writes | INTEGRATED, RUNTIME VERIFIED | capability authority model, 2026-09-22 |
| Governed evolution: propose -> apply -> rollback with ledger and audit; component inventory; the artifact runtime | INTEGRATED, RUNTIME VERIFIED | Phase 11, ARCHITECTURE §54 |
| Control Center: CLI and the served page (`scripts/serve_ui.py`) | INTEGRATED, RUNTIME VERIFIED in real Edge over CDP | ARCHITECTURE §52.54, `src/providers/browser/cdp_browser.py` |
| Gmail | read-only by construction (scope, client, no Outbox implementation, refusal) | README "Gmail - the safety boundary" |
| Persistence | atomic JSON stores under gitignored `data/` | suite |

## Devices, homes, appliances, VR, 3D, games

| Area | Actual state on main |
|---|---|
| Homes, rooms, appliances, TVs, sensors | Reached through the **generalized declared connection**: an entity is discovered from its own OpenAPI document and executed from the Control Center and from conversation, in Half Brain, behind a whole-word confirmation gate. RUNTIME VERIFIED against real **local HTTP servers** (P11-C-12, P11Z-06/07). **No physical device has ever been driven.** Automation (a condition over device state) is not on main |
| VR / AR / wearables | Not currently implemented. ARCHITECTURALLY SUPPORTED only through open surface and output registries (`SurfaceKind`, translation outputs) |
| 3D | Open 3D assembly (`model3d`) with registered shapes and motions, real glTF animation played in real Edge, and a WebGL workspace (orbit/zoom/fly-through): RUNTIME VERIFIED. No skeletal animation |
| Games and interactive worlds | One small interactive page (create -> run in a real browser -> observe errors -> revise -> v2) RUNTIME VERIFIED in Half Brain. A complex game has never been attempted. It would need a code-capable Full Brain, and no funded vendor key exists |
| Phones, new hardware, new media | Not currently implemented as specific instances; they join through the same open registries |

## Architectural principles (frozen)

**SPECIFIC EXAMPLE = VALIDATION INSTANCE. GENERAL MECHANISM = ARCHITECTURE.**

Alfred is an open-ended, evolving ecosystem. Games, VR, AR, homes,
appliances, TVs, 3D, simulations, AI agents, websites, web applications,
commerce, phones, hardware, interfaces, media, providers and models are
**not** architectural boundaries. None of them may become hardcoded
architecture, and no subsystem is named after an example. A new kind of
thing joins by extending a general mechanism (a registry, a provider axis, a
declared connection, a capability, an artifact type) and is then proven by
an instance. An example discussed in conversation is not, by itself, a
requirement to build. See ledger P11R (VI-01..14 -> GM-01..15).

**The Brain/Provider boundary is replaceable.** Alfred does not depend on any
particular model or vendor (Claude, OpenAI or any other). Changing the
provider or model must never require replacing Alfred's identity,
personality, memory, agents, capabilities, connections, projects, artifacts
or ecosystem state. Half Brain is a reduction in depth, never in function.
Making Half Brain match a frontier model is not a goal of this snapshot.

## Where to resume

1. `git checkout main` and complete the full-suite run that was owed on
   2026-10-02: `python -m pytest -q --basetemp=<a path with no digit groups>`.
   Use a machine with more than 8 GB of RAM, or run it in chunks of test
   files.
2. The next authorised work is the owner-approved GM sequence. It resumes
   **on `gm-mechanisms-wip`, not on main**: re-run its suite, re-measure
   GM-01..15 against ledger P11R, then finish step 7 (GM-05 spatial, GM-12
   capture: wiring and tests) before step 8. Nothing merges into `main`
   without the owner's decision.
3. After that, the canonical roadmap order stands: Phase **13** (multi-user
   and identity) -> **14** (knowledge, creation and external ecosystem) ->
   **15** (production) -> **12 last** (final Control Center and avatar).
   None of these has started.
4. Owner decisions still open: P11X-05 (hermetic suite), and whether to fund
   a vendor key (the only route to a capable Full Brain on this machine).
