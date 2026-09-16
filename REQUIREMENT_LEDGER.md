# Alfred OS — Canonical Requirement Ledger

**This file is the one canonical source of truth for "what does Alfred OS
need to do, and is it actually verified working."** It supersedes the
scattered per-campaign status tables inside `ARCHITECTURE.md` (§52.30–§52.48
and the older §0–§4 tables), which go stale as later campaigns supersede
earlier findings — e.g. `ARCHITECTURE.md:9268` still marks real-time
translation BLOCKED, while §52.46 (`ARCHITECTURE.md:9651`) shows it shipped
and VERIFIED WORKING five sections later. `ARCHITECTURE.md` remains the
narrative record of *how* and *when* each capability was built and stays
authoritative for implementation detail and history; this ledger is
authoritative for current status. Where they disagree, re-verify at
runtime and fix this file.

Do not create a second, competing requirement list. Update this one.

## Status vocabulary (per the owner's completion standard)

- **VERIFIED WORKING** — the actual requirement works, demonstrated at
  runtime, not merely present in code/tests/registration.
- **VERIFIED PARTIAL** — works to a real, architecturally-intentional
  limit (the limitation itself is explained, not hidden).
- **EXTERNAL ACTION REQUIRED** — the Alfred-side implementation is
  complete; only the owner's account/authorization/funded provider
  action remains.
- **HARDWARE/PHYSICAL TEST REQUIRED** — the software/device integration
  is complete; only a physical test remains (needs a human voice, a real
  phone, a real camera, etc. — none of which this session can supply).
- **VERIFIED BROKEN** — demonstrated not to work; a real defect.
- **NOT VERIFIED** — implemented, never demonstrated live.
- **NOT FOUND** — no implementation located.

## Methodology

Each entry compresses the owner's required record (requirement,
capability, historical source, architecture location, implementation
location, runtime/user access, Control Center access, dependencies,
verification evidence, status, intentional limitations, external
dependencies, hardware dependencies, regression protection) into a dense
block. Historical sources are cited as `<jsonl session id>:<line>` from
`~/.claude/projects/*/*.jsonl` (both project folders) or as an
`ARCHITECTURE.md:<line>` citation to a section already synthesising that
history. Architecture/implementation is cited as `ARCHITECTURE.md §N.NN`
plus named modules where the campaign writeup names them; this ledger
does not re-derive file:line citations already verified in that section
— it points at them rather than duplicating them, to avoid the two
documents drifting apart.

**Last reconciled:** 2026-09-16, against HEAD `0bca94a` (working tree
clean, 7785 tests passing fixed-order — re-verified this session), plus
one direct pass over the full historical transcript record (27 jsonl
files, 926 extracted user messages).

---

## A. Full Brain / Half Brain / Provider Architecture

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| PRV-01 | Alfred has a canonical identity/brain/memory/voice independent of the LLM provider | VERIFIED WORKING | ARCHITECTURE.md:107-116 (`TestBrainProviderCredentialMemoryIndependence`) | none |
| PRV-02 | Provider switching (Full Brain) | VERIFIED WORKING (construction) | ARCHITECTURE.md §52.34 (Campaign 4), `rebind_reasoning` | Real vendor answer EXTERNAL ACTION REQUIRED — unfunded key, see [[anthropic-account-unfunded]] |
| PRV-03 (L-01) | Switching provider preserves session (same Conversation, memory, identity, pending activity) — was the P0 blocker "the room dies with the session" | VERIFIED WORKING | ARCHITECTURE.md:8874-8945 (§52.34) | Real vendor answer EXTERNAL ACTION REQUIRED |
| PRV-04 | Served-UI live provider switch + persisted backend choice (L-02) | VERIFIED WORKING | §52.34, `provider.llm_provider` in GovernedConfig | none |
| PRV-05 | Two separate governed axes: vendor selection vs. `brain.manager` reasoning mode; a vendor alone never silently turns reasoning on | VERIFIED WORKING (by design) | §52.34, `test_campaign_04`; re-confirmed and the *message* fixed in §52.47 | Intentional: the Control Center used to say "Switched... live" while reasoning stayed off — now names the second axis explicitly |
| PRV-06 | Half Brain deterministic path never depends on a model being attached | VERIFIED WORKING | [[manager-agent-safety-boundaries]]: "the LLM is an additional capability, never a dependency" | none |
| PRV-07 | Half Brain is genuinely capable, not merely honest — ordinary personal statements are learned, any question word recalls, lookups answered by quoting the person's own material, corrections swap only the part named | VERIFIED WORKING | §52.47 (`extract_habitual_statement`, `quoted_from_own_material`) | none |
| PRV-08 | Local model as the Manager's whole brain | VERIFIED BROKEN on this hardware — honest, selectable, not recommended | §52.47/§52.44 — 89-254s/turn, degraded answers on this i5-6300U | HARDWARE: needs faster/GPU hardware to be practical, not a software defect |
| PRV-09 | A live credential check reachable from the Control Center (`provider llm_provider check`) | VERIFIED WORKING (mechanism); vendor unreached | §52.48, `check_authorization` | EXTERNAL ACTION REQUIRED — key unfunded |
| PRV-10 | A governed capability setting takes effect in the running session (general case, beyond the LLM-provider axis) | VERIFIED PARTIAL — persisted and honoured next session; current process says plainly it still holds the previous value | ARCHITECTURE.md:9390 (§52.42) | Intentional/honest partial, not a lie-through-omission; general-purpose live rebinding beyond the provider axis is unbuilt |

