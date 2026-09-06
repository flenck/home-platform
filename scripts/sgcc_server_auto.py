#!/usr/bin/env python3
"""95598 全自动登录 + 抓数 + MQTT 发布（服务器 Docker 版，无人工介入）。

流程：
  1. 打开持久化会话（/data/profile）→ 会话有效则跳过登录
  2. 会话失效 → 自动登录：填账号密码 → 触发验证码
  3. 验证码求解：答案条切片 + 模板匹配 + VLM 验证 → 按序点击
     → 标记校验（图像差分）→ 全部命中后点"确定"
  4. 抓取账单/余额数据 → MQTT 发布（topic: 95598/{user_id}/{type}/state）
  5. 失败控制：登录最多尝试 2 次，失败即退出（避免触发风控）

环境变量：PHONE_NUMBER, PASSWORD, VLM_API_KEY, MQTT_BROKER, MQTT_PORT,
          USER_ID, DATA_DIR（会话目录，默认 /data）
"""
import base64
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import numpy as np
from cv_lite import rgb2gray, resize as _resize, match_template, connected_components
from PIL import Image

PHONE = os.getenv("PHONE_NUMBER", "")
PASSWORD = os.getenv("PASSWORD", "")
VLM_API_KEY = os.getenv("VLM_API_KEY", "")
VLM_BASE = os.getenv("VLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
VLM_MODEL = os.getenv("VLM_MODEL", "glm-4v-flash")
MQTT_BROKER = os.getenv("MQTT_BROKER", "broker")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
USER_ID = os.getenv("USER_ID", "0000")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))

PROFILE = DATA_DIR / "profile"
LOGIN_URL = "https://95598.cn/osgweb/login"
BILL_URL = "https://95598.cn/osgweb/electricityCharge"
BALANCE_URL = "https://95598.cn/osgweb/userAcc"

BIG_BBOX = (475, 298, 805, 534)
STRIP_BBOX = (565, 253, 680, 292)
MAX_LOGIN_ATTEMPTS = 2

