"""Component loader — scans components/ directory and loads integrations."""

import importlib
import json
import logging
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

COMPONENTS_DIR = Path(__file__).parent / "components"


class Integration:
    """Metadata for a loaded component."""

    def __init__(self, domain: str, path: Path, manifest: dict[str, Any]) -> None:
        self.domain = domain
        self.path = path
        self.manifest = manifest

    @property
    def name(self) -> str:
        return self.manifest.get("name", self.domain)

    @property
    def dependencies(self) -> list[str]:
        return self.manifest.get("dependencies", [])

    @property
    def integration_type(self) -> str:
        return self.manifest.get("integration_type", "device")


def discover_components() -> dict[str, Integration]:
    """Scan the components directory and return discovered integrations."""
    integrations: dict[str, Integration] = {}

    if not COMPONENTS_DIR.exists():
        return integrations

    for item in sorted(COMPONENTS_DIR.iterdir()):
        if not item.is_dir() or item.name.startswith("_") or item.name.startswith("."):
            continue

        manifest_file = item / "manifest.json"
        if not manifest_file.exists():
            continue

        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            _LOGGER.warning("Invalid manifest in %s: %s", item.name, e)
            continue

        domain = manifest.get("domain", item.name)
        integrations[domain] = Integration(domain, item, manifest)
        _LOGGER.debug("Discovered component: %s (%s)", domain, manifest.get("name"))

    return integrations


def load_component(integration: Integration) -> Any:
    """Import and return a component's Python module."""
    module_path = f"homeplatform.components.{integration.domain}"
    return importlib.import_module(module_path)
