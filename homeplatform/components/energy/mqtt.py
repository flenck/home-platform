"""MQTT client for 95598-mqtt electricity data."""

import asyncio
import logging

import paho.mqtt.client as mqtt

from homeplatform.core import HomePlatform

_LOGGER = logging.getLogger(__name__)

# Topic: 95598/{user_id}/{sensor_type}/state
ENERGY_MQTT_TOPIC = "95598/+/+/state"

SENSOR_TYPE_LABELS = {
    "balance": "电费余额",
    "last_daily_usage": "昨日用电量",
    "yearly_usage": "年用电量",
    "yearly_charge": "年电费",
    "month_usage": "月用电量",
    "month_charge": "月电费",
}

SENSOR_TYPE_UNITS = {
    "balance": "元",
    "last_daily_usage": "kWh",
    "yearly_usage": "kWh",
    "yearly_charge": "元",
    "month_usage": "kWh",
    "month_charge": "元",
}

# Map 95598 sensor_type → internal metric name
SENSOR_TYPE_METRIC = {
    "balance": "balance",
    "last_daily_usage": "daily_usage",
    "yearly_usage": "yearly_usage",
    "yearly_charge": "yearly_charge",
    "month_usage": "monthly_usage",
    "month_charge": "monthly_charge",
}


class EnergyMQTTClient:
    """Subscribes to 95598-mqtt topics and updates StateMachine."""

    def __init__(self, hass: HomePlatform, broker: str, port: int) -> None:
        self._hass = hass
        self._loop = asyncio.get_running_loop()
        self._client = mqtt.Client()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._broker = broker
        self._port = port

    def start(self) -> None:
        self._client.connect(self._broker, self._port, 60)
        self._client.loop_start()
        _LOGGER.info("Energy MQTT subscriber started: %s:%d", self._broker, self._port)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc) -> None:
        _LOGGER.info("Energy MQTT connected (rc=%d), subscribing to %s", rc, ENERGY_MQTT_TOPIC)
        client.subscribe(ENERGY_MQTT_TOPIC)

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            topic_parts = msg.topic.split("/")
            if len(topic_parts) < 4 or topic_parts[-1] != "state":
                return

            sensor_type = topic_parts[-2]
            user_id = topic_parts[-3]

            if sensor_type not in SENSOR_TYPE_LABELS:
                return

            raw_value = msg.payload.decode().strip()
            try:
                value = float(raw_value)
            except ValueError:
                _LOGGER.warning("Non-numeric energy value: %s", raw_value)
                return

            metric = SENSOR_TYPE_METRIC[sensor_type]
            unit = SENSOR_TYPE_UNITS[sensor_type]
            entity_id = f"sensor.{metric}"

            self._loop.call_soon_threadsafe(
                self._hass.states.set,
                entity_id,
                str(value),
                {
                    "name": SENSOR_TYPE_LABELS[sensor_type],
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "user_id": user_id,
                    "sensor_id": f"95598_{user_id}_{sensor_type}",
                    "location": "energy",
                },
            )
            _LOGGER.debug("Energy %s = %s %s", sensor_type, value, unit)

        except Exception as e:
            _LOGGER.error("Failed to process energy MQTT message: %s", e)
