#!/usr/bin/env python3
"""95598 自动登录（CDP 版本）：会话失效时自动填账号密码 + YOLO优先求解点选验证码。

流程：CDP连接Chrome → 检测登录态 → 失效则导航登录页 → 填账号密码 →
      腾讯点选验证码（YOLO本地求解优先 → 答案条切片+模板匹配+VLM兜底）→ 按序点击 → 点确定。
登录失败时写状态文件 status.json，供前端展示"获取失败"提示。
依赖：numpy, PIL, requests, websocket-client, openai, cv_lite.py, sgcc_yolo_solver.py（同目录）。
"""
import base64, json, os, re, sys, time, random, urllib.request
from datetime import datetime
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
import websocket
from PIL import Image
from cv_lite import rgb2gray, resize as _resize, match_template, connected_components

try:
    import sgcc_yolo_solver
    HAVE_YOLO = True
except Exception:
    HAVE_YOLO = False

CDP_HTTP = "http://127.0.0.1:9227"
PHONE = "15718855844"
PASSWORD = "Dufan2514897261"
VLM_API_KEY = ""
VLM_BASE = "https://open.bigmodel.cn/api/paas/v4/"
VLM_MODEL = "glm-4v-flash"
LOGIN_URL = "https://95598.cn/osgweb/login"
LOG_FILE = "/home/dufan2514897261/sgcc/auto_login.log"
DATA_DIR = Path("/home/dufan2514897261/sgcc")
STATUS_FILE = Path("/home/dufan2514897261/home-platform/homeplatform/sgcc_status.json")
MAX_LOGIN_ATTEMPTS = 2


def write_status(**kw):
    """写 sgcc 状态文件（登录/采集状态，供前端读取）。"""
    try:
        old = {}
        if STATUS_FILE.exists():
            old = json.loads(STATUS_FILE.read_text())
        old.update(kw)
        old["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        STATUS_FILE.write_text(json.dumps(old, ensure_ascii=False, indent=2))
    except Exception as e:
        log(f"write_status 失败: {e}")


def load_vlm_key():
    global VLM_API_KEY
    for p in ["/home/dufan2514897261/home-platform/infra/.env"]:
        try:
            with open(p) as f:
                for line in f:
                    if line.startswith("SGCC_VLM_API_KEY="):
                        VLM_API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                        return
        except Exception:
            pass


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── CDP 工具 ──────────────────────────────────────────────
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
    for _ in range(200):  # 上限防事件流刷屏卡死
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            ws.close()
            return msg
    ws.close()
    return {}


def ev(ws_url, expression, timeout=30):
    r = cdp(ws_url, "Runtime.evaluate",
            {"expression": expression, "returnByValue": True}, timeout)
    return r.get("result", {}).get("result", {}).get("value")


def cdp_screenshot(ws_url, path):
    ws = websocket.create_connection(ws_url, timeout=30)
    ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            open(path, "wb").write(base64.b64decode(msg["result"]["data"]))
            break
    ws.close()


def cdp_mouse_click(ws_url, x, y):
    cdp(ws_url, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    time.sleep(random.uniform(0.05, 0.15))
    cdp(ws_url, "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    time.sleep(random.uniform(0.05, 0.12))
    cdp(ws_url, "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})


# ── VLM ────────────────────────────────────────────────────
def vlm_b64(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.read()).decode()


def vlm_identify(img):
    from openai import OpenAI
    client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE, timeout=20, max_retries=0)
    resp = client.chat.completions.create(
        model=VLM_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "验证码答案条里的小图，用2-4个字描述内容（如：数字5、房子、笑脸、雨伞、箭头、地图标记）"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{vlm_b64(img)}"}},
        ]}],
    )
    return resp.choices[0].message.content.strip().replace("\n", " ")[:20]


def vlm_locate(big, thumb, expect):
    from openai import OpenAI
    out = []
    try:
        client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE, timeout=20, max_retries=0)
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


