from __future__ import annotations
import config
from dashboard_styles import get_dashboard_css


def get_dashboard_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
<title>Binance Tokyo Funding Sniper (Slot Margin Ledger)</title>
<style>
{get_dashboard_css()}
</style>
</head>
<body>
<div class="container">
<div class="header">
    <div class="header-top">
        <div class="brand-block">
            <h1><span class="brand-dot"></span>Binance Tokyo Funding Sniper</h1>
            <div class="sub-text" style="color: #848e9c;">Radar Top 10 • Slot Margins (#1/#2/#3) • Auto & Manual • T-{config.ENTRY_BEFORE_SETTLE_MS}ms</div>
        </div>

        <div class="controls-deck">
            <button id="btn-trade-toggle" class="btn-toggle btn-on" onclick="toggleTrading()">
                <span class="btn-toggle-sub">MASTER ENGINE</span>
                <span id="btn-trade-label">● TRADING: ON</span>
            </button>
            
            <div class="mode-container">
                <div id="slot-mode-auto" class="mode-slot mode-slot-auto-active">
                    <button id="btn-mode-auto" class="btn-mode btn-mode-auto-active" onclick="setModeAuto()">⚡ AUTOMATIC (1)</button>
                    <div class="auto-count-row">
                        <button type="button" id="btn-auto-cnt-1" class="btn-auto-cnt btn-auto-cnt-active" onclick="setAutoCount(1)" title="Lock up to 1 coin in Automatic Mode">1</button>
                        <button type="button" id="btn-auto-cnt-2" class="btn-auto-cnt" onclick="setAutoCount(2)" title="Lock up to 2 coins in Automatic Mode">2</button>
                        <button type="button" id="btn-auto-cnt-3" class="btn-auto-cnt" onclick="setAutoCount(3)" title="Lock up to 3 coins in Automatic Mode">3</button>
                    </div>
                </div>
                <div id="slot-mode-manual" class="mode-slot mode-slot-inactive">
                    <button id="btn-mode-manual" class="btn-mode btn-mode-inactive" onclick="setModeManual()">🎯 MANUAL</button>
                    <div class="manual-sub-pill" id="manual-sub-hint">UP TO 3 COINS</div>
                </div>
            </div>
        </div>
    </div>

    <div class="header-status-bar">
        <div class="badge-mode" id="lock-badge">SELECTION: OPEN (>1m)</div>
        <div class="badge" id="ws-status">SCANNING</div>
    </div>
</div>

<div class="grid">
    <div class="card"><div class="card-title">Target Coin(s)</div><div class="card-value" id="coin">--</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color: #0ecb81;">--%</div></div>
    <div class="card"><div class="card-title">Next Settlement</div><div class="card-value" id="settle" style="color: #eaecef;">--:--:-- IST</div></div>
    <div class="card card-editable" id="margin-card" onclick="openMarginModal()" title="Click to configure #1, #2, #3 or Default Margin">
        <div class="card-title">
            <span>Base Margin (Per Order)</span>
            <span style="color: #f0b90b; font-size: 10px; font-weight: 800;">✏️ EDIT</span>
        </div>
        <div class="slot-preview-list">
            <div class="slot-preview-row slot-row-1" id="slot-row-1">
                <span><span class="slot-tag-1">#1</span><span id="slot-coin-1" class="slot-coin-empty">Waiting...</span></span>
                <span class="slot-amt-val" id="slot-val-1">${config.TRADE_MARGIN_USD:.2f}</span>
            </div>
            <div class="slot-preview-row slot-row-2" id="slot-row-2">
                <span><span class="slot-tag-2">#2</span><span id="slot-coin-2" class="slot-coin-empty">Empty</span></span>
                <span class="slot-amt-val" id="slot-val-2">${config.TRADE_MARGIN_USD:.2f}</span>
            </div>
            <div class="slot-preview-row slot-row-3" id="slot-row-3">
                <span><span class="slot-tag-3">#3</span><span id="slot-coin-3" class="slot-coin-empty">Empty</span></span>
                <span class="slot-amt-val" id="slot-val-3">${config.TRADE_MARGIN_USD:.2f}</span>
            </div>
        </div>
    </div>
    <div class="card"><div class="card-title">Clock Drift</div><div class="card-value" id="offset" style="color: #0ecb81;">0.0 ms</div></div>
</div>

<!-- TWO-PART SLOT MARGIN CONTROL MODAL -->
<div class="modal-overlay" id="margin-modal" onclick="if(event.target === this) closeMarginModal();">
    <div class="modal-box">
        <div class="modal-title">
            <span>💰 Base Margin Control</span>
            <span style="font-size: 11px; color: #848e9c; font-weight: 600;">Min: $0.30 USDT</span>
        </div>
        <div class="modal-sub">Har ek Shallow Limit Order (20%, 40%, 60%, 80% FR) par uske slot (#1, #2, #3) ka margin lagega.</div>

        <!-- PART 1: DEFAULT (ALL 3 SLOTS) -->
        <div class="default-panel">
            <div class="default-panel-header">
                <span>⚡ DEFAULT (ALL 3 SLOTS)</span>
                <span style="font-size: 10px; color: #94a3b8;">Sabhi coins me same margin</span>
            </div>
            <div class="default-input-row">
                <input type="number" id="default-margin-input" class="modal-input" step="0.05" min="0.30" placeholder="1.50" onkeydown="if(event.key==='Enter') applyDefaultToAllSlots();">
                <button type="button" class="btn-apply-all" onclick="applyDefaultToAllSlots()">Apply to All</button>
            </div>
            <div class="preset-row">
                <button type="button" class="btn-preset" onclick="setDefaultPreset(0.30)">$0.30</button>
                <button type="button" class="btn-preset" onclick="setDefaultPreset(0.50)">$0.50</button>
                <button type="button" class="btn-preset" onclick="setDefaultPreset(1.00)">$1.00</button>
                <button type="button" class="btn-preset" onclick="setDefaultPreset(1.50)">$1.50</button>
                <button type="button" class="btn-preset" onclick="setDefaultPreset(3.00)">$3.00</button>
                <button type="button" class="btn-preset" onclick="setDefaultPreset(5.00)">$5.00</button>
            </div>
        </div>

        <!-- PART 2: INDIVIDUAL SLOTS (#1, #2, #3) -->
        <div class="slots-edit-list">
            <div class="slot-edit-card slot-edit-1">
                <div class="slot-edit-info">
                    <div class="slot-edit-badge"><span class="slot-tag-1">#1</span><span id="modal-coin-1" style="color: #ffffff;">Slot #1</span></div>
                    <div class="slot-edit-sub">1st Selected / Rank #1 Auto Coin</div>
                </div>
                <div class="slot-edit-input-wrap">
                    <input type="number" id="slot-input-1" class="slot-num-input" step="0.05" min="0.30" onkeydown="if(event.key==='Enter') submitMarginModal();">
                    <span style="font-size: 11px; color: #848e9c; font-weight: 700;">USDT</span>
                </div>
            </div>

            <div class="slot-edit-card slot-edit-2">
                <div class="slot-edit-info">
                    <div class="slot-edit-badge"><span class="slot-tag-2">#2</span><span id="modal-coin-2" style="color: #ffffff;">Slot #2</span></div>
                    <div class="slot-edit-sub">2nd Selected / Rank #2 Auto Coin</div>
                </div>
                <div class="slot-edit-input-wrap">
                    <input type="number" id="slot-input-2" class="slot-num-input" step="0.05" min="0.30" onkeydown="if(event.key==='Enter') submitMarginModal();">
                    <span style="font-size: 11px; color: #848e9c; font-weight: 700;">USDT</span>
                </div>
            </div>

            <div class="slot-edit-card slot-edit-3">
                <div class="slot-edit-info">
                    <div class="slot-edit-badge"><span class="slot-tag-3">#3</span><span id="modal-coin-3" style="color: #ffffff;">Slot #3</span></div>
                    <div class="slot-edit-sub">3rd Selected / Rank #3 Auto Coin</div>
                </div>
                <div class="slot-edit-input-wrap">
                    <input type="number" id="slot-input-3" class="slot-num-input" step="0.05" min="0.30" onkeydown="if(event.key==='Enter') submitMarginModal();">
                    <span style="font-size: 11px; color: #848e9c; font-weight: 700;">USDT</span>
                </div>
            </div>
        </div>

        <div class="modal-actions">
            <button type="button" class="btn-modal-cancel" onclick="closeMarginModal()">Cancel</button>
            <button type="button" class="btn-modal-ok" onclick="submitMarginModal()">OK / SAVE</button>
        </div>
    </div>
</div>

<!-- 1. TOP 10 CANDIDATES RADAR TABLE -->
<div class="table-card">
    <div class="table-header">
        <span id="radar-title">Top 10 Funding Radar (Click row to Toggle • Select up to 3 coins in Manual)</span>
        <span id="lock-warning" style="font-size: 12px; color: #f0b90b;">Locked automatically within 1 min of settlement</span>
    </div>
    <div class="table-wrapper">
        <table style="min-width: 780px;">
            <thead>
                <tr>
                    <th style="width: 55px; text-align: center;">Select</th>
                    <th># Rank</th>
                    <th>Coin</th>
                    <th>24h Volume (USDT)</th>
                    <th>Predicted Rate</th>
                    <th>Next Settlement</th>
                    <th style="width: 100px; text-align: center;">Block Actions</th>
                </tr>
            </thead>
            <tbody id="candidates-body">
                <tr><td colspan="7" style="text-align: center; padding: 16px; color: #848e9c;">Scanning top radar candidates...</td></tr>
            </tbody>
        </table>
    </div>
</div>

<!-- 2. BLOCKED COINS MANAGEMENT CARDS -->
<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px;">
    <div class="table-card">
        <div class="table-header">
            <span style="color: #f0b90b; display: flex; align-items: center; gap: 8px;">
                <span class="btn-dot btn-dot-yellow" style="cursor: default;"></span>
                Temporary Blocked Coins (<span id="temp-count">0</span>)
            </span>
            <span style="font-size: 11px; color: #848e9c;">Click chip to Unblock</span>
        </div>
        <div id="temp-blocked-container" style="display: flex; flex-wrap: wrap; gap: 8px; min-height: 40px; align-items: center;">
            <span style="color: #848e9c; font-size: 12px;">No temporary blocked coins</span>
        </div>
        <div id="temp-see-more-btn" style="display: none; margin-top: 12px; text-align: center;">
            <button class="btn-see-more" onclick="toggleSeeMore('temp')">See More (<span id="temp-extra-count">0</span>)</button>
        </div>
    </div>

    <div class="table-card">
        <div class="table-header">
            <span style="color: #f87171; display: flex; align-items: center; gap: 8px;">
                <span class="btn-dot btn-dot-red" style="cursor: default;"></span>
                Permanently Blocked Coins (<span id="perm-count">0</span>)
            </span>
            <span style="font-size: 11px; color: #848e9c;">Click chip to Unblock</span>
        </div>
        <div id="perm-blocked-container" style="display: flex; flex-wrap: wrap; gap: 8px; min-height: 40px; align-items: center;">
            <span style="color: #848e9c; font-size: 12px;">No permanently blocked coins</span>
        </div>
        <div id="perm-see-more-btn" style="display: none; margin-top: 12px; text-align: center;">
            <button class="btn-see-more" onclick="toggleSeeMore('perm')">See More (<span id="perm-extra-count">0</span>)</button>
        </div>
    </div>
</div>

<!-- 3. TRADE HISTORY TABLE -->
<div class="table-card">
    <div class="table-header">
        <span>Last 30 Trades Precision Ledger</span>
        <span style="font-size: 12px; color: #848e9c;">Auto-Refreshes every 1s</span>
    </div>
    <div class="table-wrapper">
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Coin & Funding</th>
                    <th>Strategy Mode</th>
                    <th>Entry Timeline</th>
                    <th>Avg Entry Price</th>
                    <th>Exit Timeline</th>
                    <th>Exit Price</th>
                    <th style="min-width: 175px;">Funding Fee</th>
                    <th style="min-width: 105px;">Net PnL</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody id="trades-body">
                <tr><td colspan="10" style="text-align: center; padding: 24px; color: #848e9c;">Waiting for trades to execute...</td></tr>
            </tbody>
        </table>
    </div>
</div>

</div>

<script>
let currentSlotMargins = [{config.TRADE_MARGIN_USD}, {config.TRADE_MARGIN_USD}, {config.TRADE_MARGIN_USD}];
let currentAutoMaxCoins = 1;
let isLocked = false;
let showAllTemp = false;
let showAllPerm = false;
let currentManualList = [];
let currentManualMode = false;
let latestArmedAutoList = [];

function getResolvedSlotLabels() {{
    const activeCoins = currentManualMode ? currentManualList : latestArmedAutoList;
    const labels = [];
    for (let i = 0; i < 3; i++) {{
        if (activeCoins[i]) {{
            labels.push({{ text: activeCoins[i], active: true, enabledSlot: true }});
        }} else if (currentManualMode) {{
            labels.push({{ text: "Empty", active: false, enabledSlot: true }});
        }} else {{
            const isSlotOn = (i < currentAutoMaxCoins);
            labels.push({{ text: isSlotOn ? "Auto Slot" : "Inactive", active: false, enabledSlot: isSlotOn }});
        }}
    }}
    return labels;
}}

function openMarginModal() {{
    if (isLocked) {{
        alert("Base Margin is LOCKED because settlement is less than 1 minute away!");
        return;
    }}
    const modal = document.getElementById('margin-modal');
    const defInp = document.getElementById('default-margin-input');
    defInp.value = currentSlotMargins[0].toFixed(2);

    const slotLabels = getResolvedSlotLabels();
    for (let i = 0; i < 3; i++) {{
        const sInp = document.getElementById(`slot-input-${{i + 1}}`);
        const mCoin = document.getElementById(`modal-coin-${{i + 1}}`);
        if (sInp) sInp.value = currentSlotMargins[i].toFixed(2);
        if (mCoin) {{
            mCoin.innerText = slotLabels[i].text;
            mCoin.style.color = slotLabels[i].active ? "#ffffff" : "#848e9c";
        }}
    }}

    modal.style.display = 'flex';
    setTimeout(() => {{ defInp.focus(); defInp.select(); }}, 50);
}}

function closeMarginModal() {{
    document.getElementById('margin-modal').style.display = 'none';
}}

function setDefaultPreset(val) {{
    const formatted = Number(val).toFixed(2);
    document.getElementById('default-margin-input').value = formatted;
    for (let i = 1; i <= 3; i++) {{
        const el = document.getElementById(`slot-input-${{i}}`);
        if (el) el.value = formatted;
    }}
}}

function applyDefaultToAllSlots() {{
    const rawDef = parseFloat(document.getElementById('default-margin-input').value);
    if (isNaN(rawDef) || rawDef < 0.30) {{
        alert("⚠️ Minimum Base Margin is $0.30 USDT.");
        return;
    }}
    const formatted = rawDef.toFixed(2);
    for (let i = 1; i <= 3; i++) {{
        const el = document.getElementById(`slot-input-${{i}}`);
        if (el) el.value = formatted;
    }}
}}

async function submitMarginModal() {{
    if (isLocked) {{
        alert("Base Margin is LOCKED because settlement is less than 1 minute away!");
        closeMarginModal();
        return;
    }}
    const vals = [];
    for (let i = 1; i <= 3; i++) {{
        const v = parseFloat(document.getElementById(`slot-input-${{i}}`).value);
        if (isNaN(v) || v < 0.30) {{
            alert(`⚠️ Slot #${{i}} margin must be at least $0.30 USDT (required for $5.00 Binance min notional at 20x leverage).`);
            return;
        }}
        vals.push(Math.round(v * 100) / 100);
    }}
    try {{
        const res = await fetch('/api/set-margin', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ slots: vals }})
        }});
        const data = await res.json();
        if (!data.success) {{
            alert(data.message || "Failed to update Slot Margins.");
        }} else {{
            currentSlotMargins = data.slot_margins.map(Number);
            closeMarginModal();
            updateUI();
        }}
    }} catch(e) {{
        alert("Network error while updating Slot Margins.");
    }}
}}

