"""Conversation's reach into the ecosystem's general mechanisms (GM campaign).

Each capability here is a ``ManagerCapability`` like every other - declared
words, a ``claims`` predicate, a handler that reaches its work only through a
granted tool - registered into ``MANAGER_CAPABILITIES`` by
``src/manager/capabilities.py``. They live in their own module because they
share one thing the older capabilities do not need: knowledge of what the
ecosystem currently holds.

**What the ecosystem holds is state, and a claim sees only words.** A request
is observation history only if it names something that has actually been
observed; a request declares a watch only if it names a connection this system
really has. So the composition root binds the live vocabulary here
(``bind_ecosystem``) - the same established pattern
``bind_image_technique_values`` uses for image techniques - and a claim asks it.
Nothing is named in this module: every entity, attribute and connection a
claim recognises comes from the bound state, so the same code recognises a
kiln, a beehive, an aircraft stand or something nobody has thought of yet.

**Half Brain throughout.** Every handler here is deterministic; none needs a
model, and none pretends to one.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable, Iterable, Sequence

from src.core.contracts import Task, TaskResult, tokenize

# -- the bound vocabulary ------------------------------------------------------

_BOUND: "dict[str, Any]" = {"ecosystem": None, "connections": None}
_BOUND_LOCK = threading.Lock()


def bind_ecosystem(ecosystem: Any, connections: Any = None) -> None:
    """Called by the composition root with the live ``EcosystemState`` (and
    the connection resolver) - and again after a live rebind."""
    with _BOUND_LOCK:
        _BOUND["ecosystem"] = ecosystem
        if connections is not None:
            _BOUND["connections"] = connections


def bound_ecosystem() -> Any:
    return _BOUND["ecosystem"]


def bound_connections() -> Any:
    return _BOUND["connections"]


# -- words ---------------------------------------------------------------------

_STOP = frozenset(
    "a an the of to for in on at by and or is are was were be been it its this that these those my me "
    "what how has have had did do does from with over during since last past previous every each any "
    "all some please can could would you your i we our us tell show give let".split()
)


def _words(text: str) -> "list[str]":
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def names_in(text: str, name: str) -> bool:
    """Whether ``text`` names ``name`` as a whole run of up to five words -
    "proofing cabinet", "proofing-cabinet" and "proofing_cabinet" alike, and
    never a short name inside an unrelated word."""
    words = _words(text)
    target = _compact(name)
    if len(target) < 3:
        return False
    for start in range(len(words)):
        joined = ""
        for end in range(start, min(len(words), start + 5)):
            joined += words[end]
            if joined == target:
                return True
            if len(joined) >= len(target):
                break
    return False


def mentioned(text: str, names: Iterable[str]) -> "list[str]":
    """Every name in ``names`` that ``text`` names, longest first - so
    "hive sensor b" wins over "hive sensor"."""
    found = [name for name in names if names_in(text, name)]
    return sorted(found, key=lambda n: -len(_compact(n)))


def attribute_words(attribute: str) -> "list[str]":
    """The words an attribute path is spoken as: "chamber.temp_c" -> chamber,
    temp; the last segment first."""
    segments = [s for s in attribute.split(".") if s]
    spoken: list[str] = []
    for segment in reversed(segments):
        for word in _words(segment.replace("_", " ")):
            if len(word) >= 3 and word not in _STOP and word not in spoken:
                spoken.append(word)
    return spoken


def _same_word(attribute_word: str, spoken: "set[str]") -> bool:
    """An attribute's word said in full or abbreviated either way ("temp" and
    "temperature", "pct" and "pct"), never below three letters."""
    if attribute_word in spoken:
        return True
    return any(
        len(attribute_word) >= 3 and len(word) >= 3
        and (word.startswith(attribute_word) or attribute_word.startswith(word))
        for word in spoken
    )


def attributes_mentioned(text: str, attributes: Sequence[str], *, entity: str = "") -> "list[str]":
    """The attributes ``text`` asks about: a whole path named, or its last
    segment's own words (full or abbreviated); best (most specific) first.
    Words that are only the entity's own name are not evidence for an
    attribute - "proofing cabinet temperature" asks about temperature."""
    entity_words = set(_words(entity))
    words = {w for w in _words(text) if w not in entity_words and w not in _STOP and len(w) >= 3}
    scored: list[tuple[int, str]] = []
    for attribute in attributes:
        if names_in(text, attribute) or names_in(text, attribute.replace(".", " ")):
            scored.append((100, attribute))
            continue
        last = [w for w in _words(attribute.split(".")[-1].replace("_", " "))
                if len(w) >= 3 and w not in _STOP and w not in entity_words]
        spoken = [w for w in attribute_words(attribute) if w not in entity_words]
        if last and all(_same_word(w, words) for w in last):
            scored.append((10 + len(last), attribute))
        elif spoken and any(_same_word(w, words) for w in spoken):
            scored.append((sum(1 for w in spoken if _same_word(w, words)), attribute))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [attribute for _score, attribute in scored]


def _meaningful(text: str) -> "set[str]":
    return {w for w in tokenize(text) if len(w) >= 3 and w not in _STOP}


def pick_operation(text: str, connection: Any, candidates: Sequence[str]) -> "tuple[str | None, tuple[str, ...]]":
    """Which of ``candidates`` the words mean, from each operation's own
    declared name and description - one strictly best, or the tie."""
    if len(candidates) == 1:
        return candidates[0], ()
    wanted = _meaningful(text)
    scores = []
    for operation in candidates:
        vocabulary = _meaningful(operation.replace("_", " ")) | _meaningful(
            connection.operation_description_for(operation) if connection is not None else ""
        )
        scores.append((len(wanted & vocabulary), operation))
    scores.sort(key=lambda pair: (-pair[0], pair[1]))
    if not scores or scores[0][0] == 0:
        return None, tuple(candidates)
    tied = tuple(op for score, op in scores if score == scores[0][0])
    return (tied[0], ()) if len(tied) == 1 else (None, tied)


def parse_every(text: str) -> "int | None":
    """"every 5 minutes", "every thirty seconds", "hourly" -> seconds."""
    lowered = str(text or "").lower()
    numbers = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10, "fifteen": 15,
               "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "sixty": 60, "ninety": 90}
    match = re.search(
        r"\bevery\s+(?:(\d+(?:\.\d+)?|a|an|one|two|three|five|ten|fifteen|twenty|thirty|forty|forty-five|sixty|ninety)\s+)?"
        r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", lowered,
    )
    if match:
        amount = float(numbers.get(match.group(1), match.group(1))) if match.group(1) else 1.0
        unit = match.group(2)
        factor = 1 if unit.startswith("sec") else 60 if unit.startswith("min") else 3600 if unit.startswith(("hour", "hr")) else 86400
        return int(amount * factor)
    for word, seconds in (("hourly", 3600), ("daily", 86400), ("every minute", 60), ("every second", 5)):
        if word in lowered:
            return seconds
    return None


def _tool_json(request: Any, tool: str, operation: str, content: dict) -> "tuple[dict | None, str]":
    """Invoke a granted tool; (parsed output, "") or (None, reason)."""
    if not request.tools.permits(tool):
        return None, f"the '{tool}' capability is not installed for me right now"
    outcome = request.tools.invoke(tool, {"operation": operation, "content": json.dumps(content)})
    if not outcome.ok:
        return None, outcome.error or f"{tool} did not succeed"
    try:
        return json.loads(outcome.output), ""
    except ValueError:
        return None, f"{tool} answered with something that is not JSON"


# =============================================================================
# GM-01 - observation history
# =============================================================================

OBSERVATION_TOOL = "observation"

_HISTORY_SHAPE = re.compile(
    r"\b(?:history|over the (?:last|past)|in the (?:last|past)|(?:last|past|previous)\s+(?:\d+|few|several|an?|one|two|three|four|five|six|twelve|twenty-four)?\s*"
    r"(?:seconds?|minutes?|mins?|hours?|days?|weeks?|months?)|since|so far|today|this week|average|mean|"
    r"max(?:imum)?|min(?:imum)?|highest|lowest|peak|trend|readings?|observations?|observed|recorded|"
    r"has (?:it |that )?(?:been|changed|risen|fallen|gone)|have been|how many times|how often|range)\b",
    re.IGNORECASE,
)


def _observed_names() -> "list[str]":
    ecosystem = bound_ecosystem()
    if ecosystem is None:
        return []
    try:
        return [entry["name"] for entry in ecosystem.observations.entities()]
    except Exception:  # noqa: BLE001 - a claim never fails the request
        return []


def observation_history_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """A question about what an observed entity *was* - its history, a
    window, an aggregate - naming an entity that has really been observed."""
    text = task.description
    if not _HISTORY_SHAPE.search(text):
        return False
    # GM-09: "the peaks in X over the last day" asks what can be *found* in
    # the history - series_analysis's own shape, whose name sorts later.
    if series_analysis_intent(task, participants):
        return False
    return bool(mentioned(text, _observed_names()))


def observation_history(request: Any) -> TaskResult:
    from src.services.observations.store import parse_window

    text = request.task.description
    entities, reason = _tool_json(request, OBSERVATION_TOOL, "entities", {})
    if entities is None:
        return TaskResult.failure(request.task, request.own_name, f"I can't look at what has been observed: {reason}.")
    by_name = {e["name"]: e for e in entities["entities"]}
    named = mentioned(text, by_name)
    if not named:
        known = ", ".join(sorted(by_name)) or "nothing yet"
        return TaskResult.failure(
            request.task, request.own_name,
            f"I don't have observations of anything by that name. What I have observed: {known}.",
        )
    entity = by_name[named[0]]
    window = parse_window(text)
    attributes = attributes_mentioned(text, entity["attributes"], entity=entity["name"])
    if not attributes:
        numeric_like = [a for a in entity["attributes"]]
        if len(numeric_like) == 1:
            attributes = numeric_like
    if not attributes:
        latest, reason = _tool_json(request, OBSERVATION_TOOL, "latest", {"entity": entity["name"]})
        if latest is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't read it: {reason}.")
        lines = [f"{entity['name']} - what I have observed (latest of each):"]
        for attribute, row in latest["latest"].items():
            unit = f" {row['unit']}" if row.get("unit") else ""
            lines.append(f"  {attribute}: {row['value']}{unit} (at {row['at'][:16].replace('T', ' ')} UTC)")
        lines.append("Name one of these to see its history over a window.")
        return TaskResult.success(
            request.task, request.own_name, "\n".join(lines),
            payload={"handled_by": "observation_history", "entity": entity["name"], "attributes": list(latest["latest"])},
        )
    attribute = attributes[0]
    content = {"entity": entity["name"], "attribute": attribute}
    if window is not None:
        content["window_seconds"] = window.total_seconds()
    summary, reason = _tool_json(request, OBSERVATION_TOOL, "summary", content)
    if summary is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't summarise it: {reason}.")
    return TaskResult.success(
        request.task, request.own_name, summary["text"],
        payload={"handled_by": "observation_history", "entity": entity["name"], "attribute": attribute,
                 "summary": summary["summary"]},
    )


# -- declaring what to watch ---------------------------------------------------

_WATCH_START = re.compile(
    r"\b(?:keep an eye on|keep watching|keep track of|start (?:watching|monitoring|tracking|observing|recording)|"
    r"(?:watch|monitor|observe|sample|log)\b(?!\s+(?:out|this video|the video|this film)))",
    re.IGNORECASE,
)
_WATCH_STOP = re.compile(r"\b(?:stop|cancel|end|quit)\s+(?:watching|monitoring|tracking|observing|sampling|logging|listening)\b", re.IGNORECASE)
_LISTEN = re.compile(r"\blisten\s+(?:to|in on|in to)\b", re.IGNORECASE)
# GM-03: a watch whose pictures should be looked at, not just kept - "count
# what's in view", "see how many are there", "track the movement".
_SEE_WHAT_IS_THERE = re.compile(
    r"\b(?:count(?:ing)?|how\s+many|in\s+view|in\s+frame|what(?:'s|\s+is)\s+there|who(?:'s|\s+is)\s+there|"
    r"track(?:ing)?\s+(?:the\s+)?(?:movement|motion|things|objects)|motion|movement|moving\s+things)\b",
    re.IGNORECASE,
)


def _connection_names() -> "list[str]":
    connections = bound_connections()
    if connections is None:
        return []
    try:
        return [name for name in connections.names if connections.get(name) is not None
                and (connections.get(name).supported_operations or name != connections.default_name)]
    except Exception:  # noqa: BLE001
        return []


def _watch_names() -> "list[str]":
    ecosystem = bound_ecosystem()
    if ecosystem is None:
        return []
    try:
        names = set()
        for watch in ecosystem.watches.all():
            names.update({watch.entity, watch.connection, watch.watch_id})
        return sorted(names)
    except Exception:  # noqa: BLE001
        return []


def observation_watch_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Starting (or stopping) a watch on something this system can really
    read - a connection it has, or something it already watches."""
    text = task.description
    if _WATCH_STOP.search(text):
        return bool(mentioned(text, _watch_names()) or mentioned(text, _connection_names()))
    if not (_WATCH_START.search(text) or _LISTEN.search(text)):
        return False
    return bool(mentioned(text, _connection_names()))


