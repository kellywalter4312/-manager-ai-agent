# Alfred: Manager AI Agent (Orchestrator)

Alfred is a **provider-independent agent-orchestration system**: one
evolving ecosystem of agents, capabilities, connections and memory. It is not
a single chatbot. One idea runs through it:

> **An agent should be useful without a language model, and better with one.**

Alfred (the Manager) receives a request and decides who should handle it. He
delegates to a specialist or handles it himself, assesses the result before
reporting, and stays accountable for the outcome. Every decision has a
deterministic floor. A model, when one is genuinely available, is an
*additional* faculty layered on top of a system that already works.

**What is true today, and how each claim was verified, is in
[`CURRENT_STATE.md`](CURRENT_STATE.md).** This README describes the shape.

---

## Architecture in one picture

```
Agent = Brain + Harness + Memory + Environment + Evolution

 person / heartbeat / Control Center
            |
            v
   ALFRED (Manager) -- diagnose -> assemble -> act -> assess
            |                         |
            |  handles it himself     |  or delegates
            v                         v
   capabilities & tools        specialists (Arthur, and any the owner
   (governed, granted)         creates as data with `agent create`)
            |
            v
   connections & providers  (brain, speech, translation, knowledge, media,
                             mail, declared HTTP entities, browser, ...)
```

The axes stay separate on purpose: brain, memory, identity, voice,
capabilities and connections are each replaceable without touching the
others.

- **Brain / provider boundary.** `null` (no model), `local` (llama.cpp on
  this machine), and `anthropic`, `openai` and `xai` behind one provider
  contract. Alfred is not tied to any vendor or model. Switching provider
  never replaces identity, memory, agents, capabilities, connections,
  artifacts or ecosystem state.
- **Full Brain / Half Brain.** The system reports which cognition it really
  has. Full Brain is claimed only after a real reasoning call succeeds.
  Half Brain (no model) is the default, a reduction in depth and never in
  function: routing, delegation, assessment, memory, tools, documents and
  artifacts keep working. What needs a model is refused honestly, never
  faked.
- **Memory.** Persistent, per owner, written atomically and day-aware:
  records become history because the clock moved, with no rollover job.
  Remember, recall, correct and forget are all supported.
- **Agents.** Alfred, the Arthur email specialist, and owner-defined
  specialists created as data. Delegation, multi-agent discussions, shared
  context and persisted sessions.
- **Capabilities and governance.** Capabilities declare who may hold them
  (grantable / every agent / manager-only). The owner grants. Admin writes
  are audited. Changes to the ecosystem go through governed evolution
  (propose -> apply -> rollback, with a ledger).
- **Connections.** One generalized connection mechanism: an entity (a device,
  a service, a site) is declared or discovered from its own OpenAPI
  document, not hardcoded.
- **Control Center.** A CLI and a served web page for status, history,
  providers, capabilities, agents and conversation.

## The architectural rule

**A specific example is a validation instance. A general mechanism is the
architecture.** Games, VR, homes, appliances, 3D, commerce, websites, new
devices and new models are not boundaries and are never hardcoded. Each
joins by extending a general mechanism and is proven by an instance. See
`CURRENT_STATE.md` for which instances exist today and which do not.

---

## Safety boundaries

- **Gmail is read-only, structurally.** The OAuth scope is `gmail.readonly`
  and is verified at startup. The client calls only `messages.list` and
  `messages.get`. `Outbox` has no implementation anywhere. Any task that
  implies sending is refused before mailbox access.
- **State changes need confirmation.** An action on a connected entity is
  held behind a whole-word confirmation gate.
- **Trust in stages.** Higher-risk capabilities are withheld until each one is
  explicitly granted.
- **No silent spending.** The test suite scrubs API keys and blocks the
  network, so it cannot make a paid call by accident.

The principle throughout: *wrong things should be structurally difficult or
impossible, not merely prohibited by instructions.*

---

## Running it

Requires **Python 3.12+**. The default configuration (`MANAGER_PROVIDER=null`,
Half Brain) runs on the standard library plus `numpy`. Everything else is
optional and imported lazily (see `requirements.txt`).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt

python scripts/control_center.py                  # interactive Control Center
python scripts/control_center.py status           # one overview, then exit
python scripts/control_center.py talk "what can you do?"
python scripts/control_center.py provider         # every provider axis and its choice
python scripts/serve_ui.py                        # the Control Center as a web page

python -m pytest -q --basetemp=<a temp dir whose path has no digit groups>
```

Configuration is through environment variables (see `.env.example`) and the
governed Control Center. The defaults are conservative: no model, a
deterministic router, the built-in sample inbox, the heartbeat off, and the
machine's local timezone.

Local models (optional) live under the gitignored `data/models/`. Credentials,
tokens, the personal profile (`USER.md`), memory and all runtime state live
under gitignored paths and are never committed.

---

## Repository layout

```
scripts/      Control Center (CLI + served page), heartbeat, Gmail consent, tools
src/
  manager/        Alfred: routing, assessment, cognition, capabilities, skills
  agents/         specialists, brains, governed tools
  providers/      brain/speech/translation/media/knowledge/browser/connection providers
  services/       memory, sessions, connections, credentials, evolution, audit, ...
  control_center/ observer, views, commands
  conversation/   sessions and turn handling
  composition.py  the production composition root
tests/        the suite (pytest; no network, no keys)
ARCHITECTURE.md       design record, chronological (long)
REQUIREMENT_LEDGER.md every requirement and its verified state
CURRENT_STATE.md      what is true today: start here
IDENTITY.md / SOUL.md who Alfred is and how he behaves, read at runtime
```