## B. Personal Learning, Memory & Correction

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| MEM-01 | One canonical personal-context system, no competing hidden store | VERIFIED WORKING | §52.31 (Campaign 1): single writer `personal_context.py`; `learning_context` capability removed | none |
| MEM-02 | Alfred and Arthur share one live profile (`UserKnowledge`) | VERIFIED WORKING | §52.31, `src/services/profile/user_knowledge.py` | Was previously split — Arthur read `USER.md` directly; fixed |
| MEM-03 | Natural learning of arbitrary personal facts (education, skills, goals, schedule, relationships, life changes) with nothing hardcoded | VERIFIED WORKING | §52.31, §52.47 `extract_habitual_statement` | none |
| MEM-04 | Correction — full-fact and partial ("it is Thursdays" swaps only the weekday); genuine unresolved ambiguity ("mom's birthday is either March 24 or May 24") stays visibly unconfirmed rather than silently picking one | VERIFIED WORKING | §52.32 (Campaign 2), §52.47; Control Center `unresolved` command (`src/control_center/observer.py::unresolved_facts`) surfaces exactly this state read-only — confirming happens by restating the fact conversationally, not from that screen | Was VERIFIED BROKEN — an ambiguous correction once deleted the wrong record; fixed |
| MEM-05 | Explicit remember/forget | VERIFIED WORKING | `forget` callable wired alongside `recall`/`retain`, `src/manager/manager_skills.py:85,182,685` | none |
| MEM-06 | Retrieval however phrased (question-word independence) | VERIFIED WORKING | §52.47 | Was VERIFIED BROKEN — "who is my dentist" reached nothing |
| MEM-07 | Restart persistence | VERIFIED WORKING | `PersistentMemory` re-read every `build_manager()` call, [[composition-refactor-in-progress]] | The two-lifetime invariant (`build_runtime` once/process, `build_manager` once/heartbeat) still has no dedicated test — see N-04 |
| MEM-08 | Authorized specialist access to canonical context, no shadow copy | VERIFIED WORKING | §52.31 `UserKnowledge` | none |

