"""Energy component — receives electricity data from 95598-mqtt via MQTT."""

import logging

from homeplatform.const import EVENT_STOP
from homeplatform.core import HomePlatform

from .mqtt import SENSOR_TYPE_LABELS, SENSOR_TYPE_METRIC, SENSOR_TYPE_UNITS, EnergyMQTTClient

_LOGGER = logging.getLogger(__name__)

_mqtt_client: EnergyMQTTClient | None = None


async def async_setup(hass: HomePlatform, config: dict) -> None:
    """Set up the energy component with MQTT subscriber."""
    global _mqtt_client

    mqtt_config = hass.config.get("mqtt", {})
    broker = config.get("broker", mqtt_config.get("broker", "localhost"))
    port = config.get("port", mqtt_config.get("port", 1883))

    # Register placeholder entities
    for sensor_type, label in SENSOR_TYPE_LABELS.items():
        metric = SENSOR_TYPE_METRIC[sensor_type]
        unit = SENSOR_TYPE_UNITS[sensor_type]
        hass.states.set(
            f"sensor.{metric}",
            "unknown",
            {
                "name": label,
                "metric": metric,
                "unit": unit,
                "value": None,
            },
            fire_event=False,
        )

    _mqtt_client = EnergyMQTTClient(hass, broker, port)
    _mqtt_client.start()

    def on_stop(event_type: str, data: dict) -> None:
        if _mqtt_client:
            _mqtt_client.stop()

    hass.bus.listen(EVENT_STOP, on_stop)

    _LOGGER.info("Energy component set up (MQTT: %s:%d, %d entities)",
                  broker, port, len(SENSOR_TYPE_LABELS))
