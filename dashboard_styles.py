from __future__ import annotations


def get_dashboard_css() -> str:
    return """
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }

body { background-color: #0b0e11; color: #eaecef; padding: 14px; display: flex; justify-content: center; }
.container { width: 100%; max-width: 1260px; display: flex; flex-direction: column; gap: 14px; }

/* PROFESSIONAL TRADING TERMINAL HEADER */
.header {
    background: linear-gradient(180deg, #1e2329 0%, #181c22 100%);
    padding: 16px 18px;
    border-radius: 12px;
    border: 1px solid #2b313a;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
    display: flex;
    flex-direction: column;
    gap: 12px;
}
.header-top {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 14px;
}
.brand-block {
    display: flex;
    flex-direction: column;
    gap: 3px;
}
.header h1 {
    font-size: 17px;
    color: #f0b90b;
    font-weight: 800;
    letter-spacing: 0.2px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.brand-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #0ecb81;
    box-shadow: 0 0 8px #0ecb81;
    display: inline-block;
}

/* UNIFORM HEIGHT CONTROL DECK */
.controls-deck {
    display: flex;
    flex-wrap: wrap;
    align-items: stretch;
    gap: 10px;
}
.btn-toggle {
    height: 58px;
    min-width: 158px;
    padding: 8px 20px;
    border-radius: 10px;
    font-size: 12.5px;
    font-weight: 800;
    cursor: pointer;
    border: 1px solid transparent;
    transition: all 0.2s ease;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    gap: 2px;
    letter-spacing: 0.4px;
}
.btn-toggle-sub {
    font-size: 9px;
    font-weight: 700;
    opacity: 0.85;
    letter-spacing: 0.6px;
    text-transform: uppercase;
}
.btn-on {
    background: linear-gradient(180deg, #10d889 0%, #0bb873 100%);
    color: #062115;
    border-color: #34eba1;
    box-shadow: 0 3px 12px rgba(14, 203, 129, 0.28);
}
.btn-on:hover { filter: brightness(1.05); }
.btn-off {
    background: linear-gradient(180deg, #f05252 0%, #dc2626 100%);
    color: #ffffff;
    border-color: #f87171;
    box-shadow: 0 3px 12px rgba(239, 68, 68, 0.28);
}
.btn-off:hover { filter: brightness(1.05); }

/* SEGMENTED MODE SWITCHER (COOL CYAN AUTO + 1/2/3 PILLS & AMBER MANUAL) */
.mode-container {
    display: flex;
    align-items: stretch;
    gap: 6px;
    background: #12161b;
    padding: 4px;
    border-radius: 11px;
    border: 1px solid #2b313a;
    height: 58px;
}
.mode-slot {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    min-width: 152px;
    height: 100%;
    border-radius: 8px;
    padding: 3px;
    transition: all 0.2s ease;
}
.mode-slot-auto-active {
    background: rgba(56, 189, 248, 0.12);
    border: 1px solid rgba(56, 189, 248, 0.45);
}
.mode-slot-manual-active {
    background: rgba(240, 185, 11, 0.14);
    border: 1px solid rgba(240, 185, 11, 0.5);
}
.mode-slot-inactive {
    background: transparent;
    border: 1px solid transparent;
}

.btn-mode {
    width: 100%;
    flex: 1;
    padding: 4px 14px;
    border-radius: 6px;
    font-size: 11.5px;
    font-weight: 800;
    cursor: pointer;
    border: none;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    letter-spacing: 0.3px;
}
.btn-mode-auto-active {
    background: linear-gradient(180deg, #38bdf8 0%, #0ea5e9 100%);
    color: #041e2c;
    box-shadow: 0 2px 8px rgba(56, 189, 248, 0.3);
}
.btn-mode-manual-active {
    background: #f0b90b;
    color: #0b0e11;
    box-shadow: 0 2px 8px rgba(240, 185, 11, 0.3);
}
.btn-mode-inactive {
    background: transparent;
    color: #848e9c;
}
.btn-mode-inactive:hover {
    color: #eaecef;
    background: rgba(255, 255, 255, 0.05);
}
.btn-mode:disabled { opacity: 0.45; cursor: not-allowed; }

.auto-count-row {
    display: flex;
    gap: 4px;
    width: 100%;
    margin-top: 3px;
}
.btn-auto-cnt {
    flex: 1;
    height: 18px;
    line-height: 16px;
    border-radius: 4px;
    font-size: 10.5px;
    font-weight: 800;
    cursor: pointer;
    border: 1px solid #2b313a;
    transition: all 0.15s ease;
    text-align: center;
    background: #181d24;
    color: #848e9c;
}
.btn-auto-cnt:hover {
    border-color: #38bdf8;
    color: #eaecef;
}
.btn-auto-cnt-active {
    background: #38bdf8;
    color: #041e2c;
    border-color: #38bdf8;
    box-shadow: 0 0 6px rgba(56, 189, 248, 0.4);
}
.btn-auto-cnt:disabled { opacity: 0.45; cursor: not-allowed; }

.manual-sub-pill {
    height: 18px;
    line-height: 18px;
    text-align: center;
    font-size: 9.5px;
    font-weight: 700;
    color: #848e9c;
    letter-spacing: 0.4px;
    margin-top: 3px;
}

/* HEADER BOTTOM STATUS STRIP */
.header-status-bar {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding-top: 10px;
    border-top: 1px solid #262c35;
}
.badge {
    background: rgba(240, 185, 11, 0.12);
    color: #f0b90b;
    border: 1px solid rgba(240, 185, 11, 0.3);
    padding: 5px 12px;
    border-radius: 7px;
    font-size: 11.5px;
    font-weight: 700;
}
.badge-mode {
    background: rgba(37, 99, 235, 0.16);
    color: #60a5fa;
    padding: 5px 12px;
    border-radius: 7px;
    font-size: 11.5px;
    font-weight: 800;
    border: 1px solid rgba(96, 165, 250, 0.35);
    letter-spacing: 0.3px;
}
.badge-locked {
    background: rgba(239, 68, 68, 0.18);
    color: #f87171;
    padding: 5px 12px;
    border-radius: 7px;
    font-size: 11.5px;
    font-weight: 800;
    border: 1px solid rgba(239, 68, 68, 0.4);
    letter-spacing: 0.3px;
}

/* TOP TELEMETRY & SLOT MARGIN CARDS */
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 10px; }
.card { background: #1e2329; padding: 12px 14px; border-radius: 10px; border: 1px solid #2b313a; display: flex; flex-direction: column; justify-content: space-between; }
.card-editable { cursor: pointer; border: 1px solid rgba(240, 185, 11, 0.45); transition: 0.2s; position: relative; grid-column: span 1; min-width: 225px; }
.card-editable:hover { background: #262c34; border-color: #f0b90b; box-shadow: 0 0 12px rgba(240, 185, 11, 0.20); }
.card-title { font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center; }
.card-value { font-size: 16px; font-weight: 700; color: #f0b90b; }

.slot-preview-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    width: 100%;
}
.slot-preview-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 3px 7px;
    border-radius: 5px;
    background: #14181d;
    font-size: 11.5px;
    font-family: 'SFMono-Regular', Consolas, monospace;
    transition: opacity 0.2s;
}
.slot-row-1 { border-left: 3px solid #0ecb81; }
.slot-row-2 { border-left: 3px solid #38bdf8; }
.slot-row-3 { border-left: 3px solid #f0b90b; }
.slot-row-dim { opacity: 0.52; }
.slot-tag-1 { color: #0ecb81; font-weight: 800; }
.slot-tag-2 { color: #38bdf8; font-weight: 800; }
.slot-tag-3 { color: #f0b90b; font-weight: 800; }
.slot-coin-name { color: #eaecef; font-weight: 700; margin-left: 4px; }
.slot-coin-empty { color: #64748b; font-weight: 600; margin-left: 4px; font-style: italic; }
.slot-amt-val { color: #ffffff; font-weight: 800; }

/* TABLES & LEDGER */
.table-card { background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; overflow: hidden; }
.table-header { font-size: 15px; font-weight: 700; color: #eaecef; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
.table-wrapper { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 8px; border: 1px solid #2b313a; }
table { width: 100%; border-collapse: collapse; min-width: 1040px; font-size: 13px; text-align: left; }
th { background: #14181d; color: #848e9c; padding: 12px 14px; font-weight: 600; text-transform: uppercase; font-size: 11px; border-bottom: 1px solid #2b313a; white-space: nowrap; }
td { padding: 12px 14px; border-bottom: 1px solid #1e252e; vertical-align: middle; white-space: nowrap; }
tr.main-row:nth-child(4n+1) { background: #181d24; }
tr.sub-row { background: rgba(0, 0, 0, 0.25); border-bottom: 2px solid #2b313a; }
.sub-box { padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; font-size: 12px; border-left: 3px solid #f0b90b; background: rgba(240, 185, 11, 0.04); white-space: normal; }
.sub-item { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.sub-label { color: #848e9c; text-transform: uppercase; font-size: 10px; font-weight: 700; letter-spacing: 0.4px; }
.tag-success { background: rgba(14, 203, 129, 0.15); color: #0ecb81; padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }
.tag-cancel { background: rgba(132, 142, 156, 0.2); color: #848e9c; padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }
.tag-warn { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); padding: 4px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }
.mono { font-family: 'SFMono-Regular', Consolas, Menlo, monospace; font-size: 12px; }
.sub-text { font-size: 11px; color: #848e9c; margin-top: 2px; }

.pnl-positive { color: #0ecb81 !important; font-weight: 800; font-size: 13px; text-shadow: 0 0 8px rgba(14, 203, 129, 0.25); }
.pnl-negative { color: #f87171 !important; font-weight: 800; font-size: 13px; text-shadow: 0 0 8px rgba(248, 113, 113, 0.25); }
.pnl-neutral { color: #848e9c !important; font-size: 13px; }

/* LIMIT ORDER BOXES: GREEN = FILLED, BLUE = UNFILLED/CANCELED */
.limit-rows-wrapper { display: flex; flex-direction: column; gap: 10px; width: 100%; margin-top: 4px; }
.limit-row { display: flex; flex-wrap: wrap; gap: 10px; width: 100%; align-items: stretch; }
.limit-box { flex: 1 1 260px; max-width: 350px; padding: 10px 12px; border-radius: 8px; font-family: 'SFMono-Regular', Consolas, monospace; display: flex; flex-direction: column; gap: 6px; }
.limit-box-green { background: rgba(14, 203, 129, 0.12); border: 1px solid rgba(14, 203, 129, 0.60); border-left: 4px solid #0ecb81; color: #6ee7b7; box-shadow: 0 2px 8px rgba(14, 203, 129, 0.10); }
.limit-box-blue { background: rgba(59, 130, 246, 0.10); border: 1px solid rgba(59, 130, 246, 0.55); border-left: 4px solid #3b82f6; color: #93c5fd; box-shadow: 0 2px 8px rgba(59, 130, 246, 0.08); }
.limit-box-yellow { background: rgba(240, 185, 11, 0.10); border: 1px solid rgba(240, 185, 11, 0.55); border-left: 4px solid #f0b90b; color: #fcd34d; box-shadow: 0 2px 8px rgba(240, 185, 11, 0.08); }
.limit-box-header { font-size: 12px; font-weight: 800; padding-bottom: 5px; border-bottom: 1px solid rgba(255, 255, 255, 0.10); display: flex; justify-content: space-between; align-items: center; }
.limit-box-green .limit-box-header { color: #0ecb81; }
.limit-box-blue .limit-box-header { color: #60a5fa; }
.limit-box-yellow .limit-box-header { color: #f0b90b; }
.limit-box-metrics { font-size: 11px; color: #eaecef; font-weight: 700; display: flex; justify-content: space-between; gap: 8px; }
.limit-box-timings { font-size: 11px; line-height: 1.45; color: #cbd5e1; background: rgba(0, 0, 0, 0.25); padding: 6px 8px; border-radius: 5px; }

/* RADAR & BLOCKED CHIPS */
.candidate-row { cursor: pointer; transition: background 0.2s, border 0.2s; }
.candidate-row:hover { background: rgba(240, 185, 11, 0.08); }
.candidate-active { background: rgba(14, 203, 129, 0.20) !important; border-left: 5px solid #0ecb81 !important; box-shadow: inset 0 0 10px rgba(14, 203, 129, 0.15); }
.candidate-active td { color: #ffffff !important; }
.active-tag { background: #0ecb81; color: #0b0e11; font-size: 10px; font-weight: 800; padding: 2px 7px; border-radius: 4px; margin-left: 6px; text-transform: uppercase; display: inline-block; }
.manual-tag { background: #f0b90b; color: #0b0e11; font-size: 10px; font-weight: 800; padding: 2px 7px; border-radius: 4px; margin-left: 6px; text-transform: uppercase; display: inline-block; }

.candidate-row.row-temp-blocked { background: rgba(240, 185, 11, 0.15) !important; border-left: 5px solid #f0b90b !important; }
.candidate-row.row-temp-blocked td { color: #f0b90b !important; }
.badge-temp-block { background: #f0b90b; color: #0b0e11; font-size: 10px; font-weight: 800; padding: 2px 7px; border-radius: 4px; margin-left: 6px; text-transform: uppercase; display: inline-block; }

.dot-group { display: flex; gap: 10px; justify-content: center; align-items: center; }
.btn-dot { width: 18px; height: 18px; border-radius: 50%; border: 2px solid rgba(255, 255, 255, 0.2); cursor: pointer; transition: transform 0.2s, box-shadow 0.2s; outline: none; }
.btn-dot:hover { transform: scale(1.3); }
.btn-dot-yellow { background: #f0b90b; box-shadow: 0 0 8px rgba(240, 185, 11, 0.6); }
.btn-dot-red { background: #ef4444; box-shadow: 0 0 8px rgba(239, 68, 68, 0.6); }

.chip-blocked { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 8px; font-size: 12px; font-family: 'SFMono-Regular', Consolas, monospace; font-weight: 700; cursor: pointer; transition: all 0.2s; user-select: none; }
.chip-blocked:hover { transform: translateY(-2px); }
.chip-temp { background: rgba(240, 185, 11, 0.15); border: 1px solid rgba(240, 185, 11, 0.4); color: #f0b90b; }
.chip-temp:hover { background: rgba(240, 185, 11, 0.3); box-shadow: 0 0 10px rgba(240, 185, 11, 0.35); }
.chip-perm { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.4); color: #f87171; }
.chip-perm:hover { background: rgba(239, 68, 68, 0.3); box-shadow: 0 0 10px rgba(239, 68, 68, 0.35); }
.chip-close { font-size: 13px; opacity: 0.75; margin-left: 2px; }
.chip-close:hover { opacity: 1; color: #ffffff; }
.btn-see-more { background: #14181d; border: 1px solid #2b313a; color: #f0b90b; padding: 6px 16px; border-radius: 6px; font-size: 11px; font-weight: 700; cursor: pointer; transition: 0.2s; }
.btn-see-more:hover { background: rgba(240, 185, 11, 0.12); border-color: #f0b90b; }

/* TWO-PART SLOT MARGIN CONTROL MODAL */
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.82); z-index: 9999; justify-content: center; align-items: center; padding: 14px; overflow-y: auto; }
.modal-box { background: #1e2329; border: 1px solid #f0b90b; border-radius: 14px; padding: 18px; width: 100%; max-width: 440px; box-shadow: 0 14px 36px rgba(0, 0, 0, 0.75); display: flex; flex-direction: column; gap: 14px; }
.modal-title { font-size: 16px; font-weight: 800; color: #f0b90b; display: flex; justify-content: space-between; align-items: center; }
.modal-sub { font-size: 11.5px; color: #848e9c; line-height: 1.4; }

.default-panel {
    background: #14181d;
    border: 1px solid rgba(56, 189, 248, 0.45);
    border-radius: 10px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.default-panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11.5px;
    font-weight: 800;
    color: #38bdf8;
    letter-spacing: 0.3px;
}
.default-input-row {
    display: flex;
    gap: 8px;
    align-items: stretch;
}
.modal-input {
    width: 100%;
    padding: 9px 12px;
    border-radius: 8px;
    border: 1px solid #2b313a;
    background: #0b0e11;
    color: #ffffff;
    font-size: 16px;
    font-weight: 800;
    font-family: 'SFMono-Regular', Consolas, monospace;
    outline: none;
}
.modal-input:focus { border-color: #f0b90b; }
.btn-apply-all {
    background: linear-gradient(180deg, #38bdf8 0%, #0ea5e9 100%);
    color: #041e2c;
    border: none;
    padding: 0 14px;
    border-radius: 8px;
    font-size: 11.5px;
    font-weight: 800;
    cursor: pointer;
    white-space: nowrap;
}
.btn-apply-all:hover { filter: brightness(1.07); }

.preset-row { display: flex; flex-wrap: wrap; gap: 5px; }
.btn-preset { background: #1e2329; border: 1px solid #2b313a; color: #eaecef; padding: 4px 9px; border-radius: 6px; font-size: 11px; font-weight: 700; cursor: pointer; }
.btn-preset:hover { border-color: #38bdf8; color: #38bdf8; }

.slots-edit-list { display: flex; flex-direction: column; gap: 8px; }
.slot-edit-card {
    background: #14181d;
    border-radius: 9px;
    padding: 9px 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    border: 1px solid #2b313a;
}
.slot-edit-1 { border-left: 4px solid #0ecb81; }
.slot-edit-2 { border-left: 4px solid #38bdf8; }
.slot-edit-3 { border-left: 4px solid #f0b90b; }

.slot-edit-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.slot-edit-badge { font-size: 12px; font-weight: 800; display: flex; align-items: center; gap: 6px; }
.slot-edit-sub { font-size: 10.5px; color: #848e9c; }
.slot-edit-input-wrap { display: flex; align-items: center; gap: 6px; width: 128px; flex-shrink: 0; }
.slot-num-input {
    width: 92px;
    padding: 7px 9px;
    border-radius: 7px;
    border: 1px solid #2b313a;
    background: #0b0e11;
    color: #ffffff;
    font-size: 15px;
    font-weight: 800;
    font-family: 'SFMono-Regular', Consolas, monospace;
    text-align: right;
    outline: none;
}
.slot-num-input:focus { border-color: #0ecb81; }

.modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 2px; }
.btn-modal-cancel { background: #2b313a; color: #eaecef; border: none; padding: 9px 16px; border-radius: 8px; font-size: 13px; font-weight: 700; cursor: pointer; }
.btn-modal-ok { background: #0ecb81; color: #062115; border: none; padding: 9px 22px; border-radius: 8px; font-size: 13px; font-weight: 800; cursor: pointer; }

@media (max-width: 640px) {
    .controls-deck { width: 100%; }
    .btn-toggle { flex: 1 1 100%; min-width: 100%; }
    .mode-container { flex: 1 1 100%; width: 100%; }
    .mode-slot { flex: 1; min-width: 0; }
}
"""