def observation_watch(request: Any) -> TaskResult:
    text = request.task.description
    if _WATCH_STOP.search(text):
        listed, reason = _tool_json(request, OBSERVATION_TOOL, "watches", {})
        if listed is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't reach the watches: {reason}.")
        targets = [w for w in listed["watches"] if names_in(text, w["entity"]) or names_in(text, w["connection"]) or names_in(text, w["watch_id"])]
        if not targets:
            return TaskResult.failure(request.task, request.own_name, "I'm not watching anything by that name.")
        removed = []
        for watch in targets:
            outcome, _ = _tool_json(request, OBSERVATION_TOOL, "unwatch", {"watch_id": watch["watch_id"]})
            if outcome and outcome.get("removed"):
                removed.append(watch["text"])
        if not removed:
            return TaskResult.failure(request.task, request.own_name, "Nothing was removed.")
        return TaskResult.success(
            request.task, request.own_name,
            "Stopped: " + "; ".join(removed) + ". What was already observed is kept.",
            payload={"handled_by": "observation_watch", "removed": removed},
        )

    connections = request.connections
    named = mentioned(text, _connection_names())
    connection = connections.get(named[0]) if connections is not None and named else None
    if connection is None:
        return TaskResult.failure(request.task, request.own_name, "I don't have a connection by that name to watch.")
    if _LISTEN.search(text) and not _WATCH_START.search(text):
        return _listen_now(request, text, connection)
    readable = [op for op in connection.supported_operations if not connection.requires_confirmation_for(op)]
    if not readable:
        return TaskResult.failure(
            request.task, request.own_name,
            f"'{connection.name}' has nothing I can safely read on a schedule - every operation it offers changes something.",
        )
    operation, tied = pick_operation(text, connection, readable)
    if operation is None:
        options = ", ".join(tied or readable)
        return TaskResult.failure(request.task, request.own_name, f"Which reading of '{connection.name}' should I watch? It offers: {options}.")
    interval = parse_every(text) or 60
    # "... as <name>" ends where the next clause begins ("every ...", "and
    # count ...", "so ...") or at punctuation - never swallowing the rest.
    entity_match = re.search(
        r"\bas\s+['\"]?([a-z0-9][a-z0-9 _-]{1,40}?)['\"]?(?:\s+(?:every|and|so|then|to|for|while)\b|[,.;!?]|$)",
        text, re.IGNORECASE,
    )
    entity = entity_match.group(1).strip() if entity_match else connection.name
    content = {
        "connection": connection.name, "operation": operation, "entity": entity,
        "interval_seconds": interval, "declared_by": request.own_name,
    }
    if _SEE_WHAT_IS_THERE.search(text):
        # GM-03: "... and count what's in view" - whatever pictures this read
        # delivers go to the perception axis, whose counts become observations.
        content["frames_to"] = "perception"
    declared, reason = _tool_json(request, OBSERVATION_TOOL, "watch", content)
    if declared is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't start watching it: {reason}.")
    sampled, _ = _tool_json(request, OBSERVATION_TOOL, "sample", {"watch_id": declared["watch"]["watch_id"]})
    first = ""
    if sampled and sampled.get("outcomes"):
        outcome = sampled["outcomes"][0]
        first = (f" First reading taken: {outcome['detail']}." if outcome["ok"]
                 else f" The first reading did not succeed ({outcome['detail']}) - I'll keep trying on schedule.")
    return TaskResult.success(
        request.task, request.own_name,
        f"Watching: {declared['text']}.{first} Ask me about its history any time.",
        payload={"handled_by": "observation_watch", "watch": declared["watch"]},
    )


