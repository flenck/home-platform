"""HomePlatform — the root object that composes bus, states, config, and loads components."""

import asyncio
import logging
from typing import Any

from .bus import EventBus
from .config import Config
from .const import EVENT_START, EVENT_STARTED, EVENT_STOP, DOMAIN_SENSOR, DOMAIN_WATER_QUALITY
from .loader import Integration, discover_components, load_component
from .states import StateMachine

_LOGGER = logging.getLogger(__name__)

# Module-level reference for API access
_hass_instance: "HomePlatform | None" = None


class HomePlatform:
    """Root application object. Owns the event bus, state machine, and component lifecycle."""

    def __init__(self, config_dir: str | None = None) -> None:
        self.bus = EventBus()
        self.states = StateMachine(self.bus)
        self.config = Config(config_dir)
        self.config.load()

        self._components: dict[str, Any] = {}
        self._integrations: dict[str, Integration] = {}
        self._running = False

    async def start(self) -> None:
        """Start the platform: load components in dependency order and fire startup events."""
        self.bus.fire(EVENT_START)

        self._integrations = discover_components()
        _LOGGER.info("Discovered %d components: %s",
                      len(self._integrations),
                      ", ".join(self._integrations.keys()))

        # Load components in dependency order
        loaded: set[str] = set()
        for domain in self._integration_load_order():
            await self._setup_component(domain, loaded)
            loaded.add(domain)

        self.bus.fire(EVENT_STARTED, {"components": list(loaded)})
        self._running = True
        _LOGGER.info("Home Platform started with %d components", len(loaded))

    async def stop(self) -> None:
        """Stop the platform and fire shutdown event."""
        self._running = False
        self.bus.fire(EVENT_STOP)
        _LOGGER.info("Home Platform stopped")

    async def _setup_component(self, domain: str, loaded: set[str]) -> None:
        """Load and set up a single component with its dependencies."""
        integration = self._integrations.get(domain)
        if integration is None:
            _LOGGER.error("Component %s not found", domain)
            return

        # Ensure dependencies are loaded first
        for dep in integration.dependencies:
            if dep not in loaded:
                await self._setup_component(dep, loaded)
                loaded.add(dep)

        try:
            module = load_component(integration)
        except Exception as e:
            _LOGGER.error("Failed to load component %s: %s", domain, e)
            return

        setup_fn = getattr(module, "async_setup", None)
        if setup_fn is None:
            _LOGGER.debug("Component %s has no async_setup, skipping", domain)
            return

        try:
            component_config = self.config.get(f"components.{domain}", {})
            await setup_fn(self, component_config)
            self._components[domain] = module
            _LOGGER.info("Component loaded: %s", domain)
        except Exception as e:
            _LOGGER.error("Error setting up component %s: %s", domain, e)

    def _integration_load_order(self) -> list[str]:
        """Return integrations sorted so entity-type components load first."""
        entity_domains = [d for d, i in self._integrations.items()
                          if i.integration_type == "entity"]
        device_domains = [d for d, i in self._integrations.items()
                          if i.integration_type != "entity"]
        return entity_domains + device_domains

    @property
    def running(self) -> bool:
        return self._running