## C. Personal Guidance / Coach

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| COACH-01 | Generalized personal guidance/problem-solving — "ruthless or gentle" coaching from real goals, tasks, missed reminders; a capability of Alfred, not a separate agent | VERIFIED WORKING in both Half Brain and Full Brain, live-compared directly | §52.39 (Campaign 9); source requirement `307b6fab:696,1615-1628`, `b857600f:30`. **Live Half-Brain-vs-Full-Brain comparison, 2026-09-16** (explicit owner instruction: verify both axes, confirm Half Brain is genuinely capable, not merely honest): two real isolated systems, same conversation script (teach a goal, teach a task, ask to be coached), one with no LLM configured at all, one with a scripted `LLMProvider` standing in for a funded vendor (the project's own established discipline for exercising the Full Brain axis without a real vendor call). Half Brain answered from real taught data ("Your goal this year is to run a marathon... You have 1 open task(s)") with zero model calls; Full Brain's answer was genuinely Brain-composed and different (not a template); an unrelated open-ended question was honestly refused by Half Brain ("no reasoning capability is configured") and genuinely answered by Full Brain from the scripted response — proving the two axes are real and distinct, not one silently standing in for the other | Richer, model-composed guidance beyond the deterministic floor is EXTERNAL ACTION REQUIRED (unfunded model) — the axis itself is proven, only a real vendor account is missing |
| COACH-02 | Not a hardcoded "therapist mode"/"school mode" — natural interaction | VERIFIED WORKING | Same capability handles guidance/reflection/planning without mode-switching, §52.39 | none |
| COACH-03 | Guidance conversations feed the canonical personal-context system, no second memory | VERIFIED WORKING | Routes through `personal_context.py`, same as MEM-01 | none |

## D0. Four Separable Kinds of Knowing (found this round, governs section D)

`src/providers/knowledge.py`'s own docstring names an architectural
distinction the rest of this ledger's document/PDF rows sit inside without
stating explicitly: Alfred can know something four separable ways, kept
separate on purpose ("confusing any two of them is a lie") — general model
knowledge (the Brain, undated, attributable to no document), user-supplied
knowledge (THIS section, attributable to a named document), live information
(what the world looks like right now), and governed personal context
(section B, attributable to the person). Recorded here so the distinction
itself is traceable, not just its parts.

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| KNOW-01 | User-supplied knowledge as its own governed axis (documents/books/notes the person actually handed over), selectable through the Control Center like every other provider category | VERIFIED WORKING | `src/providers/knowledge.py`, `document_knowledge_provider.py`; `provider knowledge_provider` category (`GOVERNED_BACKEND_FIELDS`); `tests/test_stage18_knowledge_and_learning.py` (re-run 2026-09-16, passing) | "documents" (a local folder, honestly empty until populated) is the default — see PDF-01/02 for the ingestion path that populates it |
| INFO-01 | Live/current information — "what the world looks like right now", a real networked backend, never routed through the Brain | VERIFIED WORKING | `src/providers/wikipedia_live_info_provider.py` — real Wikipedia search, each result dated by its last edit; `tests/test_stage14_live_information.py`, `tests/test_stage14_production_live_information.py` (re-run 2026-09-16, passing) | Default is `null` (honestly empty) until the owner selects `live_info_provider=wikipedia`; this makes an outward network call, so it is reached only through the governed `live_information` capability, never automatically |

## D. PDF / Document Ecosystem

Cross-capability regression check, live, 2026-09-16 (round 7 of this
campaign): built a real, isolated system (`scripts.control_center.build`,
tmp data dir, no mocks) and ran one continuous conversation chaining
learning -> memory recall -> correction -> file share -> `read_content` ->
translation -> a final recall, confirming none of these turns corrupt each
other's state. All five checked outcomes passed
(`recall_ok`/`correction_ok`/`read_content_ok`/`translate_ok`/`final_recall_ok`
all True). Along the way, found and fixed a harness bug that is itself worth
recording: artifacts are owned by an agent's **display name**
(`runtime.identity.name`, e.g. "Alfred") while canonical Memory is
deliberately owned by its **stable technical key** ("MANAGER", never the
display name — see `composition.py::adopt_legacy_manager_memory`'s own
docstring). Opposite conventions, both intentional in their own domain, but
nothing in this ledger said so until now — recorded so a future caller (or
audit) doesn't lose the same hour tracing the same mismatch.

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| PDF-01 | Governed ingestion path for user-supplied files (`POST /upload`, "+ file" control, Control Center "Add file") | VERIFIED WORKING | §52.30 (REPAIR-01), `src/services/artifacts/ingestion.py` | none |
| PDF-02 | Content→units pipeline, cross-capability reuse (read/reason/convert/edit/personal-context) | VERIFIED WORKING | §52.30, `src/services/artifacts/content.py` | Document→calendar/task extraction NOT VERIFIED (needs Full Brain) — EXTERNAL ACTION REQUIRED |
| PDF-03 | Summary/Q&A/compare | VERIFIED PARTIAL — real document text reaches the Brain (scripted); real vendor answer blocked | ARCHITECTURE.md:8631 | EXTERNAL ACTION REQUIRED — unfunded |
| PDF-04 | User-authorized editing (literal replace, free-form, derived copy preserving the original) | VERIFIED WORKING (literal); free-form VERIFIED PARTIAL (scripted Brain only); found a real routing miss this round — see note | ARCHITECTURE.md:8632 | Layout-preserving in-place edit NOT VERIFIED — a regenerated text PDF is produced instead. **Found live 2026-09-16**: "Edit this document, fix the typo." routes to `general_assistance`, not `revise_artifact` — confirmed `revise_artifact_intent()` itself correctly returns True for this exact text, so the marker matching is not the problem; `general_assistance_intent()` also returns True for it, and `select_capability`'s own documented tie-break ("sorted by name so the answer is total and repeatable... not a priority list") picks whichever matching capability's name sorts first alphabetically — `general_assistance` before `revise_artifact`, by accident of naming, not by which one actually matches better. See N-07 for the broader architectural note this exposes |
| PDF-05 | PDF → voice (read aloud) | VERIFIED PARTIAL — real Kokoro synthesis verified | ARCHITECTURE.md:8634 | Speaker playback on a physical device NOT VERIFIED — HARDWARE/PHYSICAL TEST REQUIRED |
| PDF-06 | Scanned PDFs with no text layer (OCR) | VERIFIED WORKING | §52.47 — was silently broken (PowerShell console code page 437 ate every page boundary), fixed via `[Console]::OutputEncoding` pinning | none |
| PDF-07 | "This file" resolves correctly, including a Control Center upload never shared in conversation | VERIFIED WORKING | §52.47 `names_specific_content`, `shared_files` accessor | Was VERIFIED BROKEN |
| PDF-08 | .docx readable | VERIFIED WORKING | §52.35 (Campaign 5), stdlib zipfile+XML reader | none |
| PDF-09 | .html / .epub readers | VERIFIED WORKING | §52.37 (Campaign 7) | none |
| PDF-10 | Fetching a page from a live web address | NOT FOUND — by design | ARCHITECTURE.md:9134 | Needs a governed outward connection (see CON-05); not a defect |

## E. "Read Anything Aloud"

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| RD-01 | Reading is one interaction with content already in the ecosystem, not an isolated "book reader" feature | VERIFIED WORKING | Source: `32bb9d69:106-119` ("reading aloud becomes one possible interaction with content, not an isolated 'book reader' capability"); implemented §52.37 | none |
| RD-04 | Read text in images (printed) | VERIFIED WORKING (local Windows recogniser) | ARCHITECTURE.md:9133 | Camera photographs and handwriting NOT VERIFIED — HARDWARE/PHYSICAL TEST REQUIRED |
| RD-05 | Read web articles/books aloud | VERIFIED PARTIAL — saved files (.html/.epub) work | ARCHITECTURE.md:9134 | Live URL fetch NOT FOUND by design (needs CON-05) |
| RD-06 | Spoken output past the 400-char limit (`speak_in_full`) | VERIFIED WORKING (synthesis) | §52.30, `READ_ALOUD_SPOKEN_LIMIT=4000` | Playback on this machine's speakers NOT VERIFIED — HARDWARE/PHYSICAL TEST REQUIRED |

## F. Translation

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| TRN-01 | Generalized translation, no hardcoded language list | VERIFIED WORKING — 12 offline directions (en + es/fr/de/it/pt/zh, others pivot through English); Full Brain extends further | §52.46, `src/providers/translation/offline.py` | Hindi and Japanese have no installed offline packs — VERIFIED PARTIAL for those pairs specifically |
| TRN-02 | Real-time spoken/voice translation through headphones or device audio | VERIFIED WORKING | §52.46; source requirement `307b6fab:646`, `b857600f:88` | Near-real-time with device voice (4.3s); local neural voice ~13s (VERIFIED SLOW, not broken) |
| TRN-03 | Translation never echoes an untranslated phrase as a success | VERIFIED WORKING | §52.47 — "buenas noches" → "buenas noches" reported as a success was the worst bug found; fixed via a real source-language guesser | Was VERIFIED BROKEN |
| TRN-04 | Document translation | VERIFIED WORKING | §52.30/§52.46, offline-first with Full Brain fallback | none |
| TRN-05 | Configurable through the Control Center (engine, hearing, voice choice) | VERIFIED WORKING | §52.46 | none |
| TRN-06 | Recognition of a real human voice on a physical microphone | NOT VERIFIED | ARCHITECTURE.md:9734 | HARDWARE/PHYSICAL TEST REQUIRED — no human speaker in any session |

## G. Media & Content Ecosystem (Images, Video, Audio)

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| IMG-01 | Generate an image | VERIFIED WORKING for the anchored command shape; VERIFIED PARTIAL for reversed-order phrasing (see note) | §52.38, §52.48 — owner authorised one outward pollinations call: real 512x512 JPEG, model `sana` | Ordinary phrasing ("make me an image") was previously unrecognised; fixed §52.48. **Re-tested live this round (2026-09-16)** using the exact class of bug found in VID-01 (a modifier/justification clause between the request and the verb): "Make me an image of a cat" and "Can you make an image of a cat for me?" both correctly route to `media_production`; "I would love an image of a cat, can you make one?" and "I need a picture of a sunset, can you draw it?" fall through to `open_conversation`, confirmed via `select_capability` directly. **Deliberately not patched this round** — unlike VID-01's marker list, `media_production_intent`'s narrow anchoring ("anchored verb in command position, never a bare word... deliberately narrow") is explicit, stated design, not an accidental gap, and a quick fix risks new false positives ("can you make it to work by 9, I saw a nice picture yesterday" plausibly colliding). Recorded honestly as a real, confirmed, lower-confidence gap needing a careful dedicated repair with proper false-positive testing, not a rushed regex change |
| IMG-02 | Shared image reaches image analysis | VERIFIED WORKING for resolution; vision answer itself EXTERNAL ACTION REQUIRED | ARCHITECTURE.md:8869/9211 | Attributed local vision model now describes an image when the Brain has no view (§52.47) |
| IMG-03 | Image authenticity/manipulation — evidence, not a verdict | VERIFIED PARTIAL — metadata/editor-traces/JPEG error-level evidence VERIFIED WORKING; trained forensic verdict NOT FOUND (no model) | §52.38; confirmed against a genuine pollinations-generated image in §52.48; source requirement `307b6fab:1630-1636,2507`, `b857600f:59,95` | Real camera photographs and real generator outputs beyond pollinations NOT VERIFIED |
| IMG-04 | Image "enhancement/reconstruction/deconvolution" | Folded into IMG-02/IMG-03, not a separate architecture, per the owner's own instruction | Source: `307b6fab:586,689,594` — owner explicitly said treat as "an example/stress test of broader image intelligence, not automatically as a separate fourth architecture" | Tracked, not open — see IMG-02/03 for actual status |
| VID-01 | Video ingestion, clipping, derived artifacts (FFmpeg composition); AI-guided clip *selection* — "cut the funniest moments" — distinct from AI video generation (VID-02) | VERIFIED WORKING — repaired and live-demonstrated 2026-09-16, not just unit-tested | ARCHITECTURE.md §52.16 (pre-existing), imageio-ffmpeg bundled; `src/manager/capabilities.py::clip_video`, three escalating evidence paths; `tests/test_video_clipping.py` (87 tests, 2 new), `tests/test_video_understanding.py`. **Full user-facing path proven live**: built a real playable video (genuine FFmpeg mux), shared it through the real production path (`ingest_supplied_file` + `record_shared_file`, the exact mechanism `POST /upload` uses), asked for a clip in ordinary language, and verified the real output file on disk — correct duration (3.00s, exactly as asked), genuinely distinct bytes from the source, persisted. Isolated tmp dirs throughout; production `data/` confirmed byte-identical before/after (one accidental write during harness development was caught and deleted immediately) | **Real defect found and fixed this round, exactly the kind of "previously believed complete, later found broken" issue this campaign was told to hunt for.** `_CLIP_VIDEO_MARKERS` was a small closed phrase list requiring the clip-verb immediately adjacent to "this/the video/footage" ("clip the video from..."). The equally natural "clip 0:01 to 0:04 of that video" — time range *before* the object — matched nothing, fell through to `open_conversation`, and was refused with a generic "no reasoning capability configured" message instead of clipping or even a clip-specific refusal. Root-caused live (not by reading code) and fixed with an order-free verb+noun check (`_CLIP_VIDEO_VERB_NOUN_PATTERN`) that preserves the original "phrase plus evidence" false-positive guard — confirmed both the original bug phrasing and the pre-existing false-positive tests ("that was quite a clip show", a new "clip the coupon" test) behave correctly |
| VID-02 | AI-guided video generation | NOT VERIFIED / EXTERNAL ACTION REQUIRED, plus an Alfred-side routing note (see IMG-01) | ARCHITECTURE.md:6385,6549,6695 | No funded Runway/Google API key; no practical local video-diffusion model on this hardware (no CUDA GPU, ~8GB RAM). Also confirmed this round: `media_production_intent`'s deliberate command-anchoring (see IMG-01's note) affects `generate_video`/`compose`/`narrate` identically to `illustrate` — "I want a video of a futuristic city, can you generate it?" falls through to `open_conversation` the same way the image case does. Same root cause, same reason not hastily patched |
| REC-04 | Audio/video/recording transcription | VERIFIED WORKING with the local Windows recogniser on synthesised speech | ARCHITECTURE.md:8871, §52.33 | Live human voice on a physical mic NOT VERIFIED (HARDWARE); Whisper-quality EXTERNAL ACTION REQUIRED (unfunded), though `local_whisper` (faster-whisper base, int8) now exists for translation's hearing leg (§52.46) |
| FIL-01 | Uploaded video/image/audio usable by clip/image-analysis/transcribe by ordinary reference | VERIFIED WORKING | §52.35 `resolve_shared_media` | Was a gap: "this file" only resolved to any upload, not a specifically-named one |

