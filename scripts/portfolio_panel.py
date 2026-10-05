"""Generate a static portfolio status panel (output/portfolio_panel.html).

Reads the snapshot + history written by manage_portfolio():
  output/portfolio_plan.json     — latest plan snapshot (full detail)
  output/portfolio_history.jsonl — one line per portfolio cycle (trend)

Regenerate after each run:
  .venv/Scripts/python scripts/portfolio_panel.py

Stdlib only. Single self-contained HTML file, auto-refreshes every 60s.

P&L is computed from a paper ledger (output/paper_ledger.json +
output/paper_positions.json) that manage_portfolio() fills each cycle at the
run's current prices — see _update_paper_ledger() in main.py.
"""

from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"
SNAPSHOT = OUT_DIR / "portfolio_plan.json"
HISTORY = OUT_DIR / "portfolio_history.jsonl"
PANEL = OUT_DIR / "portfolio_panel.html"


def _esc(v: object) -> str:
    return html.escape(str(v))


def _fmt(amount: float, cur: str) -> str:
    return f"{amount:,.0f} {cur}"


def _fmt_dur(seconds: object) -> str:
    """Format seconds as '45s' / '1m 23s' / '1h 02m'. '—' when unknown."""
    try:
        s = max(0.0, float(seconds))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        m, sec = divmod(int(s), 60)
        return f"{m}m {sec:02d}s"
    h, rem = divmod(int(s), 3600)
    return f"{h}h {rem // 60:02d}m"


def _load_snapshot() -> dict:
    if not SNAPSHOT.exists():
        return {}
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_history(limit: int = 60) -> list[dict]:
    rows: list[dict] = []
    if not HISTORY.exists():
        return rows
    try:
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows[-limit:]


