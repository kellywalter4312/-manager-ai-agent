"""Condition events as a proactive source (GM-02 through D-2).

A condition that fired is something genuinely due to be told, exactly like a
reminder whose moment arrived - so it joins the one proactive pipeline every
caller already runs (``due_proactive_events``): the heartbeat shows it as a
desktop notification, files it in the proactive report and dedups it by its
own key; a live server posts it into the open conversation. No second delivery
system.

Only events nobody has seen or answered yet (``new``, ``proposed``) are
reported, each under a key stable for that event, so a heartbeat that runs
every minute tells the person once.
"""

from __future__ import annotations

from pathlib import Path

from src.manager.proactive_milestones import ProactiveContext, ProactiveEvent, register_proactive_source


def event_text(event) -> str:
    """How a fired condition is said to a person - Half Brain, no model."""
    text = f"{event.label} - {event.detail}."
    if event.action:
        params = event.action.get("params") or {}
        detail = f" with {', '.join(f'{k}={v}' for k, v in params.items())}" if params else ""
        text += (f" Shall I run '{event.action['operation']}' on '{event.action['connection']}'{detail}? "
                 f"(yes/no - or 'events approve {event.event_id}' in the Control Center)")
    return text


def deliver_event(manager, conversation, event, *, ecosystem=None, lock=None) -> bool:
    """Say a fired condition into a live room, as the Manager - the same room
    every surface reads - and, for a proposed action, raise the ordinary
    pending confirmation so the person's plain "yes" runs it through
    ``connected_action`` and nothing else. The event is then marked delivered
    so the proactive pipeline does not repeat it."""
    from contextlib import nullcontext

    from src.conversation.conversation import Utterance
    from src.manager.activity_session import PendingConfirmation

    skills = getattr(manager, "own_skills", None)
    speaker = skills.name if skills is not None else "Manager"
    with (lock if lock is not None else nullcontext()):
        sessions = getattr(skills, "_activity_sessions", None) if skills is not None else None
        if event.action and sessions is not None:
            action = event.action
            params = dict(action.get("params") or {})
            detail = f" with {', '.join(f'{k}={v}' for k, v in params.items())}" if params else ""
            sessions.raise_confirmation(speaker, PendingConfirmation(
                operation=action["operation"],
                description=(f"'{action['operation']}' on '{action['connection']}'{detail} will change something "
                             "on the real service. Proceed? (yes/no)"),
                connection=action["connection"], params={k: str(v) for k, v in params.items()},
            ))
        conversation.say(Utterance(
            speaker=speaker, text=event_text(event),
            detail={"handled_by": speaker, "capability": "condition_event", "event_id": event.event_id,
                    "proactive": True, "succeeded": True},
        ))
    if ecosystem is not None:
        ecosystem.events.set_status(event.event_id, "delivered", "said in the live conversation")
    return True


def _condition_source(context: ProactiveContext) -> "list[ProactiveEvent]":
    settings = context.settings
    if settings is None or not getattr(settings, "ecosystem_dir", None):
        return []
    from src.services.automation.conditions import EventLog

    return [
        ProactiveEvent(
            event_type="condition", label=event_text(event),
            dedup_key=f"condition:{event.event_id}", source="condition",
        )
        for event in EventLog(Path(settings.ecosystem_dir)).pending()
    ]


register_proactive_source("condition", _condition_source)
