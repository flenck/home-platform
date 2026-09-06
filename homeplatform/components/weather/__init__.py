"""天气组件 — 支持多个免费天气 API，定时获取本地天气。

Provider:
  - open-meteo (默认): 免费开源，无需 Key，用经纬度
  - qweather (和风天气): 需要 API Key + LocationID，返回中文天气描述

配置 (config/*.yaml):
  components:
    weather:
      enabled: true
      provider: qweather          # qweather | open-meteo
      name: "隆化"
      interval_sec: 600
      # open-meteo 用坐标
      latitude: 41.31167
      longitude: 117.725
      # qweather 用 Key + LocationID
      qweather_key: "xxxx"
      qweather_location: "101090413"

天气数据写入状态机后由 recorder 自动持久化（温度/湿度/风速）。
"""

import asyncio
import json
import logging
import urllib.request

from homeplatform.const import EVENT_STOP
from homeplatform.core import HomePlatform

_LOGGER = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# QWeather 新版: 每个凭据有专属 API Host（形如 xxx.qweatherapi.com），
# 旧公共域名 api/devapi/geoapi.qweather.com 2026 年起逐步停用 → 403 Invalid Host
QWEATHER_DEFAULT_HOST = "devapi.qweather.com"

# WMO weather codes → 中文描述 (Open-Meteo)
WMO_CODES = {
    0: "晴", 1: "基本晴朗", 2: "多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}

# 默认位置：隆化县, 承德市, 河北
DEFAULT_LOCATION = {"latitude": 41.31167, "longitude": 117.725, "name": "隆化"}
DEFAULT_INTERVAL = 600

SENSORS = [
    ("outdoor_temperature", "temperature", "室外温度", "°C"),
    ("outdoor_humidity", "humidity", "室外湿度", "%"),
    ("outdoor_wind_speed", "wind_speed", "室外风速", "km/h"),
    ("weather_condition", "weather", "天气状况", ""),
]

SENSOR_ID = "weather-open-meteo"


def _http_get(url: str, headers: dict | None = None) -> dict:
    import gzip

    req = urllib.request.Request(
        url,
        headers=headers or {
            "User-Agent": "HomePlatform/1.0",
            "Accept-Encoding": "gzip",   # 和风返回 gzip 压缩
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
        return json.loads(data.decode("utf-8"))


def _fetch_open_meteo(lat: float, lon: float) -> dict:
    url = (
        f"{OPEN_METEO_URL}?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
        "&timezone=auto&wind_speed_unit=kmh"
    )
    data = _http_get(url)
    cur = data.get("current", {})
    cond = WMO_CODES.get(cur.get("weather_code"), f"代码{cur.get('weather_code')}")
    return {
        "temp": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "condition": cond,
        "source": "open-meteo",
    }


def _fetch_qweather(api_key: str, location: str, host: str) -> dict:
    url = f"https://{host}/v7/weather/now?location={location}&key={api_key}"
    data = _http_get(url)
    code = data.get("code")
    if code != "200" or "now" not in data:
        _LOGGER.warning("QWeather error code=%s detail=%s", code, str(data.get("error", ""))[:200])
        raise RuntimeError(f"QWeather API error code={code}")
    now = data["now"]
    return {
        "temp": _to_float(now.get("temp")),
        "humidity": _to_float(now.get("humidity")),
        "wind": _to_float(now.get("windSpeed")),
        "condition": now.get("text", "未知"),
        "source": "qweather",
    }


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def async_setup(hass: HomePlatform, config: dict) -> None:
    """设置天气组件：注册实体 + 定时拉取。"""
    provider = str(config.get("provider", "open-meteo")).lower()
    loc = {
        "latitude": config.get("latitude", DEFAULT_LOCATION["latitude"]),
        "longitude": config.get("longitude", DEFAULT_LOCATION["longitude"]),
        "name": config.get("name", DEFAULT_LOCATION["name"]),
    }
    qw_key = config.get("qweather_key", "")
    qw_loc = config.get("qweather_location", "")
    qw_host = config.get("qweather_host", QWEATHER_DEFAULT_HOST)
    if provider == "qweather" and (not qw_key or not qw_loc):
        _LOGGER.error("QWeather provider needs qweather_key and qweather_location")
        return
    interval = max(60, float(config.get("interval_sec", DEFAULT_INTERVAL)))

    # 注册占位实体
    for suffix, metric, label, unit in SENSORS:
        hass.states.set(
            f"sensor.{suffix}",
            "unknown",
            {
                "metric": metric, "unit": unit, "value": None, "name": label,
                "sensor_id": SENSOR_ID, "location": loc["name"],
            },
            fire_event=False,
        )

    running = True
    task: asyncio.Task | None = None

    async def update() -> None:
        try:
            if provider == "qweather":
                w = await asyncio.to_thread(_fetch_qweather, qw_key, qw_loc, qw_host)
            else:
                w = await asyncio.to_thread(_fetch_open_meteo, loc["latitude"], loc["longitude"])
        except Exception as e:
            _LOGGER.warning("Weather fetch failed (%s): %s", provider, e)
            return

        common = {"sensor_id": SENSOR_ID, "location": loc["name"], "name": f"{loc['name']}天气"}
        if w.get("temp") is not None:
            hass.states.set("sensor.outdoor_temperature", f"{w['temp']:.1f}", {
                **common, "metric": "temperature", "unit": "°C",
                "value": round(w["temp"], 1), "condition": w.get("condition"),
            })
        if w.get("humidity") is not None:
            hass.states.set("sensor.outdoor_humidity", f"{w['humidity']:.0f}", {
                **common, "metric": "humidity", "unit": "%",
                "value": round(w["humidity"], 0),
            })
        if w.get("wind") is not None:
            hass.states.set("sensor.outdoor_wind_speed", f"{w['wind']:.1f}", {
                **common, "metric": "wind_speed", "unit": "km/h",
                "value": round(w["wind"], 1),
            })
        hass.states.set("sensor.weather_condition", w.get("condition", "未知"), {
            **common, "metric": "weather", "unit": "", "value": None,
            "temperature": w.get("temp"), "humidity": w.get("humidity"),
            "wind_speed": w.get("wind"), "source": w.get("source"),
        })
        _LOGGER.info("Weather updated (%s/%s): %s, %.1f°C, 湿度%s%%",
                      provider, loc["name"], w.get("condition"), w.get("temp") or 0, w.get("humidity") or 0)

    async def loop() -> None:
        await update()
        while running:
            await asyncio.sleep(interval)
            await update()

    task = asyncio.create_task(loop())

    def on_stop(event_type: str, data: dict) -> None:
        nonlocal running
        running = False
        if task:
            task.cancel()

    hass.bus.listen(EVENT_STOP, on_stop)
    _LOGGER.info("Weather component set up: provider=%s location=%s interval=%ss",
                  provider, loc["name"], interval)
