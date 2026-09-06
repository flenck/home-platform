#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# 全屋智能中枢 · Web 服务启动脚本
# 启动 homeplatform (FastAPI) 并托管 frontend/ 静态页面
# 默认端口 8000，可用环境变量覆盖
# ═══════════════════════════════════════════════════════════
set -e
cd "$(dirname "$0")/.."          # 切到项目根目录

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
STATIC_DIR="${STATIC_DIR:-frontend}"
HOME_CONFIG_DIR="${HOME_CONFIG_DIR:-config}"
LOG_FILE="${HOME}/homeplatform-web.log"

# 先停掉旧的（如果占用 8000）
if lsof -ti :"${PORT}" >/dev/null 2>&1; then
    echo "端口 ${PORT} 被占用，先停止旧进程..."
    lsof -ti :"${PORT}" | xargs kill 2>/dev/null || true
    sleep 1
fi

echo "▶ 启动 Home Platform → http://localhost:${PORT}"
echo "  静态目录: ${STATIC_DIR}   配置目录: ${HOME_CONFIG_DIR}"
echo "  日志: ${LOG_FILE}"

HOST="${HOST}" PORT="${PORT}" STATIC_DIR="${STATIC_DIR}" HOME_CONFIG_DIR="${HOME_CONFIG_DIR}" \
    nohup python3 -m homeplatform > "${LOG_FILE}" 2>&1 &

echo "  PID: $!  （查看日志: tail -f ${LOG_FILE}）"
