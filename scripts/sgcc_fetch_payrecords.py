#!/usr/bin/env python3
"""95598 交费记录抓取 → 写入记账模块（水电燃气分类 / 支付宝账户 / 支出）。

流程：CDP 连接常驻 Chrome(9227) → 导航 paymentRecord 交费记录页 → 解析
      (缴费时间, 缴费金额) → 与 finance_transactions 已有记录去重 → INSERT。
日志：/home/dufan2514897261/sgcc/payrecords.log
"""
import json, re, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta

import websocket

CDP_HTTP = "http://127.0.0.1:9227"
PAY_URL = "https://95598.cn/osgweb/paymentRecord"
LOG_FILE = "/home/dufan2514897261/sgcc/payrecords.log"
DB_CTN = ["docker", "exec", "-i", "home-platform-db", "psql",
          "-U", "home", "-d", "homeplatform", "-t", "-c"]

# 记账模块映射：支出 / 水电燃气(5) / 支付宝(3)
CATEGORY_ID = 5
ACCOUNT_ID = 3
NOTE_PREFIX = "电费缴费(95598)"


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_ws_url():
    with urllib.request.urlopen(CDP_HTTP + "/json/list", timeout=6) as r:
        pages = json.loads(r.read())
    for p in pages:
        if "95598" in p.get("url", ""):
            return p["webSocketDebuggerUrl"]
    return None


def ev(ws_url, expression, timeout=30):
    ws = websocket.create_connection(ws_url, timeout=timeout)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expression, "returnByValue": True}}))
    for _ in range(200):
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            ws.close()
            return msg.get("result", {}).get("result", {}).get("value")
    ws.close()
    return None


def cdp(ws_url, method, params=None, timeout=30):
    ws = websocket.create_connection(ws_url, timeout=timeout)
    ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
    for _ in range(200):
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            ws.close()
            return msg
    ws.close()
    return {}


def db_query(sql):
    try:
        out = subprocess.run(DB_CTN + [sql], capture_output=True, text=True, timeout=20)
        return out.stdout.strip()
    except Exception as e:
        log(f"DB 查询异常: {e}")
        return ""


def db_exec(sqls):
    """执行多条 SQL，返回 (成功, 失败) 计数。"""
    ok = fail = 0
    for sql in sqls:
        try:
            out = subprocess.run(DB_CTN + [sql], capture_output=True, text=True, timeout=20)
            if out.returncode == 0:
                ok += 1
            else:
                fail += 1
                log(f"SQL 失败: {sql[:80]} -> {out.stderr.strip()[:100]}")
        except Exception as e:
            fail += 1
            log(f"SQL 异常: {e}")
    return ok, fail


def fetch_payrecords(ws_url):
    """导航交费记录页并解析 (时间, 金额) 列表，失败返回 None。"""
    cdp(ws_url, "Page.navigate", {"url": PAY_URL})
    for attempt in range(3):
        time.sleep(10)
        txt = ev(ws_url, "document.body ? document.body.innerText : ''") or ""
        if "缴费金额" in txt:
            records = re.findall(r"(20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[\s\S]{0,60}?缴费金额:([\d.]+)元", txt)
            return [(t, float(a)) for t, a in records]
        url = ev(ws_url, "location.href") or ""
        log(f"第{attempt + 1}次读取未见缴费记录 URL={url} 文本={txt[:80]!r}")
    return None


def existing_keys():
    """返回已入库的电费缴费记录集合 {(date_str, amount)}。"""
    out = db_query(
        "SELECT to_char(txn_date AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI:SS'), amount "
        "FROM finance_transactions WHERE note LIKE '电费缴费%';")
    keys = set()
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 2:
            keys.add((parts[0].strip(), parts[1].strip()))
    return keys


def main():
    log("===== 95598 交费记录 → 记账 开始 =====")
    ws_url = get_ws_url()
    if not ws_url:
        log("ERROR: 未找到 95598 页面")
        sys.exit(1)

    records = fetch_payrecords(ws_url)
    if records is None:
        sys.exit(1)
    log(f"解析到交费记录 {len(records)} 条")

    existing = existing_keys()
    log(f"记账模块已有电费缴费 {len(existing)} 条")

    sqls = []
    new_count = 0
    for t, amt in records:
        key_date = t.split(" ")[0]
        key = (key_date, f"{amt:.2f}")
        if key in existing or (t, f"{amt:.2f}") in existing:
            continue
        # 用缴费时间作为 txn_date（+08 时区）
        sql = (f"INSERT INTO finance_transactions (txn_type, amount, account_id, category_id, "
               f"note, txn_date) VALUES ('expense', {amt}, {ACCOUNT_ID}, {CATEGORY_ID}, "
               f"'电费缴费(95598) {t}', '{t}+08');")
        sqls.append(sql)
        new_count += 1
        log(f"  新增: {t} 缴费 {amt} 元")

    if sqls:
        ok, fail = db_exec(sqls)
        log(f"写入完成: 成功 {ok} 条, 失败 {fail} 条")
    else:
        log("无新增交费记录（已全部入库）")


if __name__ == "__main__":
    main()
