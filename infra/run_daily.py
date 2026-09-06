#!/usr/bin/env python3
"""容器入口：按 JOB_START_TIME 每日执行一次 sgcc_server_auto.py（纯标准库）。
启动时先跑一次（便于首次部署立即验证），之后按计划时间运行。
"""
import os
import subprocess
import time
from datetime import datetime

JOB_START = os.getenv("JOB_START_TIME", "07:00")


def run_job():
    print(f"=== 开始执行 95598 任务 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
    subprocess.run(["python", "-u", "/app/sgcc_server_auto.py"], check=False)
    print("=== 任务执行结束 ===", flush=True)


print(f"调度器启动，每日 {JOB_START} 执行（首次先立即执行一次）", flush=True)
run_job()  # 首次立即执行（部署后马上验证）
last_run_date = datetime.now().date()

while True:
    now = datetime.now()
    if now.strftime("%H:%M") == JOB_START and now.date() != last_run_date:
        run_job()
        last_run_date = now.date()
    time.sleep(20)