def _axis_chart(
    values: list[float],
    labels: list[str] | None = None,
    *,
    y_title: str = "",
    x_title: str = "Portfolio cycle",
    baseline: float | None = None,
    height: int = 240,
) -> str:
    """Inline SVG line chart with labelled x and y axes, gridlines, ticks,
    and an optional dashed baseline. Empty string when < 2 points."""
    if len(values) < 2:
        return ""
    width = 660
    ml, mr, mt, mb = 74, 14, 14, 44          # margins: y labels, right, top, x labels
    iw, ih = width - ml - mr, height - mt - mb
    labels = labels or [str(i + 1) for i in range(len(values))]

    pool = list(values) + ([baseline] if baseline is not None else [])
    lo, hi = min(pool), max(pool)
    if hi == lo:
        hi, lo = hi + max(abs(hi) * 0.01, 1.0), lo - max(abs(lo) * 0.01, 1.0)
    pad = (hi - lo) * 0.10
    lo, hi = lo - pad, hi + pad
    span = hi - lo

    def yx(v: float) -> float:
        return ml + iw * (v - lo) / span

    def yy(v: float) -> float:
        return mt + ih - ih * (v - lo) / span

    step = iw / (len(values) - 1)
    pts = [(ml + i * step, yy(v)) for i, v in enumerate(values)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{ml},{mt + ih} " + poly + f" {ml + iw:.1f},{mt + ih}"

    up = values[-1] >= values[0]
    stroke = "#6fdc8c" if up else "#f08a8a"

    out = [
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
        f'aria-label="{_esc(y_title or "value")} by {_esc(x_title)}">'
    ]
    # horizontal gridlines + y tick labels
    for k in range(5):
        v = lo + span * k / 4
        y = yy(v)
        out.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + iw}" y2="{y:.1f}" '
            f'class="grid"/>'
            f'<text x="{ml - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">'
            f"{_esc(_compact(v))}</text>"
        )
    # y axis title (rotated)
    if y_title:
        out.append(
            f'<text x="16" y="{mt + ih / 2:.1f}" class="axis-title" '
            f'transform="rotate(-90 16 {mt + ih / 2:.1f})" text-anchor="middle">'
            f"{_esc(y_title)}</text>"
        )
    # x axis line + tick labels (first / middle / last, no overlap)
    out.append(
        f'<line x1="{ml}" y1="{mt + ih}" x2="{ml + iw}" y2="{mt + ih}" class="axis"/>'
    )
    for idx in sorted({0, len(values) // 2, len(values) - 1}):
        x = pts[idx][0]
        anchor = "start" if idx == 0 else ("end" if idx == len(values) - 1 else "middle")
        out.append(
            f'<text x="{x:.1f}" y="{mt + ih + 18}" class="tick" text-anchor="{anchor}">'
            f"{_esc(labels[idx][:16])}</text>"
        )
        out.append(f'<line x1="{x:.1f}" y1="{mt + ih}" x2="{x:.1f}" y2="{mt + ih + 5}" class="axis"/>')
    # baseline (e.g. starting account value)
    if baseline is not None:
        by = yy(baseline)
        out.append(
            f'<line x1="{ml}" y1="{by:.1f}" x2="{ml + iw}" y2="{by:.1f}" class="base"/>'
            f'<text x="{ml + iw}" y="{by - 6:.1f}" class="tick" text-anchor="end">'
            f"start {_esc(_compact(baseline))}</text>"
        )
    # series: area + line + dots
    out.append(f'<polygon points="{area}" fill="{stroke}" opacity="0.10"/>')
    out.append(f'<polyline points="{poly}" fill="none" stroke="{stroke}" stroke-width="2"/>')
    out += [
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{stroke}"/>' for x, y in pts
    ]
    # endpoint callout
    ex, ey = pts[-1]
    out.append(
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4.5" fill="{stroke}" class="end"/>'
        f'<text x="{ex - 6:.1f}" y="{ey - 10:.1f}" class="tick" text-anchor="end">'
        f"{_esc(_compact(values[-1]))}</text>"
    )
    if x_title:
        out.append(
            f'<text x="{ml + iw / 2:.1f}" y="{height - 6}" class="axis-title" '
            f'text-anchor="middle">{_esc(x_title)}</text>'
        )
    out.append("</svg>")
    return "".join(out)


def _compact(v: float) -> str:
    """Short number for axis ticks: 12.3k / 1.20M."""
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if a >= 1_000:
        return f"{v / 1_000:.1f}k"
    if a >= 10:
        return f"{v:,.0f}"
    return f"{v:.2f}"


def _lvl(v) -> str:
    """Render a stop/take price level; '—' when null or junk."""
    if v is None:
        return "—"
    try:
        return _esc(str(float(v)))
    except (TypeError, ValueError):
        return "—"


def _action_class(action: str) -> str:
    return {
        "open": "buy", "increase": "buy",
        "close": "sell", "decrease": "sell",
    }.get(action, "hold")


def build() -> str:
    snap = _load_snapshot()
    hist = _load_history()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not snap or not snap.get("plan"):
        body = (
            "<div class='card empty'>"
            "<h2>No portfolio plan yet</h2>"
            "<p>manage_portfolio has not produced a plan. "
            "Run the flow once, then regenerate this panel:</p>"
            "<p><code>.venv/Scripts/python scripts/portfolio_panel.py</code></p>"
            f"<p class='muted'>Generated {now} — looking for output/portfolio_plan.json</p>"
            "</div>"
        )
        return _page("Portfolio — no data", body, cur="")

    plan: dict = snap["plan"]
    actions: list[dict] = plan.get("actions", []) or []
    cur = str(snap.get("base_currency", "usd")).upper()
    account = float(snap.get("account_size", 0) or 0)
    total_target = float(plan.get("total_target", 0) or 0)
    exposure = float(plan.get("total_exposure_pct", 0) or 0)
    cash = float(plan.get("cash_remaining", 0) or 0)
    max_total = float(snap.get("max_total_exposure_pct", 60) or 60)
    max_single = float(snap.get("max_single_position_pct", 20) or 20)
    cycle = snap.get("portfolio_cycle", "?")
    saved = _esc(str(snap.get("saved_utc", "?")))
    rationale = _esc(str(plan.get("rationale", "")))
    opps: list[dict] = snap.get("opportunities", []) or []
    score_by_coin = {o.get("coin_id", ""): o.get("score", "") for o in opps}

    open_acts = [a for a in actions if float(a.get("target", 0) or 0) > 0]
    single_cap = account * max_single / 100
    expos_pct_of_cap = min(100.0, exposure / max_total * 100) if max_total else 0
    plan_dur = _fmt_dur(snap.get("duration_s"))
    pnl_data = snap.get("pnl") or {}
    portfolio_worth = float(pnl_data.get("equity", account) or account)

    # --- KPI cards ---
    kpis = (
        "<div class='kpis'>"
        f"<div class='card kpi'><span>Total invested</span><b>{_fmt(total_target, cur)}</b></div>"
        f"<div class='card kpi'><span>Exposure</span><b>{exposure:.1f}% <small>of {max_total:.0f}% cap</small></b>"
        f"<div class='bar'><div style='width:{expos_pct_of_cap:.0f}%'></div></div></div>"
        f"<div class='card kpi'><span>Cash remaining</span><b>{_fmt(cash, cur)}</b></div>"
        f"<div class='card kpi'><span>Positions</span><b>{len(open_acts)} <small>open / {len(actions)} actions</small></b></div>"
        f"<div class='card kpi'><span>Portfolio worth</span><b>{_fmt(portfolio_worth, cur)}</b></div>"
        f"<div class='card kpi'><span>Single-coin cap</span><b>{_fmt(single_cap, cur)}</b></div>"
        f"<div class='card kpi'><span>Portfolio time</span><b>⏱ {plan_dur}</b></div>"
        "</div>"
    )

    # --- positions table ---
    rows = []
    for a in sorted(actions, key=lambda x: float(x.get("target", 0) or 0), reverse=True):
        act = str(a.get("action", "hold"))
        tgt = float(a.get("target", 0) or 0)
        cur_amt = float(a.get("current", 0) or 0)
        delta = tgt - cur_amt
        d_cls = "pos" if delta > 0 else ("neg" if delta < 0 else "")
        d_txt = f"{delta:+,.0f}" if delta else "—"
        rows.append(
            "<tr>"
            f"<td><b>{_esc(a.get('symbol', '?'))}</b><br><small>{_esc(a.get('coin_id', ''))}</small></td>"
            f"<td><span class='pill {_action_class(act)}'>{_esc(act)}</span></td>"
            f"<td class='num'>{cur_amt:,.0f}</td>"
            f"<td class='num'>{tgt:,.0f}</td>"
            f"<td class='num {d_cls}'>{d_txt}</td>"
            f"<td class='num'>{(tgt / account * 100 if account else 0):.1f}%</td>"
            f"<td class='num'>{_lvl(a.get('stop_loss'))}</td>"
            f"<td class='num'>{_lvl(a.get('take_profit'))}</td>"
            f"<td>{_esc(a.get('sl_tp_source', '—'))}</td>"
            f"<td class='num'>{_esc(score_by_coin.get(a.get('coin_id', ''), '—'))}</td>"
            f"<td class='reason'>{_esc(a.get('reason', ''))}</td>"
            "</tr>"
        )
    positions = (
        "<div class='card'><h2>Positions — cycle #%s</h2>" % _esc(cycle)
        + "<table><thead><tr><th>Coin</th><th>Action</th><th>Current</th>"
        "<th>Target</th><th>Delta</th><th>Acct %</th><th>Stop</th><th>Take</th><th>SL/TP src</th><th>Score</th><th>Why</th>"
        "</tr></thead><tbody>"
        + ("".join(rows) if rows else "<tr><td colspan=11>No actions in plan.</td></tr>")
        + "</tbody></table>"
        + f"<p class='muted'>Amounts in {cur}. Delta = rebalance flow (target − current), not profit. "
        "Stop/Take = 0.5x-ATR stop-loss and 1x-ATR take-profit price levels (vs currency), informational — no orders are placed. SL/TP src: explicit = crew numbers within 20% kept, computed = deterministic ATR values used, none = unavailable.</p></div>"
    )

    # --- P&L from the paper ledger (filled at current price each cycle) ---
    if pnl_data:
        total_pnl = float(pnl_data.get("total_pnl", 0) or 0)
        realized = float(pnl_data.get("realized_pnl", 0) or 0)
        unrealized = float(pnl_data.get("unrealized_pnl", 0) or 0)
        equity = float(pnl_data.get("equity", 0) or 0)
        invested = float(pnl_data.get("invested", 0) or 0)
        n_trades = pnl_data.get("n_trades", 0)
        t_cls = "pos" if total_pnl > 0 else ("neg" if total_pnl < 0 else "")
        open_pos = pnl_data.get("open_positions") or []
        pos_rows = "".join(
            "<tr><td>%s</td><td class='num'>%.6g</td><td class='num'>%.4g</td>"
            "<td class='num'>%.4g</td><td class='num'>%.2f</td>"
            "<td class='num %s'>%+.2f</td></tr>" % (
                _esc(p.get("coin_id", "?")), float(p.get("qty", 0) or 0),
                float(p.get("avg_cost", 0) or 0), float(p.get("price", 0) or 0),
                float(p.get("market_value", 0) or 0),
                "pos" if (p.get("unrealized_pnl") or 0) > 0 else ("neg" if (p.get("unrealized_pnl") or 0) < 0 else ""),
                float(p.get("unrealized_pnl", 0) or 0),
            ) for p in open_pos
        ) or "<tr><td colspan=6>No open paper positions.</td></tr>"
        pnl = (
            "<div class='card'><h2>Profit / loss (paper ledger)</h2>"
            "<div class='kpis'>"
            f"<div class='card kpi'><span>Total P&amp;L</span><b class='{t_cls}'>{total_pnl:+,.2f} {cur}</b></div>"
            f"<div class='card kpi'><span>Realized</span><b>{realized:+,.2f} {cur}</b></div>"
            f"<div class='card kpi'><span>Unrealized</span><b>{unrealized:+,.2f} {cur}</b></div>"
            f"<div class='card kpi'><span>Equity</span><b>{equity:,.2f} {cur}</b></div>"
            f"<div class='card kpi'><span>Invested</span><b>{invested:,.2f} {cur}</b></div>"
            f"<div class='card kpi'><span>Trades logged</span><b>{n_trades}</b></div>"
            "</div>"
            "<table><thead><tr><th>Coin</th><th>Qty</th><th>Avg cost</th><th>Last</th><th>Value</th><th>UPL</th></tr></thead><tbody>"
            + pos_rows + "</tbody></table>"
            "<p class='muted'>Filled at the run's current prices (same snapshot used for SL/TP). Realized P&amp;L accrues on sells/closes; unrealized marks open positions to the latest run price.</p></div>"
        )
    else:
        pnl = (
            "<div class='card warn'><h2>Profit / loss</h2>"
            "<p>No ledger data yet — P&amp;L is computed after the first portfolio cycle fills the paper ledger (output/paper_ledger.json).</p></div>"
        )

# --- history: total portfolio worth over cycles ---
    hist_block = "<div class='card'><h2>Portfolio value over time</h2>"
    if len(hist) >= 2:
        equity_series = []
        labels = []
        for h in hist:
            eq = h.get("equity")
            if eq is None:
                # pre-ledger cycles: fall back to account + unrealized-free estimate
                eq = float(h.get("account_size", 0) or 0) - float(h.get("cash_remaining", 0) or 0) + float(h.get("cash_remaining", 0) or 0)
            equity_series.append(float(eq or 0))
            labels.append("#%s %s" % (h.get("cycle", "?"), str(h.get("ts", ""))[5:16]))
        start_val = float(hist[0].get("account_size", 0) or 0) or None
        chart = _axis_chart(
            equity_series,
            labels,
            y_title="Total portfolio worth (%s)" % cur,
            x_title="Portfolio cycle",
            baseline=start_val,
        )
        if chart:
            gain = equity_series[-1] - equity_series[0]
            pct = (gain / equity_series[0] * 100) if equity_series[0] else 0.0
            hist_block += (
                "<p class='muted'>Total portfolio worth = account value + open-position gains, "
                "per portfolio cycle (dashed line = starting account value).</p>"
                + chart
                + "<p class='muted'>Cycle-over-cycle change: <b class='%s'>%+.2f %s (%+.2f%%)</b></p>"
                % ("pos" if gain >= 0 else "neg", gain, cur, pct)
            )
        else:
            hist_block += "<p class='muted'>Not enough cycles to plot.</p>"
        hist_block += (
            "<table><thead><tr><th>Cycle</th><th>Time (UTC)</th><th>Worth</th><th>Invested</th>"
            "<th>Exposure</th><th>Cash</th><th>Positions</th><th>Took</th><th>Total P&L</th></tr></thead><tbody>"
        )
        for h in reversed(hist[-12:]):
            eq_val = float(h.get("equity", 0) or 0)
            hist_block += (
                "<tr><td>#%s</td><td><small>%s</small></td><td class='num'>%s</td>"
                "<td class='num'>%s</td>"
                "<td class='num'>%.1f%%</td><td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td><td class='num %s'>%s</td></tr>"
                % (
                    _esc(h.get("cycle", "?")), _esc(str(h.get("ts", ""))[:16]),
                    f"{eq_val:,.0f}",
                    f"{float(h.get('total_target', 0) or 0):,.0f}",
                    float(h.get("total_exposure_pct", 0) or 0),
                    f"{float(h.get('cash_remaining', 0) or 0):,.0f}",
                    _esc(h.get("n_positions", "?")),
                    _esc(_fmt_dur(h.get("duration_s"))),
                    ("pos" if float(h.get("total_pnl", 0) or 0) > 0 else ("neg" if float(h.get("total_pnl", 0) or 0) < 0 else "")),
                    f"{float(h.get('total_pnl', 0) or 0):+,.2f}",
                )
            )
        hist_block += "</tbody></table></div>"
    else:
        hist_block += "<p class='muted'>Only %d cycle(s) logged — the chart appears after 2+ cycles.</p></div>" % len(hist)

    # --- scout snapshot ---
    scout_block = "<div class='card'><h2>Signal snapshot</h2>"
    if opps:
        top = sorted(opps, key=lambda o: float(o.get("score", 0) or 0), reverse=True)[:10]
        scout_block += ("<table><thead><tr><th>Coin</th><th>Score</th><th>Risk</th><th>Reason</th>"
                        "</tr></thead><tbody>")
        for o in top:
            scout_block += ("<tr><td><b>%s</b><br><small>%s</small></td><td class='num'>%s</td>"
                            "<td>%s</td><td class='reason'>%s</td></tr>"
                            % (_esc(o.get("symbol", "?")), _esc(o.get("coin_id", "")),
                               _esc(o.get("score", "")), _esc(o.get("risk_tier", "")),
                               _esc(o.get("reason", ""))))
        scout_block += "</tbody></table>"
    else:
        scout_block += "<p class='muted'>No opportunities in snapshot.</p>"
    scout_block += "</div>"

    rationale_block = (
        f"<div class='card'><h2>Manager rationale</h2><p>{rationale or '—'}</p>"
        f"<p class='muted'>Saved {saved} · Account {_fmt(account, cur)} · "
        f"caps {max_total:.0f}% total / {max_single:.0f}% single · panel generated {now}</p></div>"
    )

    return _page(f"Portfolio — cycle #{cycle} — {cur}", kpis + positions + pnl + hist_block + scout_block + rationale_block, cur)


def _page(title: str, body: str, cur: str) -> str:
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>""" + _esc(title) + """</title>
<style>
:root { color-scheme: dark; }
body { font-family: inherit; margin: 0; padding: 16px; max-width: 1100px;
  color: var(--foreground, #e8e8e8); background: transparent; }
h1 { font-size: 1.3em; margin: 0 0 12px; }
h2 { font-size: 1em; margin: 0 0 10px; opacity: .9; }
.card { background: var(--card, #161616); border: 1px solid var(--border, #333);
  border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px; }
.kpi span { font-size: .75em; opacity: .65; display: block; margin-bottom: 4px; }
.kpi b { font-size: 1.15em; } .kpi small { font-weight: normal; opacity: .6; font-size: .75em; }
.bar { height: 6px; border-radius: 3px; background: var(--border, #333); margin-top: 8px; overflow: hidden; }
.bar div { height: 100%; background: var(--accent, #4da3ff); }
table { width: 100%; border-collapse: collapse; font-size: .85em; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border, #2a2a2a); vertical-align: top; }
th { opacity: .6; font-weight: 600; font-size: .8em; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.reason { opacity: .8; } small { opacity: .6; }
.pill { font-size: .75em; font-weight: 700; padding: 2px 8px; border-radius: 20px; text-transform: uppercase; }
.pill.buy { background: #123d22; color: #6fdc8c; }
.pill.sell { background: #471818; color: #f08a8a; }
.pill.hold { background: #2a2a2a; color: #aaa; }
.pos { color: #6fdc8c; } .neg { color: #f08a8a; }
.muted { opacity: .55; font-size: .8em; }
.warn { border-color: #6b5a2a; } .warn h2 { color: #e8c15a; }
.empty { text-align: center; padding: 40px 20px; }
code { background: #00000055; padding: 2px 6px; border-radius: 4px; }
.chart { width: 100%; height: auto; margin: 10px 0 12px; display: block; }
.chart .grid { stroke: var(--border, #2a2a2a); stroke-width: 1; stroke-dasharray: 3 4; }
.chart .axis { stroke: var(--border, #555); stroke-width: 1.2; }
.chart .base { stroke: var(--muted-foreground, #8a8a8a); stroke-width: 1.2; stroke-dasharray: 6 5; opacity: .75; }
.chart .tick { fill: var(--muted-foreground, #9a9a9a); font-size: 11px; font-family: inherit; }
.chart .axis-title { fill: var(--muted-foreground, #b0b0b0); font-size: 11.5px; font-weight: 600; font-family: inherit; }
.chart circle { opacity: .9; }
.chart circle.end { opacity: 1; stroke: var(--card, #161616); stroke-width: 2; }
</style></head><body>
<h1>""" + _esc(title) + """</h1>
""" + body + """
</body></html>
"""


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    PANEL.write_text(build(), encoding="utf-8")
    snap = _load_snapshot()
    n = len((snap.get("plan", {}) or {}).get("actions", [])) if snap else 0
    print(f"panel -> {PANEL}  ({n} actions, {'snapshot found' if snap else 'NO SNAPSHOT'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
