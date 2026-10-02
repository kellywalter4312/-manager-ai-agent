"""The Control Center's management verbs for the ecosystem's general
mechanisms (GM campaign) - one module, dispatched from
``scripts/control_center.py``'s ``run_command`` like every other verb.

**A surface, not the mechanism.** Every verb here reads or writes through the
same stores and tools conversation uses (``runtime.ecosystem`` and the GM
tools); nothing about a watch, a condition or a group is decided here. Replace
this Control Center with another surface and every mechanism keeps working,
because none of them lives in this file.

**Writes are governed exactly like the rest of the Control Center.**
``classify_mechanism_write`` names every verb that changes declared state, so
``run_command`` gates it behind confirmation and records it in the durable
audit trail; ``mechanism_write_succeeded`` recognises each verb's own success
sentence, so the trail never records a refusal as a change (or the reverse).
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.services.observations.store import parse_duration

MECHANISM_VERBS = ("observe", "observations", "conditions", "events", "tick", "listen", "sensitivity", "perceive",
                   "groups", "analyze", "views")

MECHANISM_HELP = """\
ECOSYSTEM MECHANISMS (GM campaign)
  observe                  every watch: what is read, how often, last result
  observe <connection> <operation> [every <30s|5m|1h>] [as <entity>]
          [keep <path>,<path>] [with <param>=<value>,...]
          [frames perception] [listen <20s>]
                           watch a safe read (or listen to a stream) and keep
                           what it says as observations - confirmed, audited;
                           "frames perception" hands every picture it delivers
                           to the perception axis (what is in view, counted)
  observe remove|pause|resume <watch-id>
  observe sample [<watch-id>]
                           read every watch (or one) now
  observations             every observed entity, its attributes and counts
  observations <entity> [<attribute>] [last <30m|2h|7d>]
                           that entity's history - summary, or latest values
  conditions               every declared condition and whether it holds now
  conditions add <entity> <attribute> <test> [<threshold>] [over <10m> <mean|max|min|count|change|rate>]
                 [then <connection> <operation> [<param>=<value>,...]]
                           notify when it becomes true - or propose the action
                           and wait for approval (never taken alone)
                           tests: above below at_least at_most equals
                           not_equals changes rises_by falls_by stale
  conditions remove|pause|resume <condition-id>
  conditions check [<condition-id>]
                           evaluate now
  events                   what conditions have raised, newest last
  events approve <event-id>
                           run a proposed action on its connection - confirmed,
                           audited, never implicit
  events dismiss <event-id>
  tick                     run everything due now (watches, conditions, ...)
  perceive <artifact-id|path> [about <what>]
                           find, follow and count what is in a picture or
                           recording through the perception axis; what was
                           seen is kept as observations
  analyze                  every series technique available now (governed as
                           series_technique.<id> in ecosystem)
  analyze <technique> <entity>/<attribute> [with <entity>/<attribute>] [last <7d>]
                           what that technique finds in the history
  views                    every live view (dashboard) and where it is served
  views <id>               the view resolved now, as text
  views declare <id> <Title_words> <widget> ... [every=10] [shows=personal]
                           widgets: value:<entity>/<attr>
                                    series:<entity>/<attr>[@technique][~24h]
                                    group:<name> look:<watch-id> events
                                    reading:<connection>/<operation>
                           ("_" stands for a space) - confirmed, audited;
                           shown at live/<id> and as the workspace panel live:<id>
  views remove <id>
  groups                   every group: its members, across connections
  groups <name>            its members resolved, and their latest state
  groups add <name> <connection|entity|group>:<member>[,...]
  groups remove <name> [<member>,...]
                           declare, extend or shrink a group - confirmed, audited
  listen <connection> <operation> [for <30s>] [as <entity>]
                           listen to a stream the connection declares
                           (open+stream) within limits; each event is kept
                           as an observation the moment it arrives
  sensitivity              the owner's rules for personal information,
                           retention per level, and what a cloud model may
                           receive
  sensitivity word|source|kind <match> <personal|sensitive|restricted>
                           declare a rule (a word or phrase, a connection or
                           capture, a memory kind)
  sensitivity remove <match|rule-id>
  sensitivity retention <level> <days|off>
                           forget records and readings of that level after N days
  sensitivity cloud <ordinary|personal|sensitive|restricted>
                           the highest level a vendor's reasoning model may
                           receive (default personal) - local models are
                           trusted up to sensitive
