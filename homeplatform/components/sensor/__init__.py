"""Base sensor entity and domain registration."""

import logging
from abc import ABC, abstractmethod
from typing import Any

from homeplatform.const import DOMAIN_SENSOR, STATE_UNKNOWN
from homeplatform.core import HomePlatform

_LOGGER = logging.getLogger(__name__)


class SensorEntity(ABC):
    """Base class for all sensor entities. Each sensor measures one metric."""

    def __init__(self, metric: str, name: str, unit: str = "") -> None:
        self._metric = metric
        self._name = name
        self._unit = unit
        self._value: float | None = None

    @property
    def entity_id(self) -> str:
        return f"{DOMAIN_SENSOR}.{self._metric}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def unit(self) -> str:
        return self._unit

    @property
    def state(self) -> str:
        """Human-readable state. Override for classified states (ok/problem/unknown)."""
        if self._value is None:
            return STATE_UNKNOWN
        return str(round(self._value, 1))

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def attributes(self) -> dict[str, Any]:
        return {
            "metric": self._metric,
            "unit": self._unit,
            "value": self._value,
            "name": self._name,
        }

    def set_value(self, value: float) -> None:
        self._value = value

    @abstractmethod
    def status_label(self) -> str:
        """Return a human-readable classification of the current value."""

    @abstractmethod
    def status_class(self) -> str:
        """Return a CSS class for the status (good/warn/bad)."""


async def async_setup(hass: HomePlatform, config: dict) -> None:
    """Register the sensor domain."""
    _LOGGER.info("Sensor domain registered")
