/* ════════════════════════════════════════════════════════════
   Home Platform · 全屋智能中枢 · 帅气版
   ════════════════════════════════════════════════════════════ */
const API_BASE = "";
const REFRESH_INTERVAL = 5000;

// ── State ─────────────────────────────────────────────────────
let tdsChart = null;
let tempChart = null;
let dailyEnergyChart = null;
let monthlyEnergyChart = null;
let currentPage = "overview";
let connectionOk = true;
let lastStates = [];
let lastTdsHistory = [];
let lastDaily = [];
let lastMonthly = [];

// ── Sidebar ───────────────────────────────────────────────────
const sidebar = document.getElementById("sidebar");
const hamburger = document.getElementById("hamburger");
const mainWrapper = document.getElementById("mainWrapper");
let sidebarOpen = window.innerWidth > 768;

function updateSidebar() {
    if (window.innerWidth > 768) {
        sidebar.classList.toggle("collapsed", !sidebarOpen);
        mainWrapper.classList.toggle("expanded", !sidebarOpen);
    } else {
        sidebar.classList.toggle("open", sidebarOpen);
        document.getElementById("sidebarOverlay")?.classList.toggle("visible", sidebarOpen);
    }
}
hamburger.addEventListener("click", () => { sidebarOpen = !sidebarOpen; updateSidebar(); });

const overlay = document.createElement("div");
overlay.className = "sidebar-overlay";
overlay.id = "sidebarOverlay";
overlay.addEventListener("click", () => { sidebarOpen = false; updateSidebar(); });
document.body.appendChild(overlay);
window.addEventListener("resize", updateSidebar);
updateSidebar();

// ── Page Navigation ───────────────────────────────────────────
const pageTitles = {
    overview: ["概览", "实时掌握全屋状态"],
    water: ["水质", "TDS 水质监测 · 健康评估"],
    weather: ["天气", "室外环境 · 舒适度评估"],
    energy: ["能耗", "电力消耗 · 费用洞察"],
    news: ["新闻", "AI / 科技热点 · GitHub 新奇项目"],
    baby: ["宝宝", "喂养 · 睡眠 · 成长记录"],
    finance: ["记账", "收入 · 支出 · 预算（Firefly III 风格）"],
    config: ["配置", "系统参数"],
};

document.querySelectorAll(".nav-item[data-page]").forEach(item => {
    item.addEventListener("click", () => {
        const page = item.dataset.page;
        if (item.classList.contains("disabled")) return;
        document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
        item.classList.add("active");
        currentPage = page;
        document.getElementById("pageTitle").textContent = pageTitles[page]?.[0] || page;
        document.getElementById("pageSub").textContent = pageTitles[page]?.[1] || "";
        document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
        const pageEl = document.getElementById(`page-${page}`);
        if (pageEl) pageEl.classList.add("active");
        if (page === "water") ensureTdsChart();
        if (page === "energy") { ensureEnergyCharts(); fetchEnergyData(); }
        if (page === "weather") { ensureTempChart(); fetchWeatherData(); }
        if (page === "news") fetchNewsData();
        if (page === "baby") { ensureBabyCharts(); renderBabyForm(); fetchBabyData(); }
        if (page === "finance") { ensureFinanceCharts(); fetchFinanceData(); }
        if (page === "overview") renderOverviewHot();
        if (window.innerWidth <= 768) { sidebarOpen = false; updateSidebar(); }
    });
});

// ── Live Clock ────────────────────────────────────────────────
const clockEl = document.getElementById("liveClock");
function tickClock() {
    if (!clockEl) return;
    const d = new Date();
    clockEl.textContent = d.toLocaleTimeString("zh-CN", { hour12: false });
}
setInterval(tickClock, 1000);
tickClock();

