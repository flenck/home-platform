#!/usr/bin/env python3
"""Analyze the turing click captcha from the screenshot using glm-4v-flash VLM.

Crops the answer strip + big image from captcha_current.png (scale=css so
pixels == DOM coordinates), asks the VLM for the ordered click targets,
and prints absolute page coordinates (DOM coords).
"""

import base64
import json
import os
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

SCRIPT_DIR = Path(__file__).parent
SHOT = SCRIPT_DIR / "captcha_current.png"

# DOM bboxes from the captcha dump (scale=css, pixels == DOM coords)
BIG_BBOX = (475, 298, 805, 534)          # image-area 330x236
ANSWER_BBOX = (555, 250, 680, 295)       # header-answer strip (generous)

# 智谱 VLM API Key（从环境变量读取，勿提交真实 Key）
VLM_API_KEY = os.environ.get("SGCC_VLM_API_KEY", "")
VLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
VLM_MODEL = "glm-4v-flash"


def b64(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def ask_vlm(big_img: Image.Image, ans_img: Image.Image) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE_URL)
    prompt = (
        "你是一个验证码识别专家。这是一个腾讯安全验证码'请依次点击'。\n"
        "上方小图区域(answers)显示了需要点击的目标对象图片，可能有多张，从左到右就是点击顺序。\n"
        "下方大图区域(big)里包含很多对象，你需要在大图中找到与answers里每个目标对应的对象，"
        "并按照 answers 从左到右的顺序点击它们。\n"
        "请输出严格的JSON：{\"order\": [\"对象1描述\", \"对象2描述\", ...], "
        "\"clicks\": [{\"fx\": 0.31, \"fy\": 0.47}, ...]}，"
        "其中 fx、fy 是每个点击目标在大图中的相对坐标(0-1，以左上角为原点)，clicks 的数组顺序必须与 order 一致。\n"
        "只输出JSON，不要其他文字。"
    )
    resp = client.chat.completions.create(
        model=VLM_MODEL,
        messages=[
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(ans_img)}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(big_img)}"}},
            ]},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def main():
    if not SHOT.exists():
        print("no captcha_current.png — run sgcc_login_interactive.py first")
        sys.exit(1)
    shot = Image.open(SHOT)
    print(f"screenshot size: {shot.size}")
    big = shot.crop(BIG_BBOX)
    ans = shot.crop(ANSWER_BBOX)
    big.save(SCRIPT_DIR / "captcha_big.png")
    ans.save(SCRIPT_DIR / "captcha_answers.png")
    print(f"cropped big {big.size} -> captcha_big.png, answers {ans.size} -> captcha_answers.png")

    content = ask_vlm(big, ans)
    print("VLM raw:", content[:500])
    # strip code fences
    c = content
    if "```json" in c:
        c = c.split("```json")[1].split("```")[0].strip()
    elif "```" in c:
        c = c.split("```")[1].strip()
    result = json.loads(c)

    bx, by = BIG_BBOX[0], BIG_BBOX[1]
    bw, bh = BIG_BBOX[2] - BIG_BBOX[0], BIG_BBOX[3] - BIG_BBOX[1]
    clicks = []
    for i, cl in enumerate(result.get("clicks", [])):
        abs_x = round(bx + cl["fx"] * bw)
        abs_y = round(by + cl["fy"] * bh)
        label = result.get("order", [])[i] if i < len(result.get("order", [])) else f"target{i+1}"
        clicks.append({"x": abs_x, "y": abs_y, "label": label})
    plan = {"clicks": clicks, "order": result.get("order", []), "note": "absolute DOM coords"}
    print("\nPLAN:", json.dumps(plan, ensure_ascii=False, indent=2))
    (SCRIPT_DIR / "captcha_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    print(f"\nplan written to {SCRIPT_DIR / 'captcha_plan.json'}")


if __name__ == "__main__":
    main()
