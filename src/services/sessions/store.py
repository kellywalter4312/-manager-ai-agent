"""Where interaction sessions live between runs - one file per session.

A ``Session`` (src/conversation/session.py) is the record of a room: who was in
it, everything said, the discussions held and the work handed out. This store
keeps those records on disk so a session can be listed and continued after a
restart, from whichever surface a person uses next.

**Not a memory, and never read by an agent on its own.** An agent's memory is
what its own policy admits; this is the record of an interaction. Nothing here
writes into, or reads from, any agent's store.

**One live Session per conversation.** ``session_for`` hands back the same
Session object for the same room for as long as this store is alive, so a
discussion opened on one surface is the discussion another surface sees.

**Where it writes is decided at call time.** ``DEFAULT_SESSIONS_DIR`` is read
when a store is built without a directory, never frozen at import - the same
rule the Control Center's own default paths follow - so a test or an isolated
harness redirects it by setting one attribute.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from src.conversation.conversation import Conversation
from src.conversation.session import Session
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_SESSIONS_DIR = Path("data") / "sessions"

# A session id is the conversation's own id - short hex today, but whatever it
# is, it becomes a file name only through this check, so a crafted id can never
# name a path outside the store.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def turn_detail(detail: Any) -> Any:
    """What of an utterance's opaque ``detail`` is worth keeping: the facts a
    later surface needs to show who handled a turn and how - read by name,
    so this module never imports the layer that builds a turn."""
    if detail is None:
        return None
    if isinstance(detail, (str, int, float, bool, dict, list)):
        return detail if isinstance(detail, (str, int, float, bool)) else None
    kept = {}
    for name in ("handled_by", "delegated", "capability", "succeeded", "manager"):
        value = getattr(detail, name, None)
        if isinstance(value, (str, bool)) or value is None:
            kept[name] = value
    return kept or None


class SessionStore:
    """Sessions on disk, and the live Session for each open room."""

    def __init__(self, directory: "Path | str | None" = None, *, persist: bool = True) -> None:
        # None: ``DEFAULT_SESSIONS_DIR`` as it is *when a session is saved or
        # read*, so a store held for the life of a process follows a redirect.
        self._directory = Path(directory) if directory is not None else None
        # False: sessions live for this process only and nothing is written -
        # for a caller that opted out of keeping sessions, which ``saved()``
        # and ``save()`` then report honestly rather than pretending.
        self.persist = persist
        self._live: dict[int, Session] = {}
        self.last_error: str = ""

    @property
    def directory(self) -> Path:
        if self._directory is not None:
            return self._directory
        import src.services.sessions.store as this_module

        return Path(this_module.DEFAULT_SESSIONS_DIR)

    # ------------------------------------------------------------------
    # Live sessions
    # ------------------------------------------------------------------

    def session_for(self, conversation: Conversation) -> Session:
        """The one Session for this room, created the first time it is asked
        for. Keyed by the room object itself, so a resumed room - adopted in
        place - keeps its Session."""
        session = self._live.get(id(conversation))
        if session is None or session.conversation is not conversation:
            session = Session(conversation)
            self._live[id(conversation)] = session
        return session

    def bind(self, conversation: Conversation, session: Session) -> Session:
        """Make ``session`` the live one for ``conversation`` - used when a
        saved session is resumed into an already-open room."""
        conversation.adopt(session.conversation)
        session.conversation = conversation
        for discussion in session.discussions:
            discussion.floor.rebind(conversation)
        self._live[id(conversation)] = session
        return session

    # ------------------------------------------------------------------
    # Disk
    # ------------------------------------------------------------------

    def path_for(self, session_id: str) -> Path:
        if not _SAFE_ID.match(str(session_id)):
            raise ValueError(f"'{session_id}' is not a session id")
        return self.directory / f"{session_id}.json"

    def save(self, session: Session) -> bool:
        """Write the whole session. True when it landed; on failure the reason
        is kept in ``last_error`` and logged, and False is returned - saving a
        record must never break the turn it records, and must never be silent
        about failing either."""
        if not self.persist:
            self.last_error = "this store does not keep sessions"
            return False
        try:
            path = self.path_for(session.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(session.to_dict(detail_of=turn_detail), indent=2)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(data, encoding="utf-8")
            os.replace(temporary, path)
        except Exception as exc:  # noqa: BLE001 - reported, never raised into the turn
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("could not save session %s: %s", session.id, self.last_error)
            return False
        self.last_error = ""
        return True

    def load(self, session_id: str) -> Session:
        """The saved session with this id. Raises FileNotFoundError when there
        is none, and ValueError when the id is not one."""
        path = self.path_for(session_id)
        return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def saved(self) -> tuple[dict, ...]:
        """Every saved session, newest first, as short summaries - id, when it
        started, who took part, how much was said, and whether a discussion is
        still open. A file that cannot be read is listed as unreadable rather
        than skipped silently."""
        if not self.persist or not self.directory.exists():
            return ()
        summaries = []
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                summaries.append({
                    "id": str(data["id"]),
                    "started_at": str(data["started_at"]),
                    "participants": tuple(p["name"] for p in data.get("participants", ())),
                    "utterances": len(data.get("utterances", ())),
                    "open_discussion": next(
                        (d["subject"] for d in data.get("discussions", ()) if d.get("status") == "open"), None
                    ),
                    "modified": path.stat().st_mtime,
                })
            except Exception as exc:  # noqa: BLE001 - reported as unreadable
                summaries.append({"id": path.stem, "unreadable": f"{type(exc).__name__}: {exc}",
                                  "modified": path.stat().st_mtime})
        return tuple(sorted(summaries, key=lambda s: s["modified"], reverse=True))


_PROCESS_STORE: "SessionStore | None" = None


def default_session_store() -> SessionStore:
    """The one store every surface of this process shares - so a discussion
    opened through one door is the discussion another door sees. Writes where
    ``DEFAULT_SESSIONS_DIR`` points at the time of each save."""
    global _PROCESS_STORE
    if _PROCESS_STORE is None:
        _PROCESS_STORE = SessionStore()
    return _PROCESS_STORE