def _listen_now(request: Any, text: str, connection: Any) -> TaskResult:
    """GM-04: "listen to <connection> for 30 seconds" - a bounded listen to a
    stream it declares, now."""
    streams = [op for op in connection.supported_operations if connection.supports_streaming(op)]
    if not streams:
        return TaskResult.failure(request.task, request.own_name,
                                  f"'{connection.name}' declares no stream to listen to - every operation it offers answers once.")
    operation, tied = pick_operation(text, connection, streams)
    if operation is None:
        return TaskResult.failure(request.task, request.own_name, f"Which stream? It offers: {', '.join(tied or streams)}.")
    seconds = 10.0
    match = re.search(r"\bfor\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?)\b", text, re.IGNORECASE)
    if match:
        seconds = float(match.group(1)) * (60 if match.group(2).lower().startswith("min") else 1)
    heard, reason = _tool_json(request, OBSERVATION_TOOL, "listen", {
        "connection": connection.name, "operation": operation, "seconds": min(seconds, 300),
    })
    if heard is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't listen to it: {reason}.")
    kinds = ", ".join(f"{count} {kind}" for kind, count in heard["content_types"].items()) or "nothing"
    return TaskResult.success(
        request.task, request.own_name,
        f"Listened to {connection.name}/{operation} for up to {seconds:g}s: {heard['items']} item(s) ({kinds}); "
        f"{heard['recorded']} observation(s) kept as '{heard['entity']}'"
        + (f", {heard['frames']} frame(s) received" if heard["frames"] else "") + f". Ended: {heard['ended_because']}.",
        payload={"handled_by": "observation_watch", "listened": heard},
    )


# =============================================================================
# GM-02 - condition -> event -> response
# =============================================================================

AUTOMATION_TOOL = "automation"

_TELL_ME = re.compile(
    r"\b(?:tell|alert|warn|notify|ping|message|remind|let)\s+me\s+(?:know\s+)?(?:when|if|whenever|as soon as|once|the moment)\b"
    r"|\b(?:alert|notify|warn)\s+(?:me\s+)?(?:when|if|whenever)\b",
    re.IGNORECASE,
)
_IF_THEN = re.compile(r"^\s*(?:if|when|whenever|once)\b(?P<condition>.+?),\s*(?:then\s+)?(?P<action>.+)$", re.IGNORECASE)
_CONDITION_MANAGE = re.compile(
    r"\b(?:what|which|list|show)\b.{0,30}\b(?:alerts?|conditions?|triggers?|automations?)\b.{0,20}\b(?:do i have|are (?:set|active|there)|have i set)\b"
    r"|\b(?:stop|cancel|remove|delete|drop)\s+(?:the\s+|my\s+)?(?:alerts?|conditions?|triggers?|warnings?)\b"
    r"|\b(?:stop|quit)\s+(?:alerting|warning|telling|notifying)\s+me\b",
    re.IGNORECASE,
)
_EVENTS_ASK = re.compile(
    r"\b(?:any|what|which|show|list)\b.{0,20}\b(?:alerts?|events?|triggered|fired|went off)\b"
    r"|\bhas anything (?:happened|fired|triggered|gone off)\b",
    re.IGNORECASE,
)


def _operator_in(text: str) -> "tuple[Any, int, int] | None":
    """The registered operator whose words appear in ``text`` - the longest
    phrase wins, so "falls below" is not read as "below" plus something."""
    from src.services.automation.conditions import operators

    lowered = text.lower()
    best = None
    for operator in operators():
        for word in operator.words:
            for match in re.finditer(r"\b" + re.escape(word) + r"\b", lowered):
                span = (match.start(), match.end())
                if best is None or (span[1] - span[0]) > (best[2] - best[1]):
                    best = (operator, span[0], span[1])
    return best


def _condition_subject_names() -> "list[str]":
    return sorted(set(_observed_names()) | set(_connection_names()) | set(_group_names()))


def _group_names() -> "list[str]":
    ecosystem = bound_ecosystem()
    groups = getattr(ecosystem, "groups", None) if ecosystem is not None else None
    if groups is None:
        return []
    try:
        return [g.name for g in groups.all()]
    except Exception:  # noqa: BLE001
        return []


def condition_watch_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """"Tell me when <something observed> <test> <threshold>", "if <...>,
    <do something>", or managing the conditions already declared."""
    text = task.description
    if _CONDITION_MANAGE.search(text):
        return True
    shaped = _TELL_ME.search(text) or _IF_THEN.match(text)
    if not shaped:
        return False
    return bool(mentioned(text, _condition_subject_names())) and _operator_in(text) is not None


def _parse_action(request: Any, clause: str) -> "tuple[dict | None, str]":
    """``clause`` ("turn off the heater", "set the vent to 40") as a proposed
    action on a named connection - or (None, why not)."""
    connections = request.connections
    names = mentioned(clause, _connection_names())
    if not names or connections is None:
        return None, "I couldn't tell which connection the action is on"
    connection = connections.get(names[0])
    return _operation_on(connection, clause)


def _operation_on(connection: Any, clause: str) -> "tuple[dict | None, str]":
    """Which of ``connection``'s own operations ``clause`` asks for, with the
    parameters it states - or (None, why not). Shared by a condition's action
    and a group's per-member step, so both read an action the same way."""
    operations = list(connection.supported_operations)
    words = re.sub(r"\b" + re.escape(connection.name.replace("_", " ")) + r"\b", " ", clause.lower().replace("_", " "))
    operation, tied = pick_operation(words, connection, operations)
    if operation is None:
        return None, f"which action on '{connection.name}'? It offers: {', '.join(tied or operations)}"
    params: dict = {}
    required = [p for p in connection.parameters_for(operation) if getattr(p, "required", False)]
    number = re.search(r"\bto\s+(-?\d+(?:\.\d+)?)\b", clause)
    for match in re.finditer(r"\b([a-z_][a-z0-9_]*)\s*=\s*([^\s,]+)", clause, re.IGNORECASE):
        params[match.group(1)] = match.group(2)
    if len(required) == 1 and required[0].name not in params and number:
        params[required[0].name] = number.group(1)
    missing = [p.name for p in required if p.name not in params]
    if missing:
        return None, f"'{operation}' on '{connection.name}' needs {', '.join(missing)} - say it (e.g. {missing[0]}=...)"
    return {"connection": connection.name, "operation": operation, "params": params}, ""