// ── Helpers ───────────────────────────────────────────────────
function fmtTime(iso) {
    if (!iso) return "--";
    const d = new Date(iso);
    if (isNaN(d)) return "--";
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtDateTime(iso) {
    if (!iso) return "--";
    const d = new Date(iso);
    if (isNaN(d)) return "--";
    return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function num(v) { return (v === null || v === undefined || isNaN(v)) ? null : Number(v); }
function fmt(v, digits = 1) { return v === null ? "--" : Number(v).toFixed(digits); }
function setText(id, t) { const el = document.getElementById(id); if (el) el.textContent = t; }

function animateValue(el, target, digits = 1, suffix = "") {
    if (!el) return;
    const from = parseFloat(el.textContent) || 0;
    if (target === null || target === undefined) { el.textContent = "--"; return; }
    const to = Number(target);
    if (Math.abs(to - from) < 0.01) { el.textContent = to.toFixed(digits); return; }
    const dur = 500, t0 = performance.now();
    function step(now) {
        const p = Math.min((now - t0) / dur, 1);
        const eased = 1 - Math.pow(1 - p, 3);
        el.textContent = (from + (to - from) * eased).toFixed(digits);
        if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

// ── TDS classification ────────────────────────────────────────
function classifyTds(value) {
    if (value === null || value === undefined) return ["等待数据", ""];
    if (value < 10) return ["纯净水", "pure"];
    if (value < 90) return ["山泉/矿化水", "good"];
    if (value < 260) return ["净化水", "good"];
    if (value < 600) return ["自来水", "warn"];
    return ["污染水", "bad"];
}
const TDS_ADVICE = {
    pure: "水质极佳，接近纯净水标准，可放心直饮与日常使用。",
    good: "含有适量矿物质，是理想的饮用水品质，保持当前过滤状态。",
    warn: "TDS 偏高，属于自来水/硬水范畴，建议关注净水器滤芯状态。",
    bad: "TDS 严重超标，存在污染风险，请立即检查水源与过滤系统！",
};

// ── Gauge ─────────────────────────────────────────────────────
const GAUGE_CIRC = 283; // π * 90
function updateGauge(value) {
    const arc = document.getElementById("gaugeArc");
    const gv = document.getElementById("gaugeValue");
    if (!arc) return;
    const frac = value === null || value === undefined ? 0 : Math.min(Math.max(value / 1000, 0), 1);
    arc.style.strokeDashoffset = GAUGE_CIRC * (1 - frac);
    if (gv) gv.textContent = value === null || value === undefined ? "--" : fmt(value, 0);
}

// ── Chart theme helpers ───────────────────────────────────────
const chartGrid = "rgba(255,255,255,0.05)";
const chartTick = "#8b93a7";
const axisFont = { size: 11 };

function baseScales() {
    return {
        x: { grid: { display: false }, ticks: { color: chartTick, maxRotation: 30, font: axisFont } },
        y: { beginAtZero: true, grid: { color: chartGrid }, ticks: { color: chartTick, font: axisFont } },
    };
}

// ── Water chart ───────────────────────────────────────────────
function ensureTdsChart() {
    const canvas = document.getElementById("tdsChart");
    if (!canvas || tdsChart) return;
    const ctx = canvas.getContext("2d");
    tdsChart = new Chart(ctx, {
        type: "line",
        data: { labels: [], datasets: [{ label: "TDS (ppm)", data: [], borderColor: "#22d3ee", backgroundColor: (c) => {
            const { ctx: c2, chartArea } = c.chart;
            if (!chartArea) return "rgba(34,211,238,0.05)";
            const g = c2.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
            g.addColorStop(0, "rgba(34,211,238,0.30)");
            g.addColorStop(1, "rgba(34,211,238,0.02)");
            return g;
        }, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2.5, pointBackgroundColor: "#22d3ee" }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { intersect: false, mode: "index" },
            scales: baseScales(),
            plugins: {
                legend: { display: false },
                tooltip: { backgroundColor: "#0f172a", borderColor: "rgba(34,211,238,0.3)", borderWidth: 1, titleColor: "#e8ecf4", bodyColor: "#e8ecf4", padding: 10, displayColors: false },
            },
        },
    });
}

// ── Temperature chart ─────────────────────────────────────────
function ensureTempChart() {
    const canvas = document.getElementById("tempChart");
    if (!canvas || tempChart) return;
    const ctx = canvas.getContext("2d");
    tempChart = new Chart(ctx, {
        type: "line",
        data: { labels: [], datasets: [{ label: "室外温度 (°C)", data: [], borderColor: "#fbbf24", backgroundColor: (c) => {
            const { ctx: c2, chartArea } = c.chart;
            if (!chartArea) return "rgba(251,191,36,0.05)";
            const g = c2.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
            g.addColorStop(0, "rgba(251,191,36,0.28)");
            g.addColorStop(1, "rgba(251,191,36,0.02)");
            return g;
        }, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2.5, pointBackgroundColor: "#fbbf24" }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { intersect: false, mode: "index" },
            scales: baseScales(),
            plugins: { legend: { display: false }, tooltip: { backgroundColor: "#0f172a", borderColor: "rgba(251,191,36,0.3)", borderWidth: 1, titleColor: "#e8ecf4", bodyColor: "#e8ecf4", padding: 10, displayColors: false } },
        },
    });
}

// ── Energy charts ─────────────────────────────────────────────
function ensureEnergyCharts() {
    const dc = document.getElementById("dailyEnergyChart");
    if (dc && !dailyEnergyChart) {
        dailyEnergyChart = new Chart(dc.getContext("2d"), {
            type: "bar",
            data: { labels: [], datasets: [{ label: "用电量 (kWh)", data: [], backgroundColor: (c) => {
                const { ctx, chartArea } = c.chart;
                if (!chartArea) return "rgba(34,211,238,0.5)";
                const g = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                g.addColorStop(0, "rgba(34,211,238,0.10)");
                g.addColorStop(1, "rgba(34,211,238,0.65)");
                return g;
            }, borderColor: "#22d3ee", borderWidth: 1, borderRadius: 5, maxBarThickness: 26 }] },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: baseScales(),
                plugins: { legend: { display: false }, tooltip: { backgroundColor: "#0f172a", borderColor: "rgba(34,211,238,0.3)", borderWidth: 1, titleColor: "#e8ecf4", bodyColor: "#e8ecf4", padding: 10, displayColors: false } },
            },
        });
    }
    const mc = document.getElementById("monthlyEnergyChart");
    if (mc && !monthlyEnergyChart) {
        monthlyEnergyChart = new Chart(mc.getContext("2d"), {
            data: {
                labels: [],
                datasets: [
                    { label: "用电量 (kWh)", data: [], type: "bar", backgroundColor: "rgba(139,92,246,0.45)", borderColor: "#8b5cf6", borderWidth: 1, borderRadius: 5, maxBarThickness: 30, yAxisID: "y" },
                    { label: "电费 (元)", data: [], type: "line", borderColor: "#fbbf24", backgroundColor: "rgba(251,191,36,0.08)", fill: true, tension: 0.3, pointRadius: 3, pointBackgroundColor: "#fbbf24", borderWidth: 2, yAxisID: "y1" },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: {
                    x: { grid: { display: false }, ticks: { color: chartTick, maxRotation: 45, font: axisFont } },
                    y: { beginAtZero: true, position: "left", title: { display: true, text: "kWh", color: chartTick }, grid: { color: chartGrid }, ticks: { color: chartTick, font: axisFont } },
                    y1: { beginAtZero: true, position: "right", title: { display: true, text: "元", color: chartTick }, grid: { display: false }, ticks: { color: "#fbbf24", font: axisFont } },
                },
                plugins: { legend: { labels: { color: chartTick, usePointStyle: true, boxWidth: 8 } }, tooltip: { backgroundColor: "#0f172a", borderColor: "rgba(139,92,246,0.3)", borderWidth: 1, titleColor: "#e8ecf4", bodyColor: "#e8ecf4", padding: 10, displayColors: true } },
            },
        });
    }
}

// ── Connection status ─────────────────────────────────────────
function updateConnectionStatus(ok) {
    connectionOk = ok;
    const dot = document.querySelector(".status-dot");
    const text = document.querySelector(".status-text");
    if (dot) dot.className = "status-dot" + (ok ? "" : " disconnected");
    if (text) text.textContent = ok ? "已连接" : "断开";
}

// ── Overview KPIs ─────────────────────────────────────────────
function kpiIcon(name) {
    const icons = {
        water: '<svg viewBox="0 0 24 24"><path d="M12 2c-4.5 6-8 10.5-8 14a8 8 0 0 0 16 0c0-3.5-3.5-8-8-14z"/></svg>',
        temp: '<svg viewBox="0 0 24 24"><path d="M15 13V5a3 3 0 0 0-6 0v8a5 5 0 1 0 6 0zm-3-9a1 1 0 0 1 1 1v3h-2V5a1 1 0 0 1 1-1z"/></svg>',
        hum: '<svg viewBox="0 0 24 24"><path d="M12 2c-4.5 6-8 10.5-8 14a8 8 0 0 0 16 0c0-3.5-3.5-8-8-14z"/></svg>',
        balance: '<svg viewBox="0 0 24 24"><path d="M11.8 10.9c-2.27-.59-3-1.2-3-2.15 0-1.09 1.01-1.85 2.7-1.85 1.78 0 2.44.85 2.5 2.1h2.21c-.07-1.72-1.12-3.3-3.21-3.81V3h-3v2.16c-1.94.42-3.5 1.68-3.5 3.61 0 2.31 1.91 3.46 4.7 4.13 2.5.6 3 1.48 3 2.41 0 .69-.49 1.79-2.7 1.79-2.06 0-2.87-.92-2.98-2.1h-2.2c.12 2.19 1.76 3.42 3.68 3.83V21h3v-2.15c1.95-.37 3.5-1.5 3.5-3.55 0-2.84-2.43-3.81-4.7-4.4z"/></svg>',
        bolt: '<svg viewBox="0 0 24 24"><path d="M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42A6.92 6.92 0 0 1 19 12a7 7 0 0 1-14 0c0-2.74 1.55-5.11 3.83-6.35L7.41 4.23A9 9 0 0 0 3 12a9 9 0 0 0 18 0c0-3.28-1.75-6.16-4.17-7.83z"/></svg>',
    };
    return icons[name] || "";
}

function buildKpi({ icon, iconBg, iconColor, label, value, unit, valueCls = "", sub }) {
    return `
    <div class="glass kpi-card">
        <div class="kpi-head">
            <span class="kpi-icon" style="background:${iconBg};color:${iconColor};">${icon}</span>
            <span class="kpi-label">${label}</span>
        </div>
        <div class="kpi-value-row">
            <span class="kpi-value ${valueCls}" data-kpi="${label}">${value}</span>
            ${unit ? `<span class="kpi-unit">${unit}</span>` : ""}
        </div>
        ${sub ? `<div class="kpi-sub">${sub}</div>` : ""}
    </div>`;
}

function renderOverviewKpis() {
    const grid = document.getElementById("overview-kpis");
    if (!grid) return;
    const states = lastStates;
    const w = {};
    for (const s of states) {
        const a = s.attributes || {};
        if (a.sensor_id === "weather" || a.sensor_id === "weather-open-meteo") w[a.metric] = s;
        if (a.sensor_id === "esp32-tds-sensor" || a.metric === "tds") w.tds = s;
    }

    // TDS
    const tdsVal = num(w.tds && w.tds.attributes ? w.tds.attributes.value : null);
    const [tdsLabel, tdsCls] = classifyTds(tdsVal);

    // Weather
    const tempVal = num(w.temperature && w.temperature.attributes ? w.temperature.attributes.value : null);
    const humVal = num(w.humidity && w.humidity.attributes ? w.humidity.attributes.value : null);
    const cond = (w.weather && w.weather.state) || "--";

    // Energy summary (fetched separately, store in module var)
    const e = window.__energySummary || {};

    const tdsSub = tdsVal !== null ? `<span class="arrow">●</span> ${tdsLabel}` : "";
    const tempSub = cond !== "--" ? `<span class="arrow">☁</span> ${cond}` : "";
    const projDays = energyProjection();

    grid.innerHTML =
        buildKpi({ icon: kpiIcon("water"), iconBg: "rgba(34,211,238,0.13)", iconColor: "#22d3ee", label: "水质 TDS", value: fmt(tdsVal, 0), unit: "ppm", valueCls: tdsCls, sub: tdsSub }) +
        buildKpi({ icon: kpiIcon("temp"), iconBg: "rgba(251,191,36,0.13)", iconColor: "#fbbf24", label: "室外温度", value: fmt(tempVal, 1), unit: "°C", sub: tempSub }) +
        buildKpi({ icon: kpiIcon("hum"), iconBg: "rgba(59,130,246,0.13)", iconColor: "#3b82f6", label: "室外湿度", value: fmt(humVal, 0), unit: "%" }) +
        buildKpi({ icon: kpiIcon("balance"), iconBg: "rgba(52,211,153,0.13)", iconColor: "#34d399", label: "电费余额", value: fmt(num(e.balance && e.balance.value), 2), unit: "元", valueCls: e.balance && num(e.balance.value) !== null && num(e.balance.value) < 50 ? "warn" : "", sub: projDays ? `<span class="arrow">◷</span> 预计可用 ${projDays} 天` : "" }) +
        buildKpi({ icon: kpiIcon("bolt"), iconBg: "rgba(139,92,246,0.15)", iconColor: "#8b5cf6", label: "本月用电量", value: fmt(num(e.monthly_usage && e.monthly_usage.value), 1), unit: "kWh" }) +
        buildKpi({ icon: kpiIcon("bolt"), iconBg: "rgba(248,113,113,0.13)", iconColor: "#f87171", label: "本月电费", value: fmt(num(e.monthly_charge && e.monthly_charge.value), 1), unit: "元" });

    // hero
    const tdsStat = tdsVal !== null ? `${fmt(tdsVal, 0)} ppm · ${tdsLabel}` : "暂无数据";
    setText("heroSub", `TDS ${tdsStat} · 温度 ${tempVal !== null ? fmt(tempVal, 1) + "°C" : "--"} · 余额 ${e.balance ? fmt(num(e.balance.value), 2) : "--"} 元`);
    setText("heroComponents", states.length ? `${states.length} 个` : "0");
    setText("heroUpdated", "实时刷新");
}

// ── Overview insights ─────────────────────────────────────────
function renderInsights() {
    const row = document.getElementById("overviewInsights");
    if (!row) return;
    const states = lastStates;
    const chips = [];

    // Water
    const tdsState = states.find(s => s.attributes && (s.attributes.metric === "tds"));
    const tdsVal = tdsState ? num(tdsState.attributes.value) : null;
    const [tdsLabel, tdsCls] = classifyTds(tdsVal);
    const chipCls = tdsCls === "pure" ? "chip-cyan" : tdsCls === "good" ? "chip-green" : tdsCls === "warn" ? "chip-amber" : tdsCls === "bad" ? "chip-red" : "chip-blue";
    chips.push(`<div class="insight-chip"><span class="chip-ico ${chipCls}">💧</span><span><b>水质</b> · ${tdsVal !== null ? `<b>${tdsLabel}</b> · ${TDS_ADVICE[tdsCls]}` : "等待传感器数据接入"}</span></div>`);

    // Energy projection
    const days = energyProjection();
    if (days) {
        chips.push(`<div class="insight-chip"><span class="chip-ico chip-amber">⚡</span><span><b>电费余额</b> · 按近期日均消耗预计可用 <b>${days} 天</b></span></div>`);
    } else if (lastDaily.length) {
        chips.push(`<div class="insight-chip"><span class="chip-ico chip-amber">⚡</span><span>电费数据同步中，稍后给出续航预估</span></div>`);
    }

    // Weather comfort
    const tempS = states.find(s => s.attributes && s.attributes.metric === "temperature");
    const humS = states.find(s => s.attributes && s.attributes.metric === "humidity");
    const tv = tempS ? num(tempS.attributes.value) : null;
    const hv = humS ? num(humS.attributes.value) : null;
    if (tv !== null) {
        const [label, ico, cls] = comfortOf(tv, hv);
        chips.push(`<div class="insight-chip"><span class="chip-ico ${cls}">${ico}</span><span><b>体感</b> · ${label}</span></div>`);
    }

    row.innerHTML = chips.join("");
}

// ── Weather ───────────────────────────────────────────────────
function comfortOf(temp, hum) {
    const t = num(temp);
    if (t === null) return ["--", "◉", "chip-blue"];
    let label, ico = "◉", cls = "chip-blue";
    if (t < 5) { label = "寒冷，注意保暖"; ico = "❄"; cls = "chip-blue"; }
    else if (t < 15) { label = "偏凉，建议加衣"; ico = "☁"; cls = "chip-cyan"; }
    else if (t < 24) { label = "舒适宜人"; ico = "☀"; cls = "chip-green"; }
    else if (t < 30) { label = "偏暖，体感闷热"; ico = "☀"; cls = "chip-amber"; }
    else { label = "炎热，注意防暑"; ico = "🔥"; cls = "chip-red"; }
    if (hum !== null && hum > 75 && t > 24) { label = "闷热潮湿，注意通风"; cls = "chip-red"; }
    return [label, ico, cls];
}

async function fetchWeatherData() {
    try {
        const states = await (await fetch(`${API_BASE}/api/v1/states`)).json();
        lastStates = states;
        const w = {};
        for (const s of states) {
            const a = s.attributes || {};
            if (a.sensor_id === "weather" || a.sensor_id === "weather-open-meteo") w[a.metric] = s;
        }
        const cond = (w.weather && w.weather.state) || "--";
        const temp = w.temperature ? num(w.temperature.attributes.value) : null;
        const hum = w.humidity ? num(w.humidity.attributes.value) : null;
        const wind = w.wind_speed ? num(w.wind_speed.attributes.value) : null;
        const loc = (w.temperature && (w.temperature.attributes.location || "")) || "--";

        setText("weather-condition-card", cond);
        setText("weather-location-card", `${loc} · ${cond}`);
        animateValue(document.getElementById("weather-temp-card"), temp, 1);
        animateValue(document.getElementById("weather-humidity-card"), hum, 0);
        animateValue(document.getElementById("weather-wind-card"), wind, 1);
        const [cLabel, , cCls] = comfortOf(temp, hum);
        const comfortEl = document.getElementById("weather-comfort");
        if (comfortEl) { comfortEl.textContent = cLabel; comfortEl.style.color = cCls === "chip-red" ? "var(--red)" : cCls === "chip-amber" ? "var(--amber)" : cCls === "chip-green" ? "var(--green)" : "var(--cyan)"; }

        // history
        const [h1, h2] = await Promise.all([
            fetch(`${API_BASE}/api/v1/readings?sensor_id=weather-open-meteo&limit=288`).then(r => r.json()),
            fetch(`${API_BASE}/api/v1/readings?sensor_id=weather&limit=288`).then(r => r.json()),
        ]);
        const merged = [...(h1.data || []), ...(h2.data || [])]
            .filter(r => r.metric === "temperature")
            .sort((a, b) => new Date(a.time) - new Date(b.time));
        const seen = new Set();
        const rows = merged.filter(r => { const k = r.time.slice(0, 16); if (seen.has(k)) return false; seen.add(k); return true; });
        if (rows.length > 0) {
            ensureTempChart();
            if (tempChart) {
                tempChart.data.labels = rows.map(r => fmtTime(r.time));
                tempChart.data.datasets[0].data = rows.map(r => r.value);
                tempChart.update("none");
            }
            const vals = rows.map(r => r.value);
            const lo = Math.min(...vals), hi = Math.max(...vals), last = vals[vals.length - 1];
            setText("weatherChartHint", `近 ${rows.length} 个样本 · 最低 ${fmt(lo, 1)}°C · 最高 ${fmt(hi, 1)}°C · 当前 ${fmt(last, 1)}°C`);
        }
        renderOverviewKpis();
        renderInsights();
    } catch (err) {
        console.error("Weather fetch error:", err);
    }
}

// ── Energy ────────────────────────────────────────────────────
function energyProjection() {
    const e = window.__energySummary || {};
    const balance = e.balance ? num(e.balance.value) : null;
    if (balance === null || balance <= 0) return null;
    const daily = lastDaily.map(r => num(r.value)).filter(v => v !== null && v > 0);
    if (!daily.length) return null;
    const avg = daily.reduce((a, b) => a + b, 0) / daily.length;
    if (avg <= 0) return null;
    return Math.max(Math.floor(balance / avg), 0);
}

async function fetchEnergyData() {
    try {
        const [summaryRes, dailyRes, monthlyRes] = await Promise.all([
            fetch(`${API_BASE}/api/v1/energy/summary`),
            fetch(`${API_BASE}/api/v1/energy/daily?days=30`),
            fetch(`${API_BASE}/api/v1/energy/monthly?months=36`),
        ]);
        const summary = await summaryRes.json();
        lastDaily = await dailyRes.json();
        lastMonthly = await monthlyRes.json();
        window.__energySummary = summary;

        renderEnergyPage();
    } catch (err) {
        console.error("Energy fetch error:", err);
    }
}

function renderEnergyPage() {
    const e = window.__energySummary || {};
    const bal = num(e.balance && e.balance.value);
    const mUsage = num(e.monthly_usage && e.monthly_usage.value);
    const mCharge = num(e.monthly_charge && e.monthly_charge.value);
    const yUsage = num(e.yearly_usage && e.yearly_usage.value);
    const yCharge = num(e.yearly_charge && e.yearly_charge.value);

    // 采集/登录状态提示（95598 自动登录失败时提醒手动登录）
    const st = e.sgcc_status || {};
    const alertEl = document.getElementById("energy-alert");
    if (alertEl) {
        let msg = "";
        if (st.login_failed === true || st.fetch_failed === true) {
            const t = st.updated_at ? st.updated_at.slice(5, 16).replace("-", "/") : "最近";
            msg = `⚠️ <b>电费自动获取失败</b>（${t}）：服务器自动登录未通过验证码。请在服务器的 VNC 浏览器中手动登录 95598，系统会自动学习并恢复每日采集。`;
        } else if (st.last_fetch_at && !st.fetch_ok) {
            msg = `ℹ️ 最近一次成功采集：${st.last_fetch_at.slice(5, 16).replace("-", "/")}，数据可能较旧。`;
        }
        alertEl.innerHTML = msg;
        alertEl.style.display = msg ? "block" : "none";
    }

    const grid = document.getElementById("energy-kpis");
    if (grid) {
        const days = energyProjection();
        grid.innerHTML =
            buildKpi({ icon: kpiIcon("balance"), iconBg: "rgba(52,211,153,0.13)", iconColor: "#34d399", label: "电费余额", value: fmt(bal, 2), unit: "元", valueCls: bal !== null && bal < 50 ? "warn" : "", sub: days ? `<span class="arrow">◷</span> 预计可用 ${days} 天` : "" }) +
            buildKpi({ icon: kpiIcon("bolt"), iconBg: "rgba(139,92,246,0.15)", iconColor: "#8b5cf6", label: "本月用电量", value: fmt(mUsage, 1), unit: "kWh", sub: monthlyDelta(mUsage, "usage") }) +
            buildKpi({ icon: kpiIcon("bolt"), iconBg: "rgba(248,113,113,0.13)", iconColor: "#f87171", label: "本月电费", value: fmt(mCharge, 1), unit: "元", sub: monthlyDelta(mCharge, "charge") }) +
            buildKpi({ icon: kpiIcon("bolt"), iconBg: "rgba(59,130,246,0.13)", iconColor: "#3b82f6", label: "年度用电量", value: fmt(yUsage, 1), unit: "kWh" }) +
            buildKpi({ icon: kpiIcon("balance"), iconBg: "rgba(251,191,36,0.13)", iconColor: "#fbbf24", label: "年度电费", value: fmt(yCharge, 1), unit: "元" });
    }

    // daily chart
    if (dailyEnergyChart && lastDaily.length) {
        dailyEnergyChart.data.labels = lastDaily.map(r => {
            const d = new Date(r.time);
            return d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
        });
        dailyEnergyChart.data.datasets[0].data = lastDaily.map(r => r.value);
        dailyEnergyChart.update("none");
        const vals = lastDaily.map(r => num(r.value)).filter(v => v !== null);
        if (vals.length) {
            const maxV = Math.max(...vals);
            const maxD = lastDaily[vals.indexOf(maxV)];
            const md = maxD ? new Date(maxD.time).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }) : "--";
            setText("energyDailyHint", `峰值 ${fmt(maxV, 1)} kWh（${md}）`);
        }
    }

    // monthly chart
    if (monthlyEnergyChart && lastMonthly.length) {
        monthlyEnergyChart.data.labels = lastMonthly.map(r => r.month);
        monthlyEnergyChart.data.datasets[0].data = lastMonthly.map(r => r.usage);
        monthlyEnergyChart.data.datasets[1].data = lastMonthly.map(r => r.charge);
        monthlyEnergyChart.update("none");
        setText("energyMonthlyHint", `${lastMonthly.length} 个月`);
    }

    // insight
    renderEnergyInsight(bal, mUsage, mCharge);
}

function monthlyDelta(curVal, metric = "usage") {
    if (curVal === null || lastMonthly.length < 2) return "";
    const prev = lastMonthly[lastMonthly.length - 2];
    const cur = num(curVal);
    const pv = num(metric === "charge" ? prev.charge : prev.usage);
    if (pv === null || pv === 0) return "";
    const diff = ((cur - pv) / pv) * 100;
    const cls = diff > 0 ? "down" : "up";
    const arrow = diff > 0 ? "▲" : "▼";
    return `<span class="arrow">${arrow}</span> 较 ${prev.month} ${Math.abs(diff).toFixed(1)}%`;
}

function renderEnergyInsight(bal, mUsage, mCharge) {
    const el = document.getElementById("energy-insight");
    if (!el) return;
    const parts = [];
    const days = energyProjection();
    if (bal !== null && days !== null) {
        parts.push(`💰 当前电费余额 <b>${fmt(bal, 2)} 元</b>，按近期日均消耗预计可持续 <b>约 ${days} 天</b>${days < 15 ? "，余额偏低，建议及时充值" : "，余额充足"}。`);
    } else if (bal !== null) {
        parts.push(`💰 当前电费余额 <b>${fmt(bal, 2)} 元</b>。暂无近期日用电记录，暂时无法估算可用天数。`);
    } else {
        parts.push("💰 电费余额数据等待同步…");
    }
    if (mUsage !== null) {
        parts.push(`⚡ 本月已用电 <b>${fmt(mUsage, 1)} kWh</b>，电费 <b>${fmt(mCharge, 1)} 元</b>。`);
    }
    if (lastMonthly.length >= 2) {
        const cur = lastMonthly[lastMonthly.length - 1];
        const prev = lastMonthly[lastMonthly.length - 2];
        if (prev.usage > 0) {
            const d = ((cur.usage - prev.usage) / prev.usage) * 100;
            parts.push(`📊 环比上月用电${d >= 0 ? "上升" : "下降"} <b>${Math.abs(d).toFixed(1)}%</b>${d < 0 ? "，节电效果明显 👍" : "，建议关注高耗电设备。"}`);
        }
    } else if (mUsage !== null) {
        parts.push("📊 积累更多月度数据后，将自动生成环比分析。");
    }
    el.innerHTML = parts.join("<br>");
}

// ── Water page ────────────────────────────────────────────────
function renderWaterPage(latest, history) {
    const value = latest ? num(latest.value) : null;
    const [label, cls] = classifyTds(value);

    // big value + badge
    animateValue(document.getElementById("tds-value-card"), value, 1);
    setText("tds-time-card", latest ? `更新于 ${fmtTime(latest.time)}` : "--");
    const badge = document.getElementById("tds-badge");
    if (badge) { badge.textContent = label; badge.className = "grade-badge " + cls; }
    updateGauge(value);

    // stats from history
    const vals = (history || []).map(r => num(r.value)).filter(v => v !== null);
    const min = vals.length ? Math.min(...vals) : null;
    const max = vals.length ? Math.max(...vals) : null;
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    setText("tds-min", fmt(min, 1));
    setText("tds-avg", fmt(avg, 1));
    setText("tds-max", fmt(max, 1));

    // trend: last vs avg
    const trendEl = document.getElementById("tds-trend");
    if (trendEl) {
        if (value !== null && avg !== null) {
            const diff = value - avg;
            trendEl.textContent = diff >= 0 ? `↑ +${diff.toFixed(1)}` : `↓ ${diff.toFixed(1)}`;
            trendEl.className = "mini-value " + (diff > 10 ? "bad" : diff < -10 ? "good" : "");
        } else {
            trendEl.textContent = "--";
            trendEl.className = "mini-value";
        }
    }

    // insight
    const insight = document.getElementById("water-insight");
    if (insight) {
        if (value === null) {
            insight.textContent = "等待传感器数据，将自动给出水质分析与建议。";
        } else {
            let txt = `当前 TDS <b>${fmt(value, 1)} ppm</b>，判定为 <b>${label}</b>。${TDS_ADVICE[cls]}`;
            if (avg !== null) txt += ` 近 ${vals.length} 次监测均值 ${fmt(avg, 1)} ppm`;
            if (value !== null && avg !== null && value > avg * 1.3) txt += "，当前明显高于均值，请留意水质波动。";
            insight.innerHTML = txt;
        }
    }

    // chart
    if (tdsChart && vals.length > 0) {
        tdsChart.data.labels = history.map(r => fmtTime(r.time));
        tdsChart.data.datasets[0].data = history.map(r => r.value);
        tdsChart.update("none");
        setText("waterChartHint", `近 ${vals.length} 次 · 均值 ${fmt(avg, 1)} ppm`);
    } else if (vals.length === 0) {
        setText("waterChartHint", "暂无历史数据");
    }
}

// ── Main fetch ────────────────────────────────────────────────
async function fetchData() {
    try {
        const [tdsLatestRes, tdsHistoryRes, statesRes] = await Promise.all([
            fetch(`${API_BASE}/api/v1/readings/latest?sensor_id=esp32-tds-sensor`),
            fetch(`${API_BASE}/api/v1/readings?sensor_id=esp32-tds-sensor&limit=100`),
            fetch(`${API_BASE}/api/v1/states`),
        ]);
        const latest = await tdsLatestRes.json();
        const history = await tdsHistoryRes.json();
        const states = await statesRes.json();
        lastStates = states;
        lastTdsHistory = (history && history.data) || [];

        updateConnectionStatus(true);

        if (currentPage === "water") {
            ensureTdsChart();
            renderWaterPage(latest, lastTdsHistory);
        }
        if (currentPage === "overview") {
            // energy summary for overview KPIs
            try {
                const e = await (await fetch(`${API_BASE}/api/v1/energy/summary`)).json();
                window.__energySummary = e;
            } catch (_) {}
            renderOverviewKpis();
            renderInsights();
        }
        if (currentPage === "energy") { ensureEnergyCharts(); fetchEnergyData(); }
        if (currentPage === "weather") fetchWeatherData();
        if (currentPage === "baby") fetchBabyData();

        // config page
        const cfgComponents = document.getElementById("cfg-components");
        if (cfgComponents) {
            const names = states.map(s => (s.attributes && s.attributes.name) || s.entity_id);
            cfgComponents.textContent = names.length ? names.join(" · ") : "无";
        }
    } catch (err) {
        console.error("Fetch error:", err);
        updateConnectionStatus(false);
    }
}

// ── News page ────────────────────────────────────────────────
let newsData = null;
let newsSince = "daily";
const LANG_COLORS = ["#22d3ee", "#8b5cf6", "#34d399", "#fbbf24", "#f87171", "#3b82f6", "#e879f9", "#2dd4bf"];

function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function hashLang(l) {
    let h = 0;
    for (const c of String(l)) h = (h * 31 + c.charCodeAt(0)) % 997;
    return LANG_COLORS[h % LANG_COLORS.length];
}
function fmtHnTime(ts) {
    if (!ts) return "";
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 3600) return Math.max(1, Math.floor(diff / 60)) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    return Math.floor(diff / 86400) + " 天前";
}
function fmtStars(n) {
    if (n === null || n === undefined || isNaN(n)) return "--";
    if (n >= 10000) return (n / 10000).toFixed(1) + "w";
    if (n >= 1000) return (n / 1000).toFixed(1) + "k";
    return String(n);
}

function renderGhList() {
    const list = document.getElementById("ghList");
    if (!list) return;
    const repos = (newsData && newsData.github && newsData.github[newsSince]) || [];
    if (!repos.length) {
        list.innerHTML = '<div class="news-empty">暂无数据，稍后自动更新</div>';
        return;
    }
    list.innerHTML = repos.slice(0, 12).map(r => {
        const lang = r.lang ? `<span class="gh-lang" style="--lang:${hashLang(r.lang)}">${escapeHtml(r.lang)}</span>` : "";
        const today = r.stars_today ? ` <em class="gh-today">+${fmtStars(r.stars_today)}</em>` : "";
        const desc = r.desc_cn || r.desc || "（暂无描述）";
        return `<a class="gh-card" href="${r.url}" target="_blank" rel="noopener">
            <div class="gh-top">
                <span class="gh-repo">${escapeHtml(r.repo)}</span>
                <span class="gh-stars">★ ${fmtStars(r.stars)}${today}</span>
            </div>
            <div class="gh-desc">${escapeHtml(desc)}</div>
            <div class="gh-meta">${lang}</div>
        </a>`;
    }).join("");
}

function renderNewsList() {
    const list = document.getElementById("newsList");
    if (!list) return;
    const items = (newsData && newsData.hn_news) || [];
    if (!items.length) {
        list.innerHTML = '<div class="news-empty">暂无数据，稍后自动更新</div>';
        return;
    }
    list.innerHTML = items.map(n => {
        const title = n.title_cn || n.title;
        const sub = (n.title_cn && n.title_cn !== n.title) ? n.title : "";
        return `<a class="news-card" href="${n.url}" target="_blank" rel="noopener">
            <span class="news-score">${n.score || 0}</span>
            <div class="news-body">
                <span class="news-title">${escapeHtml(title)}</span>
                <span class="news-meta">${n.by ? escapeHtml(n.by) : ""} · ${fmtHnTime(n.time)}${sub ? " · " + escapeHtml(sub.slice(0, 40)) : ""}</span>
            </div>
        </a>`;
    }).join("");
}

async function fetchNewsData() {
    await ensureNewsData();
    const up = document.getElementById("newsUpdated");
    if (up) up.textContent = "更新于 " + ((newsData && newsData.updated_at) || "--");
    renderGhList();
    renderNewsList();
    if (newsData === null) {
        const list = document.getElementById("ghList");
        if (list) list.innerHTML = '<div class="news-empty">新闻数据加载失败</div>';
    }
}

// 概览页「今日热门」：HN 关注度最高 5 条
async function ensureNewsData() {
    if (newsData !== null) return newsData;
    try {
        const res = await fetch(`${API_BASE}/data/news.json`, { cache: "no-store" });
        newsData = await res.json();
    } catch (err) {
        console.error("News fetch error:", err);
        newsData = null;
    }
    return newsData;
}

async function renderOverviewHot() {
    const box = document.getElementById("overviewHot");
    if (!box) return;
    const data = await ensureNewsData();
    if (!data || !data.hn_news || !data.hn_news.length) {
        box.style.display = "none";
        return;
    }
    const top = [...data.hn_news].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 5);
    box.innerHTML = `
        <div class="panel-label"><span class="dot cyan"></span> 今日热门 · AI 科技</div>
        <div class="hot-list">
            ${top.map((n, i) => `
            <a class="hot-item" href="${n.url}" target="_blank" rel="noopener">
                <span class="hot-rank">${i + 1}</span>
                <span class="hot-score">${n.score || 0}</span>
                <span class="hot-title">${escapeHtml(n.title_cn || n.title)}</span>
                <span class="hot-meta">${n.by ? escapeHtml(n.by) : ""}${n.time ? " · " + fmtHnTime(n.time) : ""}</span>
            </a>`).join("")}
        </div>`;
    box.style.display = "block";
}

(function () {
    const tabs = document.querySelectorAll(".gh-tab");
    tabs.forEach(t => t.addEventListener("click", () => {
        tabs.forEach(x => x.classList.remove("active"));
        t.classList.add("active");
        newsSince = t.dataset.since;
        renderGhList();
    }));
})();

// ── Baby module ────────────────────────────────────────────────
let growthChart = null;
let sleepChart = null;
let feedTimeChart = null;
let feedCountChart = null;
let babySummary = null;
let babyRecords = [];
let babyRecordsAll = [];
let babyFilter = "";
let babyType = "feeding";

const BABY_TYPE_META = {
    feeding: { label: "喂奶", icon: "🍼", color: "#f472b6" },
    sleep: { label: "睡眠", icon: "😴", color: "#8b5cf6" },
    diaper: { label: "尿布", icon: "🧷", color: "#34d399" },
    growth: { label: "成长", icon: "📈", color: "#fbbf24" },
    temperature: { label: "体温", icon: "🌡", color: "#f87171" },
    vaccination: { label: "疫苗", icon: "💉", color: "#3b82f6" },
    note: { label: "备注", icon: "📝", color: "#22d3ee" },
};

function ensureBabyCharts() {
    const gc = document.getElementById("growthChart");
    if (gc && !growthChart) {
        growthChart = new Chart(gc.getContext("2d"), {
            type: "line",
            data: {
                labels: [],
                datasets: [
                    { label: "体重 (kg)", data: [], borderColor: "#f472b6", backgroundColor: "rgba(244,114,182,0.10)", fill: true, tension: 0.35, pointRadius: 4, pointBackgroundColor: "#f472b6", borderWidth: 2.5, yAxisID: "y" },
                    { label: "身高 (cm)", data: [], borderColor: "#fbbf24", backgroundColor: "rgba(251,191,36,0.08)", fill: true, tension: 0.35, pointRadius: 4, pointBackgroundColor: "#fbbf24", borderWidth: 2.5, yAxisID: "y1" },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { intersect: false, mode: "index" },
                scales: {
                    x: { grid: { display: false }, ticks: { color: chartTick, maxRotation: 45, font: axisFont } },
                    y: { beginAtZero: false, position: "left", title: { display: true, text: "kg", color: chartTick }, grid: { color: chartGrid }, ticks: { color: chartTick, font: axisFont } },
                    y1: { beginAtZero: false, position: "right", title: { display: true, text: "cm", color: chartTick }, grid: { display: false }, ticks: { color: "#fbbf24", font: axisFont } },
                },
                plugins: { legend: { labels: { color: chartTick, usePointStyle: true, boxWidth: 8 } }, tooltip: { backgroundColor: "#0f172a", borderColor: "rgba(244,114,182,0.3)", borderWidth: 1, titleColor: "#e8ecf4", bodyColor: "#e8ecf4", padding: 10, displayColors: true } },
            },
        });
    }
    const fcc = document.getElementById("feedCountChart");
    if (fcc && !feedCountChart) {
        feedCountChart = new Chart(fcc.getContext("2d"), {
            type: "line",
            data: {
                labels: [],
                datasets: [
                    { label: "喂奶次数", data: [], borderColor: "#f472b6", backgroundColor: "rgba(244,114,182,0.12)", fill: true, tension: 0.35, pointRadius: 4, pointBackgroundColor: "#f472b6", borderWidth: 2.5 },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { intersect: false, mode: "index" },
                scales: {
                    x: { grid: { display: false }, ticks: { color: chartTick, maxRotation: 45, font: axisFont } },
                    y: { beginAtZero: true, min: 0, position: "left", title: { display: true, text: "次", color: chartTick }, grid: { color: chartGrid }, ticks: { color: chartTick, font: axisFont, stepSize: 1 } },
                },
                plugins: { legend: { display: false }, tooltip: { backgroundColor: "#0f172a", borderColor: "rgba(244,114,182,0.3)", borderWidth: 1, titleColor: "#e8ecf4", bodyColor: "#e8ecf4", padding: 10, displayColors: false, callbacks: { label: function (ctx) { return "喂奶 " + ctx.parsed.y + " 次"; } } } },
            },
        });
    }
    const sc = document.getElementById("sleepChart");
    if (sc && !sleepChart) {
        // 睡眠时间线改为纯 DOM/CSS 渲染（Chart.js 浮条会被自动重排，不可靠）
        sleepChart = { dom: sc };
    }
    const ftc = document.getElementById("feedTimeChart");
    if (ftc && !feedTimeChart) {
        feedTimeChart = new Chart(ftc.getContext("2d"), {
            type: "scatter",
            data: {
                datasets: [
                    { label: "母乳", data: [], backgroundColor: "#f472b6", pointRadius: 6, pointHoverRadius: 8 },
                    { label: "奶粉", data: [], backgroundColor: "#38bdf8", pointRadius: 6, pointHoverRadius: 8 },
                    { label: "辅食", data: [], backgroundColor: "#fbbf24", pointRadius: 6, pointHoverRadius: 8 },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { intersect: false, mode: "nearest" },
                scales: {
                    x: { type: "category", title: { display: true, text: "日期", color: chartTick }, grid: { display: false }, ticks: { color: chartTick, maxRotation: 45, font: axisFont } },
                    y: { beginAtZero: true, max: 24, position: "left", title: { display: true, text: "时刻 (点)", color: chartTick }, grid: { color: chartGrid }, ticks: { color: chartTick, font: axisFont, stepSize: 2, autoSkip: false, callback: function (v) { return v; } } },
                },
                plugins: { legend: { labels: { color: chartTick, usePointStyle: true, boxWidth: 8 } }, tooltip: { backgroundColor: "#0f172a", borderColor: "rgba(244,114,182,0.3)", borderWidth: 1, titleColor: "#e8ecf4", bodyColor: "#e8ecf4", padding: 10, displayColors: true, callbacks: { title: function (items) { return items[0] && items[0].raw && items[0].raw.x ? String(items[0].raw.x) : ""; }, label: function (ctx) { const d = ctx.raw || {}; const hh = Math.floor(d.y), mm = Math.round((d.y - hh) * 60); const t = String(hh).padStart(2, "0") + ":" + String(mm).padStart(2, "0"); const amt = d.amount !== null ? " · " + d.amount + " ml" : ""; return ctx.dataset.label + " " + t + amt; } } } },
            },
        });
    }
}

function fmtBabyDate(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "--";
    return d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

async function fetchBabyData() {
    try {
        const [summaryRes, growthRes, recordsRes] = await Promise.all([
            fetch(`${API_BASE}/api/v1/baby/summary`),
            fetch(`${API_BASE}/api/v1/baby/growth`),
            fetch(`${API_BASE}/api/v1/baby/records?days=14&limit=500`),
        ]);
        babySummary = await summaryRes.json();
        const growth = await growthRes.json();
        const records = await recordsRes.json();
        babyRecordsAll = records.data || [];
        renderBabyKpis(babySummary);
        renderFeedCountChart(babyRecordsAll);
        renderGrowthChart(growth);
        renderSleepChart(babyRecordsAll);
        renderFeedTimeChart(babyRecordsAll);
        renderBabyList();
        // 注意：不在此处重建表单，避免每 5 秒刷新重置用户正在填写/选择的内容
    } catch (err) {
        console.error("Baby fetch error:", err);
    }
}

function renderBabyKpis(s) {
    const grid = document.getElementById("baby-kpis");
    if (!grid) return;
    const g = s.growth || {};
    const weight = g.kg ? num(g.kg.value) : null;
    const height = g.cm ? num(g.cm.value) : null;
    const temp = num(s.temperature && s.temperature.value);
    const sleepActive = !!s.sleep_active;

    const lastFeed = babyRecordsAll.filter(r => r.record_type === "feeding")[0];
    let feedingSub = "";
    if (lastFeed) {
        const ld = new Date(lastFeed.start_time);
        const lhh = String(ld.getHours()).padStart(2, "0") + ":" + String(ld.getMinutes()).padStart(2, "0");
        feedingSub = `<span class="arrow">●</span> 上次 ${lhh}`;
    } else if (s.feeding_ml > 0) {
        feedingSub = `<span class="arrow">●</span> ${fmt(s.feeding_ml, 0)} ml`;
    }
    const sleepSub = sleepActive
        ? `<span class="arrow">◷</span> 睡眠中…`
        : s.sleep_hours > 0
        ? `<span class="arrow">◷</span> 今日累计`
        : "";

    grid.innerHTML =
        buildKpi({ icon: "🍼", iconBg: "rgba(244,114,182,0.13)", iconColor: "#f472b6", label: "今日喂奶", value: fmt(s.feeding_count, 0), unit: "次", sub: feedingSub }) +
        buildKpi({ icon: "😴", iconBg: "rgba(139,92,246,0.13)", iconColor: "#8b5cf6", label: "今日睡眠", value: fmt(s.sleep_hours, 1), unit: "小时", sub: sleepSub, valueCls: sleepActive ? "pulse-soft" : "" }) +
        buildKpi({ icon: "🧷", iconBg: "rgba(52,211,153,0.13)", iconColor: "#34d399", label: "今日尿布", value: fmt(s.diaper_count, 0), unit: "次" }) +
        buildKpi({ icon: "📈", iconBg: "rgba(251,191,36,0.13)", iconColor: "#fbbf24", label: "最新体重", value: fmt(weight, 2), unit: "kg", sub: weight !== null ? `<span class="arrow">●</span> ${fmtBabyDate(g.kg.time)}` : "" }) +
        buildKpi({ icon: "📏", iconBg: "rgba(59,130,246,0.13)", iconColor: "#3b82f6", label: "最新身高", value: fmt(height, 1), unit: "cm", sub: height !== null ? `<span class="arrow">●</span> ${fmtBabyDate(g.cm.time)}` : "" }) +
        buildKpi({ icon: "🌡", iconBg: "rgba(248,113,113,0.13)", iconColor: "#f87171", label: "最新体温", value: fmt(temp, 1), unit: "°C", valueCls: temp !== null && temp > 37.5 ? "warn" : "", sub: temp !== null && temp > 37.5 ? `<span class="arrow">⚠</span> 注意监测` : "" });
}

function renderGrowthChart(growth) {
    if (!growthChart || !growth.length) {
        if (growthChart && !growth.length) {
            growthChart.data.labels = [];
            growthChart.data.datasets[0].data = [];
            growthChart.data.datasets[1].data = [];
            growthChart.update("none");
            setText("growthHint", "暂无成长记录");
        }
        return;
    }
    const kg = growth.filter(r => r.unit === "kg");
    const cm = growth.filter(r => r.unit === "cm");
    const allTimes = [...new Set(growth.map(r => fmtBabyDate(r.time)))];
    growthChart.data.labels = allTimes;
    growthChart.data.datasets[0].data = kg.map(r => ({ x: fmtBabyDate(r.time), y: r.value }));
    growthChart.data.datasets[1].data = cm.map(r => ({ x: fmtBabyDate(r.time), y: r.value }));
    growthChart.update("none");
    const last = growth[growth.length - 1];
    setText("growthHint", `${growth.length} 条记录 · 最新 ${last.unit === "kg" ? "体重" : "身高"} ${fmt(last.value, last.unit === "kg" ? 2 : 1)} ${last.unit}`);
}

function renderFeedCountChart(records) {
    if (!feedCountChart) return;
    const days = [];
    for (let i = 13; i >= 0; i--) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        days.push(d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }));
    }
    const cnt = new Map(days.map(d => [d, 0]));
    for (const r of records) {
        if (r.record_type !== "feeding") continue;
        const d = fmtBabyDate(r.start_time);
        if (cnt.has(d)) cnt.set(d, cnt.get(d) + 1);
    }
    const data = days.map(d => cnt.get(d));
    feedCountChart.data.labels = days;
    feedCountChart.data.datasets[0].data = data;
    feedCountChart.update("none");
    const total = data.reduce((a, b) => a + b, 0);
    const daysWith = data.filter(v => v > 0).length;
    const max = Math.max.apply(null, data);
    const avg = daysWith > 0 ? Math.round(total / daysWith * 10) / 10 : 0;
    setText("feedCountHint", `近 14 天共 ${total} 次 · 平均每天 ${avg} 次 · 单日最多 ${max} 次`);
}

function renderSleepChart(records) {
    const host = document.getElementById("sleepChart");
    if (!host) return;
    // 近 14 天（含今天），最新日期在最右
    const days = []; const dayIdx = new Map();
    for (let i = 0; i < 14; i++) {
        const d = new Date();
        d.setDate(d.getDate() - (13 - i));
        const key = fmtBabyDate(d);
        days.push(key);
        dayIdx.set(key, i);
    }
    // 收集睡眠段：每条记录 → 按天拆分 [startH, endH]（跨天自动拆）
    const byDay = new Map(); // idx -> [{start,end}]
    let segCount = 0;
    for (const r of records) {
        if (r.record_type !== "sleep" || !r.end_time) continue;
        let st = new Date(r.start_time), en = new Date(r.end_time);
        if (isNaN(st) || isNaN(en) || en <= st) continue;
        while (st < en) {
            const key = fmtBabyDate(st);
            const idx = dayIdx.get(key);
            const dayStart = new Date(st); dayStart.setHours(0, 0, 0, 0);
            const dayEnd = new Date(dayStart); dayEnd.setDate(dayEnd.getDate() + 1);
            const segEnd = en < dayEnd ? en : dayEnd;
            const h1 = (st - dayStart) / 3600000;
            const h2 = (segEnd - dayStart) / 3600000;
            if (idx !== undefined && h2 - h1 > 0.02) {
                if (!byDay.has(idx)) byDay.set(idx, []);
                byDay.get(idx).push({ start: Math.round(h1 * 100) / 100, end: Math.round(h2 * 100) / 100 });
                segCount++;
            }
            st = segEnd;
        }
    }
    // 合并同一天相邻段
    for (const idx of byDay.keys()) {
        const list = byDay.get(idx).sort((a, b) => a.start - b.start);
        const merged = [];
        for (const s of list) {
            if (merged.length && s.start <= merged[merged.length - 1].end + 0.05) {
                merged[merged.length - 1].end = Math.max(merged[merged.length - 1].end, s.end);
            } else {
                merged.push({ start: s.start, end: s.end });
            }
        }
        byDay.set(idx, merged);
    }
    // ── 渲染：左侧时间刻度 + 14 天列，睡眠条绝对定位；日期 x 轴在底部 ──
    const SCALE_W = 52, H_PX = 480, TICK_STEP = 2;
    const fmtT = function (h) { const hh = Math.floor(h), mm = Math.round((h - hh) * 60); return String(hh).padStart(2, "0") + ":" + String(mm).padStart(2, "0"); };
    // 时间刻度（左侧每 2 小时一条虚线横贯；00:00 与 24:00 标签不越界）
    let scaleHtml = "";
    for (let h = 0; h <= 24; h += TICK_STEP) {
        const top = (h / 24) * H_PX;
        const tick = String(h).padStart(2, "0") + ":00";
        const lblTop = h === 0 ? 3 : (h === 24 ? -16 : -8);
        scaleHtml += '<div class="tl-tick" style="top:' + top + 'px"><span class="tl-tick-label" style="top:' + lblTop + 'px">' + tick + "</span></div>";
    }
    // 日期列（主体条 + 底部日期）：日期 x 轴在下面
    let colsHtml = "";
    for (let i = 0; i < days.length; i++) {
        const segs = byDay.get(i) || [];
        let barsHtml = "";
        for (const s of segs) {
            const top = (s.start / 24) * H_PX;
            const hgt = ((s.end - s.start) / 24) * H_PX;
            barsHtml += '<div class="tl-bar" style="top:' + top.toFixed(1) + 'px;height:' + Math.max(hgt, 6).toFixed(1) + 'px" title="' + days[i] + " " + fmtT(s.start) + " → " + fmtT(s.end) + "（" + Math.round((s.end - s.start) * 10) / 10 + " 小时）\"></div>";
        }
        const has = segs.length > 0;
        colsHtml += '<div class="tl-col"><div class="tl-body">' + barsHtml + '</div><div class="tl-date' + (has ? " has" : "") + '">' + days[i] + "</div></div>";
    }
    // 列容器高度 = 图高 + 日期行高（日期 x 轴在底部，不溢出遮挡）
    const DATE_H = 28;
    host.innerHTML =
        '<div class="sleep-timeline">' +
        '<div class="tl-scale" style="width:' + SCALE_W + 'px;height:' + H_PX + 'px">' + scaleHtml + "</div>" +
        '<div class="tl-cols" style="height:' + (H_PX + DATE_H) + 'px">' + colsHtml + "</div>" +
        "</div>";
    const hint = document.getElementById("sleepHint");
    if (hint) hint.textContent = "近 14 天共 " + segCount + " 段睡眠 · 纵向条为入睡→醒来时间（悬停看详情）";
}

function renderFeedTimeChart(records) {
    if (!feedTimeChart) return;
    const days = [];
    for (let i = 13; i >= 0; i--) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        days.push(d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }));
    }
    const byCat = { 母乳: [], 奶粉: [], 辅食: [] };
    const feeds = records.filter(r => r.record_type === "feeding");
    for (const r of feeds) {
        const d = new Date(r.start_time);
        if (isNaN(d)) continue;
        const key = d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
        if (!days.includes(key)) continue;
        const hour = d.getHours() + d.getMinutes() / 60;
        const cat = r.category && byCat[r.category] ? r.category : "辅食";
        byCat[cat].push({ x: key, y: Math.round(hour * 60) / 60, amount: r.amount, note: r.note });
    }
    feedTimeChart.data.labels = days;
    const cats = ["母乳", "奶粉", "辅食"];
    for (let i = 0; i < cats.length; i++) {
        const ds = feedTimeChart.data.datasets[i];
        if (ds) ds.data = byCat[cats[i]];
    }
    feedTimeChart.update("none");

    // hint：上次喂奶时间 + 距现在间隔（records 新→旧，取第一条即最近）
    const last = feeds.length ? feeds[0] : null;
    if (last) {
        const d = new Date(last.start_time);
        const hh = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
        const ago = (Date.now() - d.getTime()) / 3600000;
        const agoTxt = ago < 1 ? `${Math.round(ago * 60)} 分钟前` : ago < 24 ? `${fmt(ago, 1)} 小时前` : `${fmt(ago / 24, 1)} 天前`;
        setText("feedTimeHint", `近 14 天喂奶 ${feeds.length} 次 · 上次 ${hh}（${agoTxt}）`);
    } else {
        setText("feedTimeHint", "暂无喂奶记录，添加第一条后这里会显示每天几点喂奶");
    }
}