## H. Voice / Audio / Camera / Device

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| VOI-01 | A vendor cloud voice option alongside the free local one, on the same governed axis as the LLM provider, with the complete configure/switch/test path Control Center gives the LLM axis | VERIFIED WORKING (construction/selection/live credential check); real vendor synthesis EXTERNAL ACTION REQUIRED | `src/providers/elevenlabs_speech_provider.py`, `speech_factory.py` (`speech_provider` governed category, same `GOVERNED_BACKEND_FIELDS` pattern PRV-02 uses); `tests/test_elevenlabs_speech_provider.py` (26 tests). **Repaired this round**: `provider <category> check <backend>` previously refused every category except `llm_provider` outright, even though `ElevenLabsSpeechAdapter` already had a real, live, vendor-backed call (`available_voices()`) sitting unused. Added `probe_authorization()` (a version that lets a real auth failure raise instead of `available_voices()`'s honestly-empty-on-any-failure tuple, which can't distinguish "key rejected" from "account has zero voices") and `check_speech_authorization()` (the speech analogue of `check_authorization`), wired into the same `provider speech_provider check elevenlabs [account-id]` command shape the LLM axis already uses. `tests/test_credential_authorization.py` (15 tests, 4 new/changed) proves the full Control Center path with a scripted adapter; regression batch of 361 tests across every touched file passed | Video/image generation vendors still have no live check (construction-only `validate` only) — found, not yet repaired, tracked as a new gap below. Kokoro/Windows correctly report "not a credentialed vendor" rather than a fabricated pass when checked |
| VOI-02 | Audible playback on a speaker | NOT VERIFIED — synthesis verified (real Kokoro WAVs), never played aloud | ARCHITECTURE.md:9441 | HARDWARE/PHYSICAL TEST REQUIRED |
| VOI-03 | Voice reaches the user's own device | VERIFIED PARTIAL — page speaks with the device's own speech synthesis | ARCHITECTURE.md:9442 | Audibility on a phone NOT VERIFIED — HARDWARE |
| VOI-04 | Physical headphones | BLOCKED | ARCHITECTURE.md:9444 | HARDWARE/PHYSICAL TEST REQUIRED |
| DEV-01 | Camera capture | VERIFIED PARTIAL — control renders, uses the governed upload path | ARCHITECTURE.md:9439 | Physical camera opening NOT VERIFIED — HARDWARE |
| DEV-02 | Physical microphone | BLOCKED | ARCHITECTURE.md:9444 | HARDWARE/PHYSICAL TEST REQUIRED |
| DEV-03 | Secure origin (HTTPS) for a phone's microphone | VERIFIED WORKING on this machine (trusted chain accepted, untrusted refused) | ARCHITECTURE.md:9440, §52.43 | Physical phone NOT VERIFIED — HARDWARE |
| AMB-01 | Wake-phrase ambient voice | BLOCKED | ARCHITECTURE.md:9443 | HARDWARE — needs a device with mic + speech recognition |
| REC-03 | Physical recording device | BLOCKED | ARCHITECTURE.md:9444 | HARDWARE |
| VIS-03 | Phone layout usable | VERIFIED PARTIAL — usable, no overflow | ARCHITECTURE.md:9438 | Avatar still obscured by the greeting text — minor, tracked |
| VIS-05 | Speaking/listening/thinking avatar states | VERIFIED PARTIAL — states and motion branches exist and render | ARCHITECTURE.md:9437 | Driven by a real spoken turn NOT VERIFIED — HARDWARE |
| VIS-06 | Phone GPU / iOS Safari | BLOCKED | ARCHITECTURE.md:9444 | HARDWARE |

