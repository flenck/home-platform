#!/usr/bin/env python3
"""轻量 CV 工具（纯 numpy/PIL，替代 opencv，用于容器内）。

提供：
- rgb2gray        RGB -> 灰度（luminance）
- resize          用 PIL 缩放
- match_template  归一化互相关模板匹配（sliding_window_view 向量化）
- connected_components  8 连通分量（BFS）
- inrange         颜色范围掩码
"""
import numpy as np
from PIL import Image

_LUM = np.array([0.2989, 0.5870, 0.1140], dtype=np.float32)


def rgb2gray(img: np.ndarray) -> np.ndarray:
    return np.dot(img[..., :3].astype(np.float32), _LUM)


def resize(img: np.ndarray, width: int, height: int) -> np.ndarray:
    if img.ndim == 2:
        return np.array(Image.fromarray(img.astype(np.uint8)).resize((width, height), Image.LANCZOS))
    return np.array(Image.fromarray(img.astype(np.uint8)).resize((width, height), Image.LANCZOS))


def inrange(hsv: np.ndarray, lo: tuple, hi: tuple) -> np.ndarray:
    m = np.ones(hsv.shape[:2], dtype=bool)
    for c in range(3):
        m &= (hsv[..., c] >= lo[c]) & (hsv[..., c] <= hi[c])
    return (m * 255).astype(np.uint8)


def match_template(img: np.ndarray, tpl: np.ndarray):
    """归一化互相关，返回 (scores, (x, y))。img/tpl 均为灰度 float。"""
    from numpy.lib.stride_tricks import sliding_window_view
    H, W = img.shape
    h, w = tpl.shape
    if h > H or w > W:
        return None, (0, 0)
    t = tpl.astype(np.float64)
    t_mean = t.mean()
    t_std = np.sqrt(((t - t_mean) ** 2).sum())
    if t_std == 0:
        return None, (0, 0)
    tc = t - t_mean
    wins = sliding_window_view(img, (h, w)).astype(np.float64)  # (H-h+1, W-w+1, h, w)
    win_sum = wins.sum(axis=(2, 3))
    win_sq = (wins ** 2).sum(axis=(2, 3))
    num = np.tensordot(wins, tc, axes=([2, 3], [0, 1]))
    denom = np.sqrt(np.maximum(win_sq - win_sum ** 2 / (h * w), 0)) * t_std
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = np.where(denom > 0, num / denom, 0)
    idx = np.unravel_index(np.argmax(scores), scores.shape)
    return scores, (idx[1], idx[0])   # (x, y) top-left


def connected_components(mask: np.ndarray):
    """8 连通分量。返回 (n, [(x, y, w, h, area)], [(cx, cy)])。mask: uint8 0/255"""
    H, W = mask.shape
    labels = np.zeros((H, W), dtype=np.int32)
    comps = []
    cur = 1
    for y in range(H):
        for x in range(W):
            if mask[y, x] == 0 or labels[y, x] != 0:
                continue
            # BFS
            stack = [(y, x)]
            labels[y, x] = cur
            pts = []
            while stack:
                cy, cx = stack.pop()
                pts.append((cy, cx))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] != 0 and labels[ny, nx] == 0:
                            labels[ny, nx] = cur
                            stack.append((ny, nx))
            ys = [p[0] for p in pts]
            xs = [p[1] for p in pts]
            comps.append((min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1,
                          len(pts), (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2))
            cur += 1
    return cur - 1, comps