def vlm_verify(crop, expect):
    try:
        from openai import OpenAI
        client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE, timeout=20, max_retries=0)
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


# ── 模板匹配 ────────────────────────────────────────────────
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


# ── 答案条目标计数 ─────────────────────────────────────────
def count_strip_targets(strip_pil):
    """答案条按列方差分块，返回目标数量 N。失败返回 0。"""
    try:
        g = rgb2gray(np.array(strip_pil))
        colvar = g.var(axis=0)
        content = colvar > 5
        runs, in_run = [], False
        start = 0
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
        return len(groups)
    except Exception:
        return 0


# ── 验证码分析（坐标动态转换）──────────────────────────────
def analyze_captcha(strip_pil, big_pil, big_bbox):
    """big_bbox=(x,y,w,h) 大图在视口中的实际位置。返回 [(px,py,name)]"""
    big_672 = big_pil.resize((672, 480), Image.LANCZOS)
    strip = np.array(strip_pil)
    big_g = rgb2gray(np.array(big_672))

    # 答案条切片（按列方差分块）
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

    bx, by, bw, bh = big_bbox
    clicks = []
    for i, (a, b) in enumerate(groups):
        t = strip[:, a:b]
        t_pil = Image.fromarray(t)
        name = vlm_identify(t_pil.resize(((b - a) * 6, 50 * 6), Image.LANCZOS))
        t_g = rgb2gray(t)
        cands = template_matches(t_g, big_g)
        vlm_cands = vlm_locate(big_672, t_pil, name)
        log(f"  目标{i} {name}: TM={[(round(m,2),(cx,cy)) for m,cx,cy in cands]} VLM={vlm_cands}")

        pool = {}
        for mx, cx, cy in cands:
            pool.setdefault((cx // 20, cy // 20), []).append(("tm", mx, cx, cy))
        for cx, cy in vlm_cands:
            pool.setdefault((cx // 20, cy // 20), []).append(("vlm", 0.5, cx, cy))

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
            crop = big_672.crop((max(0, cx - 42), max(0, cy - 42),
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
        px = round(bx + cx * bw / 672)
        py = round(by + cy * bh / 480)
        clicks.append((px, py, f"{name}({mx:.2f})"))
    return clicks


# ── DOM 获取验证码元素位置 ─────────────────────────────────
def get_captcha_elements(ws_url):
    """返回 {big_bbox, strip_bbox}，通过 DOM 精确定位"""
    r = ev(ws_url, """
    (() => {
        const out = {big: null, strip: null};
        // 大图：找 background-image 含 turing.captcha 的元素
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
        // 答案条：找 img src 含 img_index 的元素
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
    return r or {}


# ── 求解验证码 ──────────────────────────────────────────────
# ── OCR 数字排序（复用本机 8001 小票 OCR 服务，零 VLM 依赖）──
OCR_HTTP = "http://127.0.0.1:8001/ocr"


def _ocr_text(pil_img, timeout=15):
    """调用本地 RapidOCR 服务识别图片文字，失败返回空串。"""
    try:
        buf = BytesIO()
        pil_img.convert("RGB").save(buf, format="PNG")
        r = requests.post(OCR_HTTP, json={"image": base64.b64encode(buf.getvalue()).decode()},
                          timeout=timeout)
        if r.ok:
            return r.json().get("raw_text", "") or ""
    except Exception:
        pass
    return ""


def ocr_order_clicks(yolo_pts, strip_pil, big_pil, big_in_composite, c_left, c_top):
    """用 OCR 数字把 YOLO 候选按答案条顺序排好，返回 [(px,py,conf,name)]。

    答案条（如“请依次点击：7 2”）识别数字序列；每个候选 crop 大图区域放大后
    OCR 数字；已识别数字固定位置，未识别候选按置信度降序补位（下限 0.25）。
    凑不齐返回 None（调用方回退/刷新重试）。
    """
    try:
        # 1. 答案条数字序列
        ans_text = _ocr_text(strip_pil)
        digits = re.findall(r"[0-9]", ans_text or "")
        if not digits:
            log(f"[OCR] 答案条未识别到数字: {ans_text!r}")
            return None
        log(f"[OCR] 答案顺序: {''.join(digits)} ({ans_text.strip()[:30]})")

        # 2. 每个候选识别数字（big 672x480 坐标体系）
        bx, by, bw, bh = big_in_composite
        big672 = big_pil.resize((672, 480), Image.LANCZOS)
        cand_map = {}
        unknown = []
        for cx, cy, conf in yolo_pts:
            gx = (cx - bx) * 672.0 / bw
            gy = (cy - by) * 480.0 / bh
            box = (max(0, int(gx) - 38), max(0, int(gy) - 38),
                   min(672, int(gx) + 38), min(480, int(gy) + 38))
            crop = big672.crop(box)
            crop = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
            t = _ocr_text(crop)
            d = re.findall(r"[0-9]", t or "")
            name = d[0] if d else "?"
            if d and name in digits and name not in cand_map:
                # 识别出的数字在答案序列里 → 固定位置
                cand_map[name] = (cx, cy, conf)
                log(f"[OCR] 候选({round(cx,1)},{round(cy,1)}) conf={float(conf):.2f} -> 数字={name} ✓")
            else:
                # 未识别 或 数字不在答案序列（干扰图标/误读）→ 按置信度备选
                unknown.append((cx, cy, conf))
                log(f"[OCR] 候选({round(cx,1)},{round(cy,1)}) conf={float(conf):.2f} -> "
                    f"{'数字=' + name if d else '未识别数字'}（备选）")

        # 3. 按答案顺序组装：已识别固定，未识别按置信度补位（下限 0.15）
        unknown.sort(key=lambda z: -float(z[2]))
        ordered = []
        for d in digits:
            if d in cand_map:
                ordered.append((cand_map.pop(d), d))
            elif unknown and float(unknown[0][2]) >= 0.15:
                u = unknown.pop(0)
                ordered.append(((u[0], u[1], u[2]), d))
                log(f"[OCR] 数字{d} 未识别，用置信度 {float(u[2]):.2f} 候选补位")
            else:
                log(f"[OCR] 数字{d} 无法确定位置（候选不足），回退")
                return None
        if len(ordered) != len(digits):
            return None
        pts = [(round(c_left + cx), round(c_top + cy), conf, d) for (cx, cy, conf), d in ordered]
        log(f"[OCR] 排序完成: {[(p[0], p[1], p[3]) for p in pts]}")
        return pts
    except Exception as e:
        log(f"[OCR] 排序异常: {e}")
        return None


def solve_captcha(ws_url):
    # 等待验证码弹窗
    captcha_ok = False
    for _ in range(12):
        time.sleep(1.5)
        wh = ev(ws_url, "document.querySelector('.tencent-captcha-dy__content, .tcaptcha-transparent') ? 'yes' : 'no'")
        if wh == "yes":
            captcha_ok = True
            break
    if not captcha_ok:
        log("验证码弹窗未出现")
        return False

    time.sleep(2.5)
    # 获取 DOM 位置
    elems = get_captcha_elements(ws_url)
    big_bbox = elems.get("big")
    strip_bbox = elems.get("strip")
    log(f"验证码元素: big={big_bbox} strip={strip_bbox}")

    # 截图
    shot_path = DATA_DIR / "captcha_shot.png"
    cdp_screenshot(ws_url, shot_path)
    shot = Image.open(shot_path)

    # 提取图片 URL（从 HTML）
    html = ev(ws_url, "document.querySelector('.tencent-captcha-dy__content, .tcaptcha-transparent')?.outerHTML || ''")
    ans_urls = list(dict.fromkeys(re.findall(r'https://turing\.captcha\.qcloud\.com/[^"\s]+', html or "")))
    ans_urls = [u.replace("&amp;", "&") for u in ans_urls if "img_index=" in u]
    big_url = None
    m = re.search(r'background-image:\s*url\(([^)]+)\)', html or "")
    if m:
        big_url = m.group(1).strip().strip("'").replace("&quot;", "").replace("&amp;", "&").strip()
        if not big_url.startswith("http"):
            big_url = None

    strip_pil = big_pil = None
    try:
        if ans_urls:
            r = requests.get(ans_urls[0], timeout=15)
            if r.ok:
                strip_pil = Image.open(BytesIO(r.content)).convert("RGB")
        if big_url:
            r = requests.get(big_url, timeout=15)
            if r.ok:
                big_pil = Image.open(BytesIO(r.content)).convert("RGB")
    except Exception as e:
        log(f"素材下载失败: {e}")

    # 截图兜底
    if big_pil is None and big_bbox:
        big_pil = shot.crop((int(big_bbox["x"]), int(big_bbox["y"]),
                              int(big_bbox["x"] + big_bbox["w"]), int(big_bbox["y"] + big_bbox["h"])))
    if strip_pil is None and strip_bbox:
        strip_pil = shot.crop((int(strip_bbox["x"]), int(strip_bbox["y"]),
                                int(strip_bbox["x"] + strip_bbox["w"]), int(strip_bbox["y"] + strip_bbox["h"])))

    if big_pil is None or strip_pil is None or big_bbox is None:
        log(f"无法获取验证码素材: big={big_pil is not None} strip={strip_pil is not None} big_bbox={big_bbox}")
        return False

    # ── YOLO 优先求解（零成本本地推理）────────────────────
    if HAVE_YOLO and big_bbox and strip_bbox:
        try:
            # 构造复合图 crop（与 captcha_watch 相同逻辑）
            top = min(strip_bbox["y"], big_bbox["y"])
            bottom = max(strip_bbox["y"] + strip_bbox["h"], big_bbox["y"] + big_bbox["h"])
            left = min(strip_bbox["x"], big_bbox["x"])
            right = max(strip_bbox["x"] + strip_bbox["w"], big_bbox["x"] + big_bbox["w"])
            pad = 5
            c_left = max(0, int(left - pad)); c_top = max(0, int(top - pad))
            c_right = int(right + pad); c_bottom = int(bottom + pad)
            composite = shot.crop((c_left, c_top, c_right, c_bottom))
            big_in_composite = (big_bbox["x"] - c_left, big_bbox["y"] - c_top,
                                big_bbox["w"], big_bbox["h"])

            yolo_pts = sgcc_yolo_solver.solve(composite, conf_thresh=0.15, big_bbox=big_in_composite)
            # 目标数优先用答案条 OCR 数字个数（列方差计数会把“请依次点击：”文字误算）
            _strip_ext = shot.crop((int(strip_bbox["x"]) - 150, int(strip_bbox["y"]) - 3,
                                    int(strip_bbox["x"]) + 150, int(strip_bbox["y"]) + 31)) \
                if strip_bbox else strip_pil
            _ans_digits = re.findall(r"[0-9]", _ocr_text(_strip_ext) or "")
            n_targets = len(_ans_digits) if _ans_digits else count_strip_targets(strip_pil)
            log(f"YOLO 候选 {len(yolo_pts)} 个, 答案条目标 {n_targets} 个: "
                f"{[(round(p[0],1), round(p[1],1)) for p in yolo_pts]}")

            # 优先：OCR 数字排序（答案条数字顺序 + 候选数字识别）
            # 答案条区域扩展（覆盖“请依次点击：7 2”整行，原 84px 仅数字部分）
            strip_ext = shot.crop((int(strip_bbox["x"]) - 150, int(strip_bbox["y"]) - 3,
                                   int(strip_bbox["x"]) + 150, int(strip_bbox["y"]) + 31)) \
                if strip_bbox else strip_pil
            ordered = ocr_order_clicks(yolo_pts, strip_ext, big_pil, big_in_composite,
                                       c_left, c_top) if yolo_pts else None
            if ordered:
                for px, py, conf, name in ordered:
                    cdp_mouse_click(ws_url, px, py)
                    time.sleep(random.uniform(0.6, 0.9))
                    log(f"  [OCR-YOLO] 点击数字{name} ({px},{py}) conf={float(conf):.2f}")
                time.sleep(1.5)
                clicked = ev(ws_url, """
                (() => {
                    const b = document.querySelector('.tencent-captcha-dy__verify-confirm-btn, .tcaptcha-confirm-btn');
                    if (b) { b.click(); return true; }
                    return false;
                })()
                """)
                log(f"[OCR-YOLO] 点击确定: {clicked}")
                time.sleep(4)
                if is_logged_in(ws_url):
                    log("OCR+YOLO 求解成功，已登录！")
                    return True
                log("OCR+YOLO 点击后未登录（可能顺序/识别有误），尝试刷新验证码")
            elif 0 < n_targets == len(yolo_pts):
                # 候选数与答案条目标数一致（OCR 不可用时的保底）→ 按置信度点击
                for cx, cy, conf in yolo_pts:
                    px = round(c_left + cx)
                    py = round(c_top + cy)
                    cdp_mouse_click(ws_url, px, py)
                    time.sleep(random.uniform(0.6, 0.9))
                    log(f"  [YOLO] 已点击 ({px},{py}) conf={conf:.2f}")
                time.sleep(1.5)
                clicked = ev(ws_url, """
                (() => {
                    const b = document.querySelector('.tencent-captcha-dy__verify-confirm-btn, .tcaptcha-confirm-btn');
                    if (b) { b.click(); return true; }
                    return false;
                })()
                """)
                log(f"[YOLO] 点击确定: {clicked}")
                time.sleep(4)
                if is_logged_in(ws_url):
                    log("YOLO 求解成功，已登录！")
                    return True
                log("YOLO 点击后未登录（可能误检），尝试刷新验证码")
            else:
                log(f"YOLO 候选数与目标数不一致（{len(yolo_pts)} != {n_targets}），降级 VLM")
        except Exception as e:
            log(f"YOLO 求解异常: {e}，降级 VLM")

    # 分析求解（VLM 兜底）
    clicks = analyze_captcha(strip_pil, big_pil,
                              (big_bbox["x"], big_bbox["y"], big_bbox["w"], big_bbox["h"]))
    if not clicks:
        log("验证码分析失败")
        return False

    # 按序点击
    for px, py, name in clicks:
        cdp_mouse_click(ws_url, px, py)
        time.sleep(random.uniform(0.6, 0.9))
        log(f"  已点击 ({px},{py}) {name}")

    time.sleep(1.5)
    # 点确定
    clicked = ev(ws_url, """
    (() => {
        const b = document.querySelector('.tencent-captcha-dy__verify-confirm-btn, .tcaptcha-confirm-btn');
        if (b) { b.click(); return true; }
        return false;
    })()
    """)
    log(f"点击确定: {clicked}")
    time.sleep(4)
    return is_logged_in(ws_url)


# ── 登录检测 ────────────────────────────────────────────────
def is_logged_in(ws_url):
    try:
        # URL 检测最可靠（登录后进入 my95598）
        url = ev(ws_url, "location.href") or ""
        if "my95598" in url:
            return True
        text = ev(ws_url, "document.body ? document.body.innerText : ''") or ""
        return any(k in text for k in [
            "电费余额", "用电量", "电费账单", "账户余额", "年电量",
            "本期电量", "我的95598", "退出登录", "户号管理", "个人设置"
        ])
    except Exception:
        return False


# ── 尝试登录 ────────────────────────────────────────────────
def try_login(ws_url):
    log("开始自动登录...")
    cdp(ws_url, "Page.navigate", {"url": LOGIN_URL})
    time.sleep(5)

    if is_logged_in(ws_url):
        log("已是登录状态")
        return True

    # 切换到账号密码登录 tab + 勾选协议
    ev(ws_url, """
    (() => {
        // 切换账号密码登录
        const tabs = document.querySelectorAll('.login-tab, [class*=tab]');
        tabs.forEach(t => { if (/账号密码|密码登录/.test(t.textContent||'')) t.click(); });
        // 勾选协议
        const cb = document.querySelector('.account-login .checked-box.un-checked, .el-checkbox__input');
        if (cb) cb.click();
        return 'ok';
    })()
    """)
    time.sleep(1)

    # 填账号密码（用原生 setter 触发 Vue 响应式）
    fill_js = f"""
    (() => {{
        const ins = document.querySelectorAll('.account-login .el-input__inner, input[type=text], input[type=password]');
        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        let phoneFilled = false, passFilled = false;
        ins.forEach(inp => {{
            if (inp.type === 'password' && !passFilled) {{
                nativeSetter.call(inp, '{PASSWORD}');
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                passFilled = true;
            }} else if (!phoneFilled) {{
                nativeSetter.call(inp, '{PHONE}');
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                phoneFilled = true;
            }}
        }});
        return 'phone=' + phoneFilled + ' pass=' + passFilled;
    }})()
    """
    result = ev(ws_url, fill_js)
    log(f"填账号密码: {result}")
    time.sleep(0.8)

    # 点登录按钮
    clicked = ev(ws_url, """
    (() => {
        const btns = document.querySelectorAll('.account-login .el-button--primary, button');
        for (const b of btns) {
            if (/登\s*录|登录/.test(b.textContent || '')) { b.click(); return 'clicked: ' + b.textContent.trim(); }
        }
        return 'no_btn';
    })()
    """)
    log(f"点登录: {clicked}")

    # 检测是否直接登录成功
    time.sleep(3)
    if is_logged_in(ws_url):
        log("直接登录成功（无验证码）")
        write_status(login_failed=False, last_login_ok=True)
        return True

    # 求解验证码
    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        log(f"验证码求解尝试 {attempt}/{MAX_LOGIN_ATTEMPTS}")
        ok = solve_captcha(ws_url)
        if ok:
            log("验证码求解成功，已登录！")
            write_status(login_failed=False, last_login_ok=True)
            return True
        # 刷新验证码重试
        try:
            ev(ws_url, "document.querySelector('.tencent-captcha-dy__footer-icon--refresh, .tcaptcha-refresh')?.click()")
        except Exception:
            pass
        time.sleep(random.uniform(6, 10))
    log("登录失败（已达最大尝试次数）")
    write_status(login_failed=True, last_login_ok=False,
                 reason="YOLO 与 VLM 均未能通过验证码，需手动登录")
    return False


# ── 主函数 ──────────────────────────────────────────────────
def main():
    load_vlm_key()
    if not VLM_API_KEY:
        log("WARN: VLM_API_KEY 未配置（YOLO 求解不需要 key，VLM 兜底将不可用）")

    ws_url = get_ws_url()
    if not ws_url:
        log("CDP 连接失败（Chrome 未运行）")
        write_status(login_failed=True, reason="Chrome 未运行")
        sys.exit(1)
    log(f"CDP 连接 OK, URL={ev(ws_url, 'location.href')}")

    if is_logged_in(ws_url):
        log("当前已登录，无需重新登录")
        write_status(login_failed=False, last_login_ok=True)
        sys.exit(0)

    ok = try_login(ws_url)
    if ok:
        log("自动登录成功")
        sys.exit(0)
    else:
        log("自动登录失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