## I. 3D Workspaces & Visual Environments

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| WS-01 | Generalized, open-ended workspace architecture — no hardcoded count or type list | VERIFIED WORKING | §52.45 (Post-Pre-Stage-9 part 2): `InterfaceFeature` opened from a closed enum to open string kinds registered via evolution handlers, §52.35 | none |
| WS-02 | Alfred's 3D workspace draws arbitrary diagrams (not just the tank/airplane examples) | VERIFIED WORKING, including reversed-order phrasing | §52.47 owner-list re-verification: "the 3D diagram draws a real WebGL scene", browser-verified | Tank/airplane were only ever illustrative test cases, per the owner's own framing this session. **Repaired this round (see N-06)**: "Make a diagram of a tank" and "I need a diagram of a tank, can you make one?" both now route correctly to `create_diagram`, with the real subject ("a tank") correctly extracted for the prompt in both phrasings — the shared `_reordered_subject` mechanism, not a one-off patch. 3 new regression tests, `test_artifact_workspace.py` (145 tests) passing |
| WS-03 | Alfred's coding workspace, a real code tool | VERIFIED WORKING | §52.47 `code_assistant` — closed the gap Alfred's own self-review had been reporting on every start | none |
| WS-04 | Adding a future agent doesn't remove Alfred's existing capabilities | VERIFIED WORKING | §52.45 — per-agent workspace grants through governance; each agent family opens surfaces independently | Regression-protected by `test_no_module_is_left_prepared_but_unwired` and the surface-registration tests |
| WS-05 | Every specialist has a real task board | VERIFIED WORKING | ARCHITECTURE.md:9648 | Was VERIFIED BROKEN |
| WS-06 | A withdrawn surface can be granted back | VERIFIED WORKING | §52.47 | Was VERIFIED BROKEN — withdrawal used to delete the governance record entirely |

## J. Interfaces vs. Workspaces

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| IFC-01 | Conversation interface is architecturally separate from workspaces | VERIFIED WORKING | §52.35 — `surface.<agent>.<kind>` distinguishes interface kinds from workspace kinds through the same open governance mechanism, not conflated | none |
| IFC-02 | No assumption of one interface or one workspace per agent | VERIFIED WORKING | §52.35, §52.45 | none |
| IFC-03 | Avatar works through appropriate interfaces, per agent | VERIFIED WORKING | ARCHITECTURE.md:350-354 (rows A, B) | none |

