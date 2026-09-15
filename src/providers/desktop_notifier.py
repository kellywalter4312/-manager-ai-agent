"""Desktop notifications - so a due reminder reaches the person when no page is
open.

The served page presents a due reminder as an in-page toast; with the page
closed nothing reached anyone. The heartbeat already finds due reminders with
no page at all, so it now also shows them through the operating system.

``WindowsToastNotifier`` uses Windows' own notification API through PowerShell -
no new dependency, no network - and confirms delivery by reading the
notification back from Windows' notification history, so "shown" is observed,
never assumed. ``NullDesktopNotifier`` is the honest default elsewhere.
Chosen through the Control Center category ``desktop_notification_provider``.
"""

from __future__ import annotations

import subprocess
import sys
from abc import ABC, abstractmethod
from xml.sax.saxutils import escape

# Windows attributes a notification to an installed app; PowerShell's own
# registered id is present on every Windows 10/11 machine.
POWERSHELL_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"
_GROUP = "alfred"

_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('__XML__')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
$toast.Tag = '__TAG__'
$toast.Group = '__GROUP__'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('__APP__').Show($toast)
Start-Sleep -Milliseconds 600
$seen = @([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory('__APP__') | Where-Object { $_.Tag -eq '__TAG__' }).Count
[Console]::Out.WriteLine("delivered=" + $seen)
"""


def _run_powershell(script: str, *, timeout: float) -> subprocess.CompletedProcess:
    """The one seam tests replace - never the real subprocess call."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


class DesktopNotifier(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for logs and configuration."""

    @abstractmethod
    def notify(self, title: str, body: str, *, tag: str) -> bool:
        """Show one notification; True only when it was observed delivered."""


class NullDesktopNotifier(DesktopNotifier):
    @property
    def name(self) -> str:
        return "null"

    def notify(self, title: str, body: str, *, tag: str) -> bool:
        return False


class WindowsToastNotifier(DesktopNotifier):
    @property
    def name(self) -> str:
        return "windows"

    @staticmethod
    def available() -> bool:
        return sys.platform.startswith("win")

    def notify(self, title: str, body: str, *, tag: str) -> bool:
        if not self.available():
            return False
        # A tag is at most 64 characters; the XML goes inside single quotes.
        safe_tag = "".join(ch for ch in str(tag) if ch.isalnum() or ch in "-_:.")[:64] or "alfred"
        xml = (
            '<toast><visual><binding template="ToastGeneric">'
            f"<text>{escape(str(title))}</text><text>{escape(str(body))}</text>"
            "</binding></visual></toast>"
        ).replace("'", "''")
        script = (
            _SCRIPT.replace("__XML__", xml).replace("__TAG__", safe_tag)
            .replace("__GROUP__", _GROUP).replace("__APP__", POWERSHELL_APP_ID)
        )
        try:
            result = _run_powershell(script, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and "delivered=1" in (result.stdout or "")


def available_desktop_notification_backends() -> "list[str]":
    return ["null", "windows"]


def build_desktop_notifier(settings) -> DesktopNotifier:
    choice = str(getattr(settings, "desktop_notifications", "null") or "null").strip().lower()
    if choice == "null":
        return NullDesktopNotifier()
    if choice == "windows":
        if not WindowsToastNotifier.available():
            raise ValueError("Windows desktop notifications are only available on Windows")
        return WindowsToastNotifier()
    raise ValueError(f"Unknown desktop notification backend {choice!r}. Available: null, windows")
