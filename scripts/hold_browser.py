#!/usr/bin/env python3
"""手动介入模式：打开 95598 登录页，保持浏览器开启，供 VNC 手动登录/过验证码。
已做反自动化检测处理（隐藏 webdriver 指纹 + 去掉 enable-automation 横幅），
使 95598 网站不把该浏览器当自动化工具而拦截登录。
登录成功后会话写入 DATA_DIR/profile，后续自动任务(run_daily)可复用。
"""
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

LOGIN_URL = os.getenv("LOGIN_URL", "https://95598.cn/osgweb/login")
PHONE = os.getenv("PHONE_NUMBER", "")
PASSWORD = os.getenv("PASSWORD", "")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
PROFILE = DATA_DIR / "profile"

ANTI = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
}
"""


def main():
    PROFILE.mkdir(parents=True, exist_ok=True)
    print("手动介入模式启动（已隐藏自动化特征），浏览器保持打开（请通过 VNC 操作）", flush=True)
    with sync_playwright() as p:
        _extra = ["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-infobars"]
        _launch = {"user_data_dir": str(PROFILE), "headless": False,
                   "viewport": {"width": 1280, "height": 900}, "locale": "zh-CN",
                   "args": _extra,
                   "ignore_default_args": ["--enable-automation"]}
        _cp = os.getenv("CHROME_PATH", "")
        if _cp:
            _launch["executable_path"] = _cp
        ctx = p.chromium.launch_persistent_context(**_launch)
        page = ctx.new_page()
        page.add_init_script(ANTI)
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        # 尽力预填账号/密码，失败则手动输入
        try:
            filled = 0
            for inp in page.locator("input").all():
                t = (inp.get_attribute("type") or "").lower()
                ph = (inp.get_attribute("placeholder") or "")
                nm = (inp.get_attribute("name") or "").lower()
                if (t in ("text", "tel") or "手机" in ph or "账号" in ph or "phone" in nm or "account" in nm) and not inp.input_value():
                    inp.fill(PHONE); filled += 1
                elif (t == "password" or "密码" in ph or "pass" in nm) and not inp.input_value():
                    inp.fill(PASSWORD); filled += 1
            print(f"预填表单: {filled} 个输入框", flush=True)
        except Exception as e:
            print(f"预填表单异常(可手动输入): {e}", flush=True)
        print("页面已打开，请在 VNC 中手动登录/过滑块验证码（登录后浏览器保持开启，会话自动保存）", flush=True)
        while True:
            time.sleep(5)


main()
