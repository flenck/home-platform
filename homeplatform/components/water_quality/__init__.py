"""Water quality component — integrates TDS sensor via MQTT."""

import logging

from homeplatform.core import HomePlatform
from homeplatform.const import EVENT_STOP

from .mqtt import MQTTWaterClient, WATER_QUALITY_EVENT
from .sensor import TDSSensor

_LOGGER = logging.getLogger(__name__)

_mqtt_client: MQTTWaterClient | None = None


async def async_setup(hass: HomePlatform, config: dict) -> None:
    """Set up the water quality component."""
    global _mqtt_client

    mqtt_config = hass.config.get("mqtt", {})
    broker = config.get("broker", mqtt_config.get("broker", "localhost"))
    port = config.get("port", mqtt_config.get("port", 1883))
    topic = config.get(
        "topic",
        f"{mqtt_config.get('topic_prefix', 'home/sensors')}/+/+",
    )

    tds_sensor = TDSSensor()
    calibration = float(config.get("calibration", 1.0))

    def on_water_data(event_type: str, data: dict) -> None:
        metric = data.get("metric", "")
        if metric != "tds":
            return

        raw_value = data["value"]
        value = round(raw_value * calibration * 10) / 10.0
        tds_sensor.set_value(value)

        hass.states.set(
            tds_sensor.entity_id,
            tds_sensor.state,
            {
                "metric": "tds",
                "value": value,
                "unit": data.get("unit", "ppm"),
                "name": tds_sensor.name,
                "status_label": tds_sensor.status_label(),
                "status_class": tds_sensor.status_class(),
                "sensor_id": data.get("sensor_id"),
                "location": data.get("location"),
                "raw_adc": data.get("raw_adc"),
                "raw_mv": data.get("raw_mv"),
            },
        )

    hass.bus.listen(WATER_QUALITY_EVENT, on_water_data)

    _mqtt_client = MQTTWaterClient(hass, broker, port, topic)
    _mqtt_client.start()

    def on_stop(event_type: str, data: dict) -> None:
        if _mqtt_client:
            _mqtt_client.stop()

    hass.bus.listen(EVENT_STOP, on_stop)

    _LOGGER.info("Water quality component set up (MQTT: %s:%d)", broker, port)
