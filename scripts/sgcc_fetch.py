#!/usr/bin/env python3
"""Fetch State Grid electricity data using persistent browser profile.

First run (manual login):
    python scripts/sgcc_fetch.py --login

Subsequent runs (auto, reuses saved session):
    python scripts/sgcc_fetch.py --mqtt 192.168.1.9:1883

The persistent browser profile at scripts/sgcc_profile/ saves cookies
and localStorage, so you only need to log in once. After that the
script runs headless and automatically fetches data.

Requires: pip install playwright paho-mqtt
           playwright install chromium
"""

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROFILE_DIR = SCRIPT_DIR / "sgcc_profile"
COOKIES_FILE = SCRIPT_DIR / "sgcc_cookies.json"
PAGE_URL = "https://95598.cn/osgweb/electricitySummary?partNo=P02021702"
TIMEOUT_MS = 60_000

# Anti-detection: hide automation traces
ANTI_DETECTION_JS = """
() => {
    // Overwrite the navigator.webdriver property
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    // Overwrite chrome.runtime
    window.chrome = { runtime: {} };
    // Overwrite permissions
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );
    // Overwrite plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });
    // Overwrite languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en'],
    });
}
"""


def extract_vue_state(page) -> dict | None:
    """Try to extract electricity data from Vue app's internal state."""
    js = """
    () => {
        const appEl = document.querySelector('#app');
        if (appEl && appEl.__vue__) {
            const vm = appEl.__vue__;
            function walk(v, depth) {
                if (depth > 10) return null;
                const data = v.$data || v;
                if (data && (data.balance !== undefined || data.electricityList || data.monthBill)) {
                    return data;
                }
                if (v.$children) {
                    for (const child of v.$children) {
                        const r = walk(child, depth + 1);
                        if (r) return r;
                    }
                }
                return null;
            }
            return walk(vm, 0);
        }
        if (appEl && appEl._vue_app) {
            const root = appEl._vue_app._instance;
            if (root && root.proxy) return root.proxy.$data;
        }
        if (window.__pinia) return window.__pinia.state.value;
        if (window.$store && window.$store.state) return window.$store.state;
        for (const key of Object.keys(window)) {
            const obj = window[key];
            if (obj && typeof obj === 'object' && obj.electricityList) return obj;
        }
        return null;
    }
    """
    try:
        return page.evaluate(js)
    except Exception as e:
        print(f"  Vue state extraction failed: {e}")
        return None


def extract_from_dom(page) -> dict | None:
    try:
        return page.evaluate("""
        () => {
            const result = {};
            const body = document.body.innerText;
            const patterns = [
                [/电费余额[^\\d]*([\\d,.]+)\\s*元/, 'balance'],
                [/昨日用电[^\\d]*([\\d,.]+)\\s*kWh/, 'last_daily_usage'],
                [/本月用电[^\\d]*([\\d,.]+)\\s*kWh/, 'month_usage'],
                [/本月电费[^\\d]*([\\d,.]+)\\s*元/, 'month_charge'],
                [/年用电量[^\\d]*([\\d,.]+)\\s*kWh/, 'yearly_usage'],
                [/年电费[^\\d]*([\\d,.]+)\\s*元/, 'yearly_charge'],
            ];
            for (const [re, key] of patterns) {
                const m = body.match(re);
                if (m) result[key] = parseFloat(m[1].replace(/,/g, ''));
            }
            return Object.keys(result).length > 0 ? result : null;
        }
        """)
    except Exception:
        return None