// ── Baby entry form ───────────────────────────────────────────
function renderBabyForm() {
    const form = document.getElementById("babyForm");
    if (!form) return;
    const active = babySummary && babySummary.sleep_active;

    const timeFields = `
        <div class="baby-field-row">
            <label>开始时间</label><input type="datetime-local" id="bf-start">
            <label>备注</label><input type="text" id="bf-note" placeholder="可选" maxlength="100">
        </div>`;

    const forms = {
        feeding: `
            <div class="baby-field-row">
                <label>类型</label>
                <select id="bf-category"><option value="母乳">母乳</option><option value="奶粉">奶粉</option><option value="辅食">辅食</option></select>
                <label>奶量 (ml)</label><input type="number" id="bf-amount" placeholder="母乳亲喂可不填" min="1" step="1">
                <label>备注</label><input type="text" id="bf-note" placeholder="可选" maxlength="100">
            </div>${timeFields}`,
        sleep: `
            <div class="baby-sleep-actions">
                <button class="baby-btn primary" id="bf-sleep-start">😴 开始睡眠</button>
                <button class="baby-btn ghost" id="bf-sleep-end" ${active ? "" : "disabled"}>☀ 结束睡眠</button>
            </div>
            <div class="baby-field-row">
                <label>开始</label><input type="datetime-local" id="bf-start">
                <label>结束</label><input type="datetime-local" id="bf-end">
                <label>备注</label><input type="text" id="bf-note" placeholder="可选" maxlength="100">
            </div>
            <div class="baby-hint">💡 点「开始睡眠」一键记录，宝宝醒了点「结束睡眠」自动计时</div>`,
        diaper: `
            <div class="baby-field-row">
                <label>类型</label>
                <select id="bf-category"><option value="湿">湿</option><option value="脏">脏</option><option value="混合">混合</option></select>
                <label>备注</label><input type="text" id="bf-note" placeholder="可选" maxlength="100">
            </div>${timeFields}`,
        growth: `
            <div class="baby-field-row">
                <label>项目</label>
                <select id="bf-category">
                    <option value="体重">体重</option><option value="身高">身高</option><option value="头围">头围</option>
                </select>
                <label>数值</label><input type="number" id="bf-value" placeholder="如 6.5" min="0" step="0.1">
                <label>单位</label><span class="bf-unit" id="bf-unit">kg</span>
            </div>${timeFields}`,
        temperature: `
            <div class="baby-field-row">
                <label>体温 (℃)</label><input type="number" id="bf-value" placeholder="如 36.8" min="34" max="42" step="0.1">
                <label>备注</label><input type="text" id="bf-note" placeholder="可选" maxlength="100">
            </div>${timeFields}`,
        vaccination: `
            <div class="baby-field-row">
                <label>疫苗名称</label><input type="text" id="bf-category" placeholder="如 乙肝第二针" maxlength="50">
                <label>备注</label><input type="text" id="bf-note" placeholder="可选" maxlength="100">
            </div>${timeFields}`,
        note: `
            <div class="baby-field-row">
                <label>记录内容</label><input type="text" id="bf-note" placeholder="如 今天第一次翻身啦！" maxlength="200">
            </div>${timeFields}`,
    };
    form.innerHTML = forms[babyType] || forms.note;

    // unit auto-switch for growth
    const catSel = document.getElementById("bf-category");
    const unitEl = document.getElementById("bf-unit");
    if (catSel && unitEl) {
        const syncUnit = () => {
            unitEl.textContent = catSel.value === "体重" ? "kg" : catSel.value === "身高" ? "cm" : "cm";
        };
        syncUnit();
        catSel.addEventListener("change", syncUnit);
    }

    // sleep one-tap actions
    const sStart = document.getElementById("bf-sleep-start");
    if (sStart) sStart.addEventListener("click", async () => {
        sStart.disabled = true;
        try {
            const res = await fetch(`${API_BASE}/api/v1/baby/sleep/start`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
            });
            if (!res.ok) throw new Error(await res.text());
            toastMsg("😴 睡眠已开始，好好休息~");
            if (babySummary) babySummary.sleep_active = true;
            renderBabyForm();
            fetchBabyData();
        } catch (e) {
            console.error(e);
            toastMsg("开始睡眠失败，请重试", true);
            sStart.disabled = false;
        }
    });
    const sEnd = document.getElementById("bf-sleep-end");
    if (sEnd) sEnd.addEventListener("click", async () => {
        sEnd.disabled = true;
        try {
            const res = await fetch(`${API_BASE}/api/v1/baby/sleep/end`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
            });
            if (!res.ok) throw new Error(await res.text());
            toastMsg("☀ 睡眠结束，记录完成！");
            if (babySummary) babySummary.sleep_active = false;
            renderBabyForm();
            fetchBabyData();
        } catch (e) {
            console.error(e);
            toastMsg("结束睡眠失败，请重试", true);
            sEnd.disabled = false;
        }
    });

    // submit button
    const submit = document.createElement("button");
    submit.className = "baby-btn primary baby-submit";
    submit.textContent = `✓ 保存${BABY_TYPE_META[babyType]?.label || ""}记录`;
    submit.addEventListener("click", submitBabyRecord);
    form.appendChild(submit);
}

