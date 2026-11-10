import os
import json
import logging
from datetime import datetime
import pytz
import threading
from flask import Flask, jsonify, render_template, request

import config

app = Flask(__name__, template_folder=".")
app.logger.setLevel(logging.ERROR)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

IST = pytz.timezone('Asia/Kolkata')

data_lock = threading.Lock()
dashboard_data = {
    "target_symbol": "Scanning...",
    "funding_rate": "0.00%",
    "active_tier": "IDLE",
    "status": "INITIALIZING...",
    "ui_logs": [],
    "recent_trades": [],
    "target_settlement": "--:--:--",
    "top_5_candidates": [],
    
    # User Control Flags
    "trading_enabled": True,             # Master switch: True = active, False = paused
    "manual_selected_symbol": "",        # Agar empty hai toh AUTO mode. Varna user chosen coin.
    "selection_locked": False,           # T-60s par true ho jayega
    "force_rescan": False                # Instant market rescan trigger
}

# --- Blocklist Memory Persistence ---
BLOCKLIST_FILE = "blocked_coins.json"

def load_blocked_coins() -> dict:
    if os.path.exists(BLOCKLIST_FILE):
        try:
            with open(BLOCKLIST_FILE, 'r') as f:
                data = json.load(f)
                return {
                    "temporary": data.get("temporary", []),
                    "permanent": data.get("permanent", [])
                }
        except Exception:
            pass
    return {"temporary": [], "permanent": []}