"""


def classify_mechanism_write(parts: "list[str]") -> "tuple[bool, str]":
    """``(True, description)`` for a verb that changes declared ecosystem
    state; ``(False, "")`` for every read."""
    if not parts:
        return False, ""
    verb = parts[0].lower()
    sub = parts[1].lower() if len(parts) > 1 else ""
    if verb == "observe":
        if sub in ("", "sample"):
            return False, ""
        if sub in ("remove", "pause", "resume") and len(parts) >= 3:
            return True, f"{sub} the watch '{parts[2]}'"
        if len(parts) >= 3:
            return True, f"watch '{parts[2]}' on '{parts[1]}' and keep what it says as observations"
    if verb == "conditions":
        if sub == "add" and len(parts) >= 5:
            then = " and propose an action when it holds" if "then" in [p.lower() for p in parts] else ""
            return True, f"declare the condition '{' '.join(parts[2:5])}'{then}"
        if sub in ("remove", "pause", "resume") and len(parts) >= 3:
            return True, f"{sub} the condition '{parts[2]}'"
    if verb == "sensitivity" and sub:
        if sub in ("word", "source", "kind") and len(parts) >= 4:
            return True, f"treat {sub} '{' '.join(parts[2:-1])}' as {parts[-1]}"
        if sub == "remove" and len(parts) >= 3:
            return True, f"remove the sensitivity rule '{' '.join(parts[2:])}'"
        if sub == "retention" and len(parts) >= 4:
            return True, f"keep {parts[2]} information for {parts[3]} day(s)"
        if sub == "cloud" and len(parts) >= 3:
            return True, f"let a vendor's reasoning model receive information up to '{parts[2]}'"
    if verb == "views" and sub in ("declare", "remove") and len(parts) >= 3:
        return True, (f"declare the live view '{parts[2]}'" if sub == "declare" else f"remove the live view '{parts[2]}'")
    if verb == "perceive" and len(parts) >= 2:
        return True, f"look at '{parts[1]}' and keep what was seen (observations and a picture)"
    if verb == "groups" and sub in ("add", "remove") and len(parts) >= 3:
        if sub == "add":
            return True, f"add {' '.join(parts[3:]) or 'members'} to the group '{parts[2]}'"
        return True, (f"remove {' '.join(parts[3:])} from the group '{parts[2]}'" if len(parts) > 3
                      else f"remove the group '{parts[2]}'")
    if verb == "events" and sub in ("approve", "dismiss") and len(parts) >= 3:
        if sub == "approve":
            return True, f"run the action proposed by event '{parts[2]}' on its real connection"
        return True, f"dismiss event '{parts[2]}'"
    return False, ""


def mechanism_write_succeeded(verb: str, text: str) -> bool:
    text = text.strip()
    if verb == "observe":
        return text.startswith(("Watching:", "Removed the watch", "Paused the watch", "Resumed the watch"))
    if verb == "conditions":
        return text.startswith(("Condition set:", "Removed the condition", "Paused the condition", "Resumed the condition"))
    if verb == "events":
        return text.startswith(("Executed:", "Dismissed"))
    if verb == "sensitivity":
        return text.startswith(("Rule set:", "Removed the rule", "Retention set:", "Cloud allowance set:"))
    if verb == "groups":
        return text.startswith(("Group set:", "Removed the group", "Group updated:"))
    if verb == "perceive":
        return text.startswith("Seen:")
    if verb == "views":
        return text.startswith(("View set:", "Removed the view"))
    return False


def _observation_tool(runtime: Any, owner: str):
    from src.agents.tools.observation import ObservationTool

    return ObservationTool(runtime.ecosystem, runtime.connections, owner_agent=owner, max_sensitivity="restricted")


def _call(tool: Any, operation: str, content: "dict | None" = None) -> "tuple[dict | None, str]":
    try:
        return json.loads(tool.run(operation=operation, content=json.dumps(content or {}))), ""
    except Exception as exc:  # noqa: BLE001 - a management screen reports, never crashes
        return None, str(exc)


def run_mechanism_command(runtime: Any, verb: str, rest: str, *, owner: str = "operator") -> str:
    if getattr(runtime, "ecosystem", None) is None:
        return "the ecosystem stores are not part of this runtime"
    if verb == "observe":
        return _run_observe(runtime, rest, owner)
    if verb == "observations":
        return _run_observations(runtime, rest)
    if verb == "conditions":
        return _run_conditions(runtime, rest, owner)
    if verb == "events":
        return _run_events(runtime, rest, owner)
    if verb == "tick":
        from src.services.automation.tick import run_ecosystem_tick

        return run_ecosystem_tick(runtime.ecosystem, runtime.connections).describe()
    if verb == "listen":
        return _run_listen(runtime, rest, owner)
    if verb == "sensitivity":
        return _run_sensitivity(runtime, rest, owner)
    if verb == "perceive":
        return _run_perceive(runtime, rest, owner)
    if verb == "groups":
        return _run_groups(runtime, rest, owner)
    if verb == "analyze":
        return _run_analyze(runtime, rest, owner)
    if verb == "views":
        return _run_views(runtime, rest, owner)
    return f"unknown ecosystem verb '{verb}'"


# -- GM-06 ---------------------------------------------------------------------

def _widget_spec(token: str) -> dict:
    """``value:<entity>/<attr>``, ``series:<entity>/<attr>[@technique][~24h]``,
    ``group:<name>``, ``look:<watch-id>``, ``reading:<connection>/<operation>``,
    ``events`` - "_" in a name stands for a space."""
    kind, _sep, spec = token.partition(":")
    kind = kind.lower()
    spec = spec.replace("_", " ") if kind in ("value", "series", "group") else spec
    if kind == "events":
        return {"kind": "events", "limit": int(spec or 8)}
    if kind == "group":
        return {"kind": "group", "group": spec}
    if kind == "look":
        return {"kind": "look", "watch": spec}
    if kind == "reading":
        connection, _s, operation = spec.partition("/")
        return {"kind": "reading", "connection": connection, "operation": operation}
    if kind in ("value", "series"):
        window = None
        if "~" in spec:
            spec, _s, span = spec.partition("~")
            window = parse_duration(span)
        technique = ""
        if "@" in spec:
            spec, _s, technique = spec.partition("@")
        entity, _s, attribute = spec.rpartition("/")
        widget = {"kind": kind, "entity": entity.strip(), "attribute": attribute.strip().replace(" ", "_")}
        if technique:
            widget["technique"] = technique.strip().replace(" ", "_")
        if window is not None:
            widget["window_seconds"] = window.total_seconds()
        return widget
    raise ValueError(f"'{token}' is not a widget - use value:, series:, group:, look:, reading: or events")


def _run_views(runtime: Any, rest: str, owner: str) -> str:
    from src.agents.tools.live_view import LiveViewTool

    tool = LiveViewTool(runtime.ecosystem, runtime.connections, owner_agent=owner, max_sensitivity="restricted")
    args = rest.split()
    if not args:
        listed, error = _call(tool, "views")
        if listed is None:
            return f"FAILED: {error}"
        if not listed["views"]:
            return ("no live views are declared.\n"
                    "declare one with: views declare <id> <title_words> <widget> <widget> ...")
        return "LIVE VIEWS\n" + "\n".join(f"  {v['text']}  -> {v['path']}" for v in listed["views"])
    sub = args[0].lower()
    try:
        if sub == "declare" and len(args) >= 4:
            view_id, title = args[1], args[2].replace("_", " ")
            widgets = [_widget_spec(t) for t in args[3:] if not t.lower().startswith(("every=", "shows="))]
            options = dict(t.split("=", 1) for t in args[3:] if t.lower().startswith(("every=", "shows=")))
            content = {"view_id": view_id, "title": title, "widgets": widgets, "declared_by": owner}
            if "every" in options:
                content["refresh_seconds"] = int(options["every"].rstrip("s"))
            if "shows" in options:
                content["max_sensitivity"] = options["shows"]
            done, error = _call(tool, "declare", content)
            if done is None:
                return f"FAILED: {error}"
            return f"View set: {done['text']} -> {done['path']}"
        if sub == "remove" and len(args) >= 2:
            done, error = _call(tool, "remove", {"view": args[1]})
            if done is None:
                return f"FAILED: {error}"
            return f"Removed the view '{args[1]}'." if done.get("removed") else f"no view called '{args[1]}'"
    except ValueError as exc:
        return f"FAILED: {exc}"
    resolved, error = _call(tool, "resolve", {"view": " ".join(args)})
    if resolved is None:
        return f"FAILED: {error}"
    lines = [f"VIEW {resolved['title']} (resolved {resolved['resolved_at'][11:19]} UTC)"]
    for widget in resolved["widgets"]:
        if widget.get("state") != "ok":
            lines.append(f"  {widget['label']}: {widget.get('note') or widget.get('state')}")
        elif "value" in widget:
            lines.append(f"  {widget['label']}: {widget['value']} {widget.get('unit', '')}".rstrip())
        elif "points" in widget:
            lines.append(f"  {widget['label']}: last {widget['last']}, range {widget['min']}..{widget['max']} "
                         f"({widget['count']} readings){' - ' + widget['technique']['detail'] if widget.get('technique') else ''}")
        elif "members" in widget:
            lines.append(f"  {widget['label']}: {len(widget['members'])} member(s), "
                         + "; ".join(f"{k} mean {a['mean']}" for k, a in widget["aggregates"].items()))
        elif "events" in widget:
            lines.append(f"  {widget['label']}: {len(widget['events'])} recent")
        else:
            lines.append(f"  {widget['label']}: ok")
    return "\n".join(lines)


# -- GM-09 ---------------------------------------------------------------------

def _run_analyze(runtime: Any, rest: str, owner: str) -> str:
    tool = _observation_tool(runtime, owner)
    text = rest.strip()
    if not text:
        listed, error = _call(tool, "techniques")
        if listed is None:
            return f"FAILED: {error}"
        return "SERIES TECHNIQUES\n" + "\n".join(
            f"  {t['id']} ({t['purpose']}{', two series' if t['inputs'] == 2 else ''}): {t['label']} - "
            f"{', '.join(t['words'][:4])}" for t in listed["techniques"])
    window = None
    match = re.search(r"\s+last\s+(\S+)\s*$", text)
    if match:
        window = parse_duration(match.group(1))
        if window is None:
            return f"'{match.group(1)}' is not a duration - try 30m, 2h, 7d"
        text = text[: match.start()].strip()
    technique, _, targets = text.partition(" ")
    first, _, second = targets.partition(" with ")
    content: dict = {"technique": technique.lower()}
    for key_entity, key_attribute, part in (("entity", "attribute", first), ("with_entity", "with_attribute", second)):
        if not part.strip():
            continue
        entity, slash, attribute = part.strip().rpartition("/")
        if not slash or not entity or not attribute:
            return "name each series as <entity>/<attribute>, e.g. analyze peaks hive counter/visits last 1d"
        content[key_entity], content[key_attribute] = entity.strip(), attribute.strip()
    if window is not None:
        content["window_seconds"] = window.total_seconds()
    done, error = _call(tool, "analyze", content)
    if done is None:
        return f"FAILED: {error}"
    result = done["result"]
    lines = [f"{result['label']}: {result['detail']}"]
    lines += [f"  {f['at'][:19].replace('T', ' ')} {f['what']} {f['value']}" for f in result["findings"][:20]]
    return "\n".join(lines)


# -- GM-15 ---------------------------------------------------------------------

def _run_groups(runtime: Any, rest: str, owner: str) -> str:
    from src.agents.tools.groups import GroupTool

    tool = GroupTool(runtime.ecosystem, runtime.connections, owner_agent=owner, max_sensitivity="restricted")
    args = rest.split()
    if not args:
        listed, error = _call(tool, "groups")
        if listed is None:
            return f"FAILED: {error}"
        if not listed["groups"]:
            return "no groups are declared.\ndeclare one with: groups add <name> <member>[,<member>...]"
        lines = ["GROUPS"]
        for group in listed["groups"]:
            lines.append(f"  {group['text']}")
            if group.get("missing"):
                lines.append(f"      not found now: {', '.join(m['name'] for m in group['missing'])}")
            if group.get("problem"):
                lines.append(f"      problem: {group['problem']}")
        return "\n".join(lines)
    sub = args[0].lower()
    if sub == "add" and len(args) >= 3:
        members = []
        for raw in " ".join(args[2:]).split(","):
            kind, _, name = raw.strip().partition(":")
            if not name:
                kind, name = "", kind
            members.append({"kind": kind.strip().lower(), "name": name.strip()} if kind else name.strip())
        existed = runtime.ecosystem.groups.get(args[1]) is not None
        done, error = _call(tool, "add" if existed else "declare",
                            {"name": args[1], "members": [m for m in members if m], "declared_by": owner})
        if done is None:
            return f"FAILED: {error}"
        missing = done["group"].get("missing") or []
        note = f"\nnot found now (kept as declared): {', '.join(m['name'] for m in missing)}" if missing else ""
        return f"{'Group updated' if existed else 'Group set'}: {done['group']['text']}{note}"
    if sub == "remove" and len(args) >= 2:
        if len(args) == 2:
            done, error = _call(tool, "remove", {"name": args[1]})
            if done is None:
                return f"FAILED: {error}"
            return f"Removed the group '{args[1]}'." if done.get("removed") else f"no group called '{args[1]}'"
        names = [n.strip() for n in " ".join(args[2:]).split(",") if n.strip()]
        done, error = _call(tool, "remove_members", {"name": args[1], "names": names})
        if done is None or not done.get("group"):
            return f"FAILED: {error or 'no group called ' + repr(args[1])}"
        return f"Group updated: {done['group']['text']}"
    state, error = _call(tool, "state", {"name": " ".join(args)})
    if state is None:
        return f"FAILED: {error}"
    lines = [f"GROUP {state['group']['text']}"]
    for member in state["members"]:
        latest = ", ".join(f"{a} {r['value']}{(' ' + r['unit']) if r.get('unit') else ''}"
                           for a, r in member["latest"].items())
        lines.append(f"  {member['entity']}: {latest or 'nothing observed yet'}")
    for attribute, agg in state["aggregates"].items():
        lines.append(f"  across the group - {attribute}: mean {agg['mean']}, min {agg['min']} ({agg['lowest']}), "
                     f"max {agg['max']} ({agg['highest']})")
    return "\n".join(lines)


# -- GM-03 ---------------------------------------------------------------------

def _run_perceive(runtime: Any, rest: str, owner: str) -> str:
    text = rest.strip()
    about = ""
    match = re.search(r"\s+about\s+(.+)$", text)
    if match:
        about, text = match.group(1).strip(), text[: match.start()].strip()
    if not text:
        backend = getattr(runtime, "perception", None)
        name = getattr(backend, "name", "none")
        return (f"perception backend: {name} - {getattr(backend, 'describes', 'not configured')}\n"
                "usage: perceive <artifact-id|path> [about <what>]")
    if getattr(runtime, "perception", None) is None:
        return "FAILED: no perception backend is part of this runtime"
    from src.agents.tools.perception import PerceptionTool

    # The Control Center looks on the owner's behalf at what the Manager holds.
    tool = PerceptionTool(runtime.artifacts, runtime.perception, runtime.ecosystem, owner_agent=runtime.identity.name)
    target = {"artifact_id": text} if re.fullmatch(r"[0-9a-f]{8,}", text.lower()) else {"path": text}
    result, error = _call(tool, "analyze", {**target, "about": about})
    if result is None:
        return f"FAILED: {error}"
    picture = f"\npicture: artifact {result['picture_artifact_id']}" if result.get("picture_artifact_id") else ""
    return f"Seen: {result['text']}\nkept: {result['observations_recorded']} observation(s){picture}"


# -- GM-13 ---------------------------------------------------------------------

def _run_sensitivity(runtime: Any, rest: str, owner: str) -> str:
    policy = runtime.ecosystem.sensitivity
    args = rest.split()
    try:
        if not args:
            lines = ["SENSITIVITY"]
            rules = policy.rules()
            lines += [f"  {r.rule_id}: {r.describe()}" for r in rules] or ["  no rules - only what a person marks in the moment"]
            retention = policy.retention()
            lines.append("  retention: " + (", ".join(f"{k} {v} day(s)" for k, v in retention.items()) or "none (kept until forgotten)"))
            lines.append(f"  a cloud reasoning model may receive up to: {policy.cloud_ceiling()}")
            return "\n".join(lines)
        sub = args[0].lower()
        if sub in ("word", "source", "kind"):
            if len(args) < 3:
                return f"usage: sensitivity {sub} <match> <personal|sensitive|restricted>"
            rule = policy.declare(sub, " ".join(args[1:-1]), args[-1], declared_by=owner)
            return f"Rule set: {rule.rule_id} - {rule.describe()}."
        if sub == "remove" and len(args) >= 2:
            target = " ".join(args[1:])
            return f"Removed the rule '{target}'." if policy.remove(target) else f"no rule matches '{target}'"
        if sub == "retention" and len(args) >= 3:
            days = None if args[2].lower() in ("off", "none", "0") else int(args[2])
            policy.set_retention(args[1], days)
            return f"Retention set: {args[1]} " + (f"kept {days} day(s)." if days else "kept until forgotten.")
        if sub == "cloud" and len(args) >= 2:
            policy.set_cloud_ceiling(args[1])
            return f"Cloud allowance set: a vendor's reasoning model may receive up to {policy.cloud_ceiling()}."
    except (ValueError, TypeError) as exc:
        return f"FAILED: {exc}"
    return "usage: sensitivity | sensitivity word|source|kind <match> <level> | remove <match> | retention <level> <days|off> | cloud <level>"


# -- GM-04 ---------------------------------------------------------------------

def _run_listen(runtime: Any, rest: str, owner: str) -> str:
    args = rest.split()
    if len(args) < 2:
        return "usage: listen <connection> <operation> [for <30s>] [as <entity>]"
    content: dict = {"connection": args[0], "operation": args[1], "seconds": 10}
    words = args[2:]
    index = 0
    while index < len(words):
        word = words[index].lower()
        if word == "for" and index + 1 < len(words):
            duration = parse_duration(words[index + 1])
            if duration is None:
                return f"'{words[index + 1]}' is not a duration - try 10s, 1m"
            content["seconds"] = duration.total_seconds()
            index += 2
        elif word == "as" and index + 1 < len(words):
            content["entity"] = " ".join(words[index + 1:])
            break
        else:
            return f"'{words[index]}' is not understood here - use for or as"
    heard, error = _call(_observation_tool(runtime, owner), "listen", content)
    if heard is None:
        return f"FAILED: {error}"
    kinds = ", ".join(f"{count} {kind}" for kind, count in heard["content_types"].items()) or "nothing"
    return (f"Listened to {args[0]}/{args[1]}: {heard['items']} item(s) ({kinds}), "
            f"{heard['recorded']} observation(s) kept as '{heard['entity']}', {heard['frames']} frame(s); "
            f"ended: {heard['ended_because']}")


# -- GM-02 ---------------------------------------------------------------------

def _automation_tool(runtime: Any, owner: str):
    from src.agents.tools.automation import AutomationTool

    return AutomationTool(runtime.ecosystem, runtime.connections, owner_agent=owner)


def _run_conditions(runtime: Any, rest: str, owner: str) -> str:
    tool = _automation_tool(runtime, owner)
    args = rest.split()
    if not args:
        listed, error = _call(tool, "conditions")
        if listed is None:
            return f"FAILED: {error}"
        if not listed["conditions"]:
            return "no conditions are declared.\ndeclare one with: conditions add <entity> <attribute> <test> [<threshold>]"
        lines = ["CONDITIONS"]
        for condition in listed["conditions"]:
            state = condition.get("state") or {}
            holds = ("holds now" if state.get("truth") else "not met") if "truth" in state else "not yet checked"
            lines.append(f"  {condition['condition_id']}: {condition['text']}"
                         f" [{'paused' if not condition['enabled'] else holds}"
                         f"{', last value ' + str(state.get('value')) if 'value' in state else ''}]")
        return "\n".join(lines)
    sub = args[0].lower()
    if sub in ("remove", "pause", "resume"):
        if len(args) < 2:
            return f"usage: conditions {sub} <condition-id>"
        outcome, error = _call(tool, sub, {"condition_id": args[1]})
        if outcome is None:
            return f"FAILED: {error}"
        if sub == "remove":
            return f"Removed the condition '{args[1]}'." if outcome.get("removed") else f"no condition called '{args[1]}'"
        if not outcome.get("condition"):
            return f"no condition called '{args[1]}'"
        return f"{'Paused' if sub == 'pause' else 'Resumed'} the condition '{outcome['condition']['condition_id']}'."
    if sub == "check":
        outcome, error = _call(tool, "evaluate", {"condition_id": args[1]} if len(args) > 1 else {})
        if outcome is None:
            return f"FAILED: {error}"
        if not outcome["fired"]:
            return "checked - nothing newly true"
        return "\n".join(f"  fired: {e['label']} - {e['detail']}" for e in outcome["fired"])
    if sub != "add" or len(args) < 4:
        return ("usage: conditions add <entity> <attribute> <test> [<threshold>] [over <10m> <aggregate>] "
                "[then <connection> <operation> [k=v,...]]")
    entity, attribute, operator = args[1], args[2], args[3]
    words = args[4:]
    content: dict = {"entity": entity, "attribute": attribute, "operator": operator, "declared_by": owner}
    if words and words[0].lower() not in ("over", "then"):
        content["threshold"] = words.pop(0)
    while words:
        word = words.pop(0).lower()
        if word == "over" and len(words) >= 2:
            duration = parse_duration(words.pop(0))
            if duration is None:
                return "'over' needs a duration, e.g. over 10m mean"
            content["window_seconds"] = duration.total_seconds()
            content["aggregate"] = words.pop(0).lower()
        elif word == "then" and len(words) >= 2:
            action = {"connection": words.pop(0), "operation": words.pop(0), "params": {}}
            if words:
                for pair in words.pop(0).split(","):
                    key, _, value = pair.partition("=")
                    if key and value:
                        action["params"][key] = value
            content["action"] = action
        else:
            return f"'{word}' is not understood here - use over or then"
    if runtime.ecosystem.observations.resolve_entity(entity):
        content["entity"] = runtime.ecosystem.observations.resolve_entity(entity)
    declared, error = _call(tool, "declare", content)
    if declared is None:
        return f"FAILED: {error}"
    fired = f"\nalready true: {declared['fired']['detail']}" if declared.get("fired") else ""
    return f"Condition set: {declared['condition']['condition_id']}: {declared['text']}{fired}"


def _run_events(runtime: Any, rest: str, owner: str) -> str:
    tool = _automation_tool(runtime, owner)
    args = rest.split()
    if not args:
        listed, error = _call(tool, "events", {"limit": 30})
        if listed is None:
            return f"FAILED: {error}"
        if not listed["events"]:
            return "no events yet"
        return "EVENTS\n" + "\n".join(f"  {e['event_id']} {e['text']}" for e in listed["events"])
    sub = args[0].lower()
    if sub in ("approve", "dismiss") and len(args) >= 2:
        outcome, error = _call(tool, sub, {"event_id": args[1]})
        if outcome is None:
            return f"FAILED: {error}"
        if sub == "dismiss":
            return f"Dismissed event '{args[1]}'."
        if outcome.get("executed"):
            return f"Executed: {outcome.get('detail') or 'done'}"
        return f"FAILED: the action did not succeed - {outcome.get('detail')}"
    return "usage: events | events approve <event-id> | events dismiss <event-id>"


# -- GM-01 ---------------------------------------------------------------------

def _run_observe(runtime: Any, rest: str, owner: str) -> str:
    tool = _observation_tool(runtime, owner)
    args = rest.split()
    if not args:
        listed, error = _call(tool, "watches")
        if listed is None:
            return f"FAILED: {error}"
        if not listed["watches"]:
            return ("nothing is watched yet.\n"
                    "watch a safe read with: observe <connection> <operation> [every 5m] [as <entity>]")
        lines = ["WATCHES"]
        for watch in listed["watches"]:
            state = watch.get("state") or {}
            last = state.get("last_sampled_at", "never")[:19].replace("T", " ")
            result = "ok" if state.get("last_ok") else ("failed" if state else "not yet")
            lines.append(f"  {watch['text']}")
            lines.append(f"      last: {last} ({result}) - {state.get('recorded', 0)} observation(s) kept"
                         + (f" - {state.get('last_detail')}" if state and not state.get('last_ok') else ""))
        return "\n".join(lines)
    sub = args[0].lower()
    if sub in ("remove", "pause", "resume"):
        if len(args) < 2:
            return f"usage: observe {sub} <watch-id>"
        registry = runtime.ecosystem.watches
        if sub == "remove":
            return (f"Removed the watch '{args[1]}'. What it observed is kept."
                    if registry.remove(args[1]) else f"no watch called '{args[1]}'")
        updated = registry.set_enabled(args[1], sub == "resume")
        if updated is None:
            return f"no watch called '{args[1]}'"
        return f"{'Resumed' if sub == 'resume' else 'Paused'} the watch '{updated.watch_id}'."
    if sub == "sample":
        sampled, error = _call(tool, "sample", {"watch_id": args[1]} if len(args) > 1 else {})
        if sampled is None:
            return f"FAILED: {error}"
        if not sampled["outcomes"]:
            return "nothing to sample - no watch is active"
        return "\n".join(
            f"  {o['watch_id']}: {'ok' if o['ok'] else 'FAILED'} - {o['detail']}" for o in sampled["outcomes"]
        )
    if len(args) < 2:
        return "usage: observe <connection> <operation> [every <30s|5m|1h>] [as <entity>] [keep <path>,...] [with k=v,...]"
    connection, operation = args[0], args[1]
    content: dict = {"connection": connection, "operation": operation, "declared_by": owner}
    words = args[2:]
    index = 0
    while index < len(words):
        word = words[index].lower()
        value = words[index + 1] if index + 1 < len(words) else ""
        if word == "every" and value:
            duration = parse_duration(value)
            if duration is None:
                return f"'{value}' is not a duration - try 30s, 5m, 1h"
            content["interval_seconds"] = int(duration.total_seconds())
        elif word == "as" and value:
            entity_words = []
            index += 1
            while index < len(words) and words[index].lower() not in ("every", "keep", "with", "frames", "listen"):
                entity_words.append(words[index])
                index += 1
            content["entity"] = " ".join(entity_words)
            continue
        elif word == "keep" and value:
            content["keep"] = [p for p in value.split(",") if p]
        elif word == "with" and value:
            params = {}
            for pair in value.split(","):
                key, _, raw = pair.partition("=")
                if key and raw:
                    params[key] = raw
            content["params"] = params
        elif word == "frames" and value:
            content["frames_to"] = value.lower()
        elif word == "listen" and value:
            duration = parse_duration(value)
            if duration is None:
                return f"'{value}' is not a duration - try 10s, 1m"
            content["listen_seconds"] = int(duration.total_seconds())
        else:
            return f"'{words[index]}' is not understood here - use every, as, keep, with, frames or listen"
        index += 2
    declared, error = _call(tool, "watch", content)
    if declared is None:
        return f"FAILED: {error}"
    sampled, _ = _call(tool, "sample", {"watch_id": declared["watch"]["watch_id"]})
    first = ""
    if sampled and sampled.get("outcomes"):
        outcome = sampled["outcomes"][0]
        first = f"\nfirst read: {'ok' if outcome['ok'] else 'FAILED'} - {outcome['detail']}"
    return f"Watching: {declared['text']}{' (replaced the previous declaration)' if declared.get('replaced') else ''}.{first}"


def _run_observations(runtime: Any, rest: str) -> str:
    tool = _observation_tool(runtime, "operator")
    args = rest.split()
    if not args:
        entities, error = _call(tool, "entities")
        if entities is None:
            return f"FAILED: {error}"
        if not entities["entities"]:
            return "nothing has been observed yet"
        lines = ["OBSERVED ENTITIES"]
        for entity in entities["entities"]:
            flag = f" [{entity['sensitivity']}]" if entity.get("sensitivity") else ""
            lines.append(f"  {entity['name']}{flag} - last {str(entity.get('last') or '?')[:19].replace('T', ' ')} UTC")
            for attribute in entity["attributes"]:
                lines.append(f"      {attribute} ({entity['counts'].get(attribute, 0)})")
        return "\n".join(lines)
    text = " ".join(args)
    window = None
    match = re.search(r"\blast\s+(\S+)\s*$", text)
    if match:
        window = parse_duration(match.group(1))
        if window is None:
            return f"'{match.group(1)}' is not a duration - try 30m, 2h, 7d"
        text = text[: match.start()].strip()
    entities, error = _call(tool, "entities")
    if entities is None:
        return f"FAILED: {error}"
    names = sorted((e["name"] for e in entities["entities"]), key=len, reverse=True)
    entity = next((n for n in names if text.lower().startswith(n.lower())), None)
    if entity is None:
        return f"'{text}' has not been observed. Observed: {', '.join(sorted(names)) or 'nothing yet'}"
    attribute = text[len(entity):].strip()
    if not attribute:
        latest, error = _call(tool, "latest", {"entity": entity})
        if latest is None:
            return f"FAILED: {error}"
        lines = [f"{entity} - latest"]
        for name, row in latest["latest"].items():
            lines.append(f"  {name}: {row['value']}{(' ' + row['unit']) if row.get('unit') else ''} at {row['at'][:19].replace('T', ' ')}")
        return "\n".join(lines)
    content = {"entity": entity, "attribute": attribute}
    if window is not None:
        content["window_seconds"] = window.total_seconds()
    summary, error = _call(tool, "summary", content)
    return summary["text"] if summary is not None else f"FAILED: {error}"
