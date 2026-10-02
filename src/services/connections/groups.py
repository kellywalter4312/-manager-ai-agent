"""Grouping entities across independent connections (GM-15).

A **group** is a name for things that belong together in the owner's world
though they reach Alfred through different connections - a room whose light
and heater come from two vendors, a line of machines, a set of sensors in one
field. P11Z-07 kept containment *within* one connection as that connection's
own data; this is the axis across connections, recorded then as absent.

**Members are references, never copies.** A member is a connection (by
name), an observed entity (by name), or another group - so a group of groups
is a place made of places. Resolution is recursive with cycles refused, and a
member that no longer exists is reported, never silently dropped or invented.

**Groups add no power.** Reading a group reads each member through its own
connection; acting on a group proposes each member's own operation through
the same confirmation gate, listed in full, and runs nothing until the person
says yes. A condition over a group (GM-02) asks whether any - or all - of its
members meet it; a history question over a group aggregates its members.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.services.declared_store import DeclaredDocument, slug

MEMBER_KINDS = ("connection", "entity", "group")


@dataclass(frozen=True)
class Member:
    kind: str
    name: str

    def __post_init__(self) -> None:
        if self.kind not in MEMBER_KINDS:
            raise ValueError(f"a group member is a {', '.join(MEMBER_KINDS)} - not '{self.kind}'")
        name = " ".join(str(self.name or "").split())
        if not name:
            raise ValueError("a group member needs a name")
        object.__setattr__(self, "name", name)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name}


@dataclass(frozen=True)
class Group:
    name: str
    members: "tuple[Member, ...]" = ()
    description: str = ""
    declared_by: str = ""
    declared_at: str = ""

    def __post_init__(self) -> None:
        name = " ".join(str(self.name or "").split())
        if not name:
            raise ValueError("a group needs a name")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "members", tuple(dict.fromkeys(self.members)))

    @property
    def key(self) -> str:
        return slug(self.name)

    def describe(self) -> str:
        members = ", ".join(f"{m.name}" + ("" if m.kind == "connection" else f" ({m.kind})") for m in self.members)
        return f"{self.name}: {members or 'no members yet'}"

    def to_dict(self) -> dict:
        return {"name": self.name, "members": [m.to_dict() for m in self.members], "description": self.description,
                "declared_by": self.declared_by, "declared_at": self.declared_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Group":
        return cls(
            name=str(data["name"]),
            members=tuple(Member(str(m["kind"]), str(m["name"])) for m in data.get("members") or ()),
            description=str(data.get("description") or ""), declared_by=str(data.get("declared_by") or ""),
            declared_at=str(data.get("declared_at") or ""),
        )


class GroupRegistry:
    def __init__(self, root: "Path | str") -> None:
        self._document = DeclaredDocument(Path(root) / "groups.json", key="groups")

    def all(self) -> "list[Group]":
        found = []
        for item in self._document.load():
            try:
                found.append(Group.from_dict(item))
            except (KeyError, ValueError, TypeError):
                continue
        return found

    def get(self, name: str) -> "Group | None":
        wanted = slug(name)
        return next((g for g in self.all() if g.key == wanted), None)

    def declare(self, group: Group) -> Group:
        if any(m.kind == "group" and slug(m.name) == group.key for m in group.members):
            raise ValueError("a group cannot contain itself")

        previous = self.get(group.name)

        def change(items: list) -> Group:
            items[:] = [i for i in items if slug(i.get("name", "")) != group.key]
            items.append(group.to_dict())
            return group
        result = self._document.update(change)
        try:
            self.resolve(group.name)
        except ValueError:
            # Refuse the change, never the group: what was declared before
            # stays exactly as it was.
            def restore(items: list) -> None:
                items[:] = [i for i in items if slug(i.get("name", "")) != group.key]
                if previous is not None:
                    items.append(previous.to_dict())
            self._document.update(restore)
            raise
        return result

    def add_members(self, name: str, members: Iterable[Member], *, declared_by: str = "") -> Group:
        existing = self.get(name)
        now = datetime.now(timezone.utc).isoformat()
        group = Group(
            name=existing.name if existing else name,
            members=(existing.members if existing else ()) + tuple(members),
            description=existing.description if existing else "",
            declared_by=existing.declared_by if existing else declared_by,
            declared_at=existing.declared_at if existing else now,
        )
        return self.declare(group)

    def remove_members(self, name: str, names: Iterable[str]) -> "Group | None":
        existing = self.get(name)
        if existing is None:
            return None
        drop = {slug(n) for n in names}
        return self.declare(Group(existing.name, tuple(m for m in existing.members if slug(m.name) not in drop),
                                  existing.description, existing.declared_by, existing.declared_at))

    def remove(self, name: str) -> bool:
        wanted = slug(name)

        def change(items: list) -> bool:
            before = len(items)
            items[:] = [i for i in items if slug(i.get("name", "")) != wanted]
            return len(items) < before
        return self._document.update(change)

    # -- resolution --------------------------------------------------------

    def resolve(self, name: str, _seen: "tuple[str, ...]" = ()) -> "list[Member]":
        """Every connection and entity the group holds, through nested
        groups, in order, each once. Raises ValueError on a cycle."""
        group = self.get(name)
        if group is None:
            return []
        if group.key in _seen:
            raise ValueError(f"groups may not contain each other in a circle ({' -> '.join(_seen + (group.name,))})")
        found: list[Member] = []
        for member in group.members:
            if member.kind == "group":
                found.extend(self.resolve(member.name, _seen + (group.key,)))
            else:
                found.append(member)
        return list(dict.fromkeys(found))

    def member_connections(self, name: str) -> "list[str]":
        return [m.name for m in self.resolve(name) if m.kind == "connection"]

    def member_entities(self, name: str, *, watched: "Mapping[str, list[str]] | None" = None) -> "list[str]":
        """The observed entities a group stands for: its entity members, and
        for each connection member the entities its watches keep (or its own
        name, when nothing renames them)."""
        entities: list[str] = []
        for member in self.resolve(name):
            if member.kind == "entity":
                entities.append(member.name)
            else:
                entities.extend((watched or {}).get(member.name) or [member.name])
        return list(dict.fromkeys(entities))
