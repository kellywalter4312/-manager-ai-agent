"""Reading a stream a service keeps sending (GM-04) - by what the bytes are,
never by which service sends them.

Four shapes cover the streams real devices and services send over HTTP:

    text/event-stream              server-sent events: "event:", "data:" lines,
                                   a blank line ends each event
    application/x-ndjson (+jsonl,  one JSON value per line (RFC 7464 record
    json-seq)                      separators tolerated)
    multipart/x-mixed-replace      a part per frame - the shape of an IP
                                   camera's live picture - each with its own
                                   Content-Type (and usually Content-Length)
    anything else                  raw binary chunks, as they arrive

Every read is bounded: at most ``max_items`` items, until ``deadline``, and a
caller's ``on_item`` may stop it early by returning False. A read that times
out ends the listen with "time"; a service that closes the stream ends it
with "closed". Nothing here retries or reconnects - the watch that asked for
the listen decides when to listen again.
"""

from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone
from typing import Any, Callable

from src.providers.connection import StreamItem, is_json_value

MAX_LINE_BYTES = 1024 * 1024
MAX_PART_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _value(text: str) -> "tuple[Any, str]":
    """(data, text): JSON when the text is JSON, otherwise the text itself."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None, text
    return (parsed, "") if is_json_value(parsed) else (None, text)


class _Reader:
    def __init__(self, response: Any, deadline: float, max_items: int,
                 on_item: "Callable[[StreamItem], Any] | None") -> None:
        self.response = response
        self.deadline = deadline
        self.max_items = max(1, int(max_items))
        self.on_item = on_item
        self.items: list[StreamItem] = []
        self.ended = ""
        self._pushed: "bytes | None" = None

    def push_back(self, line: bytes) -> None:
        """Return one line to be read again (a boundary found while reading
        a part that had no length)."""
        self._pushed = line

    def out_of_time(self) -> bool:
        if time.monotonic() >= self.deadline:
            self.ended = self.ended or "time"
            return True
        return False

    def emit(self, **fields: Any) -> bool:
        """Record one item; False when the listen must stop."""
        item = StreamItem(sequence=len(self.items), received_at=_now(), **fields)
        self.items.append(item)
        if self.on_item is not None and self.on_item(item) is False:
            self.ended = "stopped"
            return False
        if len(self.items) >= self.max_items:
            self.ended = "limit"
            return False
        return not self.out_of_time()

    def readline(self) -> "bytes | None":
        if self._pushed is not None:
            line, self._pushed = self._pushed, None
            return line
        try:
            line = self.response.readline(MAX_LINE_BYTES)
        except (socket.timeout, TimeoutError):
            self.ended = self.ended or "time"
            return None
        if not line:
            self.ended = self.ended or "closed"
            return None
        return line

    def read(self, size: int) -> "bytes | None":
        try:
            data = self.response.read(size)
        except (socket.timeout, TimeoutError):
            self.ended = self.ended or "time"
            return None
        if not data:
            self.ended = self.ended or "closed"
            return None
        return data


def _server_sent_events(reader: _Reader) -> None:
    data_lines: list[str] = []
    event_name = ""
    while not reader.out_of_time():
        raw = reader.readline()
        if raw is None:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                data, text = _value("\n".join(data_lines))
                if not reader.emit(data=data, text=text, event=event_name, content_type="text/event-stream"):
                    return
            data_lines, event_name = [], ""
            continue
        if line.startswith(":"):
            continue  # a comment / keep-alive
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "data":
            data_lines.append(value)
        elif field == "event":
            event_name = value.strip()


def _json_lines(reader: _Reader, content_type: str) -> None:
    while not reader.out_of_time():
        raw = reader.readline()
        if raw is None:
            break
        text = raw.decode("utf-8", errors="replace").strip().strip("\x1e").strip()
        if not text:
            continue
        data, leftover = _value(text)
        if not reader.emit(data=data, text=leftover, content_type=content_type):
            return


def _multipart(reader: _Reader, content_type_header: str) -> None:
    boundary = ""
    for parameter in content_type_header.split(";")[1:]:
        key, _, value = parameter.strip().partition("=")
        if key.strip().lower() == "boundary":
            boundary = value.strip().strip('"')
    if not boundary:
        reader.ended = "error"
        return
    marker = boundary.encode("latin-1") if boundary.startswith("--") else b"--" + boundary.encode("latin-1")
    while not reader.out_of_time():
        raw = reader.readline()
        if raw is None:
            return
        if not raw.strip().startswith(marker):
            continue
        if raw.strip().endswith(b"--"):
            reader.ended = "closed"
            return
        headers: dict[str, str] = {}
        while True:
            line = reader.readline()
            if line is None:
                return
            if line.strip() == b"":
                break
            key, _, value = line.decode("latin-1").partition(":")
            headers[key.strip().lower()] = value.strip()
        part_type = headers.get("content-type", "application/octet-stream")
        length = headers.get("content-length")
        if length and length.isdigit() and int(length) <= MAX_PART_BYTES:
            remaining, chunks = int(length), []
            while remaining > 0:
                piece = reader.read(min(remaining, CHUNK_BYTES))
                if piece is None:
                    return
                chunks.append(piece)
                remaining -= len(piece)
            payload = b"".join(chunks)
        else:
            # No length: the part runs to the next boundary line.
            chunks = []
            size = 0
            while True:
                line = reader.readline()
                if line is None:
                    return
                if line.strip().startswith(marker):
                    reader.push_back(line)  # it opens the next part
                    break
                chunks.append(line)
                size += len(line)
                if size > MAX_PART_BYTES:
                    reader.ended = "error"
                    return
            payload = b"".join(chunks).rstrip(b"\r\n")
        if part_type.lower().startswith(("application/json", "text/")):
            data, text = _value(payload.decode("utf-8", errors="replace"))
            keep_going = reader.emit(data=data, text=text, content_type=part_type)
        else:
            keep_going = reader.emit(payload=payload, content_type=part_type)
        if not keep_going:
            return


def _chunks(reader: _Reader, content_type: str) -> None:
    while not reader.out_of_time():
        data = reader.read(CHUNK_BYTES)
        if data is None:
            return
        if not reader.emit(payload=data, content_type=content_type or "application/octet-stream"):
            return


def read_stream(
    response: Any,
    content_type_header: str,
    *,
    max_items: int,
    max_seconds: float,
    on_item: "Callable[[StreamItem], Any] | None" = None,
) -> "tuple[list[StreamItem], str]":
    """Read ``response`` as the stream its Content-Type says it is.
    Returns (items, why the listen ended)."""
    reader = _Reader(response, time.monotonic() + max(0.1, float(max_seconds)), max_items, on_item)
    kind = content_type_header.split(";")[0].strip().lower()
    if kind == "text/event-stream":
        _server_sent_events(reader)
    elif kind in ("application/x-ndjson", "application/jsonl", "application/json-seq", "application/stream+json"):
        _json_lines(reader, kind)
    elif kind.startswith("multipart/"):
        _multipart(reader, content_type_header)
    elif kind in ("application/json", "text/plain") or kind.startswith("text/"):
        _json_lines(reader, kind or "text/plain")
    else:
        _chunks(reader, kind)
    return reader.items, reader.ended or "closed"