def condition_watch(request: Any) -> TaskResult:
    text = request.task.description
    if _CONDITION_MANAGE.search(text):
        return _manage_conditions(request, text)

    split = _IF_THEN.match(text)
    condition_text = split.group("condition") if split else text
    action_text = split.group("action") if split else ""
    if split and _TELL_ME.search(action_text):
        action_text = ""  # "if X, tell me" is a notification, not an action
    subjects = mentioned(condition_text, _condition_subject_names())
    if not subjects:
        return TaskResult.failure(request.task, request.own_name, "I couldn't tell what to watch for that.")
    subject = subjects[0]
    found = _operator_in(condition_text)
    if found is None:
        return TaskResult.failure(request.task, request.own_name, "I couldn't tell what test you mean (above, below, stops reporting, ...).")
    operator, start, end = found
    from src.services.automation.conditions import parse_threshold

    threshold = parse_threshold(operator, condition_text[end:])
    if operator.kind in ("number", "duration") and threshold is None:
        return TaskResult.failure(request.task, request.own_name, f"'{operator.name}' needs a {'number' if operator.kind == 'number' else 'length of time'} - I didn't find one.")
    if operator.kind == "text":
        threshold = " ".join(condition_text[end:].strip(" .?!,").split()[:4]) or None
        if threshold is None:
            return TaskResult.failure(request.task, request.own_name, "What value should I compare it with?")

    notes = []
    observed = _observed_names()
    entity = next((n for n in observed if n == subject), None)
    group_members = None
    if entity is None and subject in _group_names():
        entity = subject
        ecosystem = bound_ecosystem()
        group_members = ecosystem.group_entities(subject) if ecosystem is not None else []
    if entity is None:
        # A connection nobody watches yet: compose the watch first (GM-01),
        # so the condition has something to be tested against.
        interval = parse_every(text) or 60
        connection = request.connections.get(subject) if request.connections is not None else None
        readable = [op for op in (connection.supported_operations if connection else ())
                    if not connection.requires_confirmation_for(op)]
        if not readable:
            return TaskResult.failure(request.task, request.own_name, f"'{subject}' has nothing I can safely read to watch.")
        operation, tied = pick_operation(condition_text[:start], connection, readable)
        operation = operation or (readable[0] if len(readable) == 1 else None)
        if operation is None:
            return TaskResult.failure(request.task, request.own_name, f"Which reading of '{subject}'? It offers: {', '.join(tied or readable)}.")
        declared, reason = _tool_json(request, OBSERVATION_TOOL, "watch", {
            "connection": subject, "operation": operation, "entity": subject, "interval_seconds": interval,
            "declared_by": request.own_name,
        })
        if declared is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't start watching '{subject}': {reason}.")
        _tool_json(request, OBSERVATION_TOOL, "sample", {"watch_id": declared["watch"]["watch_id"]})
        notes.append(f"I'm now watching {declared['text']}.")
        entity = subject

    candidates_source = group_members or [entity]
    attributes: list[str] = []
    for member in candidates_source:
        latest, _ = _tool_json(request, OBSERVATION_TOOL, "latest", {"entity": member})
        for attribute in (latest or {}).get("latest", {}):
            if attribute not in attributes:
                attributes.append(attribute)
    chosen = attributes_mentioned(condition_text[:start] + " " + condition_text[:end], attributes, entity=entity)
    if not chosen:
        # "more than 3 things are in view": the reading named after the test.
        chosen = attributes_mentioned(condition_text, attributes, entity=entity)
    if not chosen:
        from src.services.observations.store import is_number

        if len(attributes) == 1:
            chosen = attributes
        elif operator.kind == "duration" and attributes:
            chosen = attributes[:1]
        else:
            listed = ", ".join(attributes) or "nothing yet"
            return TaskResult.failure(request.task, request.own_name, f"Which reading of '{entity}' should I test? It reports: {listed}.")
    attribute = chosen[0]

    window = None
    aggregate = "latest"
    lowered = condition_text.lower()
    from src.services.observations.store import parse_window

    if re.search(r"\b(?:average|mean)\b", lowered):
        aggregate = "mean"
        window = parse_window(lowered)
    elif re.search(r"\b(?:max|maximum|peak)\b", lowered):
        aggregate = "max"
        window = parse_window(lowered)

    action = None
    if action_text:
        action, why = _parse_action(request, action_text)
        if action is None:
            return TaskResult.failure(request.task, request.own_name, f"I understood the test but not the action: {why}.")
    quantifier = "all" if re.search(r"\b(?:all|every|each)\b", condition_text[:start].lower()) and group_members else "any"
    content = {
        "entity": entity, "attribute": attribute, "operator": operator.name, "threshold": threshold,
        "aggregate": aggregate, "window_seconds": window.total_seconds() if window else 0,
        "declared_by": request.own_name, "quantifier": quantifier,
    }
    if action:
        content["action"] = action
    declared, reason = _tool_json(request, AUTOMATION_TOOL, "declare", content)
    if declared is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't set that up: {reason}.")
    lines = [*notes, f"Condition set: {declared['text']}."]
    if action:
        lines.append("When it happens I'll propose the action and wait for your yes - I won't do it on my own.")
    else:
        lines.append("I'll tell you when it becomes true.")
    if declared.get("fired"):
        fired = declared["fired"]
        lines.append(f"It is already true now: {fired['detail']}.")
    return TaskResult.success(
        request.task, request.own_name, " ".join(lines),
        payload={"handled_by": "condition_watch", "condition": declared["condition"], "fired": declared.get("fired")},
    )


def _manage_conditions(request: Any, text: str) -> TaskResult:
    listed, reason = _tool_json(request, AUTOMATION_TOOL, "conditions", {})
    if listed is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't reach the conditions: {reason}.")
    conditions = listed["conditions"]
    if re.search(r"\b(?:stop|cancel|remove|delete|drop|quit)\b", text, re.IGNORECASE):
        targets = [c for c in conditions if names_in(text, c["entity"]) or names_in(text, c["condition_id"])]
        if not targets:
            return TaskResult.failure(request.task, request.own_name, "I don't have a condition about that.")
        removed = []
        for condition in targets:
            outcome, _ = _tool_json(request, AUTOMATION_TOOL, "remove", {"condition_id": condition["condition_id"]})
            if outcome and outcome.get("removed"):
                removed.append(condition["text"])
        return TaskResult.success(request.task, request.own_name, "Removed: " + "; ".join(removed) + ".",
                                  payload={"handled_by": "condition_watch", "removed": removed})
    if not conditions:
        return TaskResult.success(request.task, request.own_name, "No conditions are set.",
                                  payload={"handled_by": "condition_watch", "conditions": []})
    lines = ["Conditions I'm watching for:"]
    for condition in conditions:
        state = condition.get("state") or {}
        now = "true now" if state.get("truth") else "not met" if "truth" in state else "not yet checked"
        lines.append(f"  - {condition['text']} ({'paused' if not condition['enabled'] else now})")
    return TaskResult.success(request.task, request.own_name, "\n".join(lines),
                              payload={"handled_by": "condition_watch", "conditions": [c["condition_id"] for c in conditions]})


def ecosystem_events_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    ecosystem = bound_ecosystem()
    if ecosystem is None:
        return False
    # GM-06: "a dashboard with ... any alerts" asks for a view that shows them.
    if live_view_intent(task, participants):
        return False
    return bool(_EVENTS_ASK.search(task.description)) and bool(
        re.search(r"\b(?:alerts?|conditions?|triggered|fired|went off|events?)\b", task.description, re.IGNORECASE)
    )