ANTI = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    // 伪装为 Mac 平台（UA 已是 Mac，platform 必须一致，否则反爬识别）
    Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
    Object.defineProperty(navigator, 'oscpu', { get: () => 'Intel Mac OS X 10_15_7' });
    try {
        if (navigator.userAgentData) {
            Object.defineProperty(navigator, 'userAgentData', { get: () => ({
                brands: [{brand: 'Google Chrome', version: '151'}, {brand: 'Chromium', version: '151'}, {brand: 'Not_A Brand', version: '24'}],
                mobile: false, platform: 'macOS',
            })});
        }
    } catch (e) {}
}
"""


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def is_logged_in(page) -> bool:
    try:
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
        return any(k in text for k in ["电费余额", "用电量", "电费账单", "账户余额", "年电量", "本期电量"])
    except Exception:
        return False


# ── VLM ──────────────────────────────────────────────────────────────
def vlm_b64(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def vlm_identify(img: Image.Image) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE)
    resp = client.chat.completions.create(
        model=VLM_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "验证码答案条里的小图，用2-4个字描述内容（如：数字5、房子、笑脸、雨伞、箭头、地图标记）"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{vlm_b64(img)}"}},
        ]}],
    )
    return resp.choices[0].message.content.strip().replace("\n", " ")[:20]


def vlm_locate(big: Image.Image, thumb: Image.Image, expect: str) -> list:
    """让 VLM 直接在大图中定位目标，返回 (cx,cy) 候选（672x480 坐标）。"""
    from openai import OpenAI
    out = []
    try:
        client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE)
        resp = client.chat.completions.create(
            model=VLM_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "第一张是目标小图，第二张是大图（宽672高480）。在大图中找到与目标小图相同的对象，给出中心坐标。只输出JSON：{\"found\": [{\"cx\": 整数, \"cy\": 整数}]}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{vlm_b64(thumb)}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{vlm_b64(big)}"}},
            ]}],
            response_format={"type": "json_object"},
        )
        r = json.loads(resp.choices[0].message.content)
        for f in r.get("found", []):
            out.append((int(f["cx"]), int(f["cy"])))
    except Exception as e:
        log(f"  vlm_locate 失败: {e}")
    return out


def analyze_captcha(strip_pil: Image.Image, big_pil: Image.Image) -> list:
    """返回点击计划 [(page_x, page_y, name)]；失败返回 []。"""
    big_pil = big_pil.resize((672, 480), Image.LANCZOS)
    strip = np.array(strip_pil)
    big = np.array(big_pil)
    big_g = rgb2gray(big)

    # 答案条切片
    g = rgb2gray(strip)
    colvar = g.var(axis=0)
    content = colvar > 5
    runs, in_run = [], False
    for i, c in enumerate(content):
        if c and not in_run:
            start = i; in_run = True
        elif not c and in_run:
            runs.append((start, i)); in_run = False
    if in_run:
        runs.append((start, len(content)))
    groups = []
    for r in runs:
        if groups and r[0] - groups[-1][1] <= 4:
            groups[-1] = (groups[-1][0], r[1])
        else:
            groups.append(r)
    if len(groups) < 2:
        log(f"答案条分块异常: {groups}")
        return []

    clicks = []
    for i, (a, b) in enumerate(groups):
        t = strip[:, a:b]
        t_pil = Image.fromarray(t)
        name = vlm_identify(t_pil.resize(((b - a) * 6, 50 * 6), Image.LANCZOS))
        t_g = rgb2gray(t)

        # 候选池：模板匹配 top3 + VLM 定位
        cands = template_matches(t_g, big_g)
        vlm_cands = vlm_locate(big_pil, t_pil, name)
        log(f"  目标{i} {name}: TM={[(round(m,2),c) for m,_,c in [(c[0],0,c[1:]) for c in cands]]} VLM={vlm_cands}")

        pool = {}
        for mx, cx, cy in cands:
            pool.setdefault((cx // 20, cy // 20), []).append(("tm", mx, cx, cy))
        for cx, cy in vlm_cands:
            pool.setdefault((cx // 20, cy // 20), []).append(("vlm", 0.5, cx, cy))

        # 按确认强度排序：TM高分 + VLM 命中
        ranked = []
        for key, items in pool.items():
            cx = sum(it[2] for it in items) // len(items)
            cy = sum(it[3] for it in items) // len(items)
            has_vlm = any(it[0] == "vlm" for it in items)
            tm_max = max((it[1] for it in items if it[0] == "tm"), default=0)
            ranked.append((has_vlm, tm_max, cx, cy, key))
        ranked.sort(key=lambda r: (-r[0], -r[1]))

        chosen = None
        for has_vlm, tm_max, cx, cy, key in ranked:
            crop = big_pil.crop((max(0, cx - 42), max(0, cy - 42),
                                 min(672, cx + 42), min(480, cy + 42)))
            crop = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
            verified = vlm_verify(crop, name)
            if verified or tm_max >= 0.85 or has_vlm:
                chosen = (cx, cy, tm_max)
                log(f"  目标{i} 选定 ({cx},{cy}) TM={tm_max:.2f} VLM={has_vlm} 验证={verified}")
                break
        if chosen is None:
            mx, cx, cy = cands[0]
            if mx < 0.55:
                log(f"  目标{i}({name}) 无可靠位置，放弃本次")
                return []
            chosen = (cx, cy, mx)
            log(f"  目标{i} 保底 ({cx},{cy}) TM={mx:.2f}")
        cx, cy, mx = chosen
        px = round(475 + cx * 330 / 672)
        py = round(298 + cy * 236 / 480)
        clicks.append((px, py, f"{name}({mx:.2f})"))
    return clicks


def vlm_verify(crop: Image.Image, expect: str) -> bool:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE)
        resp = client.chat.completions.create(
            model=VLM_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": f"这张裁剪图中心是什么？我要找：{expect}。只输出JSON：{{\"match\": true/false}}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{vlm_b64(crop)}"}},
            ]}],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content).get("match", False)
    except Exception:
        return False


# ── 模板匹配 ─────────────────────────────────────────────────────────
def template_matches(tpl_g, big_g, top_n=3):
    cands = []
    for scale in np.arange(0.9, 4.2, 0.15):
        tw, th = max(10, int(tpl_g.shape[1] * scale)), max(10, int(tpl_g.shape[0] * scale))
        if tw >= big_g.shape[1] or th >= big_g.shape[0]:
            continue
        tg = _resize(tpl_g, tw, th).astype(np.float64)
        scores, (bx, by) = match_template(big_g.astype(np.float64), tg)
        mx = scores[by, bx] if scores is not None else 0.0
        cands.append((mx, (bx, by), tw, th))
    cands.sort(key=lambda c: -c[0])
    seen, out = set(), []
    for mx, (bx, by), tw, th in cands:
        cx, cy = bx + tw // 2, by + th // 2
        k = (cx // 40, cy // 40)
        if k in seen:
            continue
        seen.add(k)
        out.append((mx, cx, cy))
        if len(out) >= top_n:
            break
    return out


# ── 验证码求解 ───────────────────────────────────────────────────────
def solve_captcha(page, ctx) -> bool:
    """求解当前验证码。成功返回 True（已点确定并登录）。"""
    # 1) 捕获干净素材
    wh = page.evaluate("""() => {
        const w = document.querySelector('.tencent-captcha-dy__content, .tencent-captcha-dy__warp');
        return w ? w.outerHTML : '';
    }""")
    ans_urls = list(dict.fromkeys(re.findall(r'https://turing\.captcha\.qcloud\.com/[^"\s]+', wh)))
    ans_urls = [u.replace("&amp;", "&") for u in ans_urls if "img_index=" in u]
    big_url = None
    m = re.search(r'background-image:\s*url\(([^)]+)\)', wh)
    if m:
        big_url = m.group(1).strip().strip("'").replace("&quot;", "").replace("&amp;", "&").strip()
        if not big_url.startswith("http"):
            big_url = None

    strip_pil = big_pil = None
    try:
        if ans_urls:
            r = ctx.request.get(ans_urls[0], timeout=15000)
            if r.ok:
                strip_pil = Image.open(BytesIO(r.body())).convert("RGB")
        if big_url:
            r = ctx.request.get(big_url, timeout=15000)
            if r.ok:
                big_pil = Image.open(BytesIO(r.body())).convert("RGB")
    except Exception as e:
        log(f"素材下载失败: {e}")

    # 截图兜底
    shot_path = DATA_DIR / "captcha_shot.png"
    page.screenshot(path=str(shot_path), scale="css")
    shot = Image.open(shot_path)
    if strip_pil is None:
        strip_pil = shot.crop(STRIP_BBOX)
    if big_pil is None:
        big_pil = shot.crop(BIG_BBOX)
    big_pil = big_pil.resize((672, 480), Image.LANCZOS)

    strip = np.array(strip_pil)
    big = np.array(big_pil)
    big_g = rgb2gray(big)

    # 2) 分析求解
    clicks = analyze_captcha(strip_pil, big_pil)
    if not clicks:
        log("验证码分析失败")
        return False

    # 4) 按序点击
    for px, py, name in clicks:
        page.mouse.move(px, py, steps=6)
        page.wait_for_timeout(random.uniform(150, 350))
        page.mouse.click(px, py)
        page.wait_for_timeout(random.uniform(600, 900))
        log(f"  已点击 ({px},{py}) {name}")

    page.wait_for_timeout(1500)
    page.screenshot(path=str(DATA_DIR / "captcha_marked.png"), scale="css")

    # 5) 标记校验（差分）
    try:
        before = np.array(Image.open(shot_path).convert("RGB")).astype(int)
        after = np.array(Image.open(DATA_DIR / "captcha_marked.png").convert("RGB")).astype(int)
        diff = np.abs(before - after).sum(axis=2)
        region = diff[280:550, 460:820]
        n, comps = connected_components((region > 45).astype(np.uint8))
        markers = [(460 + st[5], 280 + st[6])
                   for st in comps if st[4] > 60 and st[2] <= 60 and st[3] <= 60]
        log(f"标记校验: {len(markers)} 个标记")
        if len(markers) < len(clicks):
            log("标记数量不足（有点击未命中），放弃本次登录")
            return False
    except Exception as e:
        log(f"标记校验失败: {e}")

    # 6) 点确定
    try:
        clicked = page.evaluate("""() => {
            const b = document.querySelector('.tencent-captcha-dy__verify-confirm-btn');
            if (b) { b.click(); return true; }
            return false;
        }""")
        log(f"点击确定: {clicked}")
    except Exception as e:
        log(f"确定点击异常: {e}")
    page.wait_for_timeout(4000)
    return is_logged_in(page)


def try_login(page, ctx) -> bool:
    log("开始登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)
    except Exception as e:
        log(f"登录页加载失败: {e}")
        return False
    # 登录页可能先弹滑块安全门（会话仍有效但被 IP 变化挡）
    body0 = page.evaluate("() => document.body.innerText")
    if "拖动" in body0 or "拼图" in body0:
        log("登录页出现滑块门，先求解")
        if solve_slider(page):
            page.wait_for_timeout(3000)
            if is_logged_in(page):
                log("滑块通过，会话有效")
                return True
    try:
        page.evaluate("() => { const a = document.querySelector('.account-login'); if (a) a.style.display='block'; }")
        page.wait_for_timeout(400)
        cb = page.locator(".account-login .checked-box.un-checked")
        if cb.count():
            cb.first.click(timeout=4000)
            page.wait_for_timeout(300)
        ins = None
        for _ in range(6):
            ins = page.locator(".account-login .el-input__inner")
            if ins.count() >= 2:
                break
            log("输入框未就绪，等待...")
            page.wait_for_timeout(2000)
        if ins is None or ins.count() < 2:
            log("输入框未找到（页面加载异常）")
            page.screenshot(path=str(DATA_DIR / "dbg_login.png"), scale="css")
            return False
        ins.nth(0).fill(PHONE)
        ins.nth(1).fill(PASSWORD)
        page.wait_for_timeout(150)
        page.locator(".account-login .el-button.el-button--primary").first.click(timeout=8000)
    except Exception as e:
        log(f"填表失败: {e}")
        return False

    # 等待验证码
    captcha_ok = False
    for _ in range(12):
        page.wait_for_timeout(2000)
        try:
            wh = page.evaluate("() => { const w = document.querySelector('.tencent-captcha-dy__content'); return w ? 'yes' : 'no'; }")
            if wh == "yes":
                captcha_ok = True
                break
        except Exception:
            pass
    if not captcha_ok:
        # 可能直接登录成功（无验证码）
        page.wait_for_timeout(2000)
        return is_logged_in(page)

    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        log(f"验证码求解尝试 {attempt}/{MAX_LOGIN_ATTEMPTS}")
        ok = solve_captcha(page, ctx)
        if ok:
            log("验证码求解成功，已登录！")
            return True
        # 失败：刷新验证码重试一次
        try:
            page.locator(".tencent-captcha-dy__footer-icon--refresh").click()
        except Exception:
            pass
        page.wait_for_timeout(random.uniform(6, 10))
    log("登录失败（已达最大尝试次数），放弃")
    return False


# ── 数据抓取 ─────────────────────────────────────────────────────────
def extract_page_data(page, url, label) -> dict:
    data = {}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
    except Exception as e:
        log(f"[{label}] 加载失败: {e}")
    if not is_logged_in(page):
        # 可能是滑块验证门槛（IP 变化触发），先尝试求解滑块
        if "拖动" in (page.evaluate("() => document.body.innerText") or ""):
            if solve_slider(page):
                page.wait_for_timeout(2000)
                if is_logged_in(page):
                    log(f"[{label}] 滑块通过，数据可访问")
                else:
                    log(f"[{label}] 滑块通过但仍无数据")
            else:
                log(f"[{label}] 滑块失败，会话可能失效")
                return {}
        else:
            log(f"[{label}] 会话失效")
            return {}
    import re as _re
    try:
        body = page.evaluate("() => document.body.innerText")
        for pat, key in [
            (r"电费余额[^\d]*([\d,.]+)\s*元", "balance"),
            (r"账户余额[^\d]*([\d,.]+)\s*元", "balance"),
            (r"本期电量[（(]?千瓦时[)）]?[^\d]*([\d,.]+)", "month_usage"),
            (r"本期电费[（(]?元[)）]?[^\d]*([\d,.]+)", "month_charge"),
            (r"本月用电[^\d]*([\d,.]+)\s*kWh", "month_usage"),
            (r"本月电费[^\d]*([\d,.]+)\s*元", "month_charge"),
            (r"年用电量[^\d]*([\d,.]+)\s*kWh", "yearly_usage"),
            (r"年电费[^\d]*([\d,.]+)\s*元", "yearly_charge"),
        ]:
            m = _re.search(pat, body)
            if m and key not in data:
                data[key] = float(m.group(1).replace(",", ""))
    except Exception:
        pass
    # 余额页 .num 元素
    if not data.get("balance"):
        try:
            bal = page.evaluate("""() => {
                const n = document.querySelector('.num');
                if (!n) return null;
                const v = parseFloat(n.innerText.replace(/,/g,'').trim());
                if (isNaN(v)) return null;
                const a = document.querySelector('.amttxt');
                return a && a.innerText.includes('欠费') ? -v : v;
            }""")
            if bal is not None:
                data["balance"] = bal
        except Exception:
            pass
    log(f"[{label}] 提取 {len(data)} 字段: {data}")
    return data


def vlm_solve_gap(image: Image.Image) -> float:
    """VLM 找滑块缺口 X 位置（像素）。"""
    from openai import OpenAI
    client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE)
    resp = client.chat.completions.create(
        model=VLM_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "找到图中拼图缺口（凹槽）的左侧 X 坐标。图片宽 W。只输出JSON：{\"gap_x\": 整数}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{vlm_b64(image)}"}},
        ]}],
        response_format={"type": "json_object"},
    )
    return float(json.loads(resp.choices[0].message.content).get("gap_x", 0))


def _drag_tracks(distance: int):
    import random as _r
    tracks = []
    cur = 0
    mid = distance * 3 / 5
    t = 0.02
    v = 0
    while cur < distance:
        a = _r.randint(400, 600) if cur < mid else -_r.randint(1000, 1200)
        v0 = v
        v = v0 + a * t
        mv = v0 * t + 0.5 * a * t * t
        if mv < 1:
            mv = 1
        mi = round(mv)
        if cur + mi > distance:
            mi = distance - cur
        tracks.append(mi)
        cur += mi
    return tracks


def solve_slider(page) -> bool:
    """处理页面上的滑块验证（拖动拼图）。返回 True=通过/无需处理。"""
    try:
        body = page.evaluate("() => document.body.innerText")
    except Exception:
        return True
    if "拖动" not in body and "拼图" not in body and "slideVerify" not in page.content():
        return True
    log("检测到滑块验证，开始求解...")
    try:
        page.wait_for_selector("#slideVerify", timeout=8000)
        b64 = page.evaluate('return document.getElementById("slideVerify").childNodes[0].toDataURL("image/png");')
        import base64 as _b64
        img = Image.open(BytesIO(_b64.b64decode(b64.split(",")[1]))).convert("RGB")
        rendered_w = float(page.evaluate(
            'return document.getElementById("slideVerify").childNodes[0].getBoundingClientRect().width'))
        scale = rendered_w / img.width
        gap = vlm_solve_gap(img)
        offset = int(os.getenv("SLIDER_OFFSET", "8"))
        distance = int(round(gap * scale)) + offset
        log(f"滑块: gap={gap:.0f} scale={scale:.2f} distance={distance}")

        slider = page.locator(".slide-verify-slider-mask-item, .slide-verify-slider, #slideVerify .slider")
        box = slider.bounding_box() if slider.count() else None
        if not box:
            log("滑块元素未找到")
            return False
        sx = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        page.mouse.move(sx, y)
        page.mouse.down()
        cur = 0
        for off in _drag_tracks(distance):
            cur += off
            page.mouse.move(sx + cur, y + random.choice([-1, 0, 1]))
            page.wait_for_timeout(random.uniform(12, 30))
        time.sleep(random.uniform(0.2, 0.4))
        page.mouse.up()
        page.wait_for_timeout(3000)
        body2 = page.evaluate("() => document.body.innerText")
        ok = "拖动" not in body2 and "拼图" not in body2
        log(f"滑块结果: {'通过' if ok else '失败'}")
        return ok
    except Exception as e:
        log(f"滑块求解异常: {e}")
        return False


def publish_mqtt(data: dict):
    import paho.mqtt.client as mqtt
    client = mqtt.Client()
    client.connect(MQTT_BROKER, MQTT_PORT, 10)
    client.loop_start()
    for stype, value in data.items():
        topic = f"95598/{USER_ID}/{stype}/state"
        client.publish(topic, str(value), retain=True)
        log(f"MQTT -> {topic} = {value}")
    time.sleep(0.6)
    client.loop_stop()
    client.disconnect()


def main():
    from playwright.sync_api import sync_playwright

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log(f"启动（MQTT {MQTT_BROKER}:{MQTT_PORT}，会话 {PROFILE}）")

    with sync_playwright() as p:
        _headless = os.getenv("HEADED", "0") != "1"
        # 与 Mac 真 Chrome 一致的启动参数（避免多余 flag 触发自动化检测）
        _extra = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        _launch = {"user_data_dir": str(PROFILE), "headless": _headless,
                   "viewport": {"width": 1280, "height": 900}, "locale": "zh-CN",
                   "args": _extra,
                   # 覆盖 UA 为 Mac 版 Chrome 151（Linux UA 会被 95598 标记触发安全门）
                   "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/151.0.7922.175 Safari/537.36"}
        _cp = os.getenv("CHROME_PATH", "")
        if _cp:
            _launch["executable_path"] = _cp          # 指定 Chrome 151 路径
        elif os.getenv("CHROME", "0") == "1":
            _launch["channel"] = "chrome"              # 系统 Chrome
        ctx = p.chromium.launch_persistent_context(**_launch)
        page = ctx.new_page()
        page.add_init_script(ANTI)

        logged_in = False
        # 会话检查必须在数据页（登录页永远没有电费数据）
        try:
            page.goto(BILL_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
        except Exception as e:
            log(f"数据页加载失败: {e}")
        # 滑块安全门（会话有效但 IP 变化时出现）
        if not is_logged_in(page) and "拖动" in (page.evaluate("() => document.body.innerText") or ""):
            log("检测到滑块安全门，先求解")
            solve_slider(page)
            page.wait_for_timeout(3000)
        if is_logged_in(page):
            logged_in = True
            log("会话有效，跳过登录")
        else:
            logged_in = try_login(page, ctx)

        data = {}
        if logged_in:
            data.update(extract_page_data(page, BILL_URL, "账单"))
            data.update(extract_page_data(page, BALANCE_URL, "余额"))
        else:
            log("未登录，本次不抓数据")

        if data:
            publish_mqtt(data)
            log("数据发布完成")
        else:
            log("无数据可发布")

        ctx.close()
    log("本次运行结束")


if __name__ == "__main__":
    main()
