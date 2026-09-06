#!/usr/bin/env python3
"""离线评测：在已标注样本上运行 analyze_captcha，对比真实标记位置。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
from PIL import Image

from sgcc_server_auto import analyze_captcha

DATA = Path(__file__).parent / "yolo_data"
PAD_TOP = 85  # 复合图上方答案条区域高度（3x 放大）


def rebuild_from_composite(comp: Image.Image):
    """从复合图还原自然比例答案条 + 大图。"""
    zone = np.array(comp.crop((0, 0, comp.width, PAD_TOP)).convert("L"))
    nonwhite = (zone < 240).any(axis=0)
    cols = np.where(nonwhite)[0]
    x0, x1 = int(cols.min()), int(cols.max())
    strip_zone = comp.crop((x0, 0, x1 + 1, PAD_TOP))
    strip = strip_zone.resize((strip_zone.width // 3, strip_zone.height // 3), Image.LANCZOS)
    big = comp.crop((0, PAD_TOP, comp.width, comp.height))
    return strip, big

total_ok = 0
total_gt = 0
per_sample = []

for jf in sorted(DATA.glob("sample_*.json")):
    meta = json.loads(jf.read_text())
    gts = meta.get("markers", [])
    if not gts:
        continue
    idx = jf.stem
    comp = Image.open(DATA / f"{idx}.jpg")
    strip, big = rebuild_from_composite(comp)
    preds = analyze_captcha(strip, big)
    pred_pts = [(px, py) for px, py, _n in preds]

    # 匹配：预测点与 GT 标记距离 < 25px
    matched = 0
    used = set()
    for px, py in pred_pts:
        best = None
        for gi, (gx, gy) in enumerate(gts):
            if gi in used:
                continue
            d = ((px - gx) ** 2 + (py - gy) ** 2) ** 0.5
            if best is None or d < best[0]:
                best = (d, gi)
        if best and best[0] < 25:
            matched += 1
            used.add(best[1])
    ok = matched == len(gts) and len(pred_pts) == len(gts)
    per_sample.append((idx, len(gts), matched, pred_pts, gts, ok))
    total_ok += 1 if ok else 0
    total_gt += len(gts)
    print(f"{idx}: GT={len(gts)} 命中={matched} {'✅' if ok else '❌'} pred={pred_pts}")

print(f"\n=== 总评: {total_ok}/{len(per_sample)} 样本完全正确 ===")