async function setAutoCount(cnt) {{
    if (isLocked) {{
        alert("Selection is LOCKED because settlement is less than 1 minute away!");
        return;
    }}
    try {{
        const res = await fetch('/api/set-auto-count', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ count: cnt }})
        }});
        const data = await res.json();
        if (!data.success) {{
            alert(data.message || "Failed to set Automatic coin count.");
        }} else {{
            currentAutoMaxCoins = Number(data.auto_max_coins);
            updateUI();
        }}
    }} catch(e) {{
        alert("Network error while updating Automatic coin count.");
    }}
}}

function formatDurationJs(sec) {{
    if (sec <= 0) return '0s (Settling...)';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) return `${{h}}h ${{m}}m ${{s}}s`;
    if (m > 0) return `${{m}}m ${{s}}s`;
    return `${{s}}s`;
}}

function renderLimitBoxHtml(d, groupColorClass) {{
    if (d.status === "CANCELED") {{
        const cancelFired = d.cancel_fired || '--';
        const binanceCanceled = d.binance_canceled || '--';
        const latencyMs = (d.cancel_latency_ms !== undefined && d.cancel_latency_ms !== null) ? d.cancel_latency_ms : 0;
        return `
            <div class="limit-box limit-box-blue">
                <div class="limit-box-header">
                    <span>${{d.level}}</span>
                    <span style="font-size: 10px; color: #f87171; font-weight: 800; background: rgba(239, 68, 68, 0.15); padding: 2px 6px; border-radius: 4px;">CANCELED</span>
                </div>
                <div class="limit-box-metrics">
                    <span>Target Price: <strong style="color: #ffffff;">${{d.target_price || d.price || '--'}}</strong></span>
                    <span>Target Qty: <strong style="color: #94a3b8;">${{d.target_qty || d.qty || '--'}}</strong></span>
                </div>
                <div class="limit-box-timings">
                    <div>🚀 Cancel Fired (Local VPS): <strong style="color: #ffffff;">${{cancelFired}}</strong></div>
                    <div>🎯 Binance Canceled (Engine): <strong style="color: #60a5fa;">${{binanceCanceled}}</strong></div>
                    <div style="margin-top: 2px; color: #94a3b8;">⚡ Cancel Latency: <strong style="color: #f0b90b;">${{latencyMs}} ms</strong></div>
                </div>
            </div>
        `;
    }}

    const filledBoxClass = (groupColorClass === 'limit-box-yellow') ? 'limit-box-yellow' : 'limit-box-green';
    const firstTime = d.first_time || d.time || '--';
    const lastTime = d.last_time || d.time || '--';
    const spanMs = (d.span_ms !== undefined && d.span_ms !== null) ? d.span_ms : 0;
    const fillsCount = d.fills_count || 1;
    const fillLabel = fillsCount > 1 ? `${{fillsCount}} Partial Fills` : `1 Single Fill`;

    return `
        <div class="limit-box ${{filledBoxClass}}">
            <div class="limit-box-header">
                <span>${{d.level}}</span>
                <span style="font-size: 10px; background: rgba(14, 203, 129, 0.18); color: #0ecb81; padding: 2px 6px; border-radius: 4px;">FILLED • ${{fillLabel}}</span>
            </div>
            <div class="limit-box-metrics">
                <span>Price: <strong style="color: #ffffff;">${{d.price}}</strong></span>
                <span>Total Qty: <strong style="color: #0ecb81;">${{d.qty}}</strong></span>
            </div>
            <div class="limit-box-timings">
                <div>⏱️ First Fill: <strong style="color: #ffffff;">${{firstTime}}</strong></div>
                <div>🏁 Last Fill : <strong style="color: #ffffff;">${{lastTime}}</strong></div>
                <div style="margin-top: 2px; color: #94a3b8;">⚡ Fill Window: <strong style="color: #f0b90b;">${{spanMs}} ms</strong></div>
            </div>
        </div>
    `;
}}

