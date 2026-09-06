# 全屋智能家居平台 · Home Platform

基于 ESP32-S3 的全屋智能家居系统，采用 Home Assistant 风格的组件化架构。当前支持水质监测（TDS 传感器），可扩展温度、湿度、照明、能耗等其他模块。

## 架构

```
ESP32 固件 ──MQTT──> Mosquitto ──> Home Platform Core ──> TimescaleDB
                                      │
                                      ├── 事件总线 (EventBus)
                                      ├── 状态机 (StateMachine)
                                      ├── 组件加载器 (Loader)
                                      └── REST API ──> 前端仪表盘
```

### 组件化设计 (参考 Home Assistant)

```
homeplatform/
├── core.py           # 根对象 (事件总线 + 状态机 + 生命周期)
├── bus.py            # 事件总线 (解耦组件通信)
├── states.py         # 状态机 (管理所有设备状态)
├── loader.py         # 组件扫描 & 懒加载
├── config.py         # YAML 配置管理
├── database.py       # TimescaleDB 持久化
├── api.py            # FastAPI REST 接口
├── components/
│   ├── sensor/       # 传感器实体基类
│   └── water_quality/ # 水质监测组件 (TDS)
│       ├── manifest.json
│       ├── __init__.py
│       ├── mqtt.py
│       └── sensor.py
```

每个组件 = `manifest.json` + `__init__.py`（暴露 `async_setup(hass, config)`），通过事件总线解耦通信。

## 硬件接线

TDS 传感器 → ESP32-S3：

| TDS 传感器 | ESP32-S3 |
|-----------|----------|
| + (VCC)   | 3.3V     |
| - (GND)   | GND      |
| A (Signal)| GPIO2    |

## 快速开始

### 1. 配置

编辑 `config/configuration.yaml`：

```yaml
mqtt:
  broker: "你的飞牛服务器IP"
  port: 1883

database:
  url: "postgresql+asyncpg://home:home123@localhost:5432/homeplatform"
```

### 2. 本地开发

```bash
# 安装依赖
pip install -e .

# 启动（自动加载所有组件）
python -m homeplatform
```

访问 `http://localhost:8000`。

### 3. 编译并烧录 ESP32 固件

```bash
cd firmware/tds-sensor
# 编辑 include/config.h 设置 WiFi 和 MQTT 服务器地址
pio run --target upload
pio device monitor
```

### 4. Docker 部署

```bash
cd infra
cp .env.example .env
# 编辑 .env 设置密码
docker compose up -d
```

## 添加新组件

在 `homeplatform/components/` 下创建新目录：

```
homeplatform/components/temperature/
├── manifest.json     # {"domain": "temperature", "name": "...", "dependencies": ["sensor"]}
└── __init__.py       # async def async_setup(hass, config): ...
```

组件通过 `hass.bus.listen(event, callback)` 监听事件，通过 `hass.states.set(entity_id, state, attrs)` 更新状态。前端自动发现并渲染所有实体。

## MQTT Topic 规范

```
home/sensors/{location}/{metric}
```

- `home/sensors/water/tds` — 水质 TDS
- `home/sensors/livingroom/temperature` — 客厅温度（未来）
- `home/sensors/kitchen/humidity` — 厨房湿度（未来）

## API 接口

| 端点 | 说明 |
|------|------|
| `GET /api/v1/readings/latest` | 最新读数 |
| `GET /api/v1/readings?limit=100` | 历史读数 |
| `GET /api/v1/states` | 所有实体当前状态 |

## 扩展方向

- 温度/湿度传感器
- 照明控制 (开关、调光)
- 安防传感器 (门磁、人体红外)
- 能耗监测 (智能插座)
- 接入 Home Assistant

## 技术栈

- 固件：PlatformIO + Arduino (ESP32-S3)
- 后端框架：Home Platform Core (事件总线 + 组件化)
- Web 框架：FastAPI + uvicorn
- 数据库：PostgreSQL + TimescaleDB
- 消息代理：Eclipse Mosquitto
- 前端：原生 HTML/JS + Chart.js
- 部署：Docker Compose
