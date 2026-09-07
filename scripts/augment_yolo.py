#!/usr/bin/env python3
"""离线增强：对 yolo_data 样本做平移/缩放/亮度扰动，扩到约 3 倍，标签同步变换。"""
import random
from pathlib import Path

import cv2
import numpy as np

SCRIPTS = Path(__file__).parent
DATA = SCRIPTS / "yolo_data"
AUG_DIR = SCRIPTS / "yolo_aug"

random.seed(42)
np.random.seed(42)

AUG_PER_SAMPLE = 3  # 每个样本生成 3 个增强版本


def read_labels(txt: Path):
    lines = []
    for line in txt.read_text().strip().splitlines():
        p = line.split()
        lines.append([float(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])])
    return lines


def augment(img: np.ndarray, boxes: list) -> tuple:
    """boxes: [[cx,cy,w,h] 归一化]。返回 (img, boxes)。"""
    H, W = img.shape[:2]
    # 1) 平移 ±8%
    dx = random.uniform(-0.08, 0.08) * W
    dy = random.uniform(-0.08, 0.08) * H
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    img = cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
    # 2) 缩放 0.92~1.08（围绕中心，等比）
    s = random.uniform(0.92, 1.08)
    if s != 1:
        M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
        img = cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
    # 3) 亮度/对比度
    alpha = random.uniform(0.85, 1.15)
    beta = random.uniform(-18, 18)
    img = cv2.convertScaleAbs(img, alpha=alpha, beta=beta)
    # 4) 轻微旋转 ±3°
    ang = random.uniform(-3, 3)
    M = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
    img = cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
    # 标签变换：平移 + 缩放 + 旋转后 bbox（近似：把中心旋转，wh 用旋转后外接）
    new_boxes = []
    for b in boxes:
        cls, cx, cy, w, h = b
        px, py = (cx - 0.5) * W, (cy - 0.5) * H  # 相对中心
        # 平移
        px += dx; py += dy
        # 缩放
        px *= s; py *= s
        w2, h2 = w * s, h * s
        # 旋转中心
        cx2, cy2 = W / 2, H / 2
        rx = px + cx2; ry = py + cy2
        ox, oy = cx2, cy2
        ang_r = np.deg2rad(ang)
        nx = ox + (rx - ox) * np.cos(ang_r) - (ry - oy) * np.sin(ang_r)
        ny = oy + (rx - ox) * np.sin(ang_r) + (ry - oy) * np.cos(ang_r)
        # 旋转后外接框扩大
        nw = w2 * (abs(np.cos(ang_r)) + abs(np.sin(ang_r)))
        nh = h2 * (abs(np.sin(ang_r)) + abs(np.cos(ang_r)))
        new_boxes.append([cls, nx / W, ny / H, nw, nh])
    # 裁剪越界标签（中心仍在图内才保留）
    valid = []
    for b in new_boxes:
        cls, cx, cy, w, h = b
        if 0.05 <= cx <= 0.95 and 0.05 <= cy <= 0.95:
            valid.append(b)
    return img, valid


def main():
    AUG_DIR.mkdir(exist_ok=True)
    # 清空旧增强
    for f in AUG_DIR.glob("*"):
        f.unlink()

    count = 0
    for txt in sorted(DATA.glob("sample_*.txt")):
        jpg = DATA / f"{txt.stem}.jpg"
        if not jpg.exists():
            continue
        boxes = read_labels(txt)
        if not boxes:
            continue
        img = cv2.imread(str(jpg))
        if img is None:
            continue
        for i in range(AUG_PER_SAMPLE):
            a_img, a_boxes = augment(img, boxes)
            if len(a_boxes) < 1:
                continue
            stem = f"{txt.stem}_a{i}"
            cv2.imwrite(str(AUG_DIR / f"{stem}.jpg"), a_img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            with open(AUG_DIR / f"{stem}.txt", "w") as f:
                for b in a_boxes:
                    f.write(f"{int(b[0])} {b[1]:.5f} {b[2]:.5f} {b[3]:.5f} {b[4]:.5f}\n")
            count += 1
    print(f"生成增强样本 {count} 个 → {AUG_DIR}")


if __name__ == "__main__":
    main()
