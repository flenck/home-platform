#!/usr/bin/env python3
"""验证码监听学习模式：用户正常登录，系统旁观记录验证码截图+点击坐标（不干扰登录）。

用法: python3 captcha_watch.py
输出: ~/sgcc/captcha_samples/learned/watch_YYYYMMDD_HHMMSS.{jpg,json}
"""
import base64, json, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

import websocket
from PIL import Image
from io import BytesIO

OUT_DIR = Path("/home/dufan2514897261/sgcc/captcha_samples/learned")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CDP_HTTP = "http://127.0.0.1:9227"
WATCH_TIMEOUT = 600  # 最长监听 10 分钟


def get_ws_url():
    with urllib.request.urlopen(CDP_HTTP + "/json/list", timeout=6) as r:
        pages = json.loads(r.read())
    for p in pages:
        if p.get("type") == "page":
            return p["webSocketDebuggerUrl"]
    return None


def cdp(ws_url, method, params=None, timeout=30):
    ws = websocket.create_connection(ws_url, timeout=timeout)
    ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            ws.close()
            return msg


def ev(ws_url, expression, timeout=30):
    r = cdp(ws_url, "Runtime.evaluate",
            {"expression": expression, "returnByValue": True}, timeout)
    return r.get("result", {}).get("result", {}).get("value")


def screenshot_b64(ws_url):
    ws = websocket.create_connection(ws_url, timeout=30)
    ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            data = msg["result"]["data"]
            ws.close()
            return base64.b64decode(data)
    ws.close()
    return None


def is_logged_in(ws_url):
    try:
        text = ev(ws_url, "document.body ? document.body.innerText : ''") or ""
        return ("退出" in text or "用电户号" in text or "我的95598" in text) and "账号密码或验证码登录" not in text
    except Exception:
        return False


def inject_listener(ws_url):
    r = ev(ws_url, """
    (() => {
        if (window.__watch_clicks) return 'already';
        window.__watch_clicks = [];
        document.addEventListener('click', function(e) {
            window.__watch_clicks.push({x: Math.round(e.clientX), y: Math.round(e.clientY), time: Date.now()});
        }, true);
        return 'listener on document';
    })()
    """)
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[" + now + "] 点击监听: " + str(r), flush=True)


def get_captcha_elems(ws_url):
    return ev(ws_url, """
    (() => {
        const out = {big: null, strip: null};
        const all = document.querySelectorAll('*');
        for (const el of all) {
            const bg = window.getComputedStyle(el).backgroundImage;
            if (bg && bg.includes('turing.captcha.qcloud.com')) {
                const r = el.getBoundingClientRect();
                if (r.width > 100 && r.height > 100) {
                    out.big = {x: r.x, y: r.y, w: r.width, h: r.height};
                    break;
                }
            }
        }
        const imgs = document.querySelectorAll('img');
        for (const img of imgs) {
            if (img.src && img.src.includes('img_index')) {
                const r = img.getBoundingClientRect();
                out.strip = {x: r.x, y: r.y, w: r.width, h: r.height};
                break;
            }
        }
        return out;
    })()
    """)


def capture_composite(ws_url, elems):
    """截图验证码复合图（答案条+大图），返回 (路径, 元信息) 或 None。"""
    big = elems.get("big")
    strip = elems.get("strip")
    if not big:
        return None, None
    if strip:
        top = min(strip["y"], big["y"]); bottom = max(strip["y"] + strip["h"], big["y"] + big["h"])
        left = min(strip["x"], big["x"]); right = max(strip["x"] + strip["w"], big["x"] + big["w"])
    else:
        top = max(0, big["y"] - 85); bottom = big["y"] + big["h"]
        left = big["x"]; right = big["x"] + big["w"]
    pad = 5
    left = max(0, int(left - pad)); top = max(0, int(top - pad))
    right = int(right + pad); bottom = int(bottom + pad)

    png = screenshot_b64(ws_url)
    if not png:
        return None, None
    img = Image.open(BytesIO(png))
    composite = img.crop((left, top, right, bottom))
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = OUT_DIR / ("watch_" + now + ".jpg")
    composite.convert("RGB").save(img_path, quality=95)
    return img_path, {"crop": [left, top, right, bottom], "big": big, "strip": strip}