async function toggleTrading() {{
    try {{
        const res = await fetch('/api/toggle-trading', {{ method: 'POST' }});
        const data = await res.json();
        updateToggleBtn(data.trading_enabled);
    }} catch(e) {{}}
}}

function updateToggleBtn(enabled) {{
    const btn = document.getElementById('btn-trade-toggle');
    const lbl = document.getElementById('btn-trade-label');
    if (enabled) {{
        btn.className = "btn-toggle btn-on";
        if (lbl) lbl.innerText = "● TRADING: ON";
    }} else {{
        btn.className = "btn-toggle btn-off";
        if (lbl) lbl.innerText = "■ TRADING: OFF";
    }}
}}

async function setModeAuto() {{
    if (isLocked) {{
        alert("Selection is LOCKED because settlement is less than 1 minute away!");
        return;
    }}
    try {{
        const res = await fetch('/api/reset-auto', {{ method: 'POST' }});
        const data = await res.json();
        if (!data.success) {{
            alert(data.message || "Failed to switch mode.");
        }}
        updateUI();
    }} catch(e) {{}}
}}

async function setModeManual() {{
    if (isLocked) {{
        alert("Selection is LOCKED because settlement is less than 1 minute away!");
        return;
    }}
    try {{
        const res = await fetch('/api/set-manual', {{ method: 'POST' }});
        const data = await res.json();
        if (!data.success) {{
            alert(data.message || "Failed to switch mode.");
        }} else {{
            updateUI();
        }}
    }} catch(e) {{}}
}}