def parse_state_data(raw_data: dict) -> dict:
    result = {}
    if not raw_data:
        return result

    field_maps = [
        (["balance", "Balance", "accountBalance", "eleBalance", "totalBalance"], "balance"),
        (["lastDailyUsage", "lastDaily", "yesterdayUsage", "lastElec", "last_electricity_usage"], "last_daily_usage"),
        (["monthUsage", "monthlyUsage", "monthElec", "curMonthElec", "month_electricity_usage"], "month_usage"),
        (["monthCharge", "monthlyCharge", "monthBill", "curMonthBill", "month_electricity_charge"], "month_charge"),
        (["yearUsage", "yearlyUsage", "yearElec", "annualElec", "yearly_electricity_usage"], "yearly_usage"),
        (["yearCharge", "yearlyCharge", "yearBill", "annualBill", "yearly_electricity_charge"], "yearly_charge"),
    ]

    def deep_find(obj, keys: list[str]):
        if isinstance(obj, dict):
            for k in keys:
                if k in obj:
                    return obj[k]
            for v in obj.values():
                r = deep_find(v, keys)
                if r is not None:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = deep_find(item, keys)
                if r is not None:
                    return r
        return None

    for field_keys, target in field_maps:
        val = deep_find(raw_data, field_keys)
        if val is not None:
            try:
                result[target] = float(val)
            except (ValueError, TypeError):
                pass

    return result


def publish_mqtt(broker_host: str, broker_port: int, user_id: str, data: dict) -> None:
    import paho.mqtt.client as mqtt

    client = mqtt.Client()
    client.connect(broker_host, broker_port, 10)
    client.loop_start()

    for sensor_type, value in data.items():
        topic = f"95598/{user_id}/{sensor_type}/state"
        payload = str(value)
        client.publish(topic, payload, retain=True)
        print(f"  MQTT -> {topic} = {payload}")

    time.sleep(0.5)
    client.loop_stop()
    client.disconnect()