def ecosystem_events(request: Any) -> TaskResult:
    events, reason = _tool_json(request, AUTOMATION_TOOL, "events", {"limit": 10})
    if events is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't read the events: {reason}.")
    if not events["events"]:
        return TaskResult.success(request.task, request.own_name, "Nothing has fired.",
                                  payload={"handled_by": "ecosystem_events", "events": []})
    lines = ["Recent events:"] + [f"  - {e['text']}" for e in events["events"]]
    pending = [e for e in events["events"] if e["status"] in ("proposed",)]
    if pending:
        lines.append("A proposed action waits for your approval ('events approve <id>' in the Control Center).")
    return TaskResult.success(request.task, request.own_name, "\n".join(lines),
                              payload={"handled_by": "ecosystem_events", "events": [e["event_id"] for e in events["events"]]})


# =============================================================================
# GM-13 - sensitivity rules
# =============================================================================

SENSITIVITY_TOOL = "sensitivity"
_LEVEL_WORD = r"(?P<level>sensitive|private|personal|restricted|confidential|secret)"
_TREAT_AS = re.compile(
    r"\b(?:treat|mark|consider|handle|regard|keep)\s+(?:anything|everything|all|any\s+\w+|readings?|data|information|records?|notes?)?\s*"
    r"(?:(?:about|mentioning|to do with|involving|regarding|on|concerning)\s+(?P<word>.+?)|(?:from|reported by|coming from)\s+(?P<source>.+?))"
    r"\s+as\s+" + _LEVEL_WORD + r"\b",
    re.IGNORECASE,
)
_STOP_TREATING = re.compile(
    r"\b(?:stop|no longer)\s+(?:treating|marking|considering)\s+(?P<match>.+?)\s+as\s+" + _LEVEL_WORD, re.IGNORECASE)
_WHAT_SENSITIVE = re.compile(
    r"\bwhat\b.{0,25}\b(?:treat|mark|consider|keep)\b.{0,20}\b(?:sensitive|private|restricted|confidential)\b"
    r"|\bsensitivity rules\b", re.IGNORECASE)
_RETENTION = re.compile(
    r"\b(?:forget|delete|purge|drop)\s+(?:all\s+)?(?:my\s+)?(?P<level>sensitive|private|personal|restricted|confidential)\s+"
    r"(?:things|information|records|notes|data|stuff)\s+after\s+(?P<days>\d+)\s+days?\b",
    re.IGNORECASE,
)


def _level_from(word: str) -> str:
    word = word.lower()
    return {"private": "personal", "confidential": "sensitive", "secret": "restricted"}.get(word, word)


def sensitivity_rules_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = task.description
    return bool(_TREAT_AS.search(text) or _STOP_TREATING.search(text) or _WHAT_SENSITIVE.search(text)
                or _RETENTION.search(text))


def sensitivity_rules(request: Any) -> TaskResult:
    text = request.task.description
    match = _RETENTION.search(text)
    if match:
        level, days = _level_from(match.group("level")), int(match.group("days"))
        done, reason = _tool_json(request, SENSITIVITY_TOOL, "retention", {"level": level, "days": days})
        if done is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't set that: {reason}.")
        return TaskResult.success(
            request.task, request.own_name,
            f"From now on, anything {level} is forgotten after {days} day(s) - records and readings alike.",
            payload={"handled_by": "sensitivity_rules", "retention": done["retention"]},
        )
    match = _STOP_TREATING.search(text)
    if match:
        target = " ".join(match.group("match").strip(" '\"").split()).lower()
        target = re.sub(r"^(?:anything|everything|all)\s+(?:about|mentioning|from)\s+", "", target)
        done, reason = _tool_json(request, SENSITIVITY_TOOL, "remove", {"match": target})
        if done is None or not done.get("removed"):
            return TaskResult.failure(request.task, request.own_name, f"I don't have a rule about '{target}'.")
        return TaskResult.success(request.task, request.own_name, f"Done - '{target}' is no longer treated specially.",
                                  payload={"handled_by": "sensitivity_rules", "removed": target})
    match = _TREAT_AS.search(text)
    if match:
        level = _level_from(match.group("level"))
        kind, what = ("source", match.group("source")) if match.group("source") else ("word", match.group("word"))
        what = " ".join(what.strip(" '\".,").split())
        what = re.sub(r"^(?:the|my|our)\s+", "", what, flags=re.IGNORECASE)
        if kind == "source":
            names = mentioned(what, _connection_names())
            if names:
                what = names[0]
        done, reason = _tool_json(request, SENSITIVITY_TOOL, "declare",
                                  {"kind": kind, "match": what, "level": level, "declared_by": request.own_name})
        if done is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't record that rule: {reason}.")
        return TaskResult.success(
            request.task, request.own_name,
            f"Understood: {done['text']}. From now on I keep it with me, never pass it to another agent, and never "
            "send it to a cloud model unless you allow that in the Control Center.",
            payload={"handled_by": "sensitivity_rules", "rule": done["rule"]},
        )
    rules, reason = _tool_json(request, SENSITIVITY_TOOL, "rules", {})
    if rules is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't read the rules: {reason}.")
    if not rules["rules"]:
        lines = ["Nothing is marked sensitive by a rule yet - only what you tell me in the moment "
                 "(\"remember privately ...\", \"this is confidential ...\")."]
    else:
        lines = ["What I treat carefully:"] + [f"  - {r['text']}" for r in rules["rules"]]
    if rules["retention"]:
        lines.append("Kept for: " + ", ".join(f"{level} {days} day(s)" for level, days in rules["retention"].items()))
    lines.append(f"A cloud model may receive up to: {rules['cloud_ceiling']}.")
    return TaskResult.success(request.task, request.own_name, "\n".join(lines),
                              payload={"handled_by": "sensitivity_rules", "rules": len(rules["rules"])})


# =============================================================================
# GM-15 - groups across connections
# =============================================================================

GROUPS_TOOL = "groups"

_GROUP_WORD = re.compile(r"\bgroups?\b", re.IGNORECASE)
_GROUP_LIST = re.compile(
    r"\b(?:my|our)\s+groups\b|\bgroups\s+(?:do\s+(?:i|we)\s+have|are\s+(?:there|declared|set\s+up|defined))\b|"
    r"\blist\s+(?:the\s+|all\s+)?groups\b",
    re.IGNORECASE,
)
_GROUP_DECLARE = re.compile(
    r"\b(?:make|create|declare|define|start|set\s+up|form)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?group\b|"
    r"^\s*(?:please\s+)?group\s+(?!called\b|named\b)\w|\bin(?:to)?\s+a\s+(?:new\s+)?group\b|"
    r"\b(?:add|put|include)\b.+\b(?:to|into|in)\s+(?:the\s+)?(?:[\w' -]+?\s+group\b|group\s+\w)",
    re.IGNORECASE,
)
_GROUP_REMOVE_MEMBER = re.compile(r"\b(?:remove|take|drop)\b.+\b(?:from|out\s+of)\b", re.IGNORECASE)
_GROUP_DELETE = re.compile(r"\b(?:delete|dissolve|ungroup|remove|forget)\s+(?:the\s+)?(?:[\w' -]+?\s+)?group\b", re.IGNORECASE)
_GROUP_STATE = re.compile(
    r"\bhow\s+(?:is|are)\b|\bhow's\b|\bstatus\b|\bstate\b|\bdoing\b|\bwhat(?:'s|\s+is)\s+(?:happening|going\s+on)\b|"
    r"\baverage\b|\bmean\b|\bhighest\b|\blowest\b|\bwarmest\b|\bcoldest\b|\breadings?\b|\bwho(?:'s|\s+is)\s+in\b|"
    r"\bwhat(?:'s|\s+is)\s+in\b|\bmembers?\b",
    re.IGNORECASE,
)
_GROUP_ACT = re.compile(
    r"\b(?:turn|switch|set|open|close|lock|unlock|start|stop|pause|resume|dim|raise|lower|arm|disarm|run|shut|"
    r"power|reset|restart|mute|unmute)\b",
    re.IGNORECASE,
)
_EVERYTHING = re.compile(r"\b(?:everything|all|every|each|whole)\b", re.IGNORECASE)
_GROUP_NAME_PATTERNS = (
    re.compile(r"\bgroup\s+(?:called|named)\s+[\"']?(?P<n>[^\"',.;!?]+?)[\"']?"
               r"(?=\s+(?:with|containing|of|that|which|made)\b|[,.;!?]|$)", re.IGNORECASE),
    re.compile(r"\b(?:to|into|in|from|of|out\s+of)\s+the\s+(?P<n>[\w' -]+?)\s+group\b", re.IGNORECASE),
    re.compile(r"\b(?:to|into|in|from)\s+group\s+[\"']?(?P<n>[^\"',.;!?]+?)[\"']?\s*[.!?]?$", re.IGNORECASE),
    re.compile(r"\b(?:together\s+)?(?:as|into)\s+(?:the\s+|a\s+)?[\"']?(?P<n>[^\"',.;!?]+?)[\"']?(?:\s+group)?\s*[.!?]?$",
               re.IGNORECASE),
    re.compile(r"\b(?:delete|dissolve|ungroup|remove|forget)\s+(?:the\s+)?(?P<n>[\w' -]+?)\s+group\b", re.IGNORECASE),
)


