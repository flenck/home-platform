"""Event bus for decoupled component communication."""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

MATCH_ALL = "*"

# Listeners are synchronous callbacks. If they need to do async work,
# they schedule it via asyncio.create_task or asyncio.run_coroutine_threadsafe.
CallbackType = Callable[[str, dict[str, Any]], None]


class EventBus:
    """Central event bus. Components listen and fire events through this bus.

    Events are dispatched synchronously. Listeners should be fast;
    heavy work should be scheduled as a task inside the listener.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[CallbackType]] = defaultdict(list)

    def listen(self, event_type: str, callback: CallbackType) -> Callable[[], None]:
        """Register a callback for an event type. Returns an unsubscribe function."""
        self._listeners[event_type].append(callback)

        def unsubscribe() -> None:
            self._listeners[event_type].remove(callback)

        return unsubscribe

    def fire(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Fire an event synchronously. All registered listeners are called."""
        data = data or {}
        data["event_type"] = event_type

        for cb in self._listeners.get(MATCH_ALL, []):
            try:
                cb(event_type, data)
            except Exception as e:
                _LOGGER.error("Error in listener for %s: %s", event_type, e)

        for cb in self._listeners.get(event_type, []):
            try:
                cb(event_type, data)
            except Exception as e:
                _LOGGER.error("Error in listener for %s: %s", event_type, e)
