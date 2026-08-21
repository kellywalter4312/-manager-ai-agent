"""A fake Gmail API service.

Mimics the chained call shape of googleapiclient
(``service.users().messages().list(...).execute()``) closely enough to drive
GmailMailbox, so the whole suite runs with no credentials, no network, and
without the Google packages installed.
"""

from __future__ import annotations

import base64
from typing import Any


def b64(text: str) -> str:
    """Encode as Gmail does: base64url, padding stripped."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def gmail_message(
    id_="m1",
    sender="talent@northwind-labs.example",
    subject="Senior Python Engineer at Northwind Labs",
    plain="We are hiring. Salary competitive. Apply by Friday.",
    html=None,
    internal_date=1_755_248_400_000,  # 2025-08-15T09:00:00Z in ms
    label_ids=("INBOX",),
):
    """Build a Gmail API 'full' message resource."""
    headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]

    if html is not None and plain is not None:
        payload: dict[str, Any] = {
            "mimeType": "multipart/alternative",
            "headers": headers,
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64(plain)}},
                {"mimeType": "text/html", "body": {"data": b64(html)}},
            ],
        }
    elif html is not None:
        payload = {"mimeType": "text/html", "headers": headers, "body": {"data": b64(html)}}
    else:
        payload = {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": b64(plain or "")},
        }

    return {
        "id": id_,
        "internalDate": str(internal_date),
        "labelIds": list(label_ids),
        "payload": payload,
    }


class _Executable:
    def __init__(self, result, error=None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._result


class _Messages:
    def __init__(self, service):
        self._service = service

    def list(self, userId, maxResults=None, labelIds=None):
        self._service.calls.append(("list", userId, maxResults, labelIds))
        if self._service.list_error is not None:
            return _Executable(None, self._service.list_error)

        selected = list(self._service.messages)
        if labelIds:
            wanted = set(labelIds)
            selected = [m for m in selected if wanted & set(m.get("labelIds", []))]
        selected = selected[: maxResults or len(selected)]
        return _Executable({"messages": [{"id": m["id"]} for m in selected]})

    def get(self, userId, id, format=None):
        self._service.calls.append(("get", userId, id, format))
        if self._service.get_error is not None:
            return _Executable(None, self._service.get_error)
        for message in self._service.messages:
            if message["id"] == id:
                return _Executable(message)
        return _Executable(None)


class _Labels:
    def __init__(self, service):
        self._service = service

    def list(self, userId):
        self._service.calls.append(("labels", userId, None, None))
        if self._service.labels_error is not None:
            return _Executable(None, self._service.labels_error)
        return _Executable({"labels": self._service.labels})


class _Users:
    def __init__(self, service):
        self._service = service

    def messages(self):
        return _Messages(self._service)

    def labels(self):
        return _Labels(self._service)


class FakeGmailService:
    """Stand-in for the googleapiclient Gmail service."""

    def __init__(
        self,
        messages=None,
        labels=None,
        list_error=None,
        get_error=None,
        labels_error=None,
    ):
        self.messages = list(messages or [])
        self.labels = list(labels or [{"id": "Label_7", "name": "Jobs"}])
        self.list_error = list_error
        self.get_error = get_error
        self.labels_error = labels_error
        self.calls: list[tuple] = []

    def users(self):
        return _Users(self)

    @property
    def call_names(self) -> list[str]:
        return [call[0] for call in self.calls]