async function toggleSelectCandidate(sym, isTempBlocked) {{
    if (isTempBlocked) {{
        alert(`⚠️ ${{sym}} TEMPORARY BLOCKED hai! Isme trade nahi ho sakti. Pehle niche wale card se ise unblock karein.`);
        return;
    }}
    if (isLocked) {{
        alert("Coin selection is LOCKED because settlement is less than 1 minute away!");
        return;
    }}

    if (currentManualMode && !currentManualList.includes(sym) && currentManualList.length >= 3) {{
        alert("⚠️ अधिकतम 3 कॉइन्स ही चुने जा सकते हैं! किसी पुराने कॉइन पर दोबारा क्लिक करके उसे हटाएं, फिर नया कॉइन चुनें।");
        return;
    }}

    try {{
        const res = await fetch('/api/select-coin', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ symbol: sym }})
        }});
        const data = await res.json();
        if (!data.success) {{
            alert(data.message || "Action failed.");
        }} else {{
            updateUI();
        }}
    }} catch(e) {{}}
}}

async function blockCoin(sym, type) {{
    const label = type === 'temporary' ? 'Temporary (🟡)' : 'Permanent (🔴)';
    if (!confirm(`Kya aap ${{sym}} ko ${{label}} block karna chahte hain?`)) return;
    try {{
        const res = await fetch('/api/block-coin', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ symbol: sym, type: type }})
        }});
        const data = await res.json();
        if (!data.success) {{
            alert(data.message || "Block failed");
        }} else {{
            updateUI();
        }}
    }} catch(e) {{
        alert("Network error while blocking coin");
    }}
}}