def save_blocked_coins(data: dict):
    try:
        with open(BLOCKLIST_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving blocklist: {e}")

# Load initial blocklist into memory on boot (optional, scanner loads it directly too)
blocked_memory = load_blocked_coins()

def add_ui_log(msg: str):
    """Puraane logs niche dhakelta hai, naya log upar add karta hai (max 100). Console par bhi print karta hai."""
    ts = datetime.now(IST).strftime("%H:%M:%S")
    full_msg = f"[{ts}] {msg}"
    print(full_msg)
    with data_lock:
        dashboard_data["ui_logs"].insert(0, full_msg)
        if len(dashboard_data["ui_logs"]) > 100:
            dashboard_data["ui_logs"] = dashboard_data["ui_logs"][:100]

def set_dashboard_status(msg: str):
    with data_lock:
        dashboard_data["status"] = msg

def record_completed_trade(trade_dict: dict):
    with data_lock:
        dashboard_data["recent_trades"].insert(0, trade_dict)
        if len(dashboard_data["recent_trades"]) > 30:
            dashboard_data["recent_trades"] = dashboard_data["recent_trades"][:30]


# ==============================================================
# UI ROUTES
# ==============================================================

@app.route("/")
def index():
    return render_template("dashboard_template.py")

@app.route("/api/data")
def api_data():
    with data_lock:
        return jsonify(dashboard_data)

@app.route("/api/blocklist")
def api_blocklist():
    return jsonify(load_blocked_coins())

# ==============================================================
# CONTROL ROUTES (AJAX Endpoints)
# ==============================================================

@app.route("/api/toggle_trading", methods=["POST"])
def toggle_trading():
    with data_lock:
        current_state = dashboard_data.get("trading_enabled", True)
        new_state = not current_state
        dashboard_data["trading_enabled"] = new_state
        
        status_msg = "ON" if new_state else "OFF"
        add_ui_log(f"🛠️ [USER ACTION] Master Trading Switch turned {status_msg}.")
        return jsonify({"success": True, "trading_enabled": new_state})

@app.route("/api/set_manual_target", methods=["POST"])
def set_manual_target():
    data = request.json
    symbol = data.get("symbol", "").strip().upper()
    
    with data_lock:
        if dashboard_data.get("selection_locked", False):
            return jsonify({"success": False, "error": "Selection locked! (Within 60s of settlement)"}), 400
            
        dashboard_data["manual_selected_symbol"] = symbol
        dashboard_data["force_rescan"] = True  # Trigger immediate re-evaluation
        
        if symbol:
            add_ui_log(f"👤 [USER ACTION] Manual target set to: {symbol}")
        else:
            add_ui_log("🤖 [USER ACTION] Reverted to AUTO mode.")
            
        return jsonify({"success": True, "manual_symbol": symbol})

@app.route("/api/force_rescan", methods=["POST"])
def force_rescan():
    with data_lock:
        if dashboard_data.get("selection_locked", False):
            return jsonify({"success": False, "error": "Cannot force scan while locked!"}), 400
            
        dashboard_data["force_rescan"] = True
        add_ui_log("⚡ [USER ACTION] Force instant market audit requested.")
        return jsonify({"success": True})

# ==============================================================
# BLOCKLIST ROUTES (AJAX Endpoints)
# ==============================================================

@app.route("/api/block_coin", methods=["POST"])
def block_coin():
    """Adds a coin to either temporary (1-hour/cycle) or permanent blocklist"""
    data = request.json
    symbol = data.get("symbol", "").strip().upper()
    block_type = data.get("type", "temporary") # "temporary" or "permanent"
    
    if not symbol:
        return jsonify({"success": False, "error": "Symbol is required"}), 400
        
    blocked_data = load_blocked_coins()
    
    # Remove from both first to avoid duplicates
    if symbol in blocked_data["temporary"]:
        blocked_data["temporary"].remove(symbol)
    if symbol in blocked_data["permanent"]:
        blocked_data["permanent"].remove(symbol)
        
    # Add to requested list
    if block_type == "permanent":
        blocked_data["permanent"].append(symbol)
        add_ui_log(f"🚫 [USER ACTION] Permanently blocked {symbol}.")
    else:
        blocked_data["temporary"].append(symbol)
        add_ui_log(f"🛑 [USER ACTION] Temporarily blocked {symbol} (Until next cycle).")
        
    save_blocked_coins(blocked_data)
    
    # If the currently selected coin is blocked, force a rescan
    with data_lock:
        current_target = dashboard_data.get("target_symbol", "").upper()
        if symbol == current_target or symbol == dashboard_data.get("manual_selected_symbol", ""):
            dashboard_data["manual_selected_symbol"] = ""
            dashboard_data["force_rescan"] = True
            add_ui_log(f"⚠️ Active target {symbol} was blocked! Triggering immediate re-scan...")
            
    return jsonify({"success": True, "blocklist": blocked_data})

@app.route("/api/unblock_coin", methods=["POST"])
def unblock_coin():
    """Removes a coin from blocklist"""
    data = request.json
    symbol = data.get("symbol", "").strip().upper()
    
    if not symbol:
        return jsonify({"success": False, "error": "Symbol is required"}), 400
        
    blocked_data = load_blocked_coins()
    removed = False
    
    if symbol in blocked_data["temporary"]:
        blocked_data["temporary"].remove(symbol)
        removed = True
    if symbol in blocked_data["permanent"]:
        blocked_data["permanent"].remove(symbol)
        removed = True
        
    if removed:
        save_blocked_coins(blocked_data)
        add_ui_log(f"✅ [USER ACTION] Unblocked {symbol}.")
        
    return jsonify({"success": True, "blocklist": blocked_data})

@app.route("/api/clear_temp_blocks", methods=["POST"])
def clear_temp_blocks():
    """Clears all temporary blocks (usually called automatically after settlement)"""
    blocked_data = load_blocked_coins()
    count = len(blocked_data["temporary"])
    
    if count > 0:
        blocked_data["temporary"] = []
        save_blocked_coins(blocked_data)
        add_ui_log(f"🧹 [SYSTEM] Cleared {count} temporarily blocked coins for fresh cycle.")
        
    return jsonify({"success": True, "blocklist": blocked_data})


def run_web_server():
    port = config.DASHBOARD_PORT
    print(f"🌐 UI Dashboard running at: http://127.0.0.1:{port}/")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

