# 95598 验证码训练数据状态记录

## 当前进度（2026-09-07）
- 有效样本：**10 个原始 + 30 个离线增强 = 40 个**（共 26 个真实标注目标）
- 最新模型：`yolo_train/runs/captcha_yolov8n-3/weights/best.pt`
- 验证指标（40 样本数据集，val 12 张/29 实例）：
  - **mAP50 = 0.75**，Precision 0.70，Recall 0.83
  - 全样本命中率：conf=0.25 → 85%，conf=0.1 → 92%（IoU≥0.4）

## 训练轮次
| 轮次 | 数据集 | 结果 |
|---|---|---|
| captcha_yolov8n（08-28） | 6 样本 | mAP50 0.35 |
| captcha_yolov8n-2（09-07） | 10 样本 | mAP50 0.40 |
| **captcha_yolov8n-3（09-07）** | **40 样本（+增强）** | **mAP50 0.75** |

## 样本清单（yolo_data）
| 样本 | 目标数 | 来源 |
|---|---|---|
| sample_000 | 2 | 本地收集（08-25） |
| sample_001 | 3 | 本地收集 |
| sample_002 | 3 | 本地收集 |
| sample_003 | 2 | 本地收集 |
| sample_004 | 3 | 本地收集 |
| sample_005 | 2 | 本地收集 |
| sample_006 | 3 | 本地收集（未入旧训练集） |
| sample_007 | 2 | 本地收集 |
| sample_008 | 3 | 本地收集 |
| sample_009 | 3 | 服务器 captcha_learn 9/6 样本（过滤 1 越界点击） |

增强样本：`yolo_aug/`（每个原始样本 3 个变体：平移/缩放/亮度/微旋转）

## 文件格式
- `sample_NNN.jpg`：复合图（上方答案条 + 下方大图），330x321 或 340x282
- `sample_NNN.txt`：YOLO 标签 `class cx cy w h`（归一化，class=0 target，框 46px）
- `sample_NNN.json`：元数据（标记坐标、来源）

## 明日继续
1. **每天 9:30 自动学习**（captcha_learn.py，VNC 手动点击）→ 新增样本会自动落在
   服务器 `~/sgcc/captcha_samples/learned/`（有 json 的才是有效标注）
2. 拉取新样本：`scp 服务器:learned/*.jpg+json 到本地` → 运行 `python prepare_yolo_data.py`
3. 增强+训练：`python augment_yolo.py && python train_captcha.py`
4. 目标：样本 20+ 原始后，模型 mAP50 有望 >0.9，可构建全自动求解器

## 注意事项（重要！）
1. **账号风控敏感**：95598 登录/点击次数太频繁会触发风控（已触发 2 次）
   - 每轮之间至少间隔 **90 秒**（工具 `--delay 90`）
   - 每次会话最多收集 3-5 个样本
2. 今天的样本（sample_20260907_093002.jpg）**无 json**（VNC 超时未完成点击）→ 无标注，未入训练集
3. 服务器 auto_login 目前仍是 VLM 方案（sgcc_server_auto.py）；YOLO 求解器待构建：
   加载 `best.pt` → 对实时验证码复合图 predict → 按框中心点击
