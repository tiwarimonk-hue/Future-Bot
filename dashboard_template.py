from __future__ import annotations
import config


def get_dashboard_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
<title>Binance Tokyo Funding Sniper (Dynamic Margin Ledger)</title>
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
.btn-mode-inactive:hover {{ color: #eaecef; background: rgba(255, 255, 255, 0.05); }}
.btn-mode:disabled {{ opacity: 0.4; cursor: not-allowed; }}

.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
.card {{ background: #1e2329; padding: 12px 14px; border-radius: 10px; border: 1px solid #2b313a; }}
.card-editable {{ cursor: pointer; border: 1px solid rgba(240, 185, 11, 0.45); transition: 0.2s; position: relative; }}
.card-editable:hover {{ background: #262c34; border-color: #f0b90b; box-shadow: 0 0 10px rgba(240, 185, 11, 0.18); }}
.card-title {{ font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center; }}
.card-value {{ font-size: 16px; font-weight: 700; color: #f0b90b; }}
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

.candidate-active {{ background: rgba(14, 203, 129, 0.20) !important; border-left: 5px solid #0ecb81 !important; box-shadow: inset 0 0 10px rgba(14, 203, 129, 0.15); }}
.candidate-active td {{ color: #ffffff !important; }}
.active-tag {{ background: #0ecb81; color: #0b0e11; font-size: 10px; font-weight: 800; padding: 2px 7px; border-radius: 4px; margin-left: 6px; text-transform: uppercase; display: inline-block; }}
.manual-tag {{ background: #f0b90b; color: #0b0e11; font-size: 10px; font-weight: 800; padding: 2px 7px; border-radius: 4px; margin-left: 6px; text-transform: uppercase; display: inline-block; }}

.candidate-row.row-temp-blocked {{ background: rgba(240, 185, 11, 0.15) !important; border-left: 5px solid #f0b90b !important; }}
.candidate-row.row-temp-blocked td {{ color: #f0b90b !important; }}
.badge-temp-block {{ background: #f0b90b; color: #0b0e11; font-size: 10px; font-weight: 800; padding: 2px 7px; border-radius: 4px; margin-left: 6px; text-transform: uppercase; display: inline-block; }}

.dot-group {{ display: flex; gap: 10px; justify-content: center; align-items: center; }}
.btn-dot {{ width: 18px; height: 18px; border-radius: 50%; border: 2px solid rgba(255, 255, 255, 0.2); cursor: pointer; transition: transform 0.2s, box-shadow 0.2s; outline: none; }}
.btn-dot:hover {{ transform: scale(1.3); }}
.btn-dot-yellow {{ background: #f0b90b; box-shadow: 0 0 8px rgba(240, 185, 11, 0.6); }}
.btn-dot-red {{ background: #ef4444; box-shadow: 0 0 8px rgba(239, 68, 68, 0.6); }}

.chip-blocked {{ display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 8px; font-size: 12px; font-family: 'SFMono-Regular', Consolas, monospace; font-weight: 700; cursor: pointer; transition: all 0.2s; user-select: none; }}
.chip-blocked:hover {{ transform: translateY(-2px); }}
.chip-temp {{ background: rgba(240, 185, 11, 0.15); border: 1px solid rgba(240, 185, 11, 0.4); color: #f0b90b; }}
.chip-temp:hover {{ background: rgba(240, 185, 11, 0.3); box-shadow: 0 0 10px rgba(240, 185, 11, 0.35); }}
.chip-perm {{ background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.4); color: #f87171; }}
.chip-perm:hover {{ background: rgba(239, 68, 68, 0.3); box-shadow: 0 0 10px rgba(239, 68, 68, 0.35); }}
.chip-close {{ font-size: 13px; opacity: 0.75; margin-left: 2px; }}
.chip-close:hover {{ opacity: 1; color: #ffffff; }}
.btn-see-more {{ background: #14181d; border: 1px solid #2b313a; color: #f0b90b; padding: 6px 16px; border-radius: 6px; font-size: 11px; font-weight: 700; cursor: pointer; transition: 0.2s; }}
.btn-see-more:hover {{ background: rgba(240, 185, 11, 0.12); border-color: #f0b90b; }}

.modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.78); z-index: 9999; justify-content: center; align-items: center; padding: 16px; }}
.modal-box {{ background: #1e2329; border: 1px solid #f0b90b; border-radius: 14px; padding: 20px; width: 100%; max-width: 380px; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.65); display: flex; flex-direction: column; gap: 14px; }}
.modal-title {{ font-size: 16px; font-weight: 800; color: #f0b90b; }}
.modal-sub {{ font-size: 12px; color: #848e9c; line-height: 1.4; }}
.modal-input {{ width: 100%; padding: 12px 14px; border-radius: 8px; border: 1px solid #2b313a; background: #0b0e11; color: #ffffff; font-size: 18px; font-weight: 800; font-family: 'SFMono-Regular', Consolas, monospace; outline: none; }}
.modal-input:focus {{ border-color: #f0b90b; }}
.preset-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.btn-preset {{ background: #14181d; border: 1px solid #2b313a; color: #eaecef; padding: 5px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; cursor: pointer; }}
.btn-preset:hover {{ border-color: #f0b90b; color: #f0b90b; }}
.modal-actions {{ display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px; }}
.btn-modal-cancel {{ background: #2b313a; color: #eaecef; border: none; padding: 9px 16px; border-radius: 8px; font-size: 13px; font-weight: 700; cursor: pointer; }}
.btn-modal-ok {{ background: #0ecb81; color: #0b0e11; border: none; padding: 9px 22px; border-radius: 8px; font-size: 13px; font-weight: 800; cursor: pointer; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
    <div>
        <h1>Binance Tokyo Funding Sniper</h1>
        <div class="sub-text" style="color: #848e9c;">Radar Top 10 • Multi-Coin Manual (Up to 3) • Automatic Mode • T-{config.ENTRY_BEFORE_SETTLE_MS}ms</div>
    </div>
    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
        <button id="btn-trade-toggle" class="btn-toggle btn-on" onclick="toggleTrading()">TRADING: ON</button>
        
        <div class="mode-container">
            <button id="btn-mode-auto" class="btn-mode btn-mode-auto-active" onclick="setModeAuto()">⚡ AUTOMATIC</button>
            <button id="btn-mode-manual" class="btn-mode btn-mode-inactive" onclick="setModeManual()">🎯 MANUAL</button>
        </div>

        <div class="badge-mode" id="lock-badge">SELECTION: OPEN (>1m)</div>
        <div class="badge" id="ws-status">SCANNING</div>
    </div>
</div>

<div class="grid">
    <div class="card"><div class="card-title">Target Coin(s)</div><div class="card-value" id="coin">--</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color: #0ecb81;">--%</div></div>
    <div class="card"><div class="card-title">Next Settlement</div><div class="card-value" id="settle" style="color: #eaecef;">--:--:-- IST</div></div>
    <div class="card card-editable" id="margin-card" onclick="openMarginModal()" title="Click to change Base Margin per order">
        <div class="card-title">
            <span>Base Margin</span>
            <span style="color: #f0b90b; font-size: 10px; font-weight: 800;">✏️ EDIT</span>
        </div>
        <div class="card-value" id="margin-disp" style="color: #f0b90b;">${config.TRADE_MARGIN_USD:.2f} USDT</div>
    </div>
    <div class="card"><div class="card-title">Clock Drift</div><div class="card-value" id="offset" style="color: #0ecb81;">0.0 ms</div></div>
</div>

<!-- BASE MARGIN EDIT MODAL -->
<div class="modal-overlay" id="margin-modal" onclick="if(event.target === this) closeMarginModal();">
    <div class="modal-box">
        <div class="modal-title">💰 Set Base Margin (USDT)</div>
        <div class="modal-sub">Har ek Shallow Limit Order (20%, 40%, 60%, 80% FR) par itna margin lagega. Minimum: <strong>$0.30 USDT</strong></div>
        <input type="number" id="margin-input" class="modal-input" step="0.05" min="0.30" placeholder="1.50" onkeydown="if(event.key==='Enter') submitMarginModal(); else if(event.key==='Escape') closeMarginModal();">
        <div class="preset-row">
            <button type="button" class="btn-preset" onclick="setMarginPreset(0.30)">$0.30</button>
            <button type="button" class="btn-preset" onclick="setMarginPreset(0.50)">$0.50</button>
            <button type="button" class="btn-preset" onclick="setMarginPreset(0.75)">$0.75</button>
            <button type="button" class="btn-preset" onclick="setMarginPreset(1.00)">$1.00</button>
            <button type="button" class="btn-preset" onclick="setMarginPreset(1.50)">$1.50</button>
            <button type="button" class="btn-preset" onclick="setMarginPreset(2.50)">$2.50</button>
            <button type="button" class="btn-preset" onclick="setMarginPreset(5.00)">$5.00</button>
        </div>
        <div class="modal-actions">
            <button type="button" class="btn-modal-cancel" onclick="closeMarginModal()">Cancel</button>
            <button type="button" class="btn-modal-ok" onclick="submitMarginModal()">OK</button>
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
let currentBaseMargin = {config.TRADE_MARGIN_USD};
let isLocked = false;
let showAllTemp = false;
let showAllPerm = false;
let currentManualList = [];
let currentManualMode = false;

function openMarginModal() {{
    if (isLocked) {{
        alert("Base Margin is LOCKED because settlement is less than 1 minute away!");
        return;
    }}
    const modal = document.getElementById('margin-modal');
    const inp = document.getElementById('margin-input');
    inp.value = currentBaseMargin.toFixed(2);
    modal.style.display = 'flex';
    setTimeout(() => {{ inp.focus(); inp.select(); }}, 50);
}}

function closeMarginModal() {{
    document.getElementById('margin-modal').style.display = 'none';
}}

function setMarginPreset(val) {{
    document.getElementById('margin-input').value = Number(val).toFixed(2);
}}

async function submitMarginModal() {{
    if (isLocked) {{
        alert("Base Margin is LOCKED because settlement is less than 1 minute away!");
        closeMarginModal();
        return;
    }}
    const rawVal = parseFloat(document.getElementById('margin-input').value);
    if (isNaN(rawVal) || rawVal < 0.30) {{
        alert("⚠️ Minimum Base Margin is $0.30 USDT (required for $5.00 Binance min notional at 20x leverage).");
        return;
    }}
    try {{
        const res = await fetch('/api/set-margin', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ margin: rawVal }})
        }});
        const data = await res.json();
        if (!data.success) {{
            alert(data.message || "Failed to update Base Margin.");
        }} else {{
            currentBaseMargin = Number(data.base_margin_usd);
            closeMarginModal();
            updateUI();
        }}
    }} catch(e) {{
        alert("Network error while updating Base Margin.");
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
                    <div>🚀 Cancel Fired (Local VPS): <strong style="color: #ffffff;">${{cancelFired}}</strong></div>
                    <div>🎯 Binance Canceled (Engine): <strong style="color: #60a5fa;">${{binanceCanceled}}</strong></div>
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
    if (enabled) {{
        btn.className = "btn-toggle btn-on";
        btn.innerText = "TRADING: ON";
    }} else {{
        btn.className = "btn-toggle btn-off";
        btn.innerText = "TRADING: OFF";
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
        if (data.base_margin_usd !== undefined && data.base_margin_usd !== null) {{
            currentBaseMargin = Number(data.base_margin_usd);
        }}

        document.getElementById('ws-status').innerText = data.status;
        document.getElementById('rate').innerText = data.funding_rate;
        document.getElementById('offset').innerText = data.clock_offset_ms + ' ms';
        updateToggleBtn(data.trading_enabled);

        const btnAuto = document.getElementById('btn-mode-auto');
        const btnManual = document.getElementById('btn-mode-manual');

        if (btnAuto && btnManual) {{
            btnAuto.disabled = isLocked;
            btnManual.disabled = isLocked;

            if (currentManualMode) {{
                btnAuto.className = "btn-mode btn-mode-inactive";
                btnManual.className = "btn-mode btn-mode-manual-active";
                btnManual.innerText = `🎯 MANUAL (${{currentManualList.length}}/3)`;
            }} else {{
                btnAuto.className = "btn-mode btn-mode-auto-active";
                btnManual.className = "btn-mode btn-mode-inactive";
                btnManual.innerText = "🎯 MANUAL";
            }}
        }}

        if (currentManualMode) {{
            if (currentManualList.length === 0) {{
                document.getElementById('coin').innerText = "0 Coins (PASS - IDLE)";
                document.getElementById('coin').style.color = "#f87171";
                document.getElementById('margin-disp').innerText = `$0.00 USDT (Base: $${{currentBaseMargin.toFixed(2)}})`;
            }} else {{
                document.getElementById('coin').innerText = currentManualList.join(", ");
                document.getElementById('coin').style.color = "#f0b90b";
                const totalMargin = (currentManualList.length * currentBaseMargin).toFixed(2);
                document.getElementById('margin-disp').innerText = `$${{totalMargin}} USDT (${{currentManualList.length}}x $${{currentBaseMargin.toFixed(2)}})`;
            }}
        }} else {{
            document.getElementById('coin').innerText = data.target_symbol;
            document.getElementById('coin').style.color = "#f0b90b";
            document.getElementById('margin-disp').innerText = `$${{currentBaseMargin.toFixed(2)}} USDT`;
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
                "Automatic Mode • Click any row to Switch to Manual";
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
                        activeTagHtml = `<span class="manual-tag">SELECTED #${{mIdx + 1}}</span>`;
                    }}
                }} else {{
                    const hasValidTarget = (data.target_symbol && !data.target_symbol.includes("Scanning") && !data.target_symbol.includes("--"));
                    if (hasValidTarget && (c.symbol === data.target_symbol)) {{
                        isSelected = true;
                        activeTagHtml = '<span class="active-tag">ARMED</span>';
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
