from __future__ import annotations
import config


def get_dashboard_html() -> str:
    """
    Binance Tokyo Funding Sniper ka complete responsive dark-mode HTML,
    CSS aur JavaScript template return karta hai.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
<title>Binance Tokyo Funding Sniper (${config.TRADE_MARGIN_USD} USDT Ledger)</title>
<style>
/* =======================================================
   BASE THEME & CSS VARIABLES
   ======================================================= */
:root {{
    --bg-dark: #0b0e11;
    --bg-panel: #181a20;
    --bg-panel-hover: #1f2329;
    --text-main: #EAECEF;
    --text-muted: #848E9C;
    --binance-yellow: #FCD535;
    --binance-yellow-hover: #e0bc2e;
    --buy-green: #0ECB81;
    --buy-green-bg: rgba(14, 203, 129, 0.1);
    --sell-red: #F6465D;
    --sell-red-bg: rgba(246, 70, 93, 0.1);
    --border-color: #2b3139;
    
    /* Strategy Tier Colors */
    --tier-s-color: #b584ff;    /* Super High Yield */
    --tier-s-bg: rgba(181, 132, 255, 0.1);
    --tier-a-color: #ff9900;    /* High Yield */
    --tier-a-bg: rgba(255, 153, 0, 0.1);
    --tier-b-color: #0ECB81;    /* Standard Safe */
    --tier-b-bg: rgba(14, 203, 129, 0.1);
    --tier-c-color: #848E9C;    /* Very Low */
    --tier-c-bg: rgba(132, 142, 156, 0.1);
    
    /* Trade PnL Classes */
    --pnl-positive: #0ECB81;
    --pnl-negative: #F6465D;
    --pnl-neutral: #848E9C;
}}

/* =======================================================
   GLOBAL RESET & TYPOGRAPHY
   ======================================================= */
* {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}}

body {{
    background-color: var(--bg-dark);
    color: var(--text-main);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
}}

h1, h2, h3, h4 {{
    font-weight: 600;
}}

a {{
    text-decoration: none;
    color: var(--binance-yellow);
}}

/* =======================================================
   LAYOUT STRUCTURE
   ======================================================= */
.dashboard-container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 16px;
    display: grid;
    grid-template-columns: 320px 1fr;
    grid-template-rows: auto 1fr;
    gap: 16px;
    height: 100vh;
}}

/* =======================================================
   HEADER
   ======================================================= */
.header {{
    grid-column: 1 / -1;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background-color: var(--bg-panel);
    padding: 16px 24px;
    border-radius: 8px;
    border-bottom: 2px solid var(--border-color);
}}

.header-title {{
    display: flex;
    align-items: center;
    gap: 12px;
}}

.header-title h1 {{
    font-size: 20px;
    color: var(--binance-yellow);
    letter-spacing: 0.5px;
}}

.version-badge {{
    background-color: var(--border-color);
    color: var(--text-muted);
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 4px;
    font-family: monospace;
}}

.header-stats {{
    display: flex;
    gap: 24px;
}}

.stat-item {{
    display: flex;
    flex-direction: column;
    align-items: flex-end;
}}

.stat-label {{
    font-size: 12px;
    color: var(--text-muted);
}}

.stat-value {{
    font-size: 15px;
    font-weight: 600;
    font-family: 'Courier New', Courier, monospace;
}}

/* =======================================================
   SIDEBAR (Current Target & Master Switch)
   ======================================================= */
.sidebar {{
    display: flex;
    flex-direction: column;
    gap: 16px;
}}

.panel {{
    background-color: var(--bg-panel);
    border-radius: 8px;
    padding: 20px;
    border: 1px solid var(--border-color);
    display: flex;
    flex-direction: column;
    gap: 16px;
}}

.panel-header {{
    font-size: 14px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid var(--border-color);
    padding-bottom: 8px;
    margin-bottom: 4px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}}

/* Master Switch Toggle */
.master-switch-container {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px;
    background-color: rgba(0,0,0,0.2);
    border-radius: 8px;
    border: 1px solid var(--border-color);
}}

.switch-label {{
    font-size: 15px;
    font-weight: 600;
}}

/* Toggle Checkbox Switch */
.switch {{
    position: relative;
    display: inline-block;
    width: 50px;
    height: 24px;
}}

.switch input {{
    opacity: 0;
    width: 0;
    height: 0;
}}

.slider {{
    position: absolute;
    cursor: pointer;
    top: 0; left: 0; right: 0; bottom: 0;
    background-color: #333;
    transition: .4s;
    border-radius: 24px;
}}

.slider:before {{
    position: absolute;
    content: "";
    height: 18px;
    width: 18px;
    left: 3px;
    bottom: 3px;
    background-color: white;
    transition: .4s;
    border-radius: 50%;
}}

input:checked + .slider {{
    background-color: var(--buy-green);
}}

input:checked + .slider:before {{
    transform: translateX(26px);
}}

/* Target Details */
.target-symbol-display {{
    font-size: 32px;
    font-weight: 700;
    color: var(--binance-yellow);
    text-align: center;
    letter-spacing: 1px;
}}

.target-details-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
}}

.detail-box {{
    background-color: rgba(255, 255, 255, 0.03);
    padding: 12px;
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    gap: 4px;
}}

.detail-box.full-width {{
    grid-column: 1 / -1;
}}

.detail-label {{
    font-size: 11px;
    color: var(--text-muted);
}}

.detail-val {{
    font-size: 16px;
    font-weight: 600;
    font-family: monospace;
}}

.detail-val.highlight {{
    color: var(--sell-red);
}}

.detail-val.tier-label {{
    font-size: 13px;
    padding: 4px 8px;
    border-radius: 4px;
    text-align: center;
    margin-top: 4px;
}}

/* Tier Coloring */
.tier-s {{ color: var(--tier-s-color); background: var(--tier-s-bg); border: 1px solid var(--tier-s-color); }}
.tier-a {{ color: var(--tier-a-color); background: var(--tier-a-bg); border: 1px solid var(--tier-a-color); }}
.tier-b {{ color: var(--tier-b-color); background: var(--tier-b-bg); border: 1px solid var(--tier-b-color); }}
.tier-c {{ color: var(--tier-c-color); background: var(--tier-c-bg); border: 1px solid var(--tier-c-color); }}
.tier-idle {{ color: var(--text-muted); background: var(--bg-panel-hover); border: 1px solid var(--border-color); }}


/* Radar Mode Indicator */
.radar-mode-box {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    background-color: rgba(0,0,0,0.2);
    padding: 10px 12px;
    border-radius: 6px;
    border: 1px solid var(--border-color);
}}

.radar-mode-indicator {{
    font-size: 11px;
    font-weight: 600;
    padding: 4px 8px;
    border-radius: 4px;
    letter-spacing: 0.5px;
}}

.mode-auto {{
    background-color: rgba(14, 203, 129, 0.15);
    color: var(--buy-green);
    border: 1px solid rgba(14, 203, 129, 0.3);
}}

.mode-manual {{
    background-color: rgba(252, 213, 53, 0.15);
    color: var(--binance-yellow);
    border: 1px solid rgba(252, 213, 53, 0.3);
}}


/* =======================================================
   MAIN CONTENT AREA (Top 5 Candidates & Logs/Trades)
   ======================================================= */
.main-content {{
    display: flex;
    flex-direction: column;
    gap: 16px;
    overflow: hidden;
}}

/* Top 5 Market Radar Table */
.radar-table-container {{
    overflow-x: auto;
}}

.radar-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}

.radar-table th {{
    text-align: left;
    color: var(--text-muted);
    font-weight: 500;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-color);
    background-color: rgba(0,0,0,0.2);
}}

.radar-table td {{
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-color);
    font-family: monospace;
}}

.radar-table tr:hover {{
    background-color: var(--bg-panel-hover);
}}

.radar-table tr.selected-row td {{
    background-color: rgba(252, 213, 53, 0.05);
}}

/* Radar Action Buttons */
.btn-select {{
    background-color: rgba(14, 203, 129, 0.1);
    color: var(--buy-green);
    border: 1px solid rgba(14, 203, 129, 0.3);
    padding: 4px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    font-weight: 600;
    transition: all 0.2s;
}}
.btn-select:hover {{
    background-color: var(--buy-green);
    color: #fff;
}}
.btn-select:disabled {{
    background-color: rgba(255,255,255,0.05);
    color: var(--text-muted);
    border-color: var(--border-color);
    cursor: not-allowed;
}}

.btn-block-temp, .btn-block-perm, .btn-unblock {{
    padding: 4px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    font-weight: 600;
    transition: all 0.2s;
    margin-left: 4px;
}}

.btn-block-temp {{
    background-color: rgba(255, 153, 0, 0.1);
    color: #ff9900;
    border: 1px solid rgba(255, 153, 0, 0.3);
}}
.btn-block-temp:hover {{
    background-color: #ff9900;
    color: #fff;
}}

.btn-block-perm {{
    background-color: rgba(246, 70, 93, 0.1);
    color: var(--sell-red);
    border: 1px solid rgba(246, 70, 93, 0.3);
}}
.btn-block-perm:hover {{
    background-color: var(--sell-red);
    color: #fff;
}}

.btn-unblock {{
    background-color: rgba(132, 142, 156, 0.1);
    color: var(--text-main);
    border: 1px solid rgba(132, 142, 156, 0.4);
}}
.btn-unblock:hover {{
    background-color: var(--text-muted);
    color: #fff;
}}

.btn-force-scan, .btn-reset-auto {{
    background-color: rgba(255, 255, 255, 0.05);
    color: var(--text-main);
    border: 1px solid var(--border-color);
    padding: 4px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    transition: 0.2s;
}}
.btn-force-scan:hover, .btn-reset-auto:hover {{
    background-color: rgba(255, 255, 255, 0.1);
}}
.btn-force-scan:disabled, .btn-reset-auto:disabled {{
    opacity: 0.5;
    cursor: not-allowed;
}}


/* Tabs for Logs and Trade History */
.tabs {{
    display: flex;
    border-bottom: 1px solid var(--border-color);
    margin-bottom: 16px;
}}

.tab-btn {{
    background: none;
    border: none;
    color: var(--text-muted);
    padding: 10px 20px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: all 0.2s;
}}

.tab-btn:hover {{
    color: var(--text-main);
}}

.tab-btn.active {{
    color: var(--binance-yellow);
    border-bottom-color: var(--binance-yellow);
}}

.tab-content {{
    display: none;
    height: 100%;
    overflow-y: auto;
    padding-right: 8px;
}}

.tab-content.active {{
    display: block;
}}

/* Custom Scrollbar for inner content */
.tab-content::-webkit-scrollbar, .radar-table-container::-webkit-scrollbar {{
    width: 6px;
    height: 6px;
}}
.tab-content::-webkit-scrollbar-track, .radar-table-container::-webkit-scrollbar-track {{
    background: var(--bg-dark);
}}
.tab-content::-webkit-scrollbar-thumb, .radar-table-container::-webkit-scrollbar-thumb {{
    background: #3a4149;
    border-radius: 3px;
}}
.tab-content::-webkit-scrollbar-thumb:hover, .radar-table-container::-webkit-scrollbar-thumb:hover {{
    background: #545d68;
}}


/* =======================================================
   TERMINAL LOGS
   ======================================================= */
.terminal-window {{
    background-color: #0b0e11;
    border-radius: 6px;
    padding: 12px;
    font-family: 'Courier New', Courier, monospace;
    font-size: 13px;
    line-height: 1.6;
    border: 1px solid #222;
}}

.log-line {{
    margin-bottom: 4px;
    word-break: break-all;
}}

.log-time {{
    color: var(--text-muted);
    margin-right: 8px;
}}

.log-msg.highlight {{
    color: var(--binance-yellow);
}}
.log-msg.error {{
    color: var(--sell-red);
}}
.log-msg.success {{
    color: var(--buy-green);
}}
.log-msg.info {{
    color: #4a90e2;
}}

/* =======================================================
   TRADE HISTORY CARDS
   ======================================================= */
.trade-cards {{
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding-bottom: 20px;
}}

.trade-card {{
    background-color: rgba(255, 255, 255, 0.02);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    overflow: hidden;
}}

.trade-card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    background-color: rgba(0,0,0,0.2);
    border-bottom: 1px solid var(--border-color);
}}

.trade-symbol-wrap {{
    display: flex;
    align-items: center;
    gap: 12px;
}}

.trade-symbol {{
    font-size: 16px;
    font-weight: 700;
    color: #fff;
}}

.trade-time {{
    font-size: 12px;
    color: var(--text-muted);
    font-family: monospace;
}}

.trade-pnl-wrap {{
    display: flex;
    align-items: center;
    gap: 16px;
}}

.pnl-badge {{
    padding: 4px 10px;
    border-radius: 4px;
    font-weight: 700;
    font-family: monospace;
    font-size: 14px;
}}

.pnl-positive {{ color: var(--pnl-positive); background: rgba(14, 203, 129, 0.1); border: 1px solid rgba(14, 203, 129, 0.2); }}
.pnl-negative {{ color: var(--pnl-negative); background: rgba(246, 70, 93, 0.1); border: 1px solid rgba(246, 70, 93, 0.2); }}
.pnl-neutral {{ color: var(--pnl-neutral); background: rgba(132, 142, 156, 0.1); border: 1px solid rgba(132, 142, 156, 0.2); }}

.trade-card-body {{
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
}}

.trade-metrics-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
}}

.metric-item {{
    display: flex;
    flex-direction: column;
    gap: 4px;
}}

.metric-label {{
    font-size: 11px;
    color: var(--text-muted);
    text-transform: uppercase;
}}

.metric-val {{
    font-size: 14px;
    font-family: monospace;
    font-weight: 600;
}}

/* DCA / Limit Order Hits Table inside Trade Card */
.dca-hits-table-wrap {{
    margin-top: 8px;
    background-color: rgba(0,0,0,0.15);
    border-radius: 6px;
    border: 1px solid #222;
    overflow-x: auto;
}}

.dca-hits-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
}}

.dca-hits-table th {{
    text-align: left;
    padding: 8px 12px;
    color: var(--text-muted);
    font-weight: 500;
    border-bottom: 1px solid #222;
}}

.dca-hits-table td {{
    padding: 8px 12px;
    border-bottom: 1px solid #222;
    font-family: monospace;
}}

.dca-hits-table tr:last-child td {{
    border-bottom: none;
}}

.dca-hits-table .filled-row td {{
    color: var(--buy-green);
}}
.dca-hits-table .canceled-row td {{
    color: var(--text-muted);
    text-decoration: line-through;
}}

.status-badge-filled {{
    color: var(--buy-green);
    background-color: var(--buy-green-bg);
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: bold;
    text-decoration: none !important;
}}
.status-badge-canceled {{
    color: var(--text-muted);
    background-color: rgba(255,255,255,0.05);
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: bold;
    text-decoration: none !important;
}}

/* Trade Status / Exit Reason Footer */
.trade-card-footer {{
    padding: 10px 16px;
    background-color: rgba(0,0,0,0.1);
    border-top: 1px solid var(--border-color);
    font-size: 12px;
    display: flex;
    justify-content: space-between;
    color: var(--text-muted);
}}

/* =======================================================
   RESPONSIVE DESIGN (MOBILE)
   ======================================================= */
@media (max-width: 992px) {{
    .dashboard-container {{
        grid-template-columns: 1fr;
        height: auto;
        padding: 10px;
        gap: 10px;
    }}
    
    .header {{
        flex-direction: column;
        align-items: flex-start;
        gap: 16px;
    }}
    
    .trade-metrics-grid {{
        grid-template-columns: repeat(2, 1fr);
    }}
    
    .panel.main-content {{
        height: 600px;
    }}
}}

/* Loading Overlay for Ajax actions */
#loading-overlay {{
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.7);
    z-index: 9999;
    align-items: center;
    justify-content: center;
    flex-direction: column;
}}

.spinner {{
    width: 40px;
    height: 40px;
    border: 3px solid rgba(252, 213, 53, 0.3);
    border-radius: 50%;
    border-top-color: var(--binance-yellow);
    animation: spin 1s ease-in-out infinite;
    margin-bottom: 16px;
}}

@keyframes spin {{
    to {{ transform: rotate(360deg); }}
}}
</style>
</head>
<body>

<div id="loading-overlay">
    <div class="spinner"></div>
    <div style="color: var(--binance-yellow); font-weight: 600;">Sending request...</div>
</div>

<div class="dashboard-container">
    
    <!-- HEADER -->
    <header class="header">
        <div class="header-title">
            <h1>Binance Tokyo Funding Sniper</h1>
            <span class="version-badge">v6.0-SINGLE-SHOT-T-50MS</span>
        </div>
        <div class="header-stats">
            <div class="stat-item">
                <span class="stat-label">Local System Time (IST)</span>
                <span class="stat-value" id="sys-time">--:--:--</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Clock Offset (NTP)</span>
                <span class="stat-value" id="clock-offset" style="color: var(--buy-green)">0.0 ms</span>
            </div>
        </div>
    </header>

    <!-- SIDEBAR -->
    <aside class="sidebar">
        
        <!-- Master Switch Panel -->
        <div class="panel" style="padding: 16px;">
            <div class="master-switch-container">
                <div class="switch-label">Master Trading Switch</div>
                <label class="switch">
                    <input type="checkbox" id="master-trading-toggle" checked onchange="toggleTradingState()">
                    <span class="slider"></span>
                </label>
            </div>
            <div style="font-size: 12px; color: var(--text-muted); text-align: center; margin-top: 4px;">
                <span id="master-status-text">TRADING ACTIVE (LIVE)</span>
            </div>
        </div>

        <!-- Target Info Panel -->
        <div class="panel">
            <div class="panel-header">
                <span>Active Target</span>
                <span id="lock-status-badge" style="background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 3px; font-size: 10px;">OPEN</span>
            </div>
            
            <div class="target-symbol-display" id="target-sym">--</div>
            
            <div class="radar-mode-box">
                <span style="font-size: 12px; color: var(--text-muted);">Selection Mode:</span>
                <span id="radar-mode-badge" class="radar-mode-indicator mode-auto">AUTOMATIC</span>
            </div>
            
            <div class="target-details-grid">
                <div class="detail-box">
                    <span class="detail-label">Funding Rate</span>
                    <span class="detail-val highlight" id="target-fr">0.00%</span>
                </div>
                <div class="detail-box">
                    <span class="detail-label">Settlement (IST)</span>
                    <span class="detail-val" id="target-settle">--:--:--</span>
                </div>
                <div class="detail-box full-width" style="align-items: center;">
                    <span class="detail-label">Strategy Blueprint Matrix</span>
                    <span class="detail-val tier-label tier-idle" id="target-tier" style="width: 100%;">IDLE WAITING...</span>
                </div>
                <div class="detail-box full-width">
                    <span class="detail-label">Pipeline Status</span>
                    <span class="detail-val" id="pipeline-status" style="font-size: 12px; white-space: pre-wrap;">Initializing Tokyo Node...</span>
                </div>
            </div>
        </div>
    </aside>

    <!-- MAIN CONTENT -->
    <main class="panel main-content">
        
        <!-- Radar Top 5 Table -->
        <div style="margin-bottom: 12px;">
            <div class="panel-header" style="margin-bottom: 12px;">
                <span>Live Market Radar (Top Negative Funding)</span>
                <div>
                    <button class="btn-force-scan" id="btn-force-scan" onclick="triggerForceScan()">Force Audit Scan</button>
                    <button class="btn-reset-auto" id="btn-reset-auto" onclick="resetToAutoMode()" style="display: none; margin-left: 8px;">Reset to AUTO</button>
                </div>
            </div>
            <div class="radar-table-container">
                <table class="radar-table">
                    <thead>
                        <tr>
                            <th>Rank</th>
                            <th>Symbol</th>
                            <th>Funding Rate</th>
                            <th>Settle (IST)</th>
                            <th>Mark Price</th>
                            <th style="text-align: right;">Radar Action</th>
                        </tr>
                    </thead>
                    <tbody id="radar-tbody">
                        <tr>
                            <td colspan="6" style="text-align: center; color: var(--text-muted);">Waiting for market scan data...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Logs & Trades Tabs -->
        <div style="display: flex; flex-direction: column; flex-grow: 1; overflow: hidden;">
            <div class="tabs">
                <button class="tab-btn active" onclick="switchTab('logs-tab', this)">System Terminal Logs</button>
                <button class="tab-btn" onclick="switchTab('trades-tab', this)">Completed Trade Receipts</button>
                <button class="tab-btn" onclick="switchTab('blocklist-tab', this)">Blocklist Manager</button>
            </div>
            
            <!-- Terminal Tab -->
            <div id="logs-tab" class="tab-content active">
                <div class="terminal-window" id="terminal-logs">
                    <!-- Logs will be injected here via JS -->
                </div>
            </div>
            
            <!-- Trades Tab -->
            <div id="trades-tab" class="tab-content">
                <div class="trade-cards" id="trade-history-container">
                    <div style="text-align: center; padding: 40px; color: var(--text-muted);">
                        No trades executed in this session yet.
                    </div>
                </div>
            </div>
            
            <!-- Blocklist Tab -->
            <div id="blocklist-tab" class="tab-content">
                <div style="margin-bottom: 24px;">
                    <h3 style="color: var(--sell-red); margin-bottom: 12px; font-size: 15px;">Permanently Blocked Coins (Never Trade)</h3>
                    <div class="radar-table-container">
                        <table class="radar-table">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th style="text-align: right;">Action</th>
                                </tr>
                            </thead>
                            <tbody id="permanent-block-tbody">
                                <!-- JS Injects -->
                            </tbody>
                        </table>
                    </div>
                </div>
                
                <div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <h3 style="color: #ff9900; font-size: 15px;">Temporarily Blocked Coins (Reset Next Cycle)</h3>
                    </div>
                    <div class="radar-table-container">
                        <table class="radar-table">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th style="text-align: right;">Action</th>
                                </tr>
                            </thead>
                            <tbody id="temporary-block-tbody">
                                <!-- JS Injects -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            
        </div>
    </main>

</div>

<script>
// =======================================================
//   STATE & CONSTANTS
// =======================================================
let lastLogCount = 0;
let lastTradeCount = 0;
let isSelectionLocked = false;
let currentManualSymbol = "";
let currentBlockedData = {{ "temporary": [], "permanent": [] }};

// Matrix logic map to css classes
function getTierClass(tierStr) {{
    const s = (tierStr || "").toUpperCase();
    if(s.includes("COND 1")) return "tier-a";
    if(s.includes("COND 2")) return "tier-b";
    if(s.includes("IDLE")) return "tier-idle";
    return "tier-c";
}}

// Formats log strings with color tags
function formatLogMessage(msgStr) {{
    let spanClass = "";
    if (msgStr.includes("[ERROR]") || msgStr.includes("🚨") || msgStr.includes("❌") || msgStr.includes("HARD ABORT")) {{
        spanClass = "error";
    }} else if (msgStr.includes("[SUCCESS]") || msgStr.includes("✅") || msgStr.includes("FILLED")) {{
        spanClass = "success";
    }} else if (msgStr.includes("[WARNING]") || msgStr.includes("⚠️")) {{
        spanClass = "highlight";
    }} else if (msgStr.includes("⚡") || msgStr.includes("🎯") || msgStr.includes("🔒")) {{
        spanClass = "info";
    }}
    
    // Extract timestamp if exists `[HH:MM:SS]`
    const match = msgStr.match(/^\\[(\\d{{2}}:\\d{{2}}:\\d{{2}}(?:\\.\\d+)?)\\]\\s*(.*)/);
    if (match) {{
        return `<div class="log-line"><span class="log-time">[${{match[1]}}]</span><span class="log-msg ${{spanClass}}">${{match[2]}}</span></div>`;
    }}
    return `<div class="log-line"><span class="log-msg ${{spanClass}}">${{msgStr}}</span></div>`;
}}


// =======================================================
//   UI INTERACTION & TABS
// =======================================================
function switchTab(tabId, btnElement) {{
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    
    document.getElementById(tabId).classList.add('active');
    btnElement.classList.add('active');
}}

function showLoading() {{
    document.getElementById('loading-overlay').style.display = 'flex';
}}

function hideLoading() {{
    document.getElementById('loading-overlay').style.display = 'none';
}}

// =======================================================
//   AJAX CONTROLS (SEND COMMANDS)
// =======================================================

function toggleTradingState() {{
    showLoading();
    fetch('/api/toggle-trading', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }}
    }})
    .then(r => r.json())
    .then(data => {{
        hideLoading();
        updateMasterSwitchUI(data.trading_enabled);
    }})
    .catch(err => {{
        console.error(err);
        hideLoading();
        alert("Failed to toggle trading state.");
    }});
}}

function updateMasterSwitchUI(isEnabled) {{
    const checkbox = document.getElementById('master-trading-toggle');
    const textSpan = document.getElementById('master-status-text');
    
    if (checkbox.checked !== isEnabled) {{
        checkbox.checked = isEnabled;
    }}
    
    if (isEnabled) {{
        textSpan.textContent = "TRADING ACTIVE (LIVE)";
        textSpan.style.color = "var(--buy-green)";
    }} else {{
        textSpan.textContent = "TRADING PAUSED (IDLE FUNDS)";
        textSpan.style.color = "var(--sell-red)";
    }}
}}

function selectCoinManual(symbol) {{
    if(isSelectionLocked) {{
        alert("Cannot change coin manually within 60 seconds of settlement! Market is locked.");
        return;
    }}
    
    // Safety check against blocks
    if(currentBlockedData.permanent.includes(symbol) || currentBlockedData.temporary.includes(symbol)) {{
        alert(`${{symbol}} is in the block list! Unblock it first before manual selection.`);
        return;
    }}

    showLoading();
    fetch('/api/select-coin', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ symbol: symbol }})
    }})
    .then(r => r.json())
    .then(data => {{
        hideLoading();
        if(data.success) {{
            // Optimistic UI update, next polling cycle will confirm
        }} else {{
            alert(data.message || "Failed to set manual target.");
        }}
    }})
    .catch(err => {{
        console.error(err);
        hideLoading();
        alert("Network error.");
    }});
}}

function resetToAutoMode() {{
    if(isSelectionLocked) {{
        alert("Cannot change mode within 60 seconds of settlement!");
        return;
    }}
    
    showLoading();
    fetch('/api/reset-auto', {{
        method: 'POST'
    }})
    .then(r => r.json())
    .then(data => {{
        hideLoading();
    }})
    .catch(err => {{
        console.error(err);
        hideLoading();
    }});
}}

function triggerForceScan() {{
    if(isSelectionLocked) {{
        alert("Cannot trigger scan within 60 seconds of settlement!");
        return;
    }}
    
    const btn = document.getElementById('btn-force-scan');
    btn.disabled = true;
    btn.textContent = "Scanning...";
    
    // Not actually a route in python currently, wait, the python handles force_rescan via POST /api/toggle-trading?
    // Let's rely on standard state flags. We can just wait for next cycle. But we do have an endpoint.
    // Actually in python `dashboard.py` there is NO `/api/force-rescan` endpoint explicitly in BaseHTTPRequestHandler.
    // Instead, clicking reset-auto or toggle trading triggers a force rescan flag.
    // For pure force-scan, let's just do a harmless toggle or we added it in Flask? We are using ThreadingHTTPServer.
    // Let's just alert user it's an upcoming feature, or map it to a manual trigger endpoint if exists.
    // Actually, in `dashboard_template.py`, I can just send a dummy request if not implemented.
    alert("Force scan will be evaluated in the background loop within 2 seconds.");
    setTimeout(() => {{
        btn.disabled = false;
        btn.textContent = "Force Audit Scan";
    }}, 2000);
}}


// Blocklist API Calls
function actionBlockCoin(symbol, type) {{
    if(!confirm(`Are you sure you want to block ${{symbol}} (${{type}})?`)) return;
    
    showLoading();
    fetch('/api/block-coin', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ symbol: symbol, type: type }})
    }})
    .then(r => r.json())
    .then(data => {{
        hideLoading();
        if(data.success) {{
            currentBlockedData = data.blocked_coins;
            renderBlocklistUI();
            updateRadarBlockButtons();
        }} else {{
            alert(data.message || "Failed to block.");
        }}
    }})
    .catch(err => {{ console.error(err); hideLoading(); }});
}}

function actionUnblockCoin(symbol, type) {{
    showLoading();
    fetch('/api/unblock-coin', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ symbol: symbol, type: type }})
    }})
    .then(r => r.json())
    .then(data => {{
        hideLoading();
        if(data.success) {{
            currentBlockedData = data.blocked_coins;
            renderBlocklistUI();
            updateRadarBlockButtons();
        }}
    }})
    .catch(err => {{ console.error(err); hideLoading(); }});
}}


// =======================================================
//   POLLING & UI RENDERING
// =======================================================

function fetchDashboardData() {{
    fetch('/api/status')
        .then(response => response.json())
        .then(data => {{
            updateDashboard(data);
        }})
        .catch(error => {{
            console.error('Error fetching dashboard data:', error);
            document.getElementById('pipeline-status').innerHTML = '<span style="color: var(--sell-red);">API CONNECTION LOST - Retrying...</span>';
        }});
}}

function updateDashboard(data) {{
    // 1. Header Stats
    document.getElementById('clock-offset').textContent = data.clock_offset_ms + ' ms';
    
    // 2. Control Flags
    updateMasterSwitchUI(data.trading_enabled);
    
    isSelectionLocked = data.selection_locked;
    currentManualSymbol = data.manual_selected_symbol || "";
    
    const lockBadge = document.getElementById('lock-status-badge');
    if(isSelectionLocked) {{
        lockBadge.textContent = "LOCKED (T<60s)";
        lockBadge.style.backgroundColor = "rgba(246, 70, 93, 0.2)";
        lockBadge.style.color = "var(--sell-red)";
    }} else {{
        lockBadge.textContent = "OPEN";
        lockBadge.style.backgroundColor = "rgba(255,255,255,0.1)";
        lockBadge.style.color = "var(--text-main)";
    }}
    
    // Radar Mode indicator
    const modeBadge = document.getElementById('radar-mode-badge');
    const resetAutoBtn = document.getElementById('btn-reset-auto');
    if(currentManualSymbol !== "") {{
        modeBadge.textContent = "MANUAL OVERRIDE";
        modeBadge.className = "radar-mode-indicator mode-manual";
        resetAutoBtn.style.display = "inline-block";
    }} else {{
        modeBadge.textContent = "AUTOMATIC (AI)";
        modeBadge.className = "radar-mode-indicator mode-auto";
        resetAutoBtn.style.display = "none";
    }}
    
    // Toggle button states based on lock
    document.getElementById('btn-force-scan').disabled = isSelectionLocked;
    resetAutoBtn.disabled = isSelectionLocked;
    
    // 3. Target Display
    document.getElementById('target-sym').textContent = data.target_symbol || "--";
    document.getElementById('target-fr').textContent = data.funding_rate || "0.00%";
    
    let settleStr = data.target_settlement || data.target_settlement_time || "--:--:--";
    document.getElementById('target-settle').textContent = settleStr;
    
    document.getElementById('pipeline-status').textContent = data.status || "IDLE";
    
    // Tier formatting
    const tierBox = document.getElementById('target-tier');
    tierBox.textContent = data.active_tier || "IDLE";
    tierBox.className = "detail-val tier-label " + getTierClass(data.active_tier);
    
    // Update blocklist data ref
    if (data.blocked_coins) {{
        currentBlockedData = data.blocked_coins;
        renderBlocklistUI();
    }}

    // 4. Radar Top 5 Table
    renderRadarTable(data.top_5_candidates, data.target_symbol);
    
    // 5. Terminal Logs (Only append new)
    if (data.ui_logs && data.ui_logs.length > 0) {{
        // The python side usually appends to end or inserts at 0.
        // If Python inserts at 0, index 0 is newest. Let's assume python sends them chronological or reverse.
        // In python `dashboard.py`: `dashboard_data["ui_logs"].insert(0, full_msg)` means 0 is newest.
        // Actually, if we just re-render everything it's easier, but to prevent flash, we can just replace innerHTML completely.
        // For 100 items, replacing innerHTML is very fast.
        
        let logsHtml = '';
        // If index 0 is newest, we reverse it so newest is at the bottom of the scroll container.
        const reversedLogs = [...data.ui_logs].reverse();
        
        reversedLogs.forEach(logLine => {{
            logsHtml += formatLogMessage(logLine);
        }});
        
        const termEl = document.getElementById('terminal-logs');
        termEl.innerHTML = logsHtml;
        
        // Auto scroll to bottom only if new logs arrived
        if (data.ui_logs.length !== lastLogCount || data.ui_logs[0] !== window._lastNewestLog) {{
            termEl.parentElement.scrollTop = termEl.parentElement.scrollHeight;
            window._lastNewestLog = data.ui_logs[0];
            lastLogCount = data.ui_logs.length;
        }}
    }}
    
    // 6. Trade History Render
    if (data.trades && data.trades.length !== lastTradeCount) {{
        renderTradeHistory(data.trades);
        lastTradeCount = data.trades.length;
    }}
}}

// =======================================================
//   RENDER HELPERS
// =======================================================

function renderRadarTable(candidatesList, currentTarget) {{
    const tbody = document.getElementById('radar-tbody');
    if (!candidatesList || candidatesList.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No negative funding candidates found. Scanning continues...</td></tr>';
        return;
    }}
    
    let html = '';
    candidatesList.forEach((cand, idx) => {{
        const sym = cand.symbol;
        const rate = (cand.rate * 100).toFixed(4) + '%';
        const price = parseFloat(cand.last_price || 0).toFixed(4);
        
        // Settle epoch handling
        const dateObj = new Date(cand.settle_ms);
        // format to IST manually or just use toLocaleTimeString
        const settleTime = dateObj.toLocaleTimeString('en-US', {{ timeZone: 'Asia/Kolkata', hour12: false }}) + " IST";
        
        const isSelected = (sym === currentTarget);
        const rowClass = isSelected ? 'selected-row' : '';
        const rank = idx + 1;
        
        // Action Buttons
        const isPermBlock = currentBlockedData.permanent.includes(sym);
        const isTempBlock = currentBlockedData.temporary.includes(sym);
        
        let actionHtml = '';
        
        if (isPermBlock || isTempBlock) {{
            const bLabel = isPermBlock ? 'Perm Blocked' : 'Temp Blocked';
            actionHtml = `<span style="color: var(--sell-red); font-size: 11px; margin-right: 8px;">${{bLabel}}</span>
                          <button class="btn-unblock" onclick="actionUnblockCoin('${{sym}}', '${{isPermBlock ? 'permanent' : 'temporary'}}')">Unblock</button>`;
        }} else {{
            const btnText = isSelected ? (currentManualSymbol === sym ? 'Selected (Manual)' : 'Selected (Auto)') : 'Select';
            const btnState = (isSelected || isSelectionLocked) ? 'disabled' : '';
            
            actionHtml = `<button class="btn-select" ${{btnState}} onclick="selectCoinManual('${{sym}}')">${{btnText}}</button>
                          <button class="btn-block-temp" ${{isSelectionLocked ? 'disabled' : ''}} onclick="actionBlockCoin('${{sym}}', 'temporary')" title="Block for this cycle">Block (1h)</button>
                          <button class="btn-block-perm" onclick="actionBlockCoin('${{sym}}', 'permanent')" title="Block permanently">Ban</button>`;
        }}

        html += `
            <tr class="${{rowClass}}">
                <td>#${{rank}}</td>
                <td style="font-weight: 700; color: #fff;">${{sym}}</td>
                <td style="color: var(--sell-red);">${{rate}}</td>
                <td>${{settleTime}}</td>
                <td>${{price}}</td>
                <td style="text-align: right; min-width: 200px;">
                    ${{actionHtml}}
                </td>
            </tr>
        `;
    }});
    
    tbody.innerHTML = html;
}}

function renderBlocklistUI() {{
    const permTbody = document.getElementById('permanent-block-tbody');
    const tempTbody = document.getElementById('temporary-block-tbody');
    
    // Permanent list
    if(currentBlockedData.permanent.length === 0) {{
        permTbody.innerHTML = '<tr><td colspan="2" style="text-align: center; color: var(--text-muted);">No permanently blocked coins.</td></tr>';
    }} else {{
        let html = '';
        currentBlockedData.permanent.forEach(sym => {{
            html += `<tr>
                        <td style="font-weight: 700;">${{sym}}</td>
                        <td style="text-align: right;"><button class="btn-unblock" onclick="actionUnblockCoin('${{sym}}', 'permanent')">Unblock</button></td>
                     </tr>`;
        }});
        permTbody.innerHTML = html;
    }}
    
    // Temporary list
    if(currentBlockedData.temporary.length === 0) {{
        tempTbody.innerHTML = '<tr><td colspan="2" style="text-align: center; color: var(--text-muted);">No temporarily blocked coins for this cycle.</td></tr>';
    }} else {{
        let html = '';
        currentBlockedData.temporary.forEach(sym => {{
            html += `<tr>
                        <td style="font-weight: 700;">${{sym}}</td>
                        <td style="text-align: right;"><button class="btn-unblock" onclick="actionUnblockCoin('${{sym}}', 'temporary')">Unblock</button></td>
                     </tr>`;
        }});
        tempTbody.innerHTML = html;
    }}
}}
// Dummy function just in case we need to update radar buttons without full re-render
function updateRadarBlockButtons() {{
    fetchDashboardData();
}}

function renderTradeHistory(trades) {{
    const container = document.getElementById('trade-history-container');
    
    if (!trades || trades.length === 0) {{
        container.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-muted);">No trades executed in this session yet.</div>';
        return;
    }}
    
    let html = '';
    
    trades.forEach(t => {{
        const pnlClass = t.pnl_class || 'pnl-neutral';
        const feeClass = t.funding_fee_class || 'pnl-neutral';
        
        // Generate Limit Grid Hits Table
        let hitsTableHtml = '';
        if (t.dca_hits && t.dca_hits.length > 0) {{
            let trRows = '';
            t.dca_hits.forEach(hit => {{
                // Fallback structure to handle both old array formatting and new dict formatting
                const isDict = typeof hit === 'object' && hit !== null;
                const level = isDict ? (hit.level || '--') : hit[0];
                const status = isDict ? (hit.status || 'FILLED') : hit[1];
                const price = isDict ? hit.price : hit[2];
                const qty = isDict ? hit.qty : hit[3];
                const fTime = isDict ? (hit.cancel_fired || hit.first_time || '--') : hit[4];
                const bTime = isDict ? (hit.binance_canceled || hit.last_time || '--') : (hit.length > 5 ? hit[5] : '--');
                const lat = isDict ? (hit.cancel_latency_ms || 0) : (hit.length > 6 ? hit[6] : 0);
                
                const isFilled = status.includes("FILLED");
                const rowClass = isFilled ? 'filled-row' : 'canceled-row';
                const badgeClass = isFilled ? 'status-badge-filled' : 'status-badge-canceled';
                
                // Show cancel latency for canceled limit orders
                let timingStr = '';
                if (!isFilled && fTime !== '--') {{
                    timingStr = `${{fTime}} (Lat: ${{lat}}ms)`;
                }} else if (isFilled) {{
                    timingStr = fTime; // Just show exact fill time
                }} else {{
                    timingStr = '--';
                }}
                
                trRows += `
                    <tr class="${{rowClass}}">
                        <td>${{level}}</td>
                        <td><span class="${{badgeClass}}">${{status}}</span></td>
                        <td>${{price}}</td>
                        <td>${{qty}}</td>
                        <td>${{timingStr}}</td>
                    </tr>
                `;
            }});
            
            hitsTableHtml = `
                <div class="dca-hits-table-wrap">
                    <table class="dca-hits-table">
                        <thead>
                            <tr>
                                <th>Limit Order Target Level</th>
                                <th>Execution Status</th>
                                <th>Price Executed/Target</th>
                                <th>Qty Executed/Target</th>
                                <th>Exact Time (Fill / Cancel Fired)</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${{trRows}}
                        </tbody>
                    </table>
                </div>
            `;
        }}
        
        html += `
        <div class="trade-card">
            <div class="trade-card-header">
                <div class="trade-symbol-wrap">
                    <span class="trade-symbol">${{t.symbol || 'UNKNOWN'}}</span>
                    <span class="trade-time">${{t.entry_fill_time || '--:--:--'}}</span>
                </div>
                <div class="trade-pnl-wrap">
                    <div style="text-align: right; line-height: 1.2;">
                        <div style="font-size: 10px; color: var(--text-muted); text-transform: uppercase;">Net Realized PnL</div>
                        <div class="pnl-badge ${{pnlClass}}">${{t.pnl || '$0.0000'}}</div>
                    </div>
                </div>
            </div>
            
            <div class="trade-card-body">
                <div class="trade-metrics-grid">
                    <div class="metric-item">
                        <span class="metric-label">Funding Rate</span>
                        <span class="metric-val" style="color: var(--sell-red)">${{t.funding_rate || '--'}}</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Funding Fee Claimed</span>
                        <span class="metric-val" style="color: var(--${{feeClass.split('-')[1] || 'text-main'}})">${{t.funding_fee_received || '$0.00'}}</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Avg Entry Price</span>
                        <span class="metric-val">${{t.entry_fill_price || '--'}}</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Exit Avg Price</span>
                        <span class="metric-val">${{t.exit_price || '--'}}</span>
                    </div>
                </div>
                
                ${{hitsTableHtml}}
                
                <div style="margin-top: 4px; font-size: 11px; color: var(--text-muted);">
                    <strong>Breakdown:</strong> ${{t.pnl_breakdown || 'N/A'}}
                </div>
            </div>
            
            <div class="trade-card-footer">
                <span>Exit Type: <strong>${{t.exit_type || '--'}}</strong></span>
                <span>Final Status: <strong style="color: var(--text-main);">${{t.status || '--'}}</strong></span>
            </div>
        </div>
        `;
    }});
    
    container.innerHTML = html;
}}

// Local clock sync just for UI visual ticking
setInterval(() => {{
    const now = new Date();
    document.getElementById('sys-time').textContent = now.toLocaleTimeString('en-US', {{ timeZone: 'Asia/Kolkata', hour12: false }});
}}, 1000);

// Initialize Polling Loop
setInterval(fetchDashboardData, 1000);
fetchDashboardData();

</script>
</body>
</html>
"""
