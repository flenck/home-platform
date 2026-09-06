# 95598 自动登录+抓数容器（基于 Playwright 官方镜像，已内置 Chromium）
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

RUN pip install --no-cache-dir opencv-python-headless paho-mqtt openai

COPY scripts/sgcc_server_auto.py /app/sgcc_server_auto.py

# 每日 07:00 执行（JOB_START_TIME 可覆盖）
COPY infra/run_daily.py /app/run_daily.py
CMD ["python", "-u", "/app/run_daily.py"]
