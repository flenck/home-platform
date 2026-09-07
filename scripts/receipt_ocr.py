"""小票/发票 OCR 识别服务（家庭平台记账用）

参照 custom-ocr / Invoice_detection / Invoiscope 的
「预处理 → OCR → 后处理提取字段」管线：
- OpenCV 预处理（灰度/去噪/放大）
- RapidOCR（onnxruntime 版 PaddleOCR）全文字识别
- 正则 + 关键词提取金额/日期/商家/分类

用法: ~/sgcc/venv_yolo/bin/python receipt_ocr.py --port 8001
"""
import base64
import io
import logging
import os
import re
import sys

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_LOGGER = logging.getLogger("receipt_ocr")

# ── RapidOCR 单例 ─────────────────────────────────────────────
_ocr = None


def get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _LOGGER.info("Loading RapidOCR...")
        _ocr = RapidOCR()
        _LOGGER.info("RapidOCR ready")
    return _ocr


# ── 分类关键词库（与记账模块分类对齐） ────────────────────────
CATEGORY_KEYWORDS = [
    ("餐饮", ["饭店", "餐厅", "面馆", "小吃", "咖啡", "奶茶", "麦当劳", "肯德基", "沙县",
               "烧烤", "火锅", "食堂", "外卖", "早餐", "晚餐", "美食", "餐馆", "必胜客",
               "汉堡", "炸鸡", "卤味", "烘焙", "面包", "蛋糕", "茶饮", "饮品", "夜宵"]),
    ("交通", ["加油", "石化", "石油", "地铁", "公交", "停车", "滴滴", "打车", "高铁",
               "火车", "机票", "etc", "高速", "出行", "充电"]),
    ("购物", ["超市", "便利店", "百货", "商场", "京东", "淘宝", "拼多多", "沃尔玛", "永辉",
               "711", "罗森", "天猫", "小米", "优衣库", "名创", "无印", "文具", "五金",
               "家居", "服装", "服饰"]),
    ("医疗", ["药", "医院", "诊所", "大药房", "同仁堂", "门诊", "体检"]),
    ("水电燃气", ["电费", "水费", "燃气", "电网", "水务", "供电", "自来水"]),
    ("住房", ["房租", "物业", "自如", "贝壳", "链家", "公寓", "房东"]),
    ("娱乐", ["电影", "ktv", "游戏", "网吧", "游乐", "影院", "演出", "景点", "门票"]),
    ("教育", ["书店", "培训", "教育", "教材", "课程", "打印", "复印"]),
    ("人情往来", ["红包", "礼品", "礼盒", "鲜花"]),
]

DEFAULT_CATEGORY = "其他"


def guess_category(text: str) -> str:
    """根据 OCR 全文关键词猜测分类。"""
    low = text.lower()
    for cat, kws in CATEGORY_KEYWORDS:
        for kw in kws:
            if kw in low:
                return cat
    return DEFAULT_CATEGORY


# ── 金额提取 ──────────────────────────────────────────────────
AMOUNT_LABELS = ["合计", "总计", "实收", "应收", "应付", "总额", "金额", "消费", "小计", "共",
                 "total", "amount", "paid", "due", "cash", "change"]


def extract_amount(lines: list[str]) -> float | None:
    """优先找带金额标签的行，兜底取全文最大金额。"""
    amounts = []
    # 1) 标签优先：含金额词的行里的数字
    for line in lines:
        low = line.lower()
        if any(lbl in low for lbl in AMOUNT_LABELS):
            for m in re.finditer(r"(\d{1,6}[,，]?\d{0,3}\.\d{2})", line):
                val = float(m.group(1).replace(",", "").replace("，", ""))
                amounts.append((val, 2))
    if amounts:
        # 取标签行中最接近行尾金额优先 → 直接取最大
        return max(a[0] for a in amounts)
    # 2) 兜底：全文所有两位小数数字，取最大（避免把单价/数量当总额）
    all_vals = []
    for line in lines:
        for m in re.finditer(r"(\d{1,6}[,，]?\d{0,3}\.\d{2})", line):
            all_vals.append(float(m.group(1).replace(",", "").replace("，", "")))
    if all_vals:
        return max(all_vals)
    # 3) 整数金额
    for line in lines:
        for m in re.finditer(r"[¥￥]\s*(\d{2,6})", line):
            return float(m.group(1))
    return None


# ── 日期提取 ──────────────────────────────────────────────────
def extract_date(lines: list[str]) -> str | None:
    """支持 2026-09-07 / 2026/09/07 / 2026年09月07日 / 09-07 / 9月7日。"""
    now = __import__("datetime").datetime.now()
    for line in lines:
        m = re.search(r"(20\d{2})[年./\-](\d{1,2})[月./\-](\d{1,2})", line)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m = re.search(r"(\d{1,2})[月/](\d{1,2})[日号]?", line)
        if m:
            return f"{now.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


# ── 预处理 ────────────────────────────────────────────────────
def preprocess(img: np.ndarray) -> np.ndarray:
    """灰度 → 放大 → 去噪 → 自适应二值化。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    scale = 1.0
    if w < 1000:
        scale = 1600.0 / w
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def ocr_image_bytes(data: bytes) -> dict:
    """主入口：识别小票图片字节，返回结构化结果。"""
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"ok": False, "error": "无法解析图片"}
    # 原图直接识别
    ocr = get_ocr()
    result, _ = ocr(img)
    raw_lines = [line[1] for line in result] if result else []
    # 预处理后二次识别（倾斜/低对比图），合并结果
    try:
        proc = preprocess(img)
        result2, _ = ocr(proc)
        raw_lines2 = [line[1] for line in result2] if result2 else []
        raw_lines = list(dict.fromkeys(raw_lines + raw_lines2))  # 去重保序
    except Exception as e:
        _LOGGER.warning("preprocess pass failed: %s", e)

    text = "\n".join(raw_lines)
    amount = extract_amount(raw_lines)
    date = extract_date(raw_lines)
    category = guess_category(text)
    merchant = raw_lines[0].strip() if raw_lines else ""

    return {
        "ok": True,
        "amount": amount,
        "date": date,
        "category": category,
        "merchant": merchant,
        "raw_text": text[:3000],
        "lines": raw_lines[:60],
    }


# ── FastAPI 服务 ──────────────────────────────────────────────
def run_server(port: int = 8001) -> None:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Receipt OCR", version="1.0")

    @app.post("/ocr")
    async def ocr_endpoint(request: Request):
        body = await request.json()
        img_b64 = body.get("image")
        if not img_b64:
            return JSONResponse({"ok": False, "error": "image (base64) required"}, status_code=422)
        try:
            data = base64.b64decode(img_b64)
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid base64"}, status_code=422)
        try:
            res = ocr_image_bytes(data)
        except Exception as e:
            _LOGGER.exception("OCR failed")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
        return JSONResponse(res)

    @app.get("/health")
    async def health():
        return {"ok": True}

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    port = 8001
    if len(sys.argv) > 1 and sys.argv[1] == "--port":
        port = int(sys.argv[2])
    # 预热模型（首次下载/加载）
    get_ocr()
    run_server(port)