def _group_name_in(text: str) -> "tuple[str | None, tuple[int, int] | None]":
    """The group a request names: an existing group mentioned by name, or a
    name the request gives one ("a group called ...", "the ... group", "as ...")."""
    existing = mentioned(text, _group_names())
    for pattern in _GROUP_NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            name = re.sub(r"^(?:the|a|an|my|our)\s+", "", " ".join(match.group("n").split()), flags=re.IGNORECASE)
            name = re.sub(r"\s+group$", "", name, flags=re.IGNORECASE)
            if name and name.lower() not in ("a", "the", "new", "group"):
                known = mentioned(name, _group_names())
                return (known[0] if known and _compact(known[0]) == _compact(name) else name), match.span("n")
    if existing:
        return existing[0], None
    return None, None


def _members_in(text: str) -> "list[str]":
    """Every connection, observed entity and group ``text`` names, longest
    first and each span once - so "hive sensor b" never also yields
    "hive sensor"."""
    names = sorted(set(_connection_names()) | set(_observed_names()) | set(_group_names()),
                   key=lambda n: -len(_compact(n)))
    remaining = " ".join(_words(text))
    found: list[str] = []
    for name in names:
        if names_in(remaining, name):
            found.append(name)
            spoken = " ".join(_words(name))
            remaining = remaining.replace(spoken, " ") if spoken in remaining else remaining
    return found


def entity_groups_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Declaring, reshaping, listing, reading or acting on a group of things
    across connections - by the word "group" in a declaring/managing shape,
    or by an existing group's name with a state or all-members action shape."""
    text = task.description
    named = mentioned(text, _group_names())
    if _GROUP_WORD.search(text):
        if _GROUP_LIST.search(text) or _GROUP_DECLARE.search(text) or _GROUP_DELETE.search(text):
            return True
        if named and (_GROUP_REMOVE_MEMBER.search(text) or _GROUP_STATE.search(text) or _GROUP_ACT.search(text)):
            return True
        return False
    return bool(named and (_GROUP_STATE.search(text) or (_GROUP_ACT.search(text) and _EVERYTHING.search(text))))


def _group_line(group: dict) -> str:
    missing = f" - not found now: {', '.join(m['name'] for m in group['missing'])}" if group.get("missing") else ""
    return f"{group['text']}{missing}"


def entity_groups(request: Any) -> TaskResult:
    text = request.task.description
    if _GROUP_LIST.search(text) and not mentioned(text, _group_names()):
        listed, reason = _tool_json(request, GROUPS_TOOL, "groups", {})
        if listed is None:
            return TaskResult.failure(request.task, request.own_name, f"I can't read the groups: {reason}.")
        if not listed["groups"]:
            return TaskResult.success(request.task, request.own_name,
                                      "There are no groups yet. Say e.g. \"group the <a> and the <b> as <name>\".",
                                      payload={"handled_by": "entity_groups", "groups": 0})
        return TaskResult.success(request.task, request.own_name,
                                  "Your groups:\n" + "\n".join(f"  - {_group_line(g)}" for g in listed["groups"]),
                                  payload={"handled_by": "entity_groups", "groups": len(listed["groups"])})

    name, span = _group_name_in(text)
    if name is None:
        return TaskResult.failure(request.task, request.own_name, "Which group? Name it (\"the <name> group\").")
    rest = text if span is None else text[: span[0]] + " " + text[span[1]:]
    exists = name in _group_names()

    if _GROUP_DELETE.search(text) and not _members_in(rest):
        done, reason = _tool_json(request, GROUPS_TOOL, "remove", {"name": name})
        if done is None or not done.get("removed"):
            return TaskResult.failure(request.task, request.own_name, f"There is no group called '{name}'.")
        return TaskResult.success(request.task, request.own_name,
                                  f"Removed the group '{name}'. Its members are untouched.",
                                  payload={"handled_by": "entity_groups", "removed": name})

    if exists and _GROUP_REMOVE_MEMBER.search(text):
        members = [m for m in _members_in(rest) if _compact(m) != _compact(name)]
        if not members:
            return TaskResult.failure(request.task, request.own_name, f"What should I take out of '{name}'?")
        done, reason = _tool_json(request, GROUPS_TOOL, "remove_members", {"name": name, "names": members})
        if done is None or not done.get("group"):
            return TaskResult.failure(request.task, request.own_name, f"I couldn't change '{name}': {reason}.")
        return TaskResult.success(request.task, request.own_name,
                                  f"Group updated: {_group_line(done['group'])}.",
                                  payload={"handled_by": "entity_groups", "group": done["group"]["name"]})

    if _GROUP_DECLARE.search(text):
        members = [m for m in _members_in(rest) if _compact(m) != _compact(name)]
        if not members:
            known = ", ".join(sorted(set(_connection_names()) | set(_observed_names()))) or "nothing yet"
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't tell which things belong in '{name}'. I know: {known}.",
            )
        operation = "add" if exists else "declare"
        done, reason = _tool_json(request, GROUPS_TOOL, operation,
                                  {"name": name, "members": members, "declared_by": request.own_name})
        if done is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't set up '{name}': {reason}.")
        group = done["group"]
        verb = "Group updated" if exists else "Group set"
        return TaskResult.success(
            request.task, request.own_name,
            f"{verb}: {_group_line(group)}. Ask how it is doing, set a condition on it, or act on all of it "
            "(I'll list every step and ask first).",
            payload={"handled_by": "entity_groups", "group": group["name"],
                     "members": [m["name"] for m in group["members"]]},
        )

    if not exists:
        return TaskResult.failure(request.task, request.own_name, f"There is no group called '{name}'.")

    if _GROUP_ACT.search(rest) and not _GROUP_STATE.search(rest):
        return _group_action(request, name, rest)
    return _group_state(request, name, text)


