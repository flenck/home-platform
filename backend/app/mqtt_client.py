import asyncio
import json
import os

import paho.mqtt.client as mqtt

from app.database import insert_reading

BROKER = os.getenv("MQTT_BROKER_HOST", "localhost")
PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
TOPIC = os.getenv("MQTT_TOPIC", "home/sensors/+/+")

_client = None
_loop = None


def set_event_loop(loop):
    global _loop
    _loop = loop


def _on_connect(client, userdata, flags, rc):
    print(f"MQTT connected with code {rc}")
    client.subscribe(TOPIC)


def _on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        topic_parts = msg.topic.split("/")
        location = topic_parts[2] if len(topic_parts) > 2 else "unknown"
        metric = topic_parts[3] if len(topic_parts) > 3 else "unknown"

        sensor_id = payload.get("sensor_id", f"{location}_{metric}")
        value = float(payload["value"])
        unit = payload.get("unit", "ppm")
        raw_value = payload.get("raw")
        metadata = payload.get("metadata")

        if _loop is None:
            print("Event loop not set, dropping message")
            return

        asyncio.run_coroutine_threadsafe(
            insert_reading(
                sensor_id=sensor_id,
                location=location,
                metric=metric,
                value=value,
                unit=unit,
                raw_value=raw_value,
                metadata=metadata,
            ),
            _loop,
        )
    except Exception as e:
        print(f"Failed to process MQTT message: {e}")


def start_mqtt():
    global _client
    _client = mqtt.Client()
    _client.on_connect = _on_connect
    _client.on_message = _on_message
    _client.connect(BROKER, PORT, 60)
    _client.loop_start()


def stop_mqtt():
    if _client:
        _client.loop_stop()
        _client.disconnect()