def now_ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def main():
    """常驻监听：循环检测登录成功/验证码出现，每次用户手动登录都自动记录样本。"""
    print("[" + now_ts() + "] 常驻验证码监听启动", flush=True)
    while True:
        try:
            ws_url = get_ws_url()
            if not ws_url:
                print("[" + now_ts() + "] CDP 连接失败（Chrome 9227 不可用），5s 后重试", flush=True)
                time.sleep(5)
                continue

            inject_listener(ws_url)
            captcha_meta = None
            start = time.time()
            captcha_shown = False
            logged_in = False

            while time.time() - start < WATCH_TIMEOUT:
                if is_logged_in(ws_url):
                    logged_in = True
                    print("[" + now_ts() + "] 检测到登录成功 ✅", flush=True)
                    break
                elems = get_captcha_elems(ws_url)
                if elems and elems.get("big") and not captcha_shown:
                    captcha_shown = True
                    print("[" + now_ts() + "] 验证码出现：big=" + str(elems["big"]) + " strip=" + str(elems.get("strip")), flush=True)
                    img_path, captcha_meta = capture_composite(ws_url, elems)
                    if img_path:
                        print("[" + now_ts() + "] 复合图已保存: " + img_path.name, flush=True)
                time.sleep(2)

            # 收集点击并保存样本（无论是否登录成功，只要有验证码区点击就记录）
            clicks = ev(ws_url, "JSON.stringify(window.__watch_clicks || [])")
            clicks = json.loads(clicks) if clicks else []
            if clicks and captcha_meta:
                save_annotation(clicks, captcha_meta)
            elif captcha_shown and not clicks:
                print("[" + now_ts() + "] 检测到验证码但未捕获到点击", flush=True)

            if logged_in:
                time.sleep(10)  # 登录完成后短暂休息再继续监听
            else:
                print("[" + now_ts() + "] 监听超时（" + str(WATCH_TIMEOUT) + "s），重新开始监听", flush=True)
                time.sleep(5)
        except Exception as e:
            print("[" + now_ts() + "] 监听异常: " + str(e) + "，5s 后重试", flush=True)
            time.sleep(5)


def save_annotation(clicks, captcha_meta):
    """过滤验证码区域点击、去重、保存标注 JSON。"""
    crop = captcha_meta["crop"]
    in_zone = [c for c in clicks if crop[0] <= c["x"] <= crop[2] and crop[1] <= c["y"] <= crop[3]]
    if not in_zone:
        print("[" + now_ts() + "] 无验证码区域内点击（总点击 " + str(len(clicks)) + " 个）", flush=True)
        return

    seen = set(); uniq = []
    for c in in_zone:
        key = (c["x"], c["y"])
        if key not in seen:
            seen.add(key); uniq.append(c)
    uniq.sort(key=lambda c: c["time"])

    annotation = {
        "timestamp": now_ts(),
        "source": "captcha_watch_user_login",
        "clicks_count": len(uniq),
        "clicks": [{
            "order": i,
            "viewport_xy": [c["x"], c["y"]],
            "composite_xy": [c["x"] - captcha_meta["crop"][0], c["y"] - captcha_meta["crop"][1]],
            "big_image_xy": [c["x"] - captcha_meta["big"]["x"], c["y"] - captcha_meta["big"]["y"]],
        } for i, c in enumerate(uniq)],
        "crop": captcha_meta["crop"],
        "big_bbox": captcha_meta["big"],
    }
    json_path = OUT_DIR / ("watch_" + now_ts() + ".json")
    json_path.write_text(json.dumps(annotation, indent=2, ensure_ascii=False))
    print("[" + now_ts() + "] 样本已保存: " + json_path.name + "（验证码区内 " + str(len(uniq)) + " 个点击）", flush=True)
    for i, c in enumerate(uniq):
        a = annotation["clicks"][i]["composite_xy"]
        print("    点击" + str(i + 1) + ": 复合图=(" + str(a[0]) + "," + str(a[1]) + ")", flush=True)


if __name__ == "__main__":
    main()