function toIso(localValue) {
    if (!localValue) return null;
    const d = new Date(localValue);
    if (isNaN(d)) return null;
    return d.toISOString();
}

function submitBabyRecord() {
    const val = id => { const el = document.getElementById(id); return el ? el.value.trim() : ""; };
    const numVal = id => { const v = val(id); if (!v) return null; const n = Number(v); return isNaN(n) ? null : n; };

    const startIso = toIso(val("bf-start")) || new Date().toISOString();
    const endIso = toIso(val("bf-end"));
    const body = {
        record_type: babyType,
        category: val("bf-category") || null,
        start_time: startIso,
        end_time: endIso,
        amount: numVal("bf-amount"),
        amount_unit: babyType === "feeding" ? "ml" : null,
        value: numVal("bf-value"),
        value_unit: null,
        note: val("bf-note") || null,
    };

    // unit mapping
    if (babyType === "growth") {
        const cat = body.category;
        body.value_unit = cat === "体重" ? "kg" : "cm";
    }
    if (babyType === "temperature") body.value_unit = "℃";
    if (babyType === "growth" && body.value === null) {
        toastMsg("请输入数值", true);
        return;
    }
    if (babyType === "temperature" && body.value === null) {
        toastMsg("请输入体温", true);
        return;
    }
    if (babyType === "vaccination" && !body.category) {
        toastMsg("请输入疫苗名称", true);
        return;
    }
    if (babyType === "note" && !body.note) {
        toastMsg("请输入记录内容", true);
        return;
    }
    if (babyType === "sleep" && endIso && new Date(endIso) <= new Date(startIso)) {
        toastMsg("结束时间需晚于开始时间", true);
        return;
    }

    fetch(`${API_BASE}/api/v1/baby/records`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    })
        .then(res => {
            if (!res.ok) throw new Error("保存失败");
            return res.json();
        })
        .then(() => {
            toastMsg(`✓ ${BABY_TYPE_META[babyType]?.label || ""}记录已保存`);
            if (babyType === "sleep" && babySummary) {
                babySummary.sleep_active = !endIso;
            }
            renderBabyForm();
            fetchBabyData();
        })
        .catch(e => {
            console.error(e);
            toastMsg("保存失败，请重试", true);
        });
}

