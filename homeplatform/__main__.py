"""Entry point: python -m homeplatform"""

import logging
import os

import uvicorn

from homeplatform.api import create_app
from homeplatform.core import HomePlatform

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    config_dir = os.environ.get("HOME_CONFIG_DIR", "config")
    hass = HomePlatform(config_dir)

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    app = create_app(hass)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
