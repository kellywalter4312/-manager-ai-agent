"""Turns a configured calendar-event provider *name* into a provider
*instance*.

The identical small registry ``live_info_factory.py``/
``communication_factory.py``/``celebration_factory.py`` already are.
"default" (``DefaultCalendarEventProvider``, a genuinely computed set of
occasions - see that module's own docstring for why it is not an empty
placeholder) is what an unconfigured installation gets, the same
reasoning ``celebration_factory.py`` already applies to its own
"default". "null" remains available for anyone who wants zero calendar
awareness.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.calendar_events import CalendarEventProvider
from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider
from src.providers.null_calendar_event_provider import NullCalendarEventProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

_BUILDERS: dict[str, Callable[[Settings], CalendarEventProvider]] = {
    "default": lambda settings: DefaultCalendarEventProvider(),
    "null": lambda settings: NullCalendarEventProvider(),
}


def available_calendar_event_providers() -> list[str]:
    """Every calendar-event provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_calendar_event_provider(
    settings: Settings, env: Mapping[str, str] | None = None
) -> CalendarEventProvider:
    """Construct the calendar-event provider named by
    ``settings.calendar_event_provider``.

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.calendar_event_provider]
    except KeyError:
        raise ValueError(
            f"Unknown calendar-event provider {settings.calendar_event_provider!r}. "
            f"Available: {', '.join(available_calendar_event_providers())}"
        ) from None

    provider = builder(settings)
    logger.info("calendar-event provider ready: %s", provider.name)
    return provider