// ── Baby record list ──────────────────────────────────────────
function renderBabyList() {
    const list = document.getElementById("babyList");
    if (!list) return;
    const rows = babyFilter ? babyRecordsAll.filter(r => r.record_type === babyFilter) : babyRecordsAll;
    if (!rows.length) {
        list.innerHTML = '<div class="news-empty">暂无记录，用上方「快速记录」添加第一条吧~</div>';
        return;
    }
    const byDate = {};
    for (const r of rows) {
        const key = fmtBabyDate(r.start_time);
        (byDate[key] = byDate[key] || []).push(r);
    }
    list.innerHTML = Object.entries(byDate).map(([date, items]) => `
        <div class="baby-day">
            <div class="baby-day-label">${date}</div>
            <div class="baby-day-items">
                ${items.map(r => babyItemHtml(r)).join("")}
            </div>
        </div>`).join("");
}

function babyItemHtml(r) {
    const meta = BABY_TYPE_META[r.record_type] || { label: r.record_type, icon: "📌", color: "#8b93a7" };
    const t = fmtTime(r.start_time);
    let main = "";
    switch (r.record_type) {
        case "feeding":
            main = `${r.category ? `<b>${escapeHtml(r.category)}</b> ` : ""}${r.amount !== null ? `<b>${fmt(r.amount, 0)} ml</b>` : ""}`;
            break;
        case "sleep":
            if (r.end_time) {
                const h = (new Date(r.end_time) - new Date(r.start_time)) / 3600000;
                main = h > 0 && h < 24
                    ? `睡了 <b>${fmt(h, 1)} 小时</b> <span class="baby-dim">${fmtTime(r.end_time)} 醒</span>`
                    : `睡眠记录 <span class="baby-dim">${fmtTime(r.end_time)}</span>`;
            } else {
                main = `<b class="sleeping">睡眠中…</b>`;
            }
            break;
        case "diaper":
            main = `<b>${escapeHtml(r.category || "更换")}</b>`;
            break;
        case "growth":
            main = `<b>${r.value !== null ? fmt(r.value, r.value_unit === "kg" ? 2 : 1) + " " + escapeHtml(r.value_unit || "") : "—"}</b>`;
            break;
        case "temperature":
            main = `<b>${r.value !== null ? fmt(r.value, 1) + " ℃" : "—"}</b>`;
            break;
        case "vaccination":
            main = `<b>${escapeHtml(r.category || "接种")}</b>`;
            break;
        case "note":
            main = `<span class="baby-note-text">${escapeHtml(r.note || "")}</span>`;
            break;
    }
    const note = r.note && r.record_type !== "note" ? `<span class="baby-dim"> · ${escapeHtml(r.note.slice(0, 40))}</span>` : "";
    return `
        <div class="baby-item" style="--item-color:${meta.color}">
            <span class="baby-item-icon">${meta.icon}</span>
            <span class="baby-item-time">${t}</span>
            <span class="baby-item-main">${main}</span>
            <span class="baby-item-note">${note}</span>
            <button class="baby-del" data-id="${r.id}" title="删除">✕</button>
        </div>`;
}

