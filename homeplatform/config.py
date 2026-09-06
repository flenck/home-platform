"""Configuration management via YAML file."""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from .const import CONF_DIR, CONF_MAIN_FILE

_LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "homeplatform": {
        "name": "我的家",
    },
    "mqtt": {
        "broker": "localhost",
        "port": 1883,
        "topic_prefix": "home/sensors",
    },
    "database": {
        "url": "postgresql+asyncpg://home:home123@localhost:5432/homeplatform",
    },
}


class Config:
    """Loads and provides access to configuration.yaml."""

    def __init__(self, config_dir: str | None = None) -> None:
        if config_dir is None:
            config_dir = os.environ.get("HOME_CONFIG_DIR", CONF_DIR)
        self._config_dir = Path(config_dir)
        self._data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        """Load configuration from YAML file, falling back to defaults."""
        config_file = self._config_dir / CONF_MAIN_FILE

        self._data = dict(DEFAULT_CONFIG)

        if config_file.exists():
            try:
                with open(config_file, encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
                self._deep_merge(self._data, user_config)
                _LOGGER.info("Loaded config from %s", config_file)
            except Exception as e:
                _LOGGER.warning("Failed to load %s: %s", config_file, e)
        else:
            _LOGGER.info("No config file at %s, using defaults", config_file)

        return self._data

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        """Merge override dict into base dict in place."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Config._deep_merge(base[key], value)
            else:
                base[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by dot-separated key (e.g. 'mqtt.broker')."""
        parts = key.split(".")
        node: Any = self._data
        for part in parts:
            if isinstance(node, dict):
                node = node.get(part)
                if node is None:
                    return default
            else:
                return default
        return node

    @property
    def data(self) -> dict[str, Any]:
        return self._data
