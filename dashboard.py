from __future__ import annotations
import json
import threading
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import config

IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()
trade_history: list[dict] = []

dashboard_data = {
    "target_symbol": "Scanning...",
    "funding_rate": "0.00%",
    "action_direction": "",
    "status": f"Initializing Binance Tokyo Funding Sniper (T+{config.ENTRY_AFTER_SETTLE_MS}ms)...",
    "target_settlement_time": "--:--:-- IST",
    "target_settlement_ms": 0,
    "target_settlement": "-- IST",
    "entry_target": f"-- IST (Market Buy T+{config.ENTRY_AFTER_SETTLE_MS}ms)",
    "exit_target": f"Settle Harvest Exit T+{config.EXIT_AFTER_SETTLE_MS//1000}s",
    "clock_offset_ms": 0.0,
    "margin_usd": f"{config.TRADE_MARGIN_USD} USDT",
    "trades": trade_history
}

def add_ui_log(message: str) -> None:
    timestamp = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    print(f"[{timestamp}] {message}")

def set_dashboard_status(status_msg: str) -> None:
    with data_lock:
        dashboard_data["status"] = status_msg

def record_completed_trade(trade_record: dict) -> None:
    with data_lock:
        trade_history.insert(0, trade_record)
        if len(trade_history) > 20:
            trade_history.pop()
        dashboard_data["trades"] = list(trade_history)

class FundingDashboardHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == '/api/status':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with data_lock:
                payload = json.dumps(dashboard_data).encode("utf-8")
            self.wfile.write(payload)
            return

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
<title>Binance Tokyo Funding Capture Sniper</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
body { background-color: #0b0e11; color: #eaecef; padding: 14px; display: flex; justify-content: center; }
.container { width: 100%; max-width: 1200px; display: flex; flex-direction: column; gap: 14px; }
header { background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 10px; }
header h1 { font-size: 16px; color: #f0b90b; font-weight: 700; }
.badge { background: rgba(240, 185, 11, 0.15); color: #f0b90b; padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
.card { background: #1e2329; padding: 12px 14px; border-radius: 10px; border: 1px solid #2b313a; }
.card-title { font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 4px; }
.card-value { font-size: 16px; font-weight: 700; color: #f0b90b; }
.table-card { background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; overflow: hidden; }
.table-header { font-size: 15px; font-weight: 700; color: #eaecef; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
.table-wrapper { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 8px; border: 1px solid #2b313a; }
table { width: 100%; border-collapse: collapse; min-width: 900px; font-size: 13px; text-align: left; }
th { background: #14181d; color: #848e9c; padding: 12px 14px; font-weight: 600; text-transform: uppercase; font-size: 11px; border-bottom: 1px solid #2b313a; white-space: nowrap; }
td { padding: 12px 14px; border-bottom: 1px solid #1e252e; vertical-align: middle; white-space: nowrap; }
tr.main-row:nth-child(4n+1) { background: #181d24; }
tr.sub-row { background: rgba(0, 0, 0, 0.25); border-bottom: 2px solid #2b313a; }
.sub-box { padding: 10px 14px; display: flex; flex-wrap: wrap; gap: 18px; font-size: 12px; border-left: 3px solid #f0b90b; background: rgba(240, 185, 11, 0.04); }
.sub-item { display: flex; align-items: center; gap: 6px; }
.sub-label { color: #848e9c; text-transform: uppercase; font-size: 10px; }
.tag-success { background: rgba(14, 203, 129, 0.15); color: #0ecb81; padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }
.tag-cancel { background: rgba(132, 142, 156, 0.2); color: #848e9c; padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }
.tag-warn { background: rgba(247, 166, 0, 0.15); color: #f7a600; padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }
.mono { font-family: 'SFMono-Regular', Consolas, Menlo, monospace; font-size: 12px; }
.sub-text { font-size: 11px; color: #848e9c; margin-top: 2px; }
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>Binance Tokyo Sniper</h1>
<div class="badge" id="ws-status">SCANNING</div>
</div>
<div class="grid">
<div class="card"><div class="card-title">Target Coin</div><div class="card-value" id="coin">-</div></div>
<div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color: #0ecb81;">-</div></div>
<div class="card"><div class="card-title">Next Settlement</div><div class="card-value" id="settle" style="color: #eaecef;">--</div></div>
<div class="card"><div class="card-title">Base Margin</div><div class="card-value" style="color: #f0b90b;">USDT</div></div>
<div class="card"><div class="card-title">Tokyo Clock Drift</div><div class="card-value" id="offset">0.0 ms</div></div>
</div>
<div class="table-card">
<div class="table-header">
<span>Last Trades Precision Ledger</span>
<span style="font-size: 12px; color: #848e9c; font-weight: normal;">Auto-Refreshes every 1s</span>
</div>
<div class="table-wrapper">
<table>
<thead>
<tr>
<th>#</th>
<th>Coin & Funding</th>
<th>Market Buy Timeline</th>
<th>Entry Price</th>
<th>Market Exit Timeline</th>
<th>Exit Price</th>
<th>Status</th>
</tr>
</thead>
<tbody id="trades-body">
<tr><td colspan="7" style="text-align: center; padding: 24px; color: #848e9c;">Waiting for trades to execute...</td></tr>
</tbody>
</table>
</div>
</div>
</div>
<script>
function formatDurationJs(sec) {
  if (sec <= 0) return '0s (Settling...)';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
async function updateUI() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    document.getElementById('ws-status').innerText = data.status;
    document.getElementById('coin').innerText = data.target_symbol;
    document.getElementById('rate').innerText = data.funding_rate;
    document.getElementById('offset').innerText = data.clock_offset_ms + ' ms';
    const tbody = document.getElementById('trades-body');
    if (!data.trades || data.trades.length == 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 24px; color:#848e9c;">Waiting for trades to execute...</td></tr>';
      return;
    }
    tbody.innerHTML = data.trades.map((t, idx) => {
      let statusClass = "tag-success";
      if (t.status.includes("REJECTED")) statusClass = "tag-cancel";
      else if (t.status.includes("EMERGENCY")) statusClass = "tag-warn";
      return `
        <tr class="main-row">
          <td style="color:#848e9c;">${idx+1}</td>
          <td><strong>${t.symbol}</strong><div class="sub-text" style="color:#0ecb81; font-weight:600;">Rate: ${t.funding_rate}</div></td>
          <td><div class="mono">Sent: ${t.entry_sent}</div><div class="mono sub-text">&#8594; ACK: ${t.entry_ack}</div><div class="mono sub-text" style="color:#0ecb81; font-weight:600;">&#8594; Fill: ${t.entry_fill_time || '--'}</div></td>
          <td><div class="mono" style="color:#0ecb81; font-weight:bold;">${t.entry_fill_price || '--'}</div></td>
          <td><div class="mono">Fired: ${t.exit_sent || '--'}</div><div class="mono sub-text" style="color:#eaecef;">&#8594; Closed: ${t.exit_fill_time || '--'}</div></td>
          <td><div class="mono"><strong style="color:#f0b90b; font-size:14px;">${t.exit_price || '--'}</strong></div><div class="sub-text">${t.exit_type}</div></td>
          <td><span class="${statusClass}">${t.status}</span></td>
        </tr>
        <tr class="sub-row"><td colspan="7" style="padding: 0;"><div class="sub-box"><div class="sub-item"><span class="sub-label">Execution Summary:</span><strong class="mono" style="color: #eaecef;">${t.details || '--'}</strong></div></div></td></tr>
      `;
    }).join('');
  } catch (e) {}
}
setInterval(updateUI, 1000);
updateUI();
</script>
</body>
</html>"""
        self.wfile.write(html_content.encode("utf-8"))

def run_web_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", config.DASHBOARD_PORT), FundingDashboardHandler)
    server.serve_forever()
