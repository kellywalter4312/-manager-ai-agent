"""Durable audit trail for ordinary consequential actions.

Distinct from ``src.services.evolution.ledger_store`` (which durably records
*governed evolution* transitions - a provider swapped, a specialist's identity
changed) - this records the other half the reconciliation audit named as
missing: an *ordinary* consequential action, attempted through a real granted
Tool during an ordinary conversation. Evolution already had a durable history;
sending a message, generating a document, or acting through a connection did
not - only the in-memory, per-process event log the Control Center's own
``history`` command reads.
"""
