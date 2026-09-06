"""Constants for Home Platform."""

# Lifecycle events
EVENT_START = "start"
EVENT_STARTED = "started"
EVENT_STOP = "stop"
EVENT_STATE_CHANGED = "state_changed"

# Entity state values
STATE_OK = "ok"
STATE_PROBLEM = "problem"
STATE_UNKNOWN = "unknown"

# Configuration
CONF_DIR = "config"
CONF_MAIN_FILE = "configuration.yaml"

# MQTT topic convention (kept from existing project for backward compatibility)
MQTT_TOPIC_PREFIX = "home/sensors"

# Component domains
DOMAIN_SENSOR = "sensor"
DOMAIN_WATER_QUALITY = "water_quality"
DOMAIN_ENERGY = "energy"

# HA-compatible API
DEFAULT_API_TOKEN = "homeplatform"
