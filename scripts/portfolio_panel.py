"""Generate a static portfolio status panel (output/portfolio_panel.html).

Reads the snapshot + history written by manage_portfolio():
  output/portfolio_plan.json     — latest plan snapshot (full detail)
  output/portfolio_history.jsonl — one line per portfolio cycle (trend)

Regenerate after each run:
  .venv/Scripts/python scripts/portfolio_panel.py

Stdlib only. Single self-contained HTML file, auto-refreshes every 60s.

Honest limitation: this bot produces paper allocation plans (current -> target
amounts per coin). It does NOT track entry prices or fills, so per-coin
realized P&L cannot be computed — the panel shows allocation state and
cycle-over-cycle changes, and says so where P&L would go.
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


def _sparkline(values: list[float], width: int = 560, height: int = 120) -> str:
    """Minimal inline SVG line chart. Empty string when < 2 points."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 8
    step = (width - 2 * pad) / (len(values) - 1)
    pts = [
        f"{pad + i * step:.1f},{height - pad - (v - lo) / span * (height - 2 * pad):.1f}"
        for i, v in enumerate(values)
    ]
    return (
        f'<svg viewBox="0 0 {width} {height}" class="spark" role="img">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="var(--accent)" stroke-width="2"/>'
        + "".join(
            f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="2.5" class="dot"/>'
            for p in pts
        )
        + "</svg>"
    )


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

    # --- KPI cards ---
    kpis = (
        "<div class='kpis'>"
        f"<div class='card kpi'><span>Total invested</span><b>{_fmt(total_target, cur)}</b></div>"
        f"<div class='card kpi'><span>Exposure</span><b>{exposure:.1f}% <small>of {max_total:.0f}% cap</small></b>"
        f"<div class='bar'><div style='width:{expos_pct_of_cap:.0f}%'></div></div></div>"
        f"<div class='card kpi'><span>Cash remaining</span><b>{_fmt(cash, cur)}</b></div>"
        f"<div class='card kpi'><span>Positions</span><b>{len(open_acts)} <small>open / {len(actions)} actions</small></b></div>"
        f"<div class='card kpi'><span>Account</span><b>{_fmt(account, cur)}</b></div>"
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
            f"<td class='num'>{_esc(score_by_coin.get(a.get('coin_id', ''), '—'))}</td>"
            f"<td class='reason'>{_esc(a.get('reason', ''))}</td>"
            "</tr>"
        )
    positions = (
        "<div class='card'><h2>Positions — cycle #%s</h2>" % _esc(cycle)
        + "<table><thead><tr><th>Coin</th><th>Action</th><th>Current</th>"
        "<th>Target</th><th>Delta</th><th>Acct %</th><th>Score</th><th>Why</th>"
        "</tr></thead><tbody>"
        + ("".join(rows) if rows else "<tr><td colspan=8>No actions in plan.</td></tr>")
        + "</tbody></table>"
        + f"<p class='muted'>Amounts in {cur}. Delta = rebalance flow (target − current), not profit.</p></div>"
    )

    # --- P&L honesty box ---
    pnl = (
        "<div class='card warn'><h2>Profit / loss</h2>"
        "<p>Not tracked. This bot writes paper allocation plans "
        "(current → target amounts) — it records no entry prices or fills, "
        "so per-coin and total P&amp;L cannot be computed from plan data. "
        "What <i>is</i> meaningful here: cycle-over-cycle drift in Total invested "
        "below, and per-coin Delta flow in the table above.</p></div>"
    )

    # --- history ---
    hist_block = "<div class='card'><h2>Cycle history</h2>"
    if len(hist) >= 2:
        hist_block += (
            "<p class='muted'>Total invested (%s) per portfolio cycle</p>" % cur
            + _sparkline([float(h.get("total_target", 0) or 0) for h in hist])
            + "<table><thead><tr><th>Cycle</th><th>Time (UTC)</th><th>Invested</th>"
            "<th>Exposure</th><th>Cash</th><th>Positions</th><th>Took</th></tr></thead><tbody>"
        )
        for h in reversed(hist[-12:]):
            hist_block += (
                "<tr><td>#%s</td><td><small>%s</small></td><td class='num'>%s</td>"
                "<td class='num'>%.1f%%</td><td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td></tr>"
                % (
                    _esc(h.get("cycle", "?")), _esc(str(h.get("ts", ""))[:16]),
                    f"{float(h.get('total_target', 0) or 0):,.0f}",
                    float(h.get("total_exposure_pct", 0) or 0),
                    f"{float(h.get('cash_remaining', 0) or 0):,.0f}",
                    _esc(h.get("n_positions", "?")),
                    _esc(_fmt_dur(h.get("duration_s"))),
                )
            )
        hist_block += "</tbody></table></div>"
    else:
        hist_block += "<p class='muted'>Only %d cycle(s) logged — trend appears after 2+ cycles.</p></div>" % len(hist)

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
.spark { width: 100%; height: 120px; margin: 6px 0 12px; }
.spark .dot { fill: var(--accent, #4da3ff); }
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
