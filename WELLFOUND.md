# Wellfound: Alfred project entry (copy-ready)

Prepared 2026-10-02 from `CURRENT_STATE.md`. Every claim below is either
implemented and verified, or labelled as architectural support or work in
progress. Paste the sections into the matching Wellfound fields.

---

## Project name

Alfred: provider-independent AI agent orchestration system

## One-line description

An evolving multi-agent AI ecosystem in Python: a manager agent that delegates, governs and remembers, and that works with or without an LLM.

## Description

Alfred is a personal AI ecosystem I designed and built in Python. It is an
orchestration architecture, not a chatbot. A manager agent (Alfred) takes a
request and either handles it himself or delegates it to a specialist agent.
He then checks the result before reporting.

**Implemented and verified**

- **Works without a model ("Half Brain").** With no LLM attached, Alfred still
  routes requests and delegates to a specialist. He remembers facts across
  restarts, answers from the system clock and creates documents. Anything
  that genuinely needs a model is refused honestly rather than faked.
  Re-verified on 2026-10-02.
- **Replaceable model layer.** The brain sits behind one provider contract:
  no model, a local model (llama.cpp), or Anthropic, OpenAI and xAI adapters.
  Switching provider leaves the agents' identity, memory, capabilities and
  connections untouched. The local model has been exercised end to end. The
  vendor adapters are implemented but have not yet run real paid inference.
- **Multi-agent work.** Delegation, multi-agent discussions, shared context,
  persisted sessions, and new specialists defined as data rather than code.
- **Governance.** Capabilities declare who may hold them, the owner grants
  them, admin actions are audited, and every change to the ecosystem goes
  through a governed propose -> apply -> rollback path with a ledger.
- **Persistent, day-aware memory** with recall, correction and forgetting,
  kept separate from the brain and the identity.
- **Generalized connections.** A device or service is discovered from its own
  API description instead of being hardcoded. Actions that change state
  require explicit confirmation. Verified against local test servers.
- **Control Center.** A CLI and a web interface for status, providers,
  capabilities, agents and conversation, verified in a real browser.
- **Safety by structure.** Gmail access is read-only at four independent
  layers. The test suite blocks the network so it can never spend money.

About 77,000 lines of Python and roughly 8,700 automated tests. A requirement
ledger records, for every feature, whether it is only implemented or also
integrated and verified at runtime.

**Architecturally supported, not yet built as instances**

The design is open-ended: new devices, environments, media and models join
by extending general mechanisms (registries, provider axes, declared
connections) rather than by hardcoding each one. No physical smart-home
device, VR/AR surface or full game has been built or driven yet.

**Work in progress (separate branch, unmerged)**

A further set of general mechanisms is preserved on a separate branch:
observation history, condition-triggered events, streaming transport,
structured perception, data-bound live views and sensitivity classes. Their
own tests pass, but the branch is not merged and not production-verified.

## Skills / tags

Python · AI Agents · Multi-Agent Systems · LLM Orchestration · Software
Architecture · System Design · llama.cpp · Anthropic / OpenAI APIs ·
Test-Driven Development · pytest · OAuth (Gmail API)

## Link

https://github.com/kellywalter4312/-manager-ai-agent