def _group_state(request: Any, name: str, text: str) -> TaskResult:
    from src.services.observations.store import parse_window

    window = parse_window(text)
    content: dict = {"name": name}
    if window is not None:
        content["window_seconds"] = window.total_seconds()
    state, reason = _tool_json(request, GROUPS_TOOL, "state", content)
    if state is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't read '{name}': {reason}.")
    group = state["group"]
    lines = [f"{group['name']} ({len(state['members'])} member(s)):"]
    for member in state["members"]:
        if not member["observed"]:
            lines.append(f"  {member['entity']}: nothing observed yet - watch it to keep its readings")
            continue
        parts = []
        for attribute, row in member["latest"].items():
            unit = f" {row['unit']}" if row.get("unit") else ""
            summary = (member.get("summary") or {}).get(attribute)
            if summary and summary.get("mean") is not None:
                parts.append(f"{attribute} mean {summary['mean']}{unit} (last {row['value']}{unit})")
            else:
                parts.append(f"{attribute} {row['value']}{unit}")
        lines.append(f"  {member['entity']}: " + ", ".join(parts))
    if state["aggregates"]:
        lines.append("Across the group:")
        for attribute, agg in state["aggregates"].items():
            lines.append(f"  {attribute}: mean {agg['mean']}, lowest {agg['min']} ({agg['lowest']}), "
                         f"highest {agg['max']} ({agg['highest']}) over {agg['members']} members")
    if group.get("missing"):
        lines.append("Not found now: " + ", ".join(m["name"] for m in group["missing"]))
    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines),
        payload={"handled_by": "entity_groups", "group": group["name"], "aggregates": state["aggregates"],
                 "members": [m["entity"] for m in state["members"]]},
    )


def _group_action(request: Any, name: str, clause: str) -> TaskResult:
    """One step per member connection that offers what ``clause`` asks,
    every step listed in one confirmation - nothing runs until "yes"."""
    from src.manager.activity_session import PendingConfirmation

    resolved, reason = _tool_json(request, GROUPS_TOOL, "resolve", {"name": name})
    if resolved is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't read '{name}': {reason}.")
    connections = request.connections
    steps, skipped = [], []
    for member in resolved["group"]["resolved"]:
        if member["kind"] != "connection":
            continue
        connection = connections.get(member["name"]) if connections is not None else None
        if connection is None or connection.name != member["name"]:
            skipped.append(f"{member['name']} (not a connection I have now)")
            continue
        step, why = _operation_on(connection, clause)
        if step is None:
            skipped.append(f"{member['name']} ({why})")
            continue
        steps.append(step)
    if not steps:
        detail = "; ".join(skipped) or "it has no connection members to act on"
        return TaskResult.failure(request.task, request.own_name, f"Nothing in '{name}' can do that: {detail}.")
    if request.activity_sessions is None:
        return TaskResult.failure(request.task, request.own_name,
                                  "I can't confirm actions in this context, so I won't act on the group without asking.")
    listed = "\n".join(
        f"  {i}. '{s['operation']}' on '{s['connection']}'"
        + (f" with {', '.join(f'{k}={v}' for k, v in s['params'].items())}" if s["params"] else "")
        for i, s in enumerate(steps, 1)
    )
    skipped_text = f"\nNot included: {'; '.join(skipped)}." if skipped else ""
    description = (f"For the group '{name}' I would do {len(steps)} thing(s) on the real services:\n{listed}"
                   f"{skipped_text}\nProceed with all of them? (yes/no)")
    request.activity_sessions.raise_confirmation(
        request.own_name,
        PendingConfirmation(operation=f"act on the group '{name}'", description=description, group=name,
                            steps=tuple({"connection": s["connection"], "operation": s["operation"],
                                         "params": {k: str(v) for k, v in s["params"].items()}} for s in steps)),
    )
    return TaskResult.success(request.task, request.own_name, description,
                              payload={"handled_by": "entity_groups", "group": name, "awaiting": "confirmation",
                                       "steps": steps, "skipped": skipped})


# =============================================================================
# GM-09 - series analysis techniques
# =============================================================================

_NOT_NOW = re.compile(r"^\s*(?:please\s+)?(?:tell|warn|alert|notify|let)\s+(?:me|us)\b.*\b(?:when|if|whenever)\b",
                      re.IGNORECASE)


def _series_technique_in(text: str):
    from src.services.observations.techniques import current_techniques, technique_for_request

    try:
        return technique_for_request(text, current_techniques())
    except Exception:  # noqa: BLE001 - a claim never fails the request
        return None


def series_analysis_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """A technique's own phrase ("peaks", "anything unusual", "a daily
    rhythm", "correlated with") about something that has really been
    observed - never a future condition ("tell me when ..." is GM-02's)."""
    text = task.description
    if _NOT_NOW.search(text):
        return False
    return _series_technique_in(text) is not None and bool(mentioned(text, _observed_names()))


def _numeric_attributes(request: Any, entity: str) -> "list[str]":
    from src.services.observations.store import is_number

    latest, _ = _tool_json(request, OBSERVATION_TOOL, "latest", {"entity": entity})
    return [a for a, row in ((latest or {}).get("latest") or {}).items() if is_number(row.get("value"))]


def _when(iso: str) -> str:
    return iso[:16].replace("T", " ")


def series_analysis(request: Any) -> TaskResult:
    from src.services.observations.store import parse_window

    text = request.task.description
    technique = _series_technique_in(text)
    if technique is None:
        return TaskResult.failure(request.task, request.own_name, "I couldn't tell which kind of analysis you mean.")
    entities = mentioned(text, _observed_names())
    pairs: list[tuple[str, str]] = []
    for entity in entities:
        numeric = _numeric_attributes(request, entity)
        chosen = attributes_mentioned(text, numeric, entity=entity) or (numeric if len(numeric) == 1 else [])
        if technique.inputs == 2 and len(entities) == 1:
            pairs.extend((entity, a) for a in chosen[:2])
        elif chosen:
            pairs.append((entity, chosen[0]))
        elif numeric:
            return TaskResult.failure(request.task, request.own_name,
                                      f"Which reading of '{entity}'? It reports: {', '.join(numeric)}.")
        if len(pairs) >= technique.inputs:
            break
    if len(pairs) < technique.inputs:
        need = "two readings to relate" if technique.inputs == 2 else "a reading made of numbers"
        return TaskResult.failure(request.task, request.own_name,
                                  f"{technique.label} needs {need}, and I couldn't find that in what you named.")
    content: dict = {"technique": technique.technique_id, "entity": pairs[0][0], "attribute": pairs[0][1]}
    if technique.inputs == 2:
        content.update(with_entity=pairs[1][0], with_attribute=pairs[1][1])
    window = parse_window(text)
    if window is not None:
        content["window_seconds"] = window.total_seconds()
    done, reason = _tool_json(request, OBSERVATION_TOOL, "analyze", content)
    if done is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't analyse that: {reason}.")
    result = done["result"]
    described = " and ".join(f"{i['entity']} {i['attribute'].replace('_', ' ')} ({i['readings']} readings)"
                             for i in done["inputs"])
    lines = [f"{result['label']} - {described}: {result['detail']}."]
    for finding in sorted(result["findings"], key=lambda f: f["at"])[:12]:
        extra = f" (from {finding['from']:.4g} to {finding['to']:.4g})" if "from" in finding else ""
        lines.append(f"  {_when(finding['at'])} UTC: {finding['what']} - {finding['value']:.4g}{extra}")
    if len(result["findings"]) > 12:
        lines.append(f"  ... and {len(result['findings']) - 12} more")
    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines),
        payload={"handled_by": "series_analysis", "technique": result["technique"], "numbers": result["numbers"],
                 "found": len(result["findings"]), "inputs": done["inputs"]},
    )


# =============================================================================
# GM-06 - live data-bound views
# =============================================================================

