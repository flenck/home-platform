#!/usr/bin/env python3
"""YOLO 验证码求解器（onnxruntime CPU 推理）。
输入：验证码复合图（答案条+大图）PIL Image
输出：目标中心点列表（复合图像素坐标），按置信度降序
"""
import numpy as np
import onnxruntime as ort
from PIL import Image

MODEL_PATH = "/home/dufan2514897261/sgcc/yolo_model/best.onnx"
IMGSZ = 416
CONF_THRESH = 0.25
IOU_THRESH = 0.45

_sess = None


def _get_sess():
    global _sess
    if _sess is None:
        _sess = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    return _sess


def _nms(boxes, scores, iou_thresh):
    """boxes: Nx4 (x1,y1,x2,y2 原图坐标), scores: N。返回保留索引。"""
    if len(boxes) == 0:
        return []
    order = scores.argsort()[::-1]
    keep = []
    x1 = boxes[:, 0]; y1 = boxes[:, 1]; x2 = boxes[:, 2]; y2 = boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0, xx2 - xx1)
        h = np.maximum(0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thresh]
    return keep


def solve(composite_pil, conf_thresh=CONF_THRESH, big_bbox=None):
    """返回 [(cx, cy, conf)]，复合图像素坐标。

    big_bbox: 大图在复合图中的 (x, y, w, h)；提供时过滤答案条区域
    （答案条在大图上方，其中的目标小图不应被点击）。
    """
    img_rgb = composite_pil.convert("RGB")
    w, h = img_rgb.size
    scale = min(IMGSZ / w, IMGSZ / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = np.array(img_rgb.resize((nw, nh), Image.LANCZOS))
    canvas = np.full((IMGSZ, IMGSZ, 3), 114, dtype=np.uint8)
    top, left = (IMGSZ - nh) // 2, (IMGSZ - nw) // 2
    canvas[top:top + nh, left:left + nw] = resized
    x = (canvas.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]

    sess = _get_sess()
    in_name = sess.get_inputs()[0].name
    out = sess.run(None, {in_name: x})[0]  # (1, 5, 8400)

    preds = out[0]  # 5x8400
    cx = preds[0]; cy = preds[1]; bw = preds[2]; bh = preds[3]; conf = preds[4]
    mask = conf >= conf_thresh
    idx = np.where(mask)[0]
    if idx.size == 0:
        return []

    boxes = np.stack([cx[idx] - bw[idx] / 2, cy[idx] - bh[idx] / 2,
                      cx[idx] + bw[idx] / 2, cy[idx] + bh[idx] / 2], axis=1)
    scores = conf[idx]
    keep = _nms(boxes, scores, IOU_THRESH)

    results = []
    for k in keep:
        x1 = (boxes[k][0] - left) / scale
        y1 = (boxes[k][1] - top) / scale
        x2 = (boxes[k][2] - left) / scale
        y2 = (boxes[k][3] - top) / scale
        # 裁剪到图内
        x1 = max(0, min(w, x1)); x2 = max(0, min(w, x2))
        y1 = max(0, min(h, y1)); y2 = max(0, min(h, y2))
        cx_p = (x1 + x2) / 2
        cy_p = (y1 + y2) / 2
        # 答案条过滤：大图区域内的检测才保留
        if big_bbox:
            bx, by, bw_, bh_ = big_bbox
            if not (bx <= cx_p <= bx + bw_ and by <= cy_p <= by + bh_):
                continue
        results.append((round(cx_p, 1), round(cy_p, 1), round(float(scores[k]), 3)))
    results.sort(key=lambda r: -r[2])
    return results


if __name__ == "__main__":
    import sys
    from pathlib import Path
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "yolo_data/sample_010.jpg")
    img = Image.open(p)
    pts = solve(img)
    print(f"{p.name} ({img.size[0]}x{img.size[1]}): {pts}")
    # 对比标注
    txt = p.with_suffix(".txt")
    if txt.exists():
        print("GT 标注:")
        for line in txt.read_text().strip().splitlines():
            c = line.split()
            cx, cy = float(c[1]) * img.size[0], float(c[2]) * img.size[1]
            print(f"  ({round(cx,1)}, {round(cy,1)})")