async function unblockCoin(sym, type) {{
    if (!confirm(`Kya aap ${{sym}} ko Unblock karna chahte hain?`)) return;
    try {{
        const res = await fetch('/api/unblock-coin', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ symbol: sym, type: type }})
        }});
        const data = await res.json();
        if (!data.success) {{
            alert(data.message || "Unblock failed");
        }} else {{
            updateUI();
        }}
    }} catch(e) {{
        alert("Network error while unblocking coin");
    }}
}}

function toggleSeeMore(type) {{
    if (type === 'temp') showAllTemp = !showAllTemp;
    else if (type === 'perm') showAllPerm = !showAllPerm;
    updateUI();
}}

async function updateUI() {{
    try {{
        const res = await fetch('/api/status');
        const data = await res.json();
        isLocked = !!data.selection_locked;
        currentManualMode = !!data.manual_mode;
        currentManualList = data.manual_selected_symbols || [];

        if (Array.isArray(data.slot_margins) && data.slot_margins.length === 3) {{
            currentSlotMargins = data.slot_margins.map(Number);
        }} else if (data.base_margin_usd !== undefined && data.base_margin_usd !== null) {{
            const m = Number(data.base_margin_usd);
            currentSlotMargins = [m, m, m];
        }}
        if (data.auto_max_coins !== undefined && data.auto_max_coins !== null) {{
            currentAutoMaxCoins = Number(data.auto_max_coins);
        }}

        document.getElementById('ws-status').innerText = data.status;
        document.getElementById('rate').innerText = data.funding_rate;
        document.getElementById('offset').innerText = data.clock_offset_ms + ' ms';
        updateToggleBtn(data.trading_enabled);

        const btnAuto = document.getElementById('btn-mode-auto');
        const btnManual = document.getElementById('btn-mode-manual');
        const slotAuto = document.getElementById('slot-mode-auto');
        const slotManual = document.getElementById('slot-mode-manual');
        const manualHint = document.getElementById('manual-sub-hint');

        if (btnAuto && btnManual) {{
            btnAuto.disabled = isLocked;
            btnManual.disabled = isLocked;

            if (currentManualMode) {{
                if (slotAuto) slotAuto.className = "mode-slot mode-slot-inactive";
                if (slotManual) slotManual.className = "mode-slot mode-slot-manual-active";
                btnAuto.className = "btn-mode btn-mode-inactive";
                btnAuto.innerText = "⚡ AUTOMATIC";
                btnManual.className = "btn-mode btn-mode-manual-active";
                btnManual.innerText = `🎯 MANUAL (${{currentManualList.length}}/3)`;
                if (manualHint) {{
                    manualHint.innerText = `${{currentManualList.length}} OF 3 SELECTED`;
                    manualHint.style.color = "#f0b90b";
                }}
            }} else {{
                if (slotAuto) slotAuto.className = "mode-slot mode-slot-auto-active";
                if (slotManual) slotManual.className = "mode-slot mode-slot-inactive";
                btnAuto.className = "btn-mode btn-mode-auto-active";
                btnAuto.innerText = `⚡ AUTOMATIC (${{currentAutoMaxCoins}})`;
                btnManual.className = "btn-mode btn-mode-inactive";
                btnManual.innerText = "🎯 MANUAL";
                if (manualHint) {{
                    manualHint.innerText = "UP TO 3 COINS";
                    manualHint.style.color = "#848e9c";
                }}
            }}
        }});

        [1, 2, 3].forEach(n => {{
            const cntBtn = document.getElementById(`btn-auto-cnt-${{n}}`);
            if (cntBtn) {{
                cntBtn.disabled = isLocked;
                cntBtn.className = (currentAutoMaxCoins === n) ? "btn-auto-cnt btn-auto-cnt-active" : "btn-auto-cnt";
            }}
        }});

        const hasValidTarget = (data.target_symbol && !data.target_symbol.includes("Scanning") && !data.target_symbol.includes("--") && !data.target_symbol.includes("IDLE"));
        latestArmedAutoList = hasValidTarget ? data.target_symbol.split(',').map(s => s.trim()).filter(Boolean) : [];

        if (currentManualMode) {{
            if (currentManualList.length === 0) {{
                document.getElementById('coin').innerText = "0 Coins (PASS - IDLE)";
                document.getElementById('coin').style.color = "#f87171";
            }} else {{
                document.getElementById('coin').innerText = currentManualList.join(", ");
                document.getElementById('coin').style.color = "#f0b90b";
            }}
        }} else {{
            document.getElementById('coin').innerText = data.target_symbol;
            document.getElementById('coin').style.color = "#f0b90b";
        }}

        // Update Live Slot Margin Preview Card (#1, #2, #3 + Live Coin Name)
        const slotLabels = getResolvedSlotLabels();
        for (let i = 0; i < 3; i++) {{
            const rowEl = document.getElementById(`slot-row-${{i + 1}}`);
            const coinEl = document.getElementById(`slot-coin-${{i + 1}}`);
            const valEl = document.getElementById(`slot-val-${{i + 1}}`);
            if (coinEl) {{
                coinEl.innerText = slotLabels[i].text;
                coinEl.className = slotLabels[i].active ? "slot-coin-name" : "slot-coin-empty";
            }}
            if (valEl) {{
                valEl.innerText = `$${{currentSlotMargins[i].toFixed(2)}}`;
            }}
            if (rowEl) {{
                rowEl.className = `slot-preview-row slot-row-${{i + 1}}${{slotLabels[i].enabledSlot ? '' : ' slot-row-dim'}}`;
            }}
        }}

        const lockBadge = document.getElementById('lock-badge');
        if (isLocked) {{
            lockBadge.className = "badge-locked";
            lockBadge.innerText = "🔒 SELECTION LOCKED (<=1m)";
            document.getElementById('lock-warning').innerText = "Selection is locked for execution safety";
        }} else {{
            lockBadge.className = "badge-mode";
            lockBadge.innerText = "SELECTION: OPEN (>1m)";
            document.getElementById('lock-warning').innerText = currentManualMode ? 
                `Manual Mode (${{currentManualList.length}}/3 Selected • Click to Toggle)` : 
                `Automatic Mode (Up to ${{currentAutoMaxCoins}} Coin${{currentAutoMaxCoins > 1 ? 's' : ''}} • Sweet-Spot Priority)`;
        }}

        if (data.target_settlement_ms && data.target_settlement_ms > 0) {{
            const nowMs = Date.now() + (data.clock_offset_ms || 0);
            const diffSec = Math.max(0, Math.floor((data.target_settlement_ms - nowMs) / 1000));
            const timeStr = data.target_settlement_time || '--:--:-- IST';
            document.getElementById('settle').innerText = `${{timeStr}} (${{formatDurationJs(diffSec)}})`;
        }} else {{
            document.getElementById('settle').innerText = data.target_settlement || '--:--:-- IST';
        }}

        const blockedData = data.blocked_coins || {{ temporary: [], permanent: [] }};
        const tempList = blockedData.temporary || [];
        const permList = blockedData.permanent || [];

        const cBody = document.getElementById('candidates-body');
        if (data.top_5_candidates && data.top_5_candidates.length > 0) {{
            cBody.innerHTML = data.top_5_candidates.map((c, i) => {{
                const isTemp = tempList.includes(c.symbol);
                let isSelected = false;
                let activeTagHtml = '';

                if (currentManualMode) {{
                    const mIdx = currentManualList.indexOf(c.symbol);
                    if (mIdx !== -1) {{
                        isSelected = true;
                        const sMargin = currentSlotMargins[mIdx] !== undefined ? `$${{currentSlotMargins[mIdx].toFixed(2)}}` : '';
                        activeTagHtml = `<span class="manual-tag">SELECTED #${{mIdx + 1}} (${{sMargin}})</span>`;
                    }}
                }} else {{
                    const aIdx = latestArmedAutoList.indexOf(c.symbol);
                    if (aIdx !== -1) {{
                        isSelected = true;
                        const sMargin = currentSlotMargins[aIdx] !== undefined ? `$${{currentSlotMargins[aIdx].toFixed(2)}}` : '';
                        activeTagHtml = `<span class="active-tag">ARMED #${{aIdx + 1}} (${{sMargin}})</span>`;
                    }}
                }}

                let rowClass = "candidate-row";
                if (isTemp) {{
                    rowClass += " row-temp-blocked";
                    activeTagHtml = '<span class="badge-temp-block">TEMP BLOCKED</span>';
                }} else if (isSelected) {{
                    rowClass += " candidate-active";
                }}

                return `
                    <tr class="${{rowClass}}" onclick="${{isLocked ? '' : `toggleSelectCandidate('${{c.symbol}}', ${{isTemp}})`}}">
                        <td style="text-align: center;">
                            <input type="checkbox" ${{isSelected ? 'checked' : ''}} ${{isLocked || isTemp ? 'disabled' : ''}} onclick="event.stopPropagation(); toggleSelectCandidate('${{c.symbol}}', ${{isTemp}});">
                        </td>
                        <td><strong style="color: ${{isTemp ? '#f0b90b' : (isSelected ? '#0ecb81' : (i === 0 ? '#f0b90b' : '#848e9c'))}};">#${{i + 1}}</strong></td>
                        <td><strong>${{c.symbol}}</strong>${{activeTagHtml}}</td>
                        <td style="color: #f0b90b; font-family: monospace; font-weight: 600;">${{c.vol_str || '--'}}</td>
                        <td style="color: ${{isTemp ? '#f0b90b' : '#0ecb81'}}; font-weight: 700;">${{(c.rate * 100).toFixed(4)}}%</td>
                        <td>${{c.settle_str}}</td>
                        <td style="text-align: center;">
                            <div class="dot-group">
                                <button class="btn-dot btn-dot-yellow" title="Temporary Block ${{c.symbol}}" onclick="event.stopPropagation(); blockCoin('${{c.symbol}}', 'temporary');"></button>
                                <button class="btn-dot btn-dot-red" title="Permanent Block ${{c.symbol}}" onclick="event.stopPropagation(); blockCoin('${{c.symbol}}', 'permanent');"></button>
                            </div>
                        </td>
                    </tr>
                `;
            }}).join('');
        }}

        document.getElementById('temp-count').innerText = tempList.length;
        document.getElementById('perm-count').innerText = permList.length;

        const tempContainer = document.getElementById('temp-blocked-container');
        const tempSeeMore = document.getElementById('temp-see-more-btn');
        if (tempList.length === 0) {{
            tempContainer.innerHTML = '<span style="color: #848e9c; font-size: 12px;">No temporary blocked coins</span>';
            tempSeeMore.style.display = 'none';
        }} else {{
            const visibleTemp = showAllTemp ? tempList : tempList.slice(0, 25);
            tempContainer.innerHTML = visibleTemp.map(sym => `
                <span class="chip-blocked chip-temp" onclick="unblockCoin('${{sym}}', 'temporary')" title="Click to Unblock ${{sym}}">
                    ${{sym}} <span class="chip-close">✖</span>
                </span>
            `).join('');

            if (tempList.length > 25) {{
                tempSeeMore.style.display = 'block';
                const extra = tempList.length - 25;
                tempSeeMore.querySelector('button').innerText = showAllTemp ? "Show Less" : `See More (${{extra}} more)`;
            }} else {{
                tempSeeMore.style.display = 'none';
            }}
        }}

        const permContainer = document.getElementById('perm-blocked-container');
        const permSeeMore = document.getElementById('perm-see-more-btn');
        if (permList.length === 0) {{
            permContainer.innerHTML = '<span style="color: #848e9c; font-size: 12px;">No permanently blocked coins</span>';
            permSeeMore.style.display = 'none';
        }} else {{
            const visiblePerm = showAllPerm ? permList : permList.slice(0, 25);
            permContainer.innerHTML = visiblePerm.map(sym => `
                <span class="chip-blocked chip-perm" onclick="unblockCoin('${{sym}}', 'permanent')" title="Click to Unblock ${{sym}}">
                    ${{sym}} <span class="chip-close">✖</span>
                </span>
            `).join('');

            if (permList.length > 25) {{
                permSeeMore.style.display = 'block';
                const extra = permList.length - 25;
                permSeeMore.querySelector('button').innerText = showAllPerm ? "Show Less" : `See More (${{extra}} more)`;
            }} else {{
                permSeeMore.style.display = 'none';
            }}
        }}

        const tbody = document.getElementById('trades-body');
        if (!data.trades || data.trades.length === 0) {{
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center; padding:24px; color:#848e9c;">Waiting for trades to execute...</td></tr>';
        }} else {{
            tbody.innerHTML = data.trades.map((t, idx) => {{
                let statusClass = "tag-success";
                if (t.status.includes("REJECTED")) statusClass = "tag-cancel";
                else if (t.status.includes("STOP LOSS") || t.status.includes("EMERGENCY")) statusClass = "tag-warn";

                let pnlClass = t.pnl_class || "pnl-neutral";
                let pnlValStr = t.pnl || "$0.0000";

                let fundingClass = t.funding_fee_class || "pnl-neutral";
                let fundingValStr = t.funding_fee_received || "$0.0000";
                let fundingTimeStr = (t.funding_fee_time && t.funding_fee_time !== "--:--:--") ? `🕒 ${{t.funding_fee_time}}` : "🕒 --:--:--";
                let fundingOrdersStr = t.funding_orders_summary || "0 Orders ($0.00)";

                const hitsList = t.dca_hits || [];
                let dcaHtml = '<span style="color:#848e9c;">None (No Limit Orders Filled)</span>';
                let filledCount = 0;

                if (hitsList.length > 0) {{
                    const shallowHits = hitsList.filter(d => (d.group === 'shallow') || (d.level && d.level.toLowerCase().includes('shallow')));
                    const dcaDeepHits = hitsList.filter(d => (d.group === 'dca') || (d.level && d.level.toLowerCase().includes('dca')));
                    filledCount = hitsList.filter(d => d.status !== 'CANCELED').length;

                    let rowsHtml = '';
                    if (shallowHits.length > 0) {{
                        rowsHtml += `<div class="limit-row">${{shallowHits.map(d => renderLimitBoxHtml(d, 'limit-box-green')).join('')}}</div>`;
                    }}
                    if (dcaDeepHits.length > 0) {{
                        rowsHtml += `<div class="limit-row">${{dcaDeepHits.map(d => renderLimitBoxHtml(d, 'limit-box-yellow')).join('')}}</div>`;
                    }}
                    dcaHtml = `<div class="limit-rows-wrapper">${{rowsHtml}}</div>`;
                }}

                return `
                    <tr class="main-row">
                        <td style="color:#848e9c;">${{idx + 1}}</td>
                        <td>
                            <strong>${{t.symbol}}</strong>
                            <div class="sub-text" style="color:#0ecb81; font-weight:600;">Rate: ${{t.funding_rate}}</div>
                        </td>
                        <td><span class="badge" style="font-size:10px; padding:3px 8px;">${{t.tier || 'UNIFIED HARVEST'}}</span></td>
                        <td>
                            <div class="mono">Sent: ${{t.entry_sent || '--'}}</div>
                            <div class="mono sub-text">→ ACK: ${{t.entry_ack || '--'}} (${{t.entry_rtt || '--'}})</div>
                            <div class="mono sub-text" style="color:#0ecb81; font-weight:600;">→ First Fill: ${{t.entry_fill_time || '--'}}</div>
                        </td>
                        <td>
                            <div class="mono" style="color:#0ecb81; font-weight:bold;">${{t.entry_fill_price || '--'}}</div>
                            <div class="sub-text">Weighted Avg Entry</div>
                        </td>
                        <td>
                            <div class="mono">Fired: ${{t.exit_sent || '--'}}</div>
                            <div class="mono sub-text" style="color:#eaecef;">→ Closed: ${{t.exit_fill_time || '--'}}</div>
                        </td>
                        <td>
                            <div class="mono"><strong style="color:#f0b90b; font-size:14px;">${{t.exit_price || '--'}}</strong></div>
                            <div class="sub-text">${{t.exit_type || '--'}}</div>
                        </td>
                        <td>
                            <div class="mono ${{fundingClass}}">${{fundingValStr}}</div>
                            <div class="mono sub-text" style="color:#60a5fa; font-size:10.5px; font-weight:600;">${{fundingTimeStr}}</div>
                            <div class="mono sub-text" style="color:#f0b90b; font-size:10.5px; font-weight:600;">📦 ${{fundingOrdersStr}}</div>
                        </td>
                        <td>
                            <div class="mono ${{pnlClass}}">${{pnlValStr}}</div>
                            <div class="sub-text">Net Realized</div>
                        </td>
                        <td><span class="${{statusClass}}">${{t.status}}</span></td>
                    </tr>
                    <tr class="sub-row">
                        <td colspan="10" style="padding:0;">
                            <div class="sub-box">
                                <div class="sub-item">
                                    <span class="sub-label">Execution Summary:</span>
                                    <strong class="mono" style="color:#eaecef;">${{t.details || '--'}}</strong>
                                </div>
                                <div class="sub-item">
                                    <span class="sub-label">PnL Breakdown (incl. Funding):</span>
                                    <strong class="mono" style="color:#f0b90b;">${{t.pnl_breakdown || '--'}}</strong>
                                </div>
                                <div class="sub-item" style="flex-direction: column; align-items: flex-start; width: 100%;">
                                    <span class="sub-label">Limit Order Fills (${{filledCount}} Filled / ${{hitsList.length}} Total Levels):</span>
                                    ${{dcaHtml}}
                                </div>
                            </div>
                        </td>
                    </tr>
                `;
            }}).join('');
        }}

    }} catch (e) {{}}
}}

setInterval(updateUI, 1000);
updateUI();
</script>
</body>
</html>"""
