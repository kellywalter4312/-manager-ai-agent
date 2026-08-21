# Manager AI Agent (Orchestrator Agent)

A multi-agent orchestrator built around one idea: **an agent should be useful
without a language model, and better with one.**

The Manager is the coordinator — it receives a mission, works out who should
handle it, delegates to a specialist, judges what comes back, and reports. Every
decision it makes is a deterministic rule by default. A model, when configured,
is an *additional* faculty layered on top of a system that already works.

Currently the only specialist is an **Email Agent** that reads a Gmail inbox
(read-only) and analyses it for job and freelance opportunities.

---

## How a mission flows

```
User / Heartbeat
      │
      ▼
   MANAGER ──────── D  Diagnose   which agent should handle this?   → Router
      │             A  Assemble   fetch that agent                  → Registry
      │             T  Take action  delegate and let it work        → Specialist
      │             A  Assess     check the result before reporting → Assessor
      ▼
  SPECIALIST  (Email Agent)
      │  reads the mailbox, runs deterministic analysers
      ▼
   MANAGER  assesses the result
      │
      ▼
    Report
```

The Manager contains no algorithm of its own. Routing, assembly, execution and
assessment are each performed by an injected collaborator, so swapping any of
them requires no change to the orchestrator.

---

## Brain and Memory

Every agent in the system follows the same shape, and the two halves are kept
deliberately separate:

```
AGENT
├── BRAIN     reasons, judges, decides what should happen
└── MEMORY    retains information and hands it back when asked
```

A brain that also stored things would decide what is worth keeping; a memory
that also reasoned would decide what a question means. Split, a reasoning
upgrade and a storage upgrade are independent changes.

**The Manager and each specialist hold their own brain and their own memory.**
They are never shared — separate objects over separate stores.

### Full Brain / Half Brain

The system reports which cognitive capabilities it actually has:

| Mode | Meaning |
|---|---|
| **FULL BRAIN** | A model-backed provider is reachable — deliberation and model-driven routing are available |
| **HALF BRAIN** | No model available — routing, delegation, self-handling, assessment and memory recall all still work |

**Half Brain is not a failure state.** It is the default, and the system is
fully useful in it. Capability is judged on whether a model is genuinely
reachable, not on whether an LLM object happens to exist: a reasoning component
wired to a null provider reports Half Brain, and a configured provider that is
failing reports Half Brain *degraded* rather than silently pretending.

---

## Memory architecture

Two independent questions, answered by two different layers:

**Storage lifetime**

- `EphemeralMemory` — lives in the process and dies with it. The default.
- `PersistentMemory` — one JSON file per owner, written atomically, survives
  process restart and Manager recreation.

**Temporal interpretation — "New Dawn"**

A record belongs to a calendar day, and becomes historical **because the clock
moved, not because anything ran**. There is no midnight job, no rollover, no
archive step and no second store: a record's day is derived from its timestamp
every time it is asked for.

That means the machine can be switched off across midnight, or the agent can go
quiet for a week, and nothing needs to catch up.

By default, recalled context is **today only**. Yesterday remains stored and
retrievable, but history has to be asked for deliberately — so yesterday's
conclusions cannot drift silently into today's reasoning.

### Timezone

Timestamps are stored in **UTC, always**. Which *human* day an instant fell on
is decided by a configured IANA timezone (`MANAGER_TIMEZONE`, default
`Africa/Lagos`). 23:30 UTC is already tomorrow in Lagos, and it is the person's
day that decides what still counts as current.

---

## Heartbeat

The Manager can run unattended, one cycle at a time:

```
wake → check Gmail → detect → analyse → match → rank → deduplicate → report
```

Each cycle is a single function call that performs one pass and exits. There is
no daemon and no internal sleep loop — **this project schedules nothing.** To
run it unattended you register it with your own scheduler, which is a decision
for you to make deliberately.

Cycles are protected by a lock so two runs cannot overlap, state is written
atomically, and persistence failures are reported explicitly rather than
silently swallowed. Deduplication works at two levels: Gmail message id, and a
fingerprint of role + company + source, so the same posting arriving twice is
recognised as old news.

Quiet runs write nothing. An agent that files a report every hour regardless of
whether anything happened costs you the time it was built to save.

---

## Gmail — the safety boundary

**Read-only, and structurally so.** This is the part of the project taken most
seriously.

| Capability | Available |
|---|---|
| Read messages | **Yes** |
| Send / reply / forward | **No** |
| Modify / delete / archive | **No** |
| Change labels | **No** |

