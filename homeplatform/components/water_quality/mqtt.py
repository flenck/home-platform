"""MQTT client for water quality sensor data."""

import asyncio
import json
import logging

import paho.mqtt.client as mqtt

from homeplatform.core import HomePlatform

_LOGGER = logging.getLogger(__name__)

WATER_QUALITY_EVENT = "water_quality_data"


class MQTTWaterClient:
    """Subscribes to water sensor MQTT topics and fires events on the bus."""

    def __init__(self, hass: HomePlatform, broker: str, port: int, topic: str) -> None:
        self._hass = hass
        self._loop = asyncio.get_running_loop()
        self._client = mqtt.Client()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._broker = broker
        self._port = port
        self._topic = topic

    def start(self) -> None:
        self._client.connect(self._broker, self._port, 60)
        self._client.loop_start()
        _LOGGER.info("MQTT subscriber started: %s:%s -> %s",
                      self._broker, self._port, self._topic)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc) -> None:
        _LOGGER.info("MQTT connected (rc=%d), subscribing to %s", rc, self._topic)
        client.subscribe(self._topic)

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode())
            topic_parts = msg.topic.split("/")
            location = topic_parts[2] if len(topic_parts) > 2 else "unknown"
            metric = topic_parts[3] if len(topic_parts) > 3 else "unknown"

            sensor_id = payload.get("sensor_id", f"{location}_{metric}")
            value = float(payload["value"])
            unit = payload.get("unit", "")
            raw_value = payload.get("raw")
            raw_adc = payload.get("raw_adc")
            raw_mv = payload.get("raw_mv")

            self._loop.call_soon_threadsafe(
                self._hass.bus.fire,
                WATER_QUALITY_EVENT,
                {
                    "sensor_id": sensor_id,
                    "location": location,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "raw": raw_value,
                    "raw_adc": raw_adc,
                    "raw_mv": raw_mv,
                },
            )
        except Exception as e:
            _LOGGER.error("Failed to process MQTT message: %s", e)
