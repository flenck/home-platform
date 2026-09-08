#!/bin/bash
# captcha_watch 常驻守护：未运行则启动（用户手动登录验证码自动记录学习）
P=/home/dufan2514897261/sgcc
pgrep -f "venv_yolo/bin/python $P/captcha_watch.py" >/dev/null || \
  nohup $P/venv_yolo/bin/python $P/captcha_watch.py >> $P/captcha_watch.log 2>&1 &