Four independent layers enforce this, not one:

1. The OAuth scope requested is `gmail.readonly` **only**, and the granted
   scopes are verified against it at startup.
2. The mailbox client calls only `messages.list` and `messages.get`.
3. `Outbox` is declared as an interface with **no implementation anywhere** —
   there is nothing to inject even if something asked.
4. A task whose wording implies sending is refused deterministically, before
   any mailbox access.

**No autonomous action of any kind is enabled.** Reasoning may produce advice;
nothing acts on it.

### Trust in Stages

Higher-risk capabilities are deliberately withheld rather than merely disabled.
Several are built and fully tested but intentionally **not connected**: a
tool-using agent loop, an action-execution layer, and a cognitive loop. They
stay unwired until each is explicitly granted, one stage at a time.

The principle throughout: *wrong things should be structurally difficult or
impossible, not merely prohibited by instructions.*

---

## Status

**Implemented and working**

- Manager orchestration (diagnose → assemble → act → assess)
- Capability routing, with optional model-driven routing and loud fallback
- Manager brain and specialist brain, both configurable
- Persistent memory with day-aware retrieval
- Email Agent: Gmail read-only, deterministic opportunity analysis, matching,
  ranking and reporting
- Heartbeat with locking, atomic state and explicit persistence-failure handling
- 83 modules, ~12,500 lines of source, **2,556 tests passing**

**Deliberately not connected**

- Tool-using agent loop, action executor, cognitive loop (Trust in Stages)

**Not built yet**

- Any user interface or control centre
- Additional specialists (research, coding, and so on)
- Sending, replying or applying on your behalf — and no plan to enable it
  without an explicit, staged decision

**Note on the model.** The Anthropic provider is implemented and wired, but no
real inference has been run: the account it was tested against has no credits,
so every capability above is verified against scripted providers only.

---

## Running it

Requires **Python 3.12+**. The default configuration needs **no dependencies at
all** — it runs on the standard library.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt   # only needed for pytest, Gmail or Anthropic

python main.py                    # run the Manager over a few demo tasks
python main.py --deliberate       # also print the brain's advice (never executed)

python -m pytest -q               # run the test suite
```

Everything is configured through environment variables — see `.env.example` for
the full annotated list. The defaults are deliberately conservative:

| Variable | Default | Meaning |
|---|---|---|
| `MANAGER_PROVIDER` | `null` | No model. `anthropic` requires an API key |
| `MANAGER_ROUTER` | `capability` | Deterministic routing |
| `MANAGER_BRAIN` | `null` | Manager reasoning off |
| `MANAGER_SPECIALIST_BRAIN` | `null` | Specialist reasoning off |
| `MANAGER_MEMORY` | `ephemeral` | Nothing written to disk |
| `MANAGER_MAILBOX` | `memory` | Built-in sample inbox, no Gmail |
| `MANAGER_TIMEZONE` | `Africa/Lagos` | Which calendar decides "today" |
| `MANAGER_HEARTBEAT_ENABLED` | `false` | Autonomy is opt-in |

Turning on real reasoning takes two deliberate settings and a credential, never
one. A misconfiguration — a provider nothing consumes, or a reasoning component
with no model behind it — stops the program at startup with a readable message
rather than running quietly in the wrong mode.

### Gmail (optional)

```bash
python scripts/authorize_gmail.py   # one-time, read-only consent
python scripts/heartbeat.py --dry-run   # one cycle against the sample inbox
python scripts/heartbeat.py --once      # one cycle against the configured mailbox
```

Your credentials, token, personal profile (`USER.md`), processed state and
generated reports all live under paths that are gitignored and never committed.

---

## Repository layout

```
main.py              demo entry point / composition root
scripts/             heartbeat runner, one-time Gmail authorisation
src/
  manager/           orchestrator, router, assessor, brain, cognition
  agents/            specialist base, email agent, analysers, tools
  services/          memory, mailbox, identity, profile
  heartbeat/         runner, state, locking, reporting
  providers/         model provider abstraction (null / Anthropic)
  config/            settings and configuration coherence
tests/               2,556 tests
IDENTITY.md          who the Manager is — edit to change what it handles
SOUL.md              how it behaves — soft policy, loaded at runtime
USER.example.md      template for your own profile (USER.md is gitignored)
```

`IDENTITY.md` and `SOUL.md` are read at startup and genuinely consulted — the
Manager's capabilities and behavioural limits are a document edit, not a code
change.
