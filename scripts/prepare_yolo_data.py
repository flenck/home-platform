#!/usr/bin/env python3
"""把服务器 captcha_learn 学习样本转成 YOLO 标注并纳入训练集，重新划分 train/val。"""
import json
import shutil
from pathlib import Path

SCRIPTS = Path(__file__).parent
YOLO_DATA = SCRIPTS / "yolo_data"
YOLO_TRAIN = SCRIPTS / "yolo_train"
NEW_SAMPLES = Path("/tmp/learned_new")

BOX = 46  # 目标框像素大小（与 sgcc_annotate 一致）


def convert_server_sample(jpg: Path, jsf: Path, out_idx: int):
    """服务器样本 → yolo_data/sample_NNN.{jpg,txt,json}"""
    import numpy as np
    from PIL import Image

    im = Image.open(jpg)
    W, H = im.size
    meta = json.loads(jsf.read_text())
    clicks = meta.get("clicks", [])

    lines = []
    markers = []
    for c in clicks:
        x, y = c["composite_xy"]
        # 过滤越界点（点击落在 crop 区域外 = 无效点击）
        if not (0 <= x < W and 0 <= y < H):
            continue
        cx = round(x / W, 5)
        cy = round(y / H, 5)
        w = round(BOX / W, 5)
        h = round(BOX / H, 5)
        lines.append(f"0 {cx} {cy} {w} {h}")
        markers.append([int(round(x)), int(round(y))])

    if not lines:
        print(f"  ⚠ {jpg.name}: 无有效点击，跳过")
        return None

    stem = f"sample_{out_idx:03d}"
    shutil.copy(jpg, YOLO_DATA / f"{stem}.jpg")
    (YOLO_DATA / f"{stem}.txt").write_text("\n".join(lines))
    (YOLO_DATA / f"{stem}.json").write_text(json.dumps({
        "source": "server_captcha_learn",
        "timestamp": meta.get("timestamp"),
        "markers": markers,
    }, ensure_ascii=False, indent=2))
    print(f"  ✔ {stem}: {len(lines)} 目标（过滤 {len(clicks)-len(lines)} 个越界点）")
    return stem


def rebuild_dataset():
    """把所有 sample_*.jpg/.txt 按编号划分 train/val 复制到 yolo_train。"""
    # 收集现有样本（jpg 存在且 txt 非空）
    samples = []
    for txt in sorted(YOLO_DATA.glob("sample_*.txt")):
        stem = txt.stem
        jpg = YOLO_DATA / f"{stem}.jpg"
        if not jpg.exists():
            print(f"  ⚠ {stem}: 缺 jpg，跳过")
            continue
        n = len(txt.read_text().strip().splitlines())
        if n == 0:
            print(f"  ⚠ {stem}: 空标注，跳过")
            continue
        samples.append((stem, n))
    print(f"\n共 {len(samples)} 个有效样本: {samples}")

    # 划分：最后 2 个做 val，其余 train（保证 val 有代表性）
    train_list = samples[:-2] if len(samples) > 2 else [samples[0]]
    val_list = samples[-2:] if len(samples) > 2 else samples[1:]

    for sub in ("train", "val"):
        (YOLO_TRAIN / "images" / sub).mkdir(parents=True, exist_ok=True)
        (YOLO_TRAIN / "labels" / sub).mkdir(parents=True, exist_ok=True)
        # 清空旧文件
        for f in (YOLO_TRAIN / "images" / sub).glob("*"):
            f.unlink()
        for f in (YOLO_TRAIN / "labels" / sub).glob("*"):
            f.unlink()
        for f in (YOLO_TRAIN / "labels").glob("*.cache"):
            f.unlink()

    for stem, _ in train_list:
        shutil.copy(YOLO_DATA / f"{stem}.jpg", YOLO_TRAIN / "images" / "train" / f"{stem}.jpg")
        shutil.copy(YOLO_DATA / f"{stem}.txt", YOLO_TRAIN / "labels" / "train" / f"{stem}.txt")
    for stem, _ in val_list:
        shutil.copy(YOLO_DATA / f"{stem}.jpg", YOLO_TRAIN / "images" / "val" / f"{stem}.jpg")
        shutil.copy(YOLO_DATA / f"{stem}.txt", YOLO_TRAIN / "labels" / "val" / f"{stem}.txt")

    print(f"\n数据集: train={len(train_list)} val={len(val_list)}")
    print(f"  train: {[s[0] for s in train_list]}")
    print(f"  val:   {[s[0] for s in val_list]}")


if __name__ == "__main__":
    # 1) 转换新样本
    print("=== 转换服务器学习样本 ===")
    for jpg in sorted(NEW_SAMPLES.glob("*.jpg")):
        jsf = jpg.with_suffix(".json")
        if not jsf.exists():
            print(f"  ⚠ {jpg.name}: 无标注 json（用户未完成点击），跳过")
            continue
        # 找下一个可用编号
        used = {int(p.stem.split("_")[1]) for p in YOLO_DATA.glob("sample_*.jpg")}
        nxt = max(used) + 1 if used else 0
        convert_server_sample(jpg, jsf, nxt)

    # 2) 重建训练集
    print("\n=== 重建 yolo_train 数据集 ===")
    rebuild_dataset()
    print("\n完成 ✅")