## K. Agents & Conversation Room

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| ROOM-01 | Multi-agent conversation, targeted addressing, brainstorming | VERIFIED WORKING | ARCHITECTURE.md §16 and on, addressing model (Phase 4) | none |
| ROOM-02 | Addressing works correctly across agents (not just Alfred) | VERIFIED WORKING | §52.35 finding — an earlier audit harness had addressed every message to Alfred, making this look broken when it wasn't | Corrected false-negative, not a real defect |
| ART-01 | Addressing the Manager on his own private line doesn't block delegation to a specialist | VERIFIED WORKING | §52.35 fix | Was VERIFIED BROKEN |
| HC-01 | The hardcoded component id `"alfred"` (in handlers.py, ecosystem_evolution.py, inventory.py) | VERIFIED WORKING — legacy alias kept for the one existing production record, new agents use open registration | §52.35 | none |
| No new ID | Adding a new agent expands the ecosystem without removing Alfred's abilities | VERIFIED WORKING | Same mechanism as WS-04; explicit invariant now enforced by `test_no_module_is_left_prepared_but_unwired` (251 production, 0 test-only, 0 unreachable) | none |
| No new ID | Future named domain-specific agents (Finance Agent, Media Agent, TikTokAgent, FootballComedyAgent, Children'sVideoAgent) | Explicit NON-requirement — owner rejected hardcoding these | Source: `0c651e7f:8047` ("Do NOT hardcode workflows for football, children, documentaries, TikTok, YouTube, etc.") | The open-ended specialist-registration architecture (WS-01/HC-01) is the correct generalization; do not build named single-purpose agents |
| ARTH-01 | Arthur's own mailbox/opportunity findings, viewable directly (not just via chat) | NOT FOUND — honestly declared, not hidden | `src/agents/email_agent.py::interface` declares `InterfaceFeature.MAILBOX`/`OPPORTUNITIES` but only lists `CHAT`/`ACTIVITY` as `implemented`; Control Center's own overview (`src/control_center/render.py:257-267`) shows the gap as "Planned", never as ready; `tests/test_channels_events_improvement.py::TestSpecialistInterfacesAreDiscovered::test_declared_but_unbuilt_surfaces_are_shown_as_planned` pins this as intentional-and-honest, not a bug | Found during the 2026-09-16 orphaned-functionality sweep (campaign brief §1C/§3) — Arthur genuinely reads a mailbox and genuinely produces ranked findings (reachable through chat/activity today); a direct screen for browsing those findings without asking in chat has never been built. Not previously in this ledger. No dependency beyond ordinary workspace/UI build-out; not blocked by anything external |

## L. Connections

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| CON-02 | Real Gmail read, and — per this round's explicit standard — a complete Alfred-side path to configure/reconnect/observe it, not just the read itself | EXTERNAL ACTION REQUIRED, with the full path confirmed ready | ARCHITECTURE.md:9340; re-checked this round: `connections gmail connect` correctly directs to `python scripts/authorize_gmail.py` (`scripts/control_center.py:1617-1622`), a real, complete, already-working interactive OAuth script (not a stub) that opens a browser, requests read-only scope only, and writes the token; `connections gmail status`/`validate` give real, honest status. **Both prerequisites are already in place** — `data/credentials.json` (the Google Cloud OAuth client, set up 2026-08-17) and the expired `data/token.json` (2026-08-18) both exist on disk, confirmed directly this session | Nothing left to build — the *entire* remaining step is you running `python scripts/authorize_gmail.py` and signing in once in the browser it opens. Read-only enforcement itself VERIFIED WORKING |
| CON-03 | Production connection data holds no leftover test fixtures | VERIFIED WORKING for the connections files themselves; a related leftover found elsewhere, provenance UNCONFIRMED | `data/connections/services.json`/`sites.json` both empty (verified directly, twice, independently). **New this round (2026-09-16, evolution-record sweep):** `data/evolution/state.json`'s `current_versions` map still holds `"connection.reservablething": "1 account(s), active=testaccount"` — a stale version pointer for the exact fixture connection that's now gone from `services.json`. Confirms the same class of issue (a verification script's run against production, per [[pre-stage-9-repair-ledger]]'s CON-03 finding) reached a second store, not just the connections files | The evolution `state.json` directory is gitignored too, so there's no commit trail there either. **Owner: this needs the same confirmation as before** — was clearing the connections fixtures (and leaving this evolution-state pointer stale) something you did, or should this pointer also be treated as leftover verification residue? Not touched without your word either way. (Separately, and lower-priority: the same file's `active_proposals` list has one real, fully-completed governance change from 2026-09-07 — `capability.general_conversation` 6→12 turns, proposed/evaluated/approved/applied/active — legitimate by its own history trail, just worth noting its `reason`/`expected_benefit` text is literally the words "reason" and "benefit," which reads like a verification script's placeholder rather than real authored content) |
| CON-05 | Real outward delivery (messages, calls) — and, more fundamentally, that WhatsApp/Telegram/Spotify/future services are examples of a generalized connection mechanism, never a fixed list needing new code per service | The generalized mechanism itself is VERIFIED WORKING; any specific real vendor connection is EXTERNAL ACTION REQUIRED | Re-verified this round: `tests/test_open_ended_service_discovery.py` (100 tests) — `test_no_service_catalogue_exists_anywhere_in_the_new_files` and `test_no_provider_name_is_hardcoded_in_the_new_files` prove there is no fixed vendor list anywhere in the mechanism; `test_a_second_generic_service_with_a_different_shape_needs_no_new_code` proves a second, differently-shaped service needs zero code changes; `TestConnectedActionReachesADeclaredServiceThroughTheUnmodifiedCapability` proves a real declared service answers a real `connected_action` request through the unmodified capability. The governed path is `services <name> declare <base-url> <op-spec>` (Control Center), landing in `src/services/connections/service_registry.py`, resolved into a real `DeclaredConnection` by `src/providers/connection_factory.py` with zero new subclass or `_BUILDERS` entry | No authorized outward connection (WhatsApp, Telegram, Spotify, or otherwise) exists yet — that part is genuinely EXTERNAL ACTION REQUIRED (needs the owner to declare and authorize a real one). Distinguishing "the architecture can take any future service with zero rebuild" (true, proven) from "a specific vendor is connected today" (not true) was missing from this row before this round |
| MARY-01 | Multi-website interaction by name, generalized (not one hardcoded salon site) | VERIFIED WORKING, generalized | §52.47 owner-list re-verification: "two local sites, confirmed click, the salon's server received the booking" | Originally flagged NOT FOUND in the 2026-09-14 audit; resolved and generalized since |

## M. Evolution & Ecosystem Governance

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| EVO-01 | Inventory, propose, history | VERIFIED WORKING | ARCHITECTURE.md:9386, §52.40 (Campaign 12) | none |
| EVO-02 | Approve, apply, verify, rollback | VERIFIED WORKING | ARCHITECTURE.md:9387 | none |
| EVO-03 | `register_handler` — a brand-new category with zero engine change, through the Control Center | VERIFIED WORKING | ARCHITECTURE.md:9388; this is the canonical mechanism, "Ecosystem Evolution B is NOT a zero-code requirement" per [[pre-stage-9-repair-ledger]] | none |
| EVO-04 | Governance refusals (unapproved, unknown, fabricated, double rollback) | VERIFIED WORKING | ARCHITECTURE.md:9389 | none |
| EVO-05 | One governed lifecycle, not two (the old `src/extensions.py` DISCOVER/VALIDATE/PLAN/APPROVE/APPLY/VERIFY duplicate) | VERIFIED WORKING — duplicate removed | §52.48 | none |
| EVO-06 | Self-improvement: Alfred observes his own real state and proposes changes, but can never apply, pre-approve, or widen his own permissions | VERIFIED WORKING | `src/improvement/governance.py` (`observations -> worth raising? -> proposal -> [a person]`, no function that applies a change exists); Control Center `review` command (`scripts/control_center.py:3246`), read-only; ARCHITECTURE.md:358 ("Self-improvement, no self-granted permission"), §8 item 4 "Trust in Stages" (permission moves only when a person moves it); `tests/test_channels_events_improvement.py` — 83 tests re-run 2026-09-16, all passing, including `TestImprovementCannotModifyAnything` and `TestImprovementObservesRealState` | Found this round in this ledger's own C-suite sweep — was already tracked in ARCHITECTURE.md's older §0 table (row K) but had no row in this ledger despite being wired to a real Control Center command. A proposal can suggest widening a permission or weakening a test; the *system* refuses to construct such a Proposal in the first place, so governance itself cannot argue past it |

## N. Architecture Hygiene

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| N-01 | No module left prepared-but-unwired | VERIFIED WORKING — production 251, test_only 0, unreachable 0 | §52.48, `test_no_module_is_left_prepared_but_unwired` | Regression-protected by that test itself |
| N-02 | One objective loop, not two (`src/agents/loop.py`, `cognitive_loop.py`, `actions.py` removed) | VERIFIED WORKING | §52.48 — canonical is `objective_loop.py`/`pursue_objective` | §0.3's row was stale (still said `cognitive_loop.py` was 🟡 PREPARED); fixed 2026-09-16 alongside N-05 |
| N-03 | Audit trail records what actually happened (not misclassifying successful writes as failures) | VERIFIED WORKING | §52.47 — `_administrative_write_succeeded` fixed for newer wording | Was VERIFIED BROKEN — every `provider ... use` success was logged as `failed` |
| N-04 | `build_runtime`/`build_manager` two-lifetime invariant has a dedicated test | VERIFIED WORKING — closed 2026-09-16 | `tests/test_composition.py` (6 tests, all passing): constructs `Runtime` and calls both functions directly for the first time; proves collaborators are shared across ticks, memories are independent per tick, and — the actual regression guard — an edit made entirely outside the process (simulating a collapsed single-lifetime design) is invisible to an already-built Manager and only visible to a fresh `build_manager()` call | Was Kelly's own "wired ≠ reachable ≠ durable" defect class reappearing inside its own fix; now closed and regression-protected. See [[composition-refactor-in-progress]] (needs updating — it still says this test doesn't exist) |
| N-05 | `ARCHITECTURE.md` §0 (front matter) reflects current reality | VERIFIED WORKING — fixed 2026-09-16 | Header now cites HEAD `0bca94a` and 7785 tests; §0.3's `cognitive_loop.py`/`actions.py` row now says "Removed, 2026-09-15 (§52.48)" instead of "🟡 PREPARED"; `tests/test_agent_system_foundation.py`'s module docstring (which pointed at a since-renamed `TestCognitiveLoopRemainsStagedButReal` class) corrected to name the real current class, `TestTheBrainDrivenLoopIsTheCanonicalOne` | Was L-08 in [[pre-stage-9-repair-ledger]], open across 13+ campaigns; closed this session. §0 remains hand-maintained narrative, not auto-derived — can drift again, spot-check periodically |
| N-07 | `select_capability`'s tie-break when two capabilities both claim the same request is alphabetical by capability name, not by which one actually matches more specifically | Confirmed by design, real consequence found live | `src/manager/capabilities.py::select_capability`'s own docstring: "A handler that recognises its own request is asked first... Sorted by name so the answer is total and repeatable... This is not a priority list." Real instance found this round (see PDF-04): `general_assistance_intent` and `revise_artifact_intent` both return True for "Edit this document, fix the typo.", and `general_assistance` wins purely because "g" sorts before "r" | Deliberately not touched this round — `select_capability` is the dispatch point for all 44 registered capabilities; narrowing `general_assistance_intent` or adding real specificity-based tie-breaking is a change with system-wide blast radius that needs its own careful, dedicated pass with full regression coverage, not a change made in passing while investigating something else. Recorded so it is not lost — the next time two capabilities are found to overlap this way, this is why |
| N-06 | A recurring intent-detection pattern: several capabilities recognize a command only when the verb is adjacent to (or precedes) its object, and silently fall through to `open_conversation` — an unhelpful generic refusal — for the equally natural "object first, justification, then the ask" phrasing | Mostly VERIFIED WORKING now — a shared, generalized mechanism was designed and applied; see status per instance | Found this round starting from a live VID-01 bug report, then deliberately checked for siblings via `select_capability` directly, six confirmed instances total: `_CLIP_VIDEO_MARKERS` (VID-01, **fixed** — an order-free verb+noun regex, `_CLIP_VIDEO_VERB_NOUN_PATTERN`); `create_task` (**fixed**, "I have a task: X" — found while live-testing Half Brain vs Full Brain, not from the original VID-01 investigation); `_DIAGRAM_MARKERS`/create_diagram (**fixed**); `_HANDOUT_MARKERS`/create_artifact (**fixed**); `media_production_intent`'s four verbs — narrate/illustrate/compose/generate_video (IMG-01/VID-02, confirmed live, **deliberately still not fixed** — anchoring is explicit stated design there, not an oversight) | **The generalized mechanism**, per the owner's explicit instruction not to patch individual sentences: a shared `_reordered_subject(text, *, nouns)` fallback (`src/manager/capabilities.py`, and an independently-implemented but same-shaped `_reordered_task_subject` in `src/services/memory/tasks.py`, matching that module's own established "reimplemented, not imported, so this module stays independent" convention already used for `_after_marker`) recognizes two equally natural reordered shapes — "I have/need/want a `<noun>`[:] X" and "I have/need/want a `<noun>` of X, can/could/would you `<verb>`?" — parameterized by each capability's own noun set so an unrelated "I have a meeting at 3pm" can never be mistaken for a task/diagram/handout, the identical precision every existing marker list already holds itself to. Wired into intent detection AND extraction (where extraction matters — `create_artifact` passes the whole text to the Brain regardless, so only needed the intent fix). 8 new regression tests across 3 files; full test-file regression (`test_artifact_workspace.py`, `test_phase5_1_tasks.py`, `test_manager_capabilities.py`, and 6 more files spanning conversation/governance/workspace routing) all passing. `media_production` was deliberately left as its own documented exception — the new shared mechanism reduces the risk that made this call originally, so it is now a live candidate for the same fix, not ruled out, just not done in this pass |

## P. Personal Organization (canonical roadmap Phase 5)

Added 2026-09-16: this whole phase — [[alfred-os-canonical-roadmap]] names it
explicitly ("Tasks ✅, Calendar, Reminders/alarms, Planning, Deadlines,
School dates, IT dates, Appointments, Projects") — had zero rows in this
ledger despite being a registered, tested capability area. Found via the
`MANAGER_CAPABILITIES` registry (`create_task`/`complete_task`/`update_task`/
`reopen_task`/`list_tasks`, `calendar`, `personal_recurring_events`), not
via any historical transcript search — a gap in this ledger's own first
pass, not in the underlying system.

| ID | Requirement | Status | Evidence | Dependencies / Limitations |
|---|---|---|---|---|
| ORG-01 | Tasks — create/complete/reopen/update/list, real lifecycle state | VERIFIED WORKING, including reversed-order phrasing | `src/services/memory/tasks.py`, `tests/test_phase5_1_tasks.py` (now 65+ tests, re-run 2026-09-16) | Deliberately minimal by design — title + status only, no due date/priority/recurrence on the task record itself (that structure lives in ORG-02 instead, kept separate on purpose per `tasks.py`'s own docstring). **Repaired this round (see N-06)**: "I have a task: finish the report." previously matched nothing at all and silently fell through to `open_conversation` — found live while verifying Half Brain vs Full Brain coaching, not from the original video-clip investigation. Fixed with the same shared `_reordered_subject` mechanism as WS-02/create_artifact |
| ORG-02 | Generalized calendar/organization items — a free-text title plus date/time/recurrence, never a fixed vocabulary of activity types | VERIFIED WORKING | `src/services/memory/calendar_items.py` (`CalendarItemCandidate`, `Recurrence`); `tests/test_calendar_items.py`, `tests/test_calendar_capability.py` (107 tests passing, re-run 2026-09-16); `test_dated_appointment_with_time` exercises the generic path with an ordinary appointment, not a special case | This is the correct generalization the campaign brief's §3 asks for: "school dates", "IT dates", "appointments", "deadlines" and "projects" are not separate features — each is the identical shape (title + date/time/recurrence) under a different title. No code change is needed to support a new kind of organizational item |
| ORG-03 | Reminders/alarms — reach the person proactively, not only when asked, including with no page open | VERIFIED WORKING — live-demonstrated 2026-09-16, not just mocked | ARCHITECTURE.md §52.36 (Campaign 6): `tasks.extract_reminder`, due events sourced as `"reminder"`, delivered as a proactive `message` experience, shown in the Control Center `calendar` view and listed conversationally (§52.47). The "no page open" leg is `src/providers/desktop_notifier.py::WindowsToastNotifier` — real Windows Toast API via PowerShell, delivery confirmed by reading the notification back from Windows' own notification history rather than assumed. `tests/test_live_capabilities_and_notifications.py`'s own delivery test mocks the subprocess call; I additionally ran the real, unmocked path directly on this machine this session — a genuine toast was shown and delivery was independently confirmed via the real readback (`delivered=True`), not a constructed claim | Requires the owner to choose `desktop_notification_provider=windows` (default is `null`, honestly silent) — a Control Center setting, not a missing capability |
| ORG-04 | Planning / Deadlines / School dates / IT dates / Appointments / Projects as named roadmap items | Folded into ORG-02, not separate architecture | Per the same generalization principle the campaign brief states for learning (§3) and the ledger already applies to IMG-04/agents (K) | Not a gap — building six separate "kinds" would be exactly the hardcoding the campaign brief warns against (§1, §3). Flagging here explicitly so this phase's six named items are traceable to one row each was checked, not silently dropped |

## O. Explicit Scope Boundaries (non-requirements, preserve these)

| ID | Statement | Source | Status |
|---|---|---|---|
| SCOPE-01 | No separate native mobile application — the served responsive web page is the only client | `6aa63c6f:4606,7892` ("No separate mobile application." / "Do not build separate mobile application.") | Honoured — architecture is one served page, phone-responsive |
| SCOPE-02 | The ruthless personal coach is a capability of Alfred, not a separate Coach Agent | `307b6fab:1628` | Honoured — see COACH-01 |
| SCOPE-03 | Reading aloud is one interaction with content, not an isolated "book reader" feature | `b857600f:119` | Honoured — see RD-01 |
| SCOPE-04 | Translation is part of the Voice+Communication+Connection+Intelligence architecture, not a standalone translator | `b857600f:88` | Honoured — see §52.46 |
| SCOPE-05 | Do not hardcode domain-specific agents (finance/media/TikTok/football/children's-video) | `0c651e7f:8047` | Honoured — see K, "Future named domain-specific agents" |

---

## Open items requiring the owner, summarized

**Decision needed (not a software gap):**
- CON-03 — the fixtures are factually gone, but nobody in this session cleared them and the owner has not confirmed it was intentional. Needs a one-line confirmation, not further investigation.

**External action required (Alfred-side work is complete):**
- Funded Anthropic (or other vendor) key: unlocks PRV-02/03, PDF-03, PDF-04 (free-form), COACH-01 (richer), IMG-02 (vision answer), PDF-02 (calendar/task extraction).
- Gmail re-authorization (CON-02): an interactive OAuth sign-in only the owner can run.
- Funded video-generation vendor key (VID-02).

**Hardware/physical test required (nothing left to build):**
- VOI-02, VOI-03, VOI-04, DEV-01 (physical), DEV-02, DEV-03 (physical phone), AMB-01, REC-03, RD-04 (camera/handwriting), RD-06 (playback), TRN-06, VIS-05, VIS-06, PDF-05 (playback).

**Genuinely open, software-only, no owner action required:**
- PRV-10 — general-purpose governed-setting live rebinding beyond the LLM-provider axis (intentional partial today, but worth closing if effort allows).
- CON-05, PDF-10 — both correctly gated on a governed outward connection that doesn't exist yet; not defects, but also not yet built.
- **VID/IMG check parity** — found 2026-09-16 while repairing VOI-01: `video_generation_provider`/`video_understanding_provider`/`image_generation_provider` still have no live "does the vendor accept this key" check, only construction-only `validate`. The mechanism now exists twice (LLM, speech) with an identical shape each time; a third/fourth implementation is feasible but needs each vendor's own real probe call identified first (unlike ElevenLabs, none was found already sitting unused this round) — not attempted yet, tracked so it isn't lost.

**Closed this session (2026-09-16), not open anymore:**
- N-05 (ARCHITECTURE.md §0 front matter refreshed, and a stale docstring
  reference in `tests/test_agent_system_foundation.py` corrected to name
  the test class that has existed at this HEAD all along).
- N-04 (composition two-lifetime invariant now has a dedicated,
  passing regression test — `tests/test_composition.py`).

CON-03 is NOT closed — see "Decision needed" above.
