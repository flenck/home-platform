#!/usr/bin/env python3
"""Train YOLOv8-n on 95598 captcha click-target detection.

Uses the composite images (answer strip on top, big image below) with single
class 'target'. Small dataset — moderate epochs, strong augmentation.
"""
from pathlib import Path
from ultralytics import YOLO

DATA = str(Path(__file__).parent / "yolo_train" / "data.yaml")
MODEL = "yolov8n.pt"          # COCO 预训练
EPOCHS = 120
IMGSZ = 416

model = YOLO(MODEL)
results = model.train(
    data=DATA,
    epochs=EPOCHS,
    imgsz=IMGSZ,
    device="mps",             # Apple GPU
    patience=40,
    batch=8,
    augment=True,
    workers=2,
    project=str(Path(__file__).parent / "yolo_train" / "runs"),
    name="captcha_yolov8n",
    verbose=True,
)
print("训练完成，最优模型: runs/captcha_yolov8n/weights/best.pt")