LIVE_VIEW_TOOL = "live_view"
_VIEW_NOUN = re.compile(
    r"\b(?:dashboards?|live\s+views?|status\s+boards?|monitoring\s+(?:screens?|panels?|pages?|views?)|"
    r"control\s+panels?|live\s+panels?|operations?\s+(?:screens?|views?|interfaces?)|live\s+display)\b",
    re.IGNORECASE,
)
_VIEW_MAKE = re.compile(r"\b(?:make|create|build|set\s+up|put\s+together|give\s+me|i\s+want|i'd\s+like|show\s+me)\b",
                        re.IGNORECASE)
_VIEW_ADD = re.compile(r"\b(?:add|put|include)\b.+\b(?:to|on|onto|in)\b", re.IGNORECASE)
_VIEW_TAKE = re.compile(r"\b(?:remove|take|drop)\b.+\b(?:from|off|out\s+of)\b", re.IGNORECASE)
_VIEW_DELETE = re.compile(r"\b(?:delete|remove|get\s+rid\s+of)\s+(?:the\s+|my\s+)?(?:[\w' -]+?\s+)?(?:dashboard|live\s+view)\b",
                          re.IGNORECASE)
_VIEW_LIST = re.compile(r"\b(?:my|our|which|what|list)\b.*\b(?:dashboards|live\s+views)\b", re.IGNORECASE)
_VIEW_EVENTS = re.compile(r"\b(?:alerts?|events?|what\s+fired|warnings?|notifications?)\b", re.IGNORECASE)
_VIEW_TITLE = re.compile(r"\b(?:called|named|titled)\s+[\"']?(?P<t>[^\"',.;!?]+?)[\"']?(?=\s+(?:with|showing|for|of|that)\b|[,.;!?]|$)",
                         re.IGNORECASE)


def _view_names() -> "list[str]":
    ecosystem = bound_ecosystem()
    if ecosystem is None:
        return []
    try:
        return [v.title for v in ecosystem.views.all()] + [v.view_id for v in ecosystem.views.all()]
    except Exception:  # noqa: BLE001
        return []


def _bound_subjects(text: str) -> "list[str]":
    return mentioned(text, sorted(set(_observed_names()) | set(_group_names())))


def live_view_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """A dashboard / live view named in a making, changing, listing or
    removing shape - bound to things that are really observed (a view of
    nothing observed is ordinary document work, not this)."""
    text = task.description
    if not _VIEW_NOUN.search(text):
        return False
    if _VIEW_LIST.search(text) or _VIEW_DELETE.search(text):
        return True
    if mentioned(text, _view_names()) and (_VIEW_ADD.search(text) or _VIEW_TAKE.search(text)):
        return True
    return bool(_VIEW_MAKE.search(text) and (_bound_subjects(text) or _VIEW_EVENTS.search(text)))


def _widgets_for(request: Any, text: str) -> "tuple[list[dict], list[str]]":
    """The widgets a request names: each observed reading as its history
    (and, when a series technique is named, through it), each group whole,
    what a camera watch sees, and the events conditions raised."""
    from src.services.observations.store import parse_window

    window = parse_window(text)
    seconds = window.total_seconds() if window else 86400
    technique = _series_technique_in(text)
    shaped = technique.technique_id if technique is not None and technique.inputs == 1 else ""
    widgets: list[dict] = []
    notes: list[str] = []
    ecosystem = bound_ecosystem()
    watched_entities = {w.entity: w for w in ecosystem.watches.all()} if ecosystem is not None else {}
    for name in _bound_subjects(text):
        if name in _group_names():
            widgets.append({"kind": "group", "group": name})
            continue
        watch = watched_entities.get(name)
        if watch is not None and watch.frames_to:
            widgets.append({"kind": "look", "watch": watch.watch_id})
        numeric = _numeric_attributes(request, name)
        chosen = attributes_mentioned(text, numeric, entity=name) or numeric[:4]
        if not chosen and not (watch is not None and watch.frames_to):
            notes.append(f"'{name}' has no numeric readings yet")
        for attribute in chosen:
            widget = {"kind": "series", "entity": name, "attribute": attribute, "window_seconds": seconds}
            if shaped:
                widget["technique"] = shaped
            widgets.append(widget)
    if _VIEW_EVENTS.search(text):
        widgets.append({"kind": "events", "limit": 8})
    return widgets, notes


def live_view(request: Any) -> TaskResult:
    text = request.task.description
    if _VIEW_LIST.search(text) and not mentioned(text, _view_names()):
        listed, reason = _tool_json(request, LIVE_VIEW_TOOL, "views", {})
        if listed is None:
            return TaskResult.failure(request.task, request.own_name, f"I can't read the views: {reason}.")
        if not listed["views"]:
            return TaskResult.success(request.task, request.own_name,
                                      "There are no live views yet - ask for one, e.g. \"make me a dashboard of ...\".",
                                      payload={"handled_by": "live_view", "views": 0})
        lines = ["Your live views:"] + [f"  - {v['text']} (open: {v['path']})" for v in listed["views"]]
        return TaskResult.success(request.task, request.own_name, "\n".join(lines),
                                  payload={"handled_by": "live_view", "views": len(listed["views"])})
    named = mentioned(text, _view_names())
    if named and _VIEW_DELETE.search(text) and not _VIEW_TAKE.search(text.replace(named[0], "")):
        done, reason = _tool_json(request, LIVE_VIEW_TOOL, "remove", {"view": named[0]})
        if done is None or not done.get("removed"):
            return TaskResult.failure(request.task, request.own_name, f"I couldn't remove '{named[0]}': {reason}.")
        return TaskResult.success(request.task, request.own_name, f"Removed the live view '{named[0]}'.",
                                  payload={"handled_by": "live_view", "removed": named[0]})
    if named and _VIEW_TAKE.search(text):
        rest = re.sub(re.escape(named[0]), " ", text, flags=re.IGNORECASE)
        labels = _bound_subjects(rest)
        if not labels:
            return TaskResult.failure(request.task, request.own_name, f"What should I take off '{named[0]}'?")
        done, reason = _tool_json(request, LIVE_VIEW_TOOL, "remove_widgets", {"view": named[0], "labels": labels})
        if done is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't change '{named[0]}': {reason}.")
        return TaskResult.success(request.task, request.own_name, f"Updated: {done['text']}.",
                                  payload={"handled_by": "live_view", "view": done["view"]["view_id"]})
    rest = re.sub(re.escape(named[0]), " ", text, flags=re.IGNORECASE) if named else text
    widgets, notes = _widgets_for(request, rest)
    if not widgets:
        known = ", ".join(sorted(set(_observed_names()) | set(_group_names()))) or "nothing yet"
        return TaskResult.failure(request.task, request.own_name,
                                  f"I couldn't tell what the view should show. What is observed: {known}.")
    if named and _VIEW_ADD.search(text):
        done, reason = _tool_json(request, LIVE_VIEW_TOOL, "add", {"view": named[0], "widgets": widgets})
        verb = "Updated"
    else:
        match = _VIEW_TITLE.search(text)
        title = match.group("t").strip() if match else "Live view: " + ", ".join(_bound_subjects(text) or ["events"])
        done, reason = _tool_json(request, LIVE_VIEW_TOOL, "declare",
                                  {"title": title[:80], "widgets": widgets, "declared_by": request.own_name})
        verb = "Live view ready"
    if done is None:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't set up the view: {reason}.")
    view = done["view"]
    lines = [f"{verb}: {done['text']}.",
             f"Open it at live/{view['view_id']} - it redraws itself every {view['refresh_seconds']}s as readings arrive. "
             f"To keep it in a workspace, add the panel live:{view['view_id']} to that workspace in the Control Center."]
    lines += [f"Note: {n}." for n in notes]
    return TaskResult.success(request.task, request.own_name, "\n".join(lines),
                              payload={"handled_by": "live_view", "view": view["view_id"],
                                       "widgets": [w["kind"] for w in view["widgets"]]})