def is_logged_in(page) -> bool:
    """Check if the current page shows electricity data or login form."""
    try:
        text = page.evaluate("() => document.body.innerText")
        # If we see electricity-related text, we're logged in
        indicators = ["电费余额", "用电", "电量", "电费账单", "electricity"]
        if any(ind in text for ind in indicators):
            return True
        # If we see login elements, we're not
        if "登录" in text[:500] or "验证码" in text[:500]:
            return False
        # Check for captcha
        if page.evaluate("""() => {
            const el = document.querySelector('.captcha, #captcha, [id*="captcha"], .yidun, .turing");
            return el !== null;
        }"""):
            return False
        return False
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Fetch SGCC electricity data")
    parser.add_argument("--login", action="store_true",
                        help="Open browser for manual login (first-time setup)")
    parser.add_argument("--mqtt", help="MQTT broker host:port (e.g. 192.168.1.9:1883)")
    parser.add_argument("--user-id", default="0000", help="User ID for MQTT topic")
    parser.add_argument("--screenshot", help="Save debug screenshot")
    parser.add_argument("--no-headless", action="store_true",
                        help="Run with visible browser (for debugging)")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: pip install playwright && playwright install chromium")
        sys.exit(1)

    headless = not args.login and not args.no_headless

    if args.login:
        print("=" * 55)
        print("  FIRST-TIME SETUP: Opening browser for manual login")
        print("  1. Log in at the 95598.cn page")
        print("  2. Navigate to the electricity summary page")
        print("  3. Verify you can see your data, then close the browser")
        print("  Session will be saved for future automated runs.")
        print("=" * 55)

    mqtt_broker = None
    mqtt_port = 1883
    if args.mqtt:
        parts = args.mqtt.split(":")
        mqtt_broker = parts[0]
        mqtt_port = int(parts[1]) if len(parts) > 1 else 1883

    with sync_playwright() as p:
        # Launch args to avoid headless detection
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-setuid-sandbox",
        ]

        if PROFILE_DIR.exists():
            # Use persistent context with saved profile
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/130.0.0.0 Safari/537.36",
                locale="zh-CN",
                args=launch_args,
            )
            print(f"Using persistent profile: {PROFILE_DIR}")
        else:
            # Fresh context with cookies from file
            context = p.chromium.launch(
                headless=headless,
                args=launch_args,
            ).new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/130.0.0.0 Safari/537.36",
                locale="zh-CN",
            )
            # Set cookies if file exists
            if COOKIES_FILE.exists():
                cookies = json.loads(COOKIES_FILE.read_text())
                # Playwright expects expires as number (Unix timestamp in seconds)
                for c in cookies:
                    if "expires" in c and isinstance(c["expires"], (int, float)):
                        c["expires"] = int(c["expires"])
                context.add_cookies(cookies)
                print(f"Set {len(cookies)} cookies from {COOKIES_FILE}")

        page = context.new_page()

        # Inject anti-detection before any navigation
        page.add_init_script(ANTI_DETECTION_JS)

        # Intercept API responses
        api_responses: list[dict] = []
        def on_response(response):
            if response.ok and "application/json" in (response.headers.get("content-type", "")):
                url = response.url
                if any(kw in url.lower() for kw in
                       ["electricity", "balance", "bill", "usage", "account", "elec"]):
                    try:
                        api_responses.append({"url": url, "json": response.json()})
                    except Exception:
                        pass
        page.on("response", on_response)

        print(f"Loading {PAGE_URL} ...")
        try:
            page.goto(PAGE_URL, wait_until="networkidle", timeout=TIMEOUT_MS)
            page.wait_for_timeout(5000)
        except Exception as e:
            print(f"Page load error: {e}")

        if args.screenshot:
            page.screenshot(path=args.screenshot, full_page=True)
            print(f"Screenshot: {args.screenshot}")

        if args.login:
            print("\nBrowser is open. Log in, then press Enter here to finish...")
            input()
            page.screenshot(path=str(SCRIPT_DIR / "sgcc_login_done.png"), full_page=True)
            print("Session saved to", PROFILE_DIR)
            context.close()
            return

        # Check if we're actually logged in
        if not is_logged_in(page):
            print("Not logged in — session expired or cookies invalid.")
            page.screenshot(path=str(SCRIPT_DIR / "sgcc_debug_login.png"), full_page=True)
            print(f"Debug screenshot saved to scripts/sgcc_debug_login.png")

            # If running with saved profile but got login page, offer to re-login
            if headless:
                print("\nTip: Run with --no-headless to see the browser, or --login to re-authenticate.")
            context.close()
            sys.exit(1)

        # ── Data extraction ────────────────────────────────────────────
        data = {}
        for resp in api_responses:
            parsed = parse_state_data(resp["json"])
            data.update(parsed)
        if data:
            print(f"  [API] Got {len(data)} fields from {len(api_responses)} calls")
        else:
            vue_state = extract_vue_state(page)
            if vue_state:
                data = parse_state_data(vue_state)
                if data:
                    print(f"  [Vue] Got {len(data)} fields")
        if not data:
            data = extract_from_dom(page) or {}
            if data:
                print(f"  [DOM] Got {len(data)} fields")
            else:
                html = page.content()
                debug_file = SCRIPT_DIR / "sgcc_debug_page.html"
                debug_file.write_text(html)
                print(f"FAILED — page saved to {debug_file}")
                context.close()
                sys.exit(1)

        context.close()

    # ── Output ────────────────────────────────────────────────────────
    print("\n========== 电费数据 ==========")
    labels = {
        "balance": ("电费余额", "元"),
        "last_daily_usage": ("昨日用电", "kWh"),
        "month_usage": ("月用电量", "kWh"),
        "month_charge": ("月电费", "元"),
        "yearly_usage": ("年用电量", "kWh"),
        "yearly_charge": ("年电费", "元"),
    }
    for key, (label, unit) in labels.items():
        if key in data:
            print(f"  {label}: {data[key]} {unit}")

    if mqtt_broker and data:
        print(f"\nPublishing to MQTT {mqtt_broker}:{mqtt_port} ...")
        publish_mqtt(mqtt_broker, mqtt_port, args.user_id, data)
        print("Done!")

    return data


if __name__ == "__main__":
    main()
