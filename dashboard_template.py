from __future__ import annotations
import config


def get_dashboard_html() -> str:
    """
    Binance Tokyo Funding Sniper ka complete responsive dark-mode HTML,
    CSS aur JavaScript template return karta hai (Multi-Coin Auto + Manual Checkbox UI).
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
<title>Binance Tokyo Funding Sniper (${config.TRADE_MARGIN_USD} USDT Ledger)</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}

body {{ background-color: #0b0e11; color: #eaecef; padding: 14px; display: flex; justify-content: center; }}
.container {{ width: 100%; max-width: 1260px; display: flex; flex-direction: column; gap: 14px; }}
.header {{ background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 10px; }}
.header h1 {{ font-size: 16px; color: #f0b90b; font-weight: 700; }}
.badge {{ background: rgba(240, 185, 11, 0.15); color: #f0b90b; padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
.badge-mode {{ background: rgba(37, 99, 235, 0.2); color: #60a5fa; padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; border: 1px solid rgba(96, 165, 250, 0.4); }}
.badge-locked {{ background: rgba(239, 68, 68, 0.2); color: #f87171; padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; border: 1px solid rgba(239, 68, 68, 0.4); }}

.btn-toggle {{ padding: 7px 16px; border-radius: 8px; font-size: 12px; font-weight: 700; cursor: pointer; border: none; transition: 0.2s; }}
.btn-on {{ background: #0ecb81; color: #ffffff; }}
.btn-off {{ background: #ef4444; color: #ffffff; }}

.mode-container {{ display: flex; gap: 6px; align-items: center; background: #14181d; padding: 4px; border-radius: 10px; border: 1px solid #2b313a; }}
.btn-mode {{ padding: 6px 14px; border-radius: 7px; font-size: 11px; font-weight: 700; cursor: pointer; border: none; transition: 0.2s; display: flex; align-items: center; gap: 4px; }}
.btn-mode-auto-active {{ background: #0ecb81; color: #ffffff; box-shadow: 0 0 8px rgba(14, 203, 129, 0.35); }}
.btn-mode-manual-active {{ background: #f0b90b; color: #0b0e11; box-shadow: 0 0 8px rgba(240, 185, 11, 0.35); font-weight: 800; }}
.btn-mode-inactive {{ background: transparent; color: #848e9c; }}

.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
.card {{ background: #1e2329; padding: 12px 14px; border-radius: 10px; border: 1px solid #2b313a; }}
.card-title {{ font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 4px; }}
.card-value {{ font-size: 15px; font-weight: 700; color: #f0b90b; word-break: break-word; }}
.table-card {{ background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; overflow: hidden; }}
.table-header {{ font-size: 15px; font-weight: 700; color: #eaecef; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }}
.table-wrapper {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 8px; border: 1px solid #2b313a; }}
table {{ width: 100%; border-collapse: collapse; min-width: 1040px; font-size: 13px; text-align: left; }}
th {{ background: #14181d; color: #848e9c; padding: 12px 14px; font-weight: 600; text-transform: uppercase; font-size: 11px; border-bottom: 1px solid #2b313a; white-space: nowrap; }}
td {{ padding: 12px 14px; border-bottom: 1px solid #1e252e; vertical-align: middle; white-space: nowrap; }}
tr.main-row:nth-child(4n+1) {{ background: #181d24; }}
tr.sub-row {{ background: rgba(0, 0, 0, 0.25); border-bottom: 2px solid #2b313a; }}

.sub-box {{ padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; font-size: 12px; border-left: 3px solid #f0b90b; background: rgba(240, 185, 11, 0.04); white-space: normal; }}
.sub-item {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
.sub-label {{ color: #848e9c; text-transform: uppercase; font-size: 10px; font-weight: 700; letter-spacing: 0.4px; }}
.tag-success {{ background: rgba(14, 203, 129, 0.15); color: #0ecb81; padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }}
.tag-cancel {{ background: rgba(132, 142, 156, 0.2); color: #848e9c; padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }}
.tag-warn {{ background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }}
.mono {{ font-family: 'SFMono-Regular', Consolas, Menlo, monospace; font-size: 12px; }}
.sub-text {{ font-size: 11px; color: #848e9c; margin-top: 2px; }}

.pnl-positive {{ color: #0ecb81 !important; font-weight: 800; font-size: 13px; text-shadow: 0 0 8px rgba(14, 203, 129, 0.25); }}
.pnl-negative {{ color: #f87171 !important; font-weight: 800; font-size: 13px; text-shadow: 0 0 8px rgba(248, 113, 113, 0.25); }}
.pnl-neutral {{ color: #848e9c !important; font-size: 13px; }}

.limit-rows-wrapper {{ display: flex; flex-direction: column; gap: 10px; width: 100%; margin-top: 4px; }}
.limit-row {{ display: flex; flex-wrap: wrap; gap: 10px; width: 100%; align-items: stretch; }}
.limit-box {{ flex: 1 1 260px; max-width: 350px; padding: 10px 12px; border-radius: 8px; font-family: 'SFMono-Regular', Consolas, monospace; display: flex; flex-direction: column; gap: 6px; }}
.limit-box-blue {{ background: rgba(59, 130, 246, 0.10); border: 1px solid rgba(59, 130, 246, 0.55); border-left: 4px solid #3b82f6; color: #93c5fd; box-shadow: 0 2px 8px rgba(59, 130, 246, 0.08); }}
.limit-box-yellow {{ background: rgba(240, 185, 11, 0.10); border: 1px solid rgba(240, 185, 11, 0.55); border-left: 4px solid #f0b90b; color: #fcd34d; box-shadow: 0 2px 8px rgba(240, 185, 11, 0.08); }}
.limit-box-header {{ font-size: 12px; font-weight: 800; padding-bottom: 5px; border-bottom: 1px solid rgba(255, 255, 255, 0.10); display: flex; justify-content: space-between; align-items: center; }}
.limit-box-blue .limit-box-header {{ color: #60a5fa; }}
.limit-box-yellow .limit-box-header {{ color: #f0b90b; }}
.limit-box-metrics {{ font-size: 11px; color: #eaecef; font-weight: 700; display: flex; justify-content: space-between; gap: 8px; }}
.limit-box-timings {{ font-size: 11px; line-height: 1.45; color: #cbd5e1; background: rgba(0, 0, 0, 0.25); padding: 6px 8px; border-radius: 5px; }}

.candidate-row {{ cursor: pointer; transition: background 0.2s, border 0.2s; }}
.candidate-row:hover {{ background: rgba(240, 185, 11, 0.08); }}

.candidate-auto-active {{
    background: rgba(14, 203, 129, 0.18) !important;
    border-left: 5px solid #0ecb81 !important;
}}
.candidate-manual-active {{
    background: rgba(240, 185, 11, 0.20) !important;
    border-left: 5px solid #f0b90b !important;
}}

.active-auto-tag {{
    background: #0ecb81;
    color: #0b0e11;
    font-size: 10px;
    font-weight: 800;
    padding: 2px 7px;
    border-radius: 4px;
    margin-left: 6px;
    display: inline-block;
}}
.active-manual-tag {{
    background: #f0b90b;
    color: #0b0e11;
    font-size: 10px;
    font-weight: 800;
    padding: 2px 7px;
    border-radius: 4px;
    margin-left: 6px;
    display: inline-block;
}}

.candidate-row.row-temp-blocked {{
    background: rgba(240, 185, 11, 0.15) !important;
    border-left: 5px solid #f0b90b !important;
}}
.badge-temp-block {{
    background: #f0b90b;
    color: #0b0e11;
    font-size: 10px;
    font-weight: 800;
    padding: 2px 7px;
    border-radius: 4px;
    margin-left: 6px;
    display: inline-block;
}}

.dot-group {{ display: flex; gap: 10px; justify-content: center; align-items: center; }}
.btn-dot {{
    width: 18px;
    height: 18px;
    border-radius: 50%;
    border: 2px solid rgba(255, 255, 255, 0.2);
    cursor: pointer;
    transition: transform 0.2s;
    outline: none;
}}
.btn-dot:hover {{ transform: scale(1.25); }}
.btn-dot-yellow {{ background: #f0b90b; box-shadow: 0 0 6px rgba(240, 185, 11, 0.5); }}
.btn-dot-red {{ background: #ef4444; box-shadow: 0 0 6px rgba(239, 68, 68, 0.5); }}

.chip-blocked {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    border-radius: 8px;
    font-size: 12px;
    font-family: 'SFMono-Regular', Consolas, monospace;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s;
}}
.chip-blocked:hover {{ transform: translateY(-2px); }}
.chip-temp {{ background: rgba(240, 185, 11, 0.15); border: 1px solid rgba(240, 185, 11, 0.4); color: #f0b90b; }}
.chip-perm {{ background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.4); color: #f87171; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
    <div>
        <h1>Binance Tokyo Funding Sniper</h1>
        <div style="font-size: 11px; color: #848e9c;">Radar Top 10 &bull; Auto Top 2 + Manual Add &bull; T-{config.ENTRY_BEFORE_SETTLE_MS}ms</div>
    </div>
    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
        <button id="btn-trade-toggle" class="btn-toggle btn-on" onclick="toggleTrading()">TRADING: ON</button>
        
        <div class="mode-container">
            <button id="btn-mode-auto" class="btn-mode btn-mode-auto-active" onclick="setModeAuto()">⚡ AUTO TOP 2</button>
            <button id="btn-mode-manual" class="btn-mode btn-mode-inactive" onclick="setModeManualPrompt()">🎯 ADD MANUAL</button>
        </div>

        <div class="badge-mode" id="lock-badge">SELECTION: OPEN (>1m)</div>
        <div class="badge" id="ws-status">SCANNING</div>
    </div>
</div>

<div class="grid">
    <div class="card"><div class="card-title">Armed Target Coins</div><div class="card-value" id="coin">--</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color: #0ecb81;">--%</div></div>
    <div class="card"><div class="card-title">Next Settlement</div><div class="card-value" id="settle" style="color: #eaecef;">--:--:-- IST</div></div>
    <div class="card"><div class="card-title">Base Margin</div><div class="card-value" style="color: #f0b90b;">${config.TRADE_MARGIN_USD} USDT</div></div>
    <div class="card"><div class="card-title">Clock Drift</div><div class="card-value" id="offset" style="color: #0ecb81;">0.0 ms</div></div>
</div>

<!-- TOP 10 CANDIDATES RADAR TABLE -->
<div class="table-card">
    <div class="table-header">
        <span>Top 10 Radar (🟢 AUTO ARMED &bull; 🟡 MANUAL ARMED)</span>
        <span id="lock-warning" style="font-size: 12px; color: #f0b90b;">Locked automatically within 1 min of settlement</span>
    </div>
    <div class="table-wrapper">
        <table style="min-width: 780px;">
            <thead>
                <tr>
                    <th style="width: 45px; text-align: center;">Select</th>
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

<!-- BLOCKED COINS MANAGEMENT CARDS -->
<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px;">
    <div class="table-card">
        <div class="table-header"><span style="color: #f0b90b;">Temporary Blocked Coins (<span id="temp-count">0</span>)</span></div>
        <div id="temp-blocked-container" style="display: flex; flex-wrap: wrap; gap: 8px; min-height: 40px; align-items: center;"></div>
    </div>
    <div class="table-card">
        <div class="table-header"><span style="color: #f87171;">Permanently Blocked Coins (<span id="perm-count">0</span>)</span></div>
        <div id="perm-blocked-container" style="display: flex; flex-wrap: wrap; gap: 8px; min-height: 40px; align-items: center;"></div>
    </div>
</div>

<!-- TRADE HISTORY TABLE -->
<div class="table-card">
    <div class="table-header"><span>Last 20 Trades Precision Ledger</span></div>
    <div class="table-wrapper">
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Coin &amp; Funding</th>
                    <th>Strategy Mode</th>
                    <th>Entry Timeline</th>
                    <th>Avg Entry Price</th>
                    <th>Exit Timeline</th>
                    <th>Exit Price</th>
                    <th>Funding Fee</th>
                    <th>Net PnL</th>
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
let isLocked = false;

function renderLimitBoxHtml(d, boxColorClass) {{
    if (d.status === "CANCELED") {{
        const cancelFired = d.cancel_fired || '--';
        const binanceCanceled = d.binance_canceled || '--';
        const latencyMs = (d.cancel_latency_ms !== undefined && d.cancel_latency_ms !== null) ? d.cancel_latency_ms : 0;
        return `
            <div class="limit-box ${{boxColorClass}}" style="opacity: 0.90; border-style: dashed;">
                <div class="limit-box-header">
                    <span>${{d.level}}</span>
                    <span style="font-size: 10px; color: #f87171; font-weight: 800; background: rgba(239, 68, 68, 0.15); padding: 2px 6px; border-radius: 4px;">CANCELED</span>
                </div>
                <div class="limit-box-metrics">
                    <span>Target Price: <strong style="color: #ffffff;">${{d.target_price || d.price || '--'}}</strong></span>
                    <span>Target Qty: <strong style="color: #94a3b8;">${{d.target_qty || d.qty || '--'}}</strong></span>
                </div>
                <div class="limit-box-timings">
                    <div>🚀 Cancel Fired: <strong style="color: #ffffff;">${{cancelFired}}</strong></div>
                    <div>🎯 Binance Canceled: <strong style="color: #60a5fa;">${{binanceCanceled}}</strong></div>
                    <div style="margin-top: 2px; color: #94a3b8;">⚡ Cancel Latency: <strong style="color: #f0b90b;">${{latencyMs}} ms</strong></div>
                </div>
            </div>
        `;
    }}

    const firstTime = d.first_time || d.time || '--';
    const lastTime = d.last_time || d.time || '--';
    const spanMs = (d.span_ms !== undefined && d.span_ms !== null) ? d.span_ms : 0;
    const fillsCount = d.fills_count || 1;
    const fillLabel = fillsCount > 1 ? `${{fillsCount}} Partial Fills` : `1 Single Fill`;

    return `
        <div class="limit-box ${{boxColorClass}}">
            <div class="limit-box-header">
                <span>${{d.level}}</span>
                <span style="font-size: 10px; opacity: 0.85;">${{fillLabel}}</span>
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
    btn.className = enabled ? "btn-toggle btn-on" : "btn-toggle btn-off";
    btn.innerText = enabled ? "TRADING: ON" : "TRADING: OFF";
}}

async function setModeAuto() {{
    if (isLocked) return alert("Selection is LOCKED!");
    try {{
        await fetch('/api/reset-auto', {{ method: 'POST' }});
    }} catch(e) {{}}
}}

function setModeManualPrompt() {{
    if (isLocked) return alert("Selection is LOCKED!");
    alert("🎯 Niche table me kisi bhi coin par click karke use Extra Armed list me add ya remove kar sakte hain!");
}}

async function toggleSelectCandidate(sym, isTempBlocked) {{
    if (isTempBlocked) return alert(`⚠️ ${{sym}} TEMPORARY BLOCKED hai!`);
    if (isLocked) return alert("Selection is LOCKED!");
    try {{
        await fetch('/api/select-coin', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ symbol: sym }})
        }});
    }} catch(e) {{}}
}}

async function blockCoin(sym, type) {{
    if (!confirm(`Block ${{sym}} (${{type}})?`)) return;
    try {{
        await fetch('/api/block-coin', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ symbol: sym, type: type }})
        }});
        updateUI();
    }} catch(e) {{}}
}}

async function unblockCoin(sym, type) {{
    if (!confirm(`Unblock ${{sym}}?`)) return;
    try {{
        await fetch('/api/unblock-coin', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ symbol: sym, type: type }})
        }});
        updateUI();
    }} catch(e) {{}}
}}

async function updateUI() {{
    try {{
        const res = await fetch('/api/status');
        const data = await res.json();
        isLocked = !!data.selection_locked;

        document.getElementById('ws-status').innerText = data.status;
        document.getElementById('coin').innerText = data.target_symbol || '--';
        document.getElementById('rate').innerText = data.funding_rate;
        document.getElementById('offset').innerText = data.clock_offset_ms + ' ms';
        updateToggleBtn(data.trading_enabled);

        const autoList = data.auto_armed_symbols || [];
        const manualList = data.manual_selected_symbols || [];
        const blockedData = data.blocked_coins || {{ temporary: [], permanent: [] }};
        const tempList = blockedData.temporary || [];
        const permList = blockedData.permanent || [];

        const cBody = document.getElementById('candidates-body');
        if (data.top_5_candidates && data.top_5_candidates.length > 0) {{
            cBody.innerHTML = data.top_5_candidates.map((c, i) => {{
                const isTemp = tempList.includes(c.symbol);
                const isAuto = autoList.includes(c.symbol);
                const isManual = manualList.includes(c.symbol);
                const isArmed = isAuto || isManual;

                let rowClass = "candidate-row";
                if (isTemp) rowClass += " row-temp-blocked";
                else if (isAuto) rowClass += " candidate-auto-active";
                else if (isManual) rowClass += " candidate-manual-active";

                let tagHtml = '';
                if (isTemp) tagHtml = '<span class="badge-temp-block">TEMP BLOCKED</span>';
                else if (isAuto) tagHtml = '<span class="active-auto-tag">⚡ AUTO ARMED</span>';
                else if (isManual) tagHtml = '<span class="active-manual-tag">🎯 MANUAL ARMED</span>';

                return `
                    <tr class="${{rowClass}}" onclick="${{isLocked ? '' : `toggleSelectCandidate('${{c.symbol}}', ${{isTemp}})`}}">
                        <td style="text-align: center;">
                            <input type="checkbox" ${{isArmed ? 'checked' : ''}} ${{isLocked || isTemp ? 'disabled' : ''}}>
                        </td>
                        <td><strong>#${{i + 1}}</strong></td>
                        <td><strong>${{c.symbol}}</strong>${{tagHtml}}</td>
                        <td style="color: #f0b90b; font-family: monospace;">${{c.vol_str || '--'}}</td>
                        <td style="color: #0ecb81; font-weight: 700;">${{(c.rate * 100).toFixed(4)}}%</td>
                        <td>${{c.settle_str}}</td>
                        <td style="text-align: center;">
                            <div class="dot-group">
                                <button class="btn-dot btn-dot-yellow" onclick="event.stopPropagation(); blockCoin('${{c.symbol}}', 'temporary');"></button>
                                <button class="btn-dot btn-dot-red" onclick="event.stopPropagation(); blockCoin('${{c.symbol}}', 'permanent');"></button>
                            </div>
                        </td>
                    </tr>
                `;
            }}).join('');
        }}

        document.getElementById('temp-count').innerText = tempList.length;
        document.getElementById('perm-count').innerText = permList.length;

        document.getElementById('temp-blocked-container').innerHTML = tempList.map(s => `
            <span class="chip-blocked chip-temp" onclick="unblockCoin('${{s}}', 'temporary')">${{s}} ✖</span>
        `).join('') || '<span style="color: #848e9c; font-size: 12px;">No temporary blocked coins</span>';

        document.getElementById('perm-blocked-container').innerHTML = permList.map(s => `
            <span class="chip-blocked chip-perm" onclick="unblockCoin('${{s}}', 'permanent')">${{s}} ✖</span>
        `).join('') || '<span style="color: #848e9c; font-size: 12px;">No permanently blocked coins</span>';

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
                let fundingTimeStr = (t.funding_fee_time && t.funding_fee_time !== "--:--:--") ? `@ ${{t.funding_fee_time}}` : "--:--:--";

                const hitsList = t.dca_hits || [];
                let dcaHtml = '<span style="color:#848e9c;">None (No Limit Orders Filled)</span>';

                if (hitsList.length > 0) {{
                    const shallowHits = hitsList.filter(d => (d.group === 'shallow') || (d.level && d.level.toLowerCase().includes('shallow')));
                    const dcaDeepHits = hitsList.filter(d => (d.group === 'dca') || (d.level && d.level.toLowerCase().includes('dca')));

                    let rowsHtml = '';
                    if (shallowHits.length > 0) {{
                        rowsHtml += `<div class="limit-row">${{shallowHits.map(d => renderLimitBoxHtml(d, 'limit-box-blue')).join('')}}</div>`;
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
                            <div class="mono sub-text">&#8594; ACK: ${{t.entry_ack || '--'}} (${{t.entry_rtt || '--'}})</div>
                            <div class="mono sub-text" style="color:#0ecb81; font-weight:600;">&#8594; First Fill: ${{t.entry_fill_time || '--'}}</div>
                        </td>
                        <td>
                            <div class="mono" style="color:#0ecb81; font-weight:bold;">${{t.entry_fill_price || '--'}}</div>
                            <div class="sub-text">Weighted Avg Entry</div>
                        </td>
                        <td>
                            <div class="mono">Fired: ${{t.exit_sent || '--'}}</div>
                            <div class="mono sub-text" style="color:#eaecef;">&#8594; Closed: ${{t.exit_fill_time || '--'}}</div>
                        </td>
                        <td>
                            <div class="mono"><strong style="color:#f0b90b; font-size:14px;">${{t.exit_price || '--'}}</strong></div>
                            <div class="sub-text">${{t.exit_type || '--'}}</div>
                        </td>
                        <td>
                            <div class="mono ${{fundingClass}}">${{fundingValStr}}</div>
                            <div class="sub-text" style="color:#848e9c; font-size:10px;">${{fundingTimeStr}}</div>
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
                                    <span class="sub-label">Limit Order Fills (${{hitsList.length}} Levels Hit):</span>
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
