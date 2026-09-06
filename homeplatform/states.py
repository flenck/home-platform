"""State machine for managing all entity states."""

import logging
from datetime import datetime, timezone
from typing import Any

from .bus import EventBus
from .const import EVENT_STATE_CHANGED, STATE_UNKNOWN

_LOGGER = logging.getLogger(__name__)


class State:
    """An immutable snapshot of an entity's state at a point in time."""

    __slots__ = ("entity_id", "state", "attributes", "last_updated")

    def __init__(
        self,
        entity_id: str,
        state: str = STATE_UNKNOWN,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}
        self.last_updated = datetime.now(timezone.utc)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": self.attributes,
            "last_updated": self.last_updated.isoformat(),
        }


class StateMachine:
    """Manages the current state of all entities."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._states: dict[str, State] = {}

    def set(
        self,
        entity_id: str,
        state: str,
        attributes: dict[str, Any] | None = None,
        *,
        fire_event: bool = True,
    ) -> State:
        """Set the state of an entity and optionally fire a state-changed event."""
        old_state = self._states.get(entity_id)
        new_state = State(entity_id, state, attributes)

        self._states[entity_id] = new_state

        if fire_event:
            self._bus.fire(
                EVENT_STATE_CHANGED,
                {
                    "entity_id": entity_id,
                    "old_state": old_state.as_dict() if old_state else None,
                    "new_state": new_state.as_dict(),
                },
            )

        return new_state

    def get(self, entity_id: str) -> State | None:
        """Get the current state of an entity."""
        return self._states.get(entity_id)

    def get_all(self) -> list[State]:
        """Get all current entity states."""
        return list(self._states.values())
