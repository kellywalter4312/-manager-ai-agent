# GM mechanisms — work in progress (branch `gm-mechanisms-wip`)

**This branch is preserved work in progress. It is not Phase 11, it is not
merged into `main`, and nothing on it is production verified.**

Recorded 2026-10-02 during the handoff. `main` stays at the verified Phase 11
state (`f8892f5` + current-state documentation); this branch preserves,
unchanged, everything a session built on top of `f8892f5` on the evening of
2026-09-30 (17:14–22:24 local), so none of it is lost.

## Where it comes from

Phase 11 closure (REQUIREMENT_LEDGER.md section P11R, ARCHITECTURE.md §54.5–54.6)
recorded fifteen general mechanisms (GM-01..GM-15) and two contract
deficiencies (D-1, D-2). The owner approved their build order on 2026-09-30:

1. D-1 + GM-01 → 2. D-2 + GM-02 → 3. GM-04 → 4. GM-13 → 5. GM-03/15 →
6. GM-06/09 → 7. GM-05/12 → 8. GM-07/14 → 9. GM-08 → 10. GM-10/11

The session worked through steps 1–6 and stopped partway through step 7.
It updated neither REQUIREMENT_LEDGER.md nor ARCHITECTURE.md. The ledger's
P11R rows still describe the mechanisms as measured before this work, and
they have **not** been re-measured.

## Actual state, from the tree itself

| Step | Mechanism | Code | Wired into production imports | Tests | State |
|---|---|---|---|---|---|
| 1 | D-1 + GM-01 observation history | `src/services/observations/` (store, extract, watches), `src/agents/tools/observation.py`, `src/providers/connection.py` | yes (composition via `src/services/ecosystem_state.py`, Control Center) | `tests/test_gm01_observation_history.py` (29) | IMPLEMENTED, INTEGRATED, test-verified |
| 2 | D-2 + GM-02 conditions → events | `src/services/automation/`, `src/manager/proactive_conditions.py`, `src/agents/tools/automation.py` | yes (composition, `scripts/heartbeat.py`, `scripts/serve_ui.py`) | `tests/test_gm02_conditions.py` (20) | IMPLEMENTED, INTEGRATED, test-verified |
| 3 | GM-04 streaming/binary transport | `src/providers/stream_readers.py` (via `declared_connection.py`) | yes | `tests/test_gm04_streams.py` (11) | IMPLEMENTED, INTEGRATED, test-verified |
| 4 | GM-13 sensitivity class | `src/core/sensitivity.py`, `src/services/memory/sensitivity.py`, `src/agents/tools/sensitivity.py` | yes (manager, session, shared context, observer, exchange) | `tests/test_gm13_sensitivity.py` (13) | IMPLEMENTED, INTEGRATED, test-verified |
| 5 | GM-03 structured perception | `src/services/perception/`, `src/providers/perception/`, `src/agents/tools/perception.py` | yes (composition, registry factory, `serve_ui.py`) | `tests/test_gm03_perception.py` (24) | IMPLEMENTED, INTEGRATED, test-verified |
| 5 | GM-15 cross-connection groups | `src/services/connections/groups.py`, `src/agents/tools/groups.py` | yes (via `ecosystem_state.py`, Control Center) | `tests/test_gm15_groups.py` (10) | IMPLEMENTED, INTEGRATED, test-verified |
| 6 | GM-06 live data-bound views | `src/services/views/`, `src/agents/tools/live_view.py`, `ui/live_view.js` | yes (`serve_ui.py`, Control Center) | `tests/test_gm06_live_views.py` (10) | IMPLEMENTED, INTEGRATED; own tests pass, but **breaks three existing guards** (see below) |
| 6 | GM-09 series techniques | `src/services/observations/techniques.py`, `src/core/techniques.py` | yes (evolution handlers/inventory, image techniques) | `tests/test_gm09_series_techniques.py` (15) | IMPLEMENTED, INTEGRATED, test-verified |
| 7 | GM-05 spatial model | `src/services/spatial/` (geometry, model, reconstruct), `src/agents/tools/spatial.py` | **no**: nothing outside these files imports them | **none** | CODE PRESENT ONLY: not integrated, not tested, not verified |
| 7 | GM-12 capture pipeline | `src/services/capture/` (pipeline, policy), `src/agents/tools/capture.py` | **no**: nothing outside these files imports them | **none** | CODE PRESENT ONLY: not integrated, not tested, not verified |

Shared: `tests/gm_support.py` (isolates every owner-state location for the
GM tests), `src/services/declared_store.py`, `src/services/ecosystem_state.py`,
`src/manager/ecosystem_capabilities.py`, `src/control_center/ecosystem_commands.py`,
plus edits to 31 tracked files (composition, settings, manager, manager skills,
capabilities, Control Center, served UI, audit log, evolution, connections).

"Test-verified" means only that the step's own test file passes against the
real production composition, with owner state isolated. It does **not** mean:

- RUNTIME VERIFIED at a real owner door (served page, CLI) with real devices.
  No physical device, camera or sensor stream has been driven.
- Checked with the real local model (Full Brain). Memory note: a scripted
  double has hidden Full-Brain-only defects before (Phase 10, Phase 11 P11F).
- PRODUCTION VERIFIED in any sense.

## Verification run at preservation time (2026-10-02)

Full suite on exactly this tree, 2026-10-02 16:48–17:16, before the commit:
`python -m pytest -q --basetemp=<digit-free path>` gave **6 failed, 8830 passed,
2 skipped**, exit 1. Production `data/` was byte-identical before and after (90
files, md5 manifest), and the working tree was unchanged by the run.

All 132 tests in the eight GM test files passed. The six failures are
existing architecture guards that this work breaks. **They are not repaired
here**, by the owner's instruction for this handoff:

| Failing test | What it caught |
|---|---|
| `test_repository_recovery.py::...::test_no_module_is_left_prepared_but_unwired` | GM-05/GM-12 modules (`src/services/spatial/*`, `src/services/capture/*`, `src/agents/tools/spatial.py`, `src/agents/tools/capture.py`) are prepared but unwired |
| `test_repository_recovery.py::...::test_the_dead_duplicate_prototype_is_gone_and_nothing_is_unreachable` | the same modules are unreachable from production |
| `test_architecture_audit.py::...::test_no_executable_literal_names_the_manager` | `src/services/capture/pipeline.py:352` hardcodes `'Alfred'` instead of asking who the Manager is |
| `test_live_ui.py::...::test_the_server_names_no_agent` | `scripts/serve_ui.py` (GM-06 live page) emits `alfredlive.watch(...)` |
| `test_open_workspace_types.py::...::test_the_page_renders_panels_not_named_types_or_agents` | `ui/index.html` (GM-06 live panel) names `window.AlfredLive` |
| `test_stage17_20_live_visual_correction.py::...::test_no_python_module_learned_about_any_of_this` | `scripts/serve_ui.py` (GM-06 live page) carries its own `viewport` meta / page markup in Python |

So the branch is **not green**. Steps 1–6 pass their own tests but violate
three existing guards (an agent name hardcoded in the live-view code and in the
capture pipeline, and page markup in Python). Step 7 is unwired.

## Resuming

Resume on this branch, not on `main`. First re-run the suite. Then repair the
four guard violations listed above in steps 1–6, without weakening the
guards. Then re-measure GM-01..GM-15 against the P11R rows. GM-05 and
GM-12 come next, and they need wiring plus their own tests before either can be
called implemented. Only after that does step 8 start. The owner decides when,
if ever, any of this merges into `main`.
