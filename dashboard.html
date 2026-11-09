<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Binance Master Quant Cockpit - Dual Strategy</title>
    <style>
        :root {
            --bg-color: #06090f;
            --card-bg: #111827;
            --card-border: #1f2937;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent-cyan: #00f0ff;
            --accent-indigo: #6366f1;
            --green: #10b981;
            --red: #f43f5e;
            --blue: #38bdf8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background-color: var(--bg-color); color: var(--text-main); padding: 12px; font-size: 0.85rem; }
        
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid var(--card-border); padding-bottom: 10px; }
        h1 { font-size: 1.2rem; color: var(--accent-cyan); display: flex; align-items: center; gap: 6px; text-shadow: 0 0 10px rgba(0,240,255,0.3); }
        .status-badge { background: rgba(16, 185, 129, 0.15); color: var(--green); padding: 3px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; border: 1px solid var(--green); }

        .strategy-container { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 14px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
        .strategy-header { border-bottom: 2px solid var(--accent-cyan); padding-bottom: 8px; margin-bottom: 12px; }
        .strategy-title { font-size: 1rem; color: var(--accent-cyan); font-weight: bold; display: flex; justify-content: space-between; align-items: center; }
        .strategy-features { font-size: 0.75rem; color: var(--text-muted); margin-top: 4px; line-height: 1.3; }

        .sub-section-title { font-size: 0.82rem; color: var(--blue); margin: 12px 0 6px 0; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
        
        /* Armed Mini-Boxes Container */
        .armed-chips-container { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
        .armed-chip { background: rgba(0, 240, 255, 0.08); border: 1px solid var(--accent-cyan); border-radius: 6px; padding: 6px 10px; font-size: 0.78rem; display: flex; flex-direction: column; min-width: 100px; }
        .armed-chip .coin { font-weight: bold; color: var(--accent-cyan); }
        .armed-chip .rate { font-size: 0.72rem; color: var(--red); }
        
        .radar-table, .ledger-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; margin-bottom: 10px; }
        .radar-table th, .radar-table td, .ledger-table th, .ledger-table td { padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--card-border); }
        .radar-table th, .ledger-table th { background-color: rgba(255,255,255,0.02); color: var(--text-muted); font-weight: 600; }
        .radar-table tr:hover, .ledger-table tr:hover { background-color: rgba(255,255,255,0.01); }

        .btn-action { background: var(--green); color: #000; border: none; padding: 3px 8px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 0.75rem; transition: 0.2s; }
        .btn-action:hover { opacity: 0.8; }
        .btn-disarm { background: var(--red); color: #fff; border: none; padding: 3px 8px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 0.75rem; transition: 0.2s; }
        .btn-disarm:hover { opacity: 0.8; }

        /* Ledger Card Styling */
        .ledger-card { background: rgba(0,0,0,0.5); border: 1px solid var(--card-border); border-radius: 8px; padding: 10px; margin-bottom: 10px; font-size: 0.78rem; }
        .ledger-header { display: flex; justify-content: space-between; color: var(--accent-cyan); font-weight: bold; border-bottom: 1px dashed var(--card-border); padding-bottom: 5px; margin-bottom: 6px; }
        .shallow-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: 6px; }
        .shallow-box { background: rgba(56, 189, 248, 0.08); border: 1px solid var(--blue); border-radius: 5px; padding: 6px; font-size: 0.72rem; }
        .shallow-box.cancelled { border-color: var(--red); background: rgba(244, 63, 94, 0.08); }
        
        .empty-state { text-align: center; color: var(--text-muted); padding: 15px; font-style: italic; }
    </style>
</head>
<body>

    <header>
        <h1>⚡ Master Quant Cockpit</h1>
        <div id="system-status" class="status-badge">DUAL ENGINE ACTIVE</div>
    </header>

    <!-- ================= STRATEGY 1 ================= -->
    <div class="strategy-container">
        <div class="strategy-header">
            <div class="strategy-title">
                <span>STRATEGY 1: TOKYO EXTREME SNIPER (T-100ms Limit Burst)</span>
            </div>
            <div class="strategy-features">
                Features: Tokyo Fast-Lane WebSocket | Single-Shot Limit Burst | 3 Shallow Limits (30/50/70%) | DCA Grid Layers | Dynamic SL & Fast Shallow Cancel Burst.
            </div>
        </div>

        <!-- Armed Mini-Boxes Box on Top -->
        <div class="sub-section-title"><span>🛡️ Armed Sniper Target Pool</span></div>
        <div id="s1-armed-chips" class="armed-chips-container">
            <div class="empty-state" style="padding:6px;">No coins currently armed.</div>
        </div>

        <!-- Top 20 Radar Table -->
        <div class="sub-section-title"><span>Top 20 Funding Radar (Click Arm / Disarm)</span></div>
        <div style="max-height: 200px; overflow-y: auto;">
            <table class="radar-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Funding Rate</th>
                        <th>Next Settlement</th>
                        <th>Mode</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody id="s1-radar-body">
                    <tr><td colspan="5" class="empty-state">Loading Strategy 1 Radar...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Precision Ledger -->
        <div class="sub-section-title"><span>Strategy 1: Last 20 Trades Precision Ledger</span></div>
        <div id="s1-ledger-container">
            <div class="empty-state">Waiting for Strategy 1 precision execution...</div>
        </div>
    </div>


    <!-- ================= STRATEGY 2 ================= -->
    <div class="strategy-container">
        <div class="strategy-header">
            <div class="strategy-title">
                <span>STRATEGY 2: AUTO TOP-3 QUANT ARBITRAGE (T+220ms Market Entry | -0.25% Threshold)</span>
            </div>
            <div class="strategy-features">
                Features: $0.5 Margin ($10 Notional @ 20x) | T+220ms Precise Market Buy | -200% Funding Rate SL Shield | +0.10% Take Profit Limit | 10-Minute Hard Exit Timeout.
            </div>
        </div>

        <!-- Armed Mini-Boxes Box on Top -->
        <div class="sub-section-title"><span>🚀 Strategy 2 Active Target Pool</span></div>
        <div id="s2-armed-chips" class="armed-chips-container">
            <div class="empty-state" style="padding:6px;">No coins currently armed.</div>
        </div>

        <!-- Top 20 Radar Table -->
        <div class="sub-section-title"><span>Top 20 Negative Funding Radar (Click Arm / Disarm)</span></div>
        <div style="max-height: 200px; overflow-y: auto;">
            <table class="radar-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Funding Rate</th>
                        <th>Next Settlement</th>
                        <th>Mode</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody id="s2-radar-body">
                    <tr><td colspan="5" class="empty-state">Loading Strategy 2 Radar...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Execution Ledger -->
        <div class="sub-section-title"><span>Strategy 2: Execution Ledger (T+220ms Market Entry)</span></div>
        <div style="overflow-x: auto;">
            <table class="ledger-table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Coin & Funding</th>
                        <th>Entry Time & Price</th>
                        <th>Exit Time & Price</th>
                        <th>Details / Outcome</th>
                    </tr>
                </thead>
                <tbody id="s2-ledger-body">
                    <tr><td colspan="5" class="empty-state">Waiting for Strategy 2 trades to execute...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function toggleArm(strategy, symbol, action) {
            await fetch('/api/arm-control', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ strategy, symbol, action })
            });
            fetchDashboardData();
        }

        async function fetchDashboardData() {
            try {
                let res = await fetch('/api/status');
                let data = await res.json();

                // Strategy 1 Radar & Armed Chips
                let s1RadarHtml = "";
                let s1ArmedChipsHtml = "";
                if(data.strategy1_top20 && data.strategy1_top20.length > 0) {
                    data.strategy1_top20.forEach(c => {
                        if(c.is_armed) {
                            s1ArmedChipsHtml += `
                                <div class="armed-chip">
                                    <span class="coin">${c.symbol}</span>
                                    <span class="rate">${c.rate_str}</span>
                                </div>
                            `;
                        }
                        let btn = c.is_armed ? `<button class="btn-disarm" onclick="toggleArm(1, '${c.symbol}', 'disarm')">Disarm</button>` : `<button class="btn-action" onclick="toggleArm(1, '${c.symbol}', 'arm')">Arm</button>`;
                        s1RadarHtml += `<tr><td><strong>${c.symbol}</strong></td><td style="color:var(--red);">${c.rate_str}</td><td>${c.settle_str}</td><td>${c.arm_mode}</td><td>${btn}</td></tr>`;
                    });
                } else { s1RadarHtml = `<tr><td colspan="5" class="empty-state">No negative funding coins found.</td></tr>`; }
                
                document.getElementById('s1-radar-body').innerHTML = s1RadarHtml;
                document.getElementById('s1-armed-chips').innerHTML = s1ArmedChipsHtml || `<div class="empty-state" style="padding:6px;">No coins currently armed.</div>`;

                // Strategy 1 Precision Ledger
                let s1LedgerContainer = document.getElementById('s1-ledger-container');
                if(data.trades && data.trades.length > 0) {
                    let html = "";
                    data.trades.forEach((t, i) => {
                        let shallowHtml = "";
                        if(t.dca_hits && t.dca_hits.length > 0) {
                            t.dca_hits.forEach(d => {
                                shallowHtml += `
                                    <div class="shallow-box cancelled">
                                        <strong>${d.level}</strong><br>
                                        Price: ${d.price}<br>
                                        Qty: ${d.qty}<br>
                                        <span style="color:var(--red);">⚡ Cancel Fired</span><br>
                                        <span style="color:var(--green);">${d.time}</span>
                                    </div>
                                `;
                            });
                        } else {
                            shallowHtml = `<div class="empty-state" style="grid-column: span 3; padding:4px;">No shallow fills recorded in this cycle.</div>`;
                        }

                        html += `
                            <div class="ledger-card">
                                <div class="ledger-header">
                                    <span>#${i+1} ${t.symbol} | ${t.funding_rate}</span>
                                    <span style="color:var(--green);">${t.status}</span>
                                </div>
                                <div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:6px; line-height:1.4;">
                                    <strong>STRATEGY MODE:</strong> ${t.tier}<br>
                                    <strong>ENTRY TIMELINE:</strong> Sent: ${t.entry_sent} | Ack: ${t.entry_ack} | First Fill: ${t.entry_fill_time}<br>
                                    <strong>EXECUTION SUMMARY:</strong> Avg Entry: ${t.entry_fill_price} | Exit: ${t.exit_price} | PnL: ${t.pnl}
                                </div>
                                <div class="sub-section-title" style="margin:4px 0;"><span>LIMIT ORDER FILLS & SHALLOW CANCELS (30/50/70% FR)</span></div>
                                <div class="shallow-grid">${shallowHtml}</div>
                            </div>
                        `;
                    });
                    s1LedgerContainer.innerHTML = html;
                }

                // Strategy 2 Radar & Armed Chips
                let s2RadarHtml = "";
                let s2ArmedChipsHtml = "";
                if(data.strategy2 && data.strategy2.top20 && data.strategy2.top20.length > 0) {
                    data.strategy2.top20.forEach(c => {
                        if(c.is_armed) {
                            s2ArmedChipsHtml += `
                                <div class="armed-chip">
                                    <span class="coin">${c.symbol}</span>
                                    <span class="rate">${c.rate_str}</span>
                                </div>
                            `;
                        }
                        let btn = c.is_armed ? `<button class="btn-disarm" onclick="toggleArm(2, '${c.symbol}', 'disarm')">Disarm</button>` : `<button class="btn-action" onclick="toggleArm(2, '${c.symbol}', 'arm')">Arm</button>`;
                        s2RadarHtml += `<tr><td><strong>${c.symbol}</strong></td><td style="color:var(--red);">${c.rate_str}</td><td>${c.settle_str}</td><td>${c.arm_mode}</td><td>${btn}</td></tr>`;
                    });
                } else { s2RadarHtml = `<tr><td colspan="5" class="empty-state">No negative funding coins found.</td></tr>`; }
                
                document.getElementById('s2-radar-body').innerHTML = s2RadarHtml;
                document.getElementById('s2-armed-chips').innerHTML = s2ArmedChipsHtml || `<div class="empty-state" style="padding:6px;">No coins currently armed.</div>`;

                // Strategy 2 Ledger
                let s2Body = document.getElementById('s2-ledger-body');
                if(data.strategy2_trades && data.strategy2_trades.length > 0) {
                    let html = "";
                    data.strategy2_trades.forEach((t, i) => {
                        html += `
                            <tr>
                                <td>${i+1}</td>
                                <td><strong>${t.coin}</strong><br><span style="color:var(--accent-cyan);">${t.rate}</span></td>
                                <td>${t.entry_time}<br>Price: ${t.entry_price || '--'}</td>
                                <td>${t.exit_time}<br>Price: ${t.exit_price || '--'}</td>
                                <td>${t.detail}<br><span class="${t.badge_class}">${t.badge_text}</span></td>
                            </tr>
                        `;
                    });
                    s2Body.innerHTML = html;
                }

            } catch (err) { console.error("Dashboard sync error:", err); }
        }

        setInterval(fetchDashboardData, 1000);
        fetchDashboardData();
    </script>
</body>
</html>