function bindBabyListEvents() {
    const list = document.getElementById("babyList");
    if (!list) return;
    list.addEventListener("click", e => {
        const del = e.target.closest(".baby-del");
        if (!del) return;
        const id = del.dataset.id;
        if (!confirm("删除这条记录？")) return;
        fetch(`${API_BASE}/api/v1/baby/records/${id}`, { method: "DELETE" })
            .then(res => {
                if (!res.ok) throw new Error("删除失败");
                toastMsg("已删除");
                fetchBabyData();
            })
            .catch(err => { console.error(err); toastMsg("删除失败", true); });
    });
}

// toast helper
let toastTimer = null;
function toastMsg(msg, isErr = false) {
    let el = document.getElementById("babyToast");
    if (!el) {
        el = document.createElement("div");
        el.id = "babyToast";
        el.className = "baby-toast";
        document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.background = isErr ? "rgba(248,113,113,0.92)" : "rgba(52,211,153,0.92)";
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

// baby tab switching
(function () {
    document.addEventListener("click", e => {
        const tab = e.target.closest(".baby-tab");
        if (tab) {
            document.querySelectorAll(".baby-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            babyType = tab.dataset.type;
            renderBabyForm();
        }
        const ftab = e.target.closest(".baby-filter .gh-tab");
        if (ftab) {
            document.querySelectorAll(".baby-filter .gh-tab").forEach(t => t.classList.remove("active"));
            ftab.classList.add("active");
            babyFilter = ftab.dataset.filter || "";
            renderBabyList();
        }
    });
    bindBabyListEvents();
})();

// ── Finance (记账，参照 Firefly III) ──────────────────────────
let finTrendChart = null;
let finCatChart = null;
let finAccounts = [];
let finCategories = [];
let finTxnType = "expense";

function curFinMonth() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
}

function ensureFinanceCharts() {
    const trendCanvas = document.getElementById("finTrendChart");
    if (trendCanvas && !finTrendChart) {
        finTrendChart = new Chart(trendCanvas.getContext("2d"), {
            type: "bar",
            data: { labels: [], datasets: [
                { label: "收入", data: [], backgroundColor: "rgba(52,211,153,0.55)", borderRadius: 5 },
                { label: "支出", data: [], backgroundColor: "rgba(248,113,113,0.55)", borderRadius: 5 },
            ] },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: {
                    x: { stacked: false, grid: { color: chartGrid }, ticks: { color: chartTick, font: axisFont } },
                    y: { beginAtZero: true, grid: { color: chartGrid }, ticks: { color: chartTick, font: axisFont, callback: v => fmtMoney(v) } },
                },
                plugins: { legend: { labels: { color: chartTick, font: axisFont } } },
            },
        });
    }
    const catCanvas = document.getElementById("finCatChart");
    if (catCanvas && !finCatChart) {
        finCatChart = new Chart(catCanvas.getContext("2d"), {
            type: "doughnut",
            data: { labels: [], datasets: [{ data: [], backgroundColor: [
                "#f472b6", "#38bdf8", "#fbbf24", "#34d399", "#8b5cf6", "#f87171", "#a3d5e8", "#e1b98f", "#94d8c3", "#c9a7e8", "#eaa7b2", "#94d4d0"
            ], borderWidth: 0 }] },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: "62%",
                plugins: {
                    legend: { position: "bottom", labels: { color: chartTick, font: axisFont, boxWidth: 10, boxHeight: 10, padding: 8 } },
                    tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmtMoney(ctx.parsed)}` } },
                },
            },
        });
    }
}

async function fetchFinanceData() {
    try {
        const month = curFinMonth();
        const [summaryRes, trendRes, txRes, accRes, catRes] = await Promise.all([
            fetch(`${API_BASE}/api/v1/finance/summary?month=${month}`),
            fetch(`${API_BASE}/api/v1/finance/trend?months=6`),
            fetch(`${API_BASE}/api/v1/finance/transactions?month=${month}&limit=100`),
            fetch(`${API_BASE}/api/v1/finance/accounts`),
            fetch(`${API_BASE}/api/v1/finance/categories`),
        ]);
        const summary = await summaryRes.json();
        const trend = await trendRes.json();
        const txs = (await txRes.json()).data || [];
        finAccounts = await accRes.json();
        finCategories = await catRes.json();
        renderFinanceKpis(summary);
        renderFinTrend(trend);
        renderFinCat(summary);
        renderFinAccounts();
        renderFinBudgets(summary);
        renderFinTxs(txs);
        renderFinFormOptions();
    } catch (err) {
        console.error("Finance fetch error:", err);
    }
}

function renderFinanceKpis(s) {
    const grid = document.getElementById("finance-kpis");
    if (!grid) return;
    const saveRate = s.income > 0 ? Math.round((s.balance / s.income) * 100) : 0;
    const catsTop = s.category_spend[0];
    grid.innerHTML =
        buildKpi({ icon: "💰", iconBg: "rgba(52,211,153,0.13)", iconColor: "#34d399", label: "本月收入", value: fmtMoney(s.income), unit: "", valueCls: "money-in", sub: `<span class="arrow">●</span> ${s.month}` }) +
        buildKpi({ icon: "💸", iconBg: "rgba(248,113,113,0.13)", iconColor: "#f87171", label: "本月支出", value: fmtMoney(s.expense), unit: "", valueCls: "money-out", sub: catsTop ? `<span class="arrow">●</span> ${catsTop.icon} ${catsTop.name} ${fmtMoney(catsTop.spent)}` : "" }) +
        buildKpi({ icon: "⚖️", iconBg: "rgba(139,92,246,0.13)", iconColor: "#8b5cf6", label: "本月结余", value: fmtMoney(s.balance), unit: "", valueCls: s.balance >= 0 ? "" : "warn", sub: s.income > 0 ? `<span class="arrow">●</span> 储蓄率 ${saveRate}%` : "" });
}

function fmtMoney(v) {
    const n = Number(v);
    if (isNaN(n)) return "¥0.00";
    return "¥" + n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function renderFinTrend(trend) {
    if (!finTrendChart) return;
    finTrendChart.data.labels = trend.map(t => t.month.slice(2).replace("-", "/"));
    finTrendChart.data.datasets[0].data = trend.map(t => t.income);
    finTrendChart.data.datasets[1].data = trend.map(t => t.expense);
    finTrendChart.update();
    const hint = document.getElementById("finTrendHint");
    if (hint) hint.textContent = "柱状对比 · 含转账外收支";
}

function renderFinCat(summary) {
    if (!finCatChart) return;
    const cats = summary.category_spend || [];
    finCatChart.data.labels = cats.map(c => `${c.icon} ${c.name}`);
    finCatChart.data.datasets[0].data = cats.map(c => c.spent);
    finCatChart.update();
    const hint = document.getElementById("finCatHint");
    if (hint) hint.textContent = cats.length ? `本月 ${cats.length} 个分类有支出` : "暂无支出记录";
}

function renderFinAccounts() {
    const list = document.getElementById("finAccountsList");
    if (!list) return;
    list.innerHTML = finAccounts.map(a => `
        <div class="fin-account-row">
            <span class="fin-account-icon">${a.icon || "🏦"}</span>
            <span class="fin-account-name">${esc(a.name)}</span>
            <span class="fin-account-bal ${a.balance < 0 ? "warn" : ""}">${fmtMoney(a.balance)}</span>
            <button class="fin-del" data-del-account="${a.id}" title="删除账户">✕</button>
        </div>`).join("") || '<div class="fin-empty">暂无账户，先添加一个吧</div>';
    list.querySelectorAll("[data-del-account]").forEach(btn => {
        btn.onclick = async () => {
            if (!confirm("确定删除该账户？")) return;
            const res = await fetch(`${API_BASE}/api/v1/finance/accounts/${btn.dataset.delAccount}`, { method: "DELETE" });
            if (!res.ok) { alert(res.status === 409 ? "该账户已有流水，不能删除" : "删除失败"); return; }
            fetchFinanceData();
        };
    });
    const hint = document.getElementById("finAccountHint");
    if (hint) hint.textContent = `${finAccounts.length} 个账户 · 余额含初始与流水`;
}

function renderFinBudgets(summary) {
    const list = document.getElementById("finBudgetList");
    if (!list) return;
    const budgets = summary.budgets || [];
    list.innerHTML = budgets.map(b => {
        const over = b.pct > 100;
        const barColor = over ? "#f87171" : b.pct > 80 ? "#fbbf24" : "#34d399";
        return `
        <div class="fin-budget-row">
            <div class="fin-budget-head">
                <span class="fin-budget-name">${b.category_icon} ${esc(b.category_name)}</span>
                <span class="fin-budget-num ${over ? "warn" : ""}">${fmtMoney(b.spent)} / ${fmtMoney(b.amount)}</span>
            </div>
            <div class="fin-budget-bar"><div class="fin-budget-fill" style="width:${Math.min(b.pct, 100)}%;background:${barColor}"></div></div>
            <div class="fin-budget-pct ${over ? "warn" : ""}">${b.pct}%</div>
        </div>`;
    }).join("") || '<div class="fin-empty">本月还没有预算，下面设置一个吧（空分类=总预算）</div>';
    const hint = document.getElementById("finBudgetHint");
    if (hint) hint.textContent = `${budgets.length} 项预算 · 超支标红`;
}

function renderFinTxs(txs) {
    const list = document.getElementById("finTxList");
    if (!list) return;
    list.innerHTML = txs.map(t => {
        const sign = t.txn_type === "income" ? "+" : t.txn_type === "transfer" ? "⇄" : "-";
        const cls = t.txn_type === "income" ? "money-in" : t.txn_type === "transfer" ? "tx-transfer" : "money-out";
        const when = new Date(t.txn_date).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
        const detail = t.txn_type === "transfer"
            ? `${t.account_icon || ""} ${t.account_name} → ${t.target_account_name || "?"}`
            : `${t.category_icon || ""} ${t.category_name || "未分类"} · ${t.account_name}`;
        return `
        <div class="fin-tx-row">
            <span class="fin-tx-type fin-tx-${t.txn_type}">${t.txn_type === "income" ? "收" : t.txn_type === "transfer" ? "转" : "支"}</span>
            <div class="fin-tx-main">
                <div class="fin-tx-note">${esc(t.note || detail)}</div>
                <div class="fin-tx-detail">${detail} · ${when}</div>
            </div>
            <span class="fin-tx-amount ${cls}">${sign}${fmtMoney(t.amount).replace("¥", "¥")}</span>
            <button class="fin-del" data-del-tx="${t.id}" title="删除">✕</button>
        </div>`;
    }).join("") || '<div class="fin-empty">本月还没有交易记录</div>';
    list.querySelectorAll("[data-del-tx]").forEach(btn => {
        btn.onclick = async () => {
            if (!confirm("确定删除这笔交易？")) return;
            await fetch(`${API_BASE}/api/v1/finance/transactions/${btn.dataset.delTx}`, { method: "DELETE" });
            fetchFinanceData();
        };
    });
    const hint = document.getElementById("finTxHint");
    if (hint) hint.textContent = `本月 ${txs.length} 笔 · 点击 ✕ 删除`;
}

function renderFinFormOptions() {
    const accSel = document.getElementById("finAccount");
    const tgtSel = document.getElementById("finTargetAccount");
    const catSel = document.getElementById("finCategory");
    if (accSel) accSel.innerHTML = finAccounts.map(a => `<option value="${a.id}">${a.icon || ""} ${esc(a.name)}</option>`).join("");
    if (tgtSel) tgtSel.innerHTML = finAccounts.map(a => `<option value="${a.id}">${a.icon || ""} ${esc(a.name)}</option>`).join("");
    if (catSel) catSel.innerHTML = finCategories.map(c => `<option value="${c.id}">${c.icon || ""} ${esc(c.name)}</option>`).join("");
    const budgetCat = document.getElementById("finBudgetCat");
    if (budgetCat) budgetCat.innerHTML = '<option value="">🎯 总预算</option>' + finCategories.map(c => `<option value="${c.id}">${c.icon || ""} ${esc(c.name)}</option>`).join("");
    const dateInput = document.getElementById("finDate");
    if (dateInput && !dateInput.value) {
        const d = new Date();
        dateInput.value = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    }
}

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

(function bindFinanceEvents() {
    document.querySelectorAll("#financeTabs .finance-tab").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("#financeTabs .finance-tab").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            finTxnType = btn.dataset.ftype;
            const tgt = document.getElementById("finTargetAccount");
            const cat = document.getElementById("finCategory");
            if (tgt) tgt.style.display = finTxnType === "transfer" ? "" : "none";
            if (cat) cat.style.display = finTxnType === "transfer" ? "none" : "";
        });
    });

    const saveBtn = document.getElementById("finSaveBtn");
    if (saveBtn) saveBtn.addEventListener("click", async () => {
        const amount = parseFloat(document.getElementById("finAmount").value);
        const accountId = parseInt(document.getElementById("finAccount").value, 10);
        if (!amount || amount <= 0 || !accountId) { alert("请填写金额并选择账户"); return; }
        const payload = {
            txn_type: finTxnType,
            amount,
            account_id: accountId,
            note: document.getElementById("finNote").value.trim(),
        };
        if (finTxnType === "transfer") {
            const targetId = parseInt(document.getElementById("finTargetAccount").value, 10);
            if (!targetId || targetId === accountId) { alert("请选择不同的转账目标账户"); return; }
            payload.target_account_id = targetId;
        } else {
            payload.category_id = parseInt(document.getElementById("finCategory").value, 10) || null;
        }
        const dateVal = document.getElementById("finDate").value;
        if (dateVal) payload.txn_date = new Date(dateVal + "T12:00:00").toISOString();
        try {
            const res = await fetch(`${API_BASE}/api/v1/finance/transactions`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert("保存失败：" + (err.detail || res.status));
                return;
            }
            document.getElementById("finAmount").value = "";
            document.getElementById("finNote").value = "";
            fetchFinanceData();
        } catch (e) {
            alert("网络错误：" + e.message);
        }
    });

    const addAccBtn = document.getElementById("finAddAccountBtn");
    if (addAccBtn) addAccBtn.addEventListener("click", async () => {
        const name = document.getElementById("finNewAccount").value.trim();
        if (!name) { alert("请输入账户名称"); return; }
        const bal = parseFloat(document.getElementById("finNewAccountBal").value) || 0;
        await fetch(`${API_BASE}/api/v1/finance/accounts`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, initial_balance: bal }),
        });
        document.getElementById("finNewAccount").value = "";
        document.getElementById("finNewAccountBal").value = "";
        fetchFinanceData();
    });

    const setBudgetBtn = document.getElementById("finSetBudgetBtn");
    if (setBudgetBtn) setBudgetBtn.addEventListener("click", async () => {
        const amount = parseFloat(document.getElementById("finBudgetAmount").value);
        if (!amount || amount <= 0) { alert("请输入预算金额"); return; }
        const catVal = document.getElementById("finBudgetCat").value;
        await fetch(`${API_BASE}/api/v1/finance/budgets`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ month: curFinMonth(), category_id: catVal ? parseInt(catVal, 10) : null, amount }),
        });
        document.getElementById("finBudgetAmount").value = "";
        fetchFinanceData();
    });
})();

// ── Init ──────────────────────────────────────────────────────
ensureTdsChart();
ensureTempChart();
ensureEnergyCharts();
ensureBabyCharts();
ensureFinanceCharts();
fetchData();
renderOverviewHot();
fetchBabyData();
fetchFinanceData();
setInterval(fetchData, REFRESH_INTERVAL);

// greet based on time of day
(function () {
    const h = new Date().getHours();
    const g = document.getElementById("heroGreet");
    if (g) g.textContent = h < 6 ? "夜深了 🌙" : h < 12 ? "早上好 ☀" : h < 18 ? "下午好 🌤" : "晚上好 🌙";
})();
