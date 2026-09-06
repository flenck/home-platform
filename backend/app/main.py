import asyncio
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.mqtt_client import set_event_loop, start_mqtt, stop_mqtt
from app.routers import readings


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_event_loop(asyncio.get_running_loop())
    start_mqtt()
    yield
    stop_mqtt()


app = FastAPI(title="Home Platform", lifespan=lifespan)

app.include_router(readings.router, prefix="/api/v1")


@app.middleware("http")
async def no_cache_static(request, call_next):
    # Static assets are mounted without cache headers; without this,
    # browsers apply heuristic caching and won't pick up frontend updates.
    # Root "/" serves index.html but its path has no extension, so handle it too.
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


static_dir = os.getenv("STATIC_DIR", "app/static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
