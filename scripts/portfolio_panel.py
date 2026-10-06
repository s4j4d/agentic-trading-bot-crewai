"""Generate a static portfolio status panel (output/portfolio_panel.html).

Reads the snapshot + history written by manage_portfolio():
  output/portfolio_plan.json     — latest plan snapshot (full detail)
  output/portfolio_history.jsonl — one line per portfolio cycle (trend)

Regenerate after each run:
  .venv/Scripts/python scripts/portfolio_panel.py

Stdlib only. Single self-contained HTML file, auto-refreshes every 60s.

P&L comes from a paper ledger (output/paper_ledger.json +
output/paper_positions.json) that manage_portfolio() fills each cycle at the
run's current prices — see _update_paper_ledger() in main.py.

Equity there is `account_size + realized + unrealized`: cash is
(account_size - invested), so adding back the market value of open positions
cancels the `invested` term. It used to subtract `invested` a second time,
which understated equity by exactly the amount sitting in open positions the
moment any capital was deployed — fixed in main.py, pinned by
tests/test_paper_ledger_equity.py.
"""

from __future__ import annotations

import html
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"
SNAPSHOT = OUT_DIR / "portfolio_plan.json"
HISTORY = OUT_DIR / "portfolio_history.jsonl"
PANEL = OUT_DIR / "portfolio_panel.html"

HISTORY_WINDOW = 60          # cycles plotted/listed on the page
SIGNAL_SLICES = 10           # opportunities shown in the signal table


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _esc(v: object) -> str:
    return html.escape(str(v))


def _fmt(amount: float, cur: str) -> str:
    return f"{amount:,.0f} {cur}"


def _num(v: object, default: float = 0.0) -> float:
    """float() that never raises — snapshot fields are LLM/fill-in shaped."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _int(v: object, default: object = 0) -> object:
    """int() that never raises; passes `default` through when junk arrives."""
    try:
        return int(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


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


def _lvl(v: object) -> str:
    """Render a stop/take price level; '—' when null or junk."""
    if v is None:
        return "—"
    try:
        return _esc(str(float(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"


def _action_class(action: object) -> str:
    return {
        "open": "buy", "increase": "buy",
        "close": "sell", "decrease": "sell",
    }.get(str(action).lower(), "hold")


def _pnl_class(v: object) -> str:
    n = _num(v)
    return "pos" if n > 0 else ("neg" if n < 0 else "")


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------


def _load_snapshot() -> tuple[dict, str | None]:
    """(snapshot, problem). `problem` is set when the file exists but cannot
    be used — an unreadable snapshot must not masquerade as 'no data yet'."""
    if not SNAPSHOT.exists():
        return {}, "missing"
    try:
        raw = SNAPSHOT.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"unreadable ({exc.strerror or exc})"
    try:
        snap = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON (line {exc.lineno})"
    if not isinstance(snap, dict):
        return {}, "not a JSON object"
    return snap, None


def _load_history(limit: int = HISTORY_WINDOW) -> list[dict]:
    rows: list[dict] = []
    if not HISTORY.exists():
        return rows
    try:
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        return []
    return rows[-limit:]


def _first_account_size() -> float | None:
    """Account size of the EARLIEST logged cycle.

    The page charts only the last HISTORY_WINDOW rows, so hist[0] silently
    re-bases once the bot has run longer than that. The dashed 'start'
    baseline must stay anchored to the true first cycle.
    """
    row = _load_history(limit=10 ** 9)
    return row[0].get("account_size") if row else None


def _age(saved_utc: object, now: datetime) -> str:
    """Human age of the snapshot: 'just now' / '4m old' / '3d old' / '—'."""
    try:
        ts = datetime.fromisoformat(str(saved_utc).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    secs = (now - ts).total_seconds()
    if secs < 0:
        return "in the future"
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m old"
    if secs < 86400:
        return f"{int(secs // 3600)}h old"
    return f"{int(secs // 86400)}d old"


def _stamp(ts: object) -> str:
    """'2026-10-06 08:32 UTC' from an ISO string; '—' when absent/junk."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(ts)[:16].replace("T", " ") or "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _cycle_label(row: dict) -> str:
    """'#12 10-06 08:32' — short enough for an x-axis tick, no stray 'T'."""
    cyc = _esc(row.get("cycle", "?"))
    ts = str(row.get("ts", ""))
    try:
        stamp = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        stamp = ts[5:16].replace("T", " ")
    return f"#{cyc} {stamp}"


def _row_worth(row: dict) -> float:
    """Total portfolio worth for one history row.

    Rows predating the paper ledger have no equity key; fall back to the
    account size. Shared by the chart and the table so the two can never
    disagree, and a genuine equity of 0 is never mistaken for "missing".
    """
    eq = row.get("equity")
    if eq is None:
        return _num(row.get("account_size"))
    return _num(eq, _num(row.get("account_size")))


# ---------------------------------------------------------------------------
# SVG chart + donut
# ---------------------------------------------------------------------------


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
    # Tolerate a short/None label list rather than IndexError-ing on it.
    if not labels or len(labels) < len(values):
        labels = list(labels or []) + [str(i + 1) for i in range(len(labels or []), len(values))]

    pool = list(values) + ([baseline] if baseline is not None else [])
    lo, hi = min(pool), max(pool)
    flat = lo == hi          # nothing moved: ticks would all round to the same string
    flat_value = lo          # remember it before padding shifts lo/hi
    if hi == lo:
        hi, lo = hi + max(abs(hi) * 0.01, 1.0), lo - max(abs(lo) * 0.01, 1.0)
    pad = (hi - lo) * 0.10
    lo, hi = lo - pad, hi + pad
    span = hi - lo

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
    for k in ([2] if flat else [0, 1, 2, 3, 4]):
        v = lo + span * k / 4
        y = yy(v)
        out.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + iw}" y2="{y:.1f}" '
            f'class="grid"/>'
            f'<text x="{ml - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">'
            f"{_esc(_compact(v))}</text>"
        )
    if flat:
        out.append(
            f'<text x="{ml + iw / 2:.1f}" y="{mt + 14:.1f}" class="tick" '
            f'text-anchor="middle">unchanged at {_esc(_compact(flat_value))}</text>'
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
            f"{_esc(str(labels[idx])[:16])}</text>"
        )
        out.append(
            f'<line x1="{x:.1f}" y1="{mt + ih}" x2="{x:.1f}" y2="{mt + ih + 5}" class="axis"/>'
        )
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
    out += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{stroke}"/>' for x, y in pts]
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


_PALETTE = [
    "#4da3ff", "#6fdc8c", "#e8c15a", "#b48cff", "#f08a8a",
    "#5fd0d0", "#e8a15a", "#7fa0ff", "#d06f9e", "#9a9a9a",
]
_CASH_COLOR = "#4a4a4a"


def _arc(cx: float, cy: float, r: float, a0: float, a1: float) -> str:
    """SVG wedge from a0 to a1. A full turn is split in half because
    identical start/end points make an SVG arc render as nothing."""
    sweep = a1 - a0
    if sweep >= 2 * math.pi - 1e-6:
        mid = a0 + math.pi
        return _arc(cx, cy, r, a0, mid) + _arc(cx, cy, r, mid, a1)
    large = 1 if sweep > math.pi else 0
    x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
    return (
        f"M {cx:.1f} {cy:.1f} L {x0:.1f} {y0:.1f} "
        f"A {r:.1f} {r:.1f} 0 {large} 1 {x1:.1f} {y1:.1f} Z"
    )


def _allocation_donut(slices: list[tuple[str, float, str]], size: int = 150) -> str:
    """SVG donut. slices = [(label, value, color)]. Empty string if total<=0."""
    total = sum(v for _, v, _ in slices)
    if total <= 0:
        return ""
    r = size / 2 - 14
    cx = cy = size / 2
    angle = -math.pi / 2
    wedges = []
    for label, v, color in slices:
        if v <= 0:
            continue
        a0 = angle
        a1 = angle + (v / total) * 2 * math.pi
        wedges.append(
            f'<path d="{_arc(cx, cy, r, a0, a1)}" fill="{color}" opacity="0.9">'
            f'<title>{_esc(label)}: {v:,.0f} ({v / total * 100:.1f}%)</title></path>'
        )
        angle = a1
    n_open = sum(1 for label, _, _ in slices if label != "cash")
    inner = r * 0.55
    return (
        f'<svg viewBox="0 0 {size} {size}" class="donut" role="img" '
        f'aria-label="allocation by position">'
        + "".join(wedges)
        + f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{inner:.1f}"/>'
        + f'<text x="{cx:.1f}" y="{cy - 2:.1f}" class="tick" text-anchor="middle">{n_open}</text>'
        + f'<text x="{cx:.1f}" y="{cy + 14:.1f}" class="tick" text-anchor="middle">open</text>'
        + "</svg>"
    )


# ---------------------------------------------------------------------------
# page sections
# ---------------------------------------------------------------------------


def _strip(snap: dict, now: datetime, cur: str) -> str:
    age = _age(snap.get("saved_utc"), now)
    stale = age == "—" or age.endswith("d old") or age == "in the future"
    badge = (
        f"<span class='dot {'warn' if stale else 'live'}'></span>"
        f"<span>snapshot <b>{_esc(age)}</b></span>"
    )
    return (
        "<div class='strip'>"
        f"{badge}"
        f"<span>cycle <b>#{_esc(snap.get('portfolio_cycle', '?'))}</b></span>"
        f"<span>saved <b>{_esc(_stamp(snap.get('saved_utc')))}</b></span>"
        f"<span>base <b>{_esc(cur)}</b></span>"
        f"<span>caps <b>{_num(snap.get('max_total_exposure_pct'), 60):.0f}% total"
        f" / {_num(snap.get('max_single_position_pct'), 20):.0f}% single</b></span>"
        f"<span>page built <b>{now.strftime('%Y-%m-%d %H:%M UTC')}</b></span>"
        f"<span>auto-refresh <b>60s</b></span>"
        "</div>"
    )


def _kpis(plan: dict, snap: dict, actions: list[dict], pnl: dict, cur: str) -> str:
    account = _num(snap.get("account_size"))
    total_target = _num(plan.get("total_target"))
    exposure = _num(plan.get("total_exposure_pct"))
    cash = _num(plan.get("cash_remaining"))
    max_total = _num(snap.get("max_total_exposure_pct"), 60) or 60
    max_single = _num(snap.get("max_single_position_pct"), 20)
    n_open = sum(1 for a in actions if _num(a.get("target")) > 0)
    raw_equity = pnl.get("equity")
    worth = account if raw_equity is None else _num(raw_equity, account)
    cap_fill = min(100.0, exposure / max_total * 100)
    return (
        "<div class='kpis'>"
        f"<div class='card kpi'><span>Portfolio worth</span><b>{_fmt(worth, cur)}</b></div>"
        f"<div class='card kpi'><span>Invested</span><b>{_fmt(total_target, cur)}</b></div>"
        f"<div class='card kpi'><span>Cash</span><b>{_fmt(cash, cur)}</b></div>"
        f"<div class='card kpi'><span>Exposure</span><b>{exposure:.1f}%</b>"
        f"<div class='bar'><div style='width:{cap_fill:.0f}%'></div></div>"
        f"<small>of {max_total:.0f}% total cap</small></div>"
        f"<div class='card kpi'><span>Positions</span><b>{n_open}</b>"
        f"<small>{len(actions)} action(s) this cycle</small></div>"
        f"<div class='card kpi'><span>Per-coin cap</span>"
        f"<b>{_fmt(account * max_single / 100, cur)}</b>"
        f"<small>{max_single:.0f}% of account</small></div>"
        f"<div class='card kpi'><span>Cycle time</span><b>{_fmt_dur(snap.get('duration_s'))}</b>"
        f"<small>portfolio step</small></div>"
        "</div>"
    )


def _allocation(actions: list[dict], cash: float) -> str:
    ranked = sorted(actions, key=lambda a: _num(a.get("target")), reverse=True)
    slices = [
        (
            str(a.get("symbol") or a.get("coin_id") or "?"),
            _num(a.get("target")),
            _PALETTE[i % len(_PALETTE)],
        )
        for i, a in enumerate(ranked)
        if _num(a.get("target")) > 0
    ]
    if cash > 0:
        slices.append(("cash", cash, _CASH_COLOR))
    donut = _allocation_donut(slices)
    total = sum(v for _, v, _ in slices)
    # proportional bar per slice: denser and more readable than a bare legend
    legend = "".join(
        f"<li><span class='sw' style='background:{c}'></span>"
        f"<span class='nm'>{_esc(l)}</span>"
        f"<span class='track'><span style='width:{(v / total * 100) if total else 0:.1f}%;"
        f"background:{c}'></span></span>"
        f"<b>{v:,.0f}<small>{(' · %.1f%%' % (v / total * 100)) if total else ''}</small></b></li>"
        for l, v, c in slices
    )
    body = donut or "<p class='muted'>Nothing allocated — 100% cash.</p>"
    return (
        "<div class='card'><h2>Allocation</h2><div class='alloc'>"
        + body
        + (f"<ul class='legend'>{legend}</ul>" if legend else "")
        + "</div></div>"
    )


def _positions(actions: list[dict], score_by_coin: dict, account: float, cur: str) -> str:
    rows = []
    for a in sorted(actions, key=lambda x: _num(x.get("target")), reverse=True):
        tgt = _num(a.get("target"))
        now_amt = _num(a.get("current"))
        delta = tgt - now_amt
        weight = (tgt / account * 100) if account else 0.0
        rows.append(
            "<tr>"
            f"<td><b>{_esc(a.get('symbol') or a.get('coin_id') or '?')}</b>"
            f"<br><small>{_esc(a.get('coin_id', ''))}</small></td>"
            f"<td><span class='pill {_action_class(a.get('action', 'hold'))}'>"
            f"{_esc(a.get('action', 'hold'))}</span></td>"
            f"<td class='num'>{now_amt:,.0f}</td>"
            f"<td class='num'>{tgt:,.0f}</td>"
            f"<td class='num {_pnl_class(delta) if delta else ''}'>"
            f"{f'{delta:+,.0f}' if delta else '—'}</td>"
            f"<td class='num'>{weight:.1f}%</td>"
            f"<td class='num'>{_lvl(a.get('stop_loss'))}</td>"
            f"<td class='num'>{_lvl(a.get('take_profit'))}</td>"
            f"<td>{_esc(a.get('sl_tp_source') or '—')}</td>"
            f"<td class='num'>{_esc(score_by_coin.get(a.get('coin_id', ''), '—'))}</td>"
            f"<td class='reason'>{_esc(a.get('reason', ''))}</td>"
            "</tr>"
        )
    table = (
        "<div class='scroll'><table><thead><tr><th>Coin</th><th>Action</th>"
        "<th>Current</th><th>Target</th><th>Delta</th>"
        "<th>Acct<br>%</th>"
        "<th>Stop</th><th>Take</th>"
        "<th>SL/TP<br>src</th>"
        "<th>Score</th><th>Why</th>"
        "</tr></thead><tbody>"
        + ("".join(rows) if rows else "<tr><td colspan='11'>No actions in plan.</td></tr>")
        + "</tbody></table></div>"
    )
    return (
        "<div class='card'><h2>Planned actions</h2>"
        + table
        + f"<p class='muted'>Amounts in {cur}. <b>Delta</b> is the rebalance flow "
        "(target − current), not profit — a positive delta is capital to deploy, "
        "not a gain. <b>Stop</b>/<b>Take</b> are 0.5x-ATR / 1x-ATR price levels in "
        "the quote currency, informational: no orders are placed. "
        "<b>SL/TP src</b>: explicit = crew-supplied level within 20% of ATR kept, "
        "computed = deterministic ATR used, none = unavailable (e.g. rate-limited). "
        "<b>Score</b> is the scout/analyst score for that coin.</p></div>"
    )


def _pnl_section(pnl: dict, cur: str) -> str:
    if not pnl:
        return (
            "<div class='card warn'><h2>Profit / loss</h2>"
            "<p>No ledger data yet — the paper ledger is filled at the end of the "
            "first portfolio cycle that trades. Until then, treat the exposure "
            "figures as the plan, not a result.</p></div>"
        )
    open_pos = pnl.get("open_positions") or []
    rows = "".join(
        "<tr><td>%s</td><td class='num'>%.6g</td><td class='num'>%.4g</td>"
        "<td class='num'>%.4g</td><td class='num'>%.2f</td>"
        "<td class='num %s'>%+.2f</td></tr>"
        % (
            _esc(p.get("coin_id", "?")),
            _num(p.get("qty")),
            _num(p.get("avg_cost")),
            _num(p.get("price")),
            _num(p.get("market_value")),
            _pnl_class(p.get("unrealized_pnl")),
            _num(p.get("unrealized_pnl")),
        )
        for p in open_pos
    ) or "<tr><td colspan='6'>No open paper positions.</td></tr>"
    return (
        "<div class='card'><h2>Profit / loss <small class='muted'>paper ledger, "
        "not real orders</small></h2><div class='kpis'>"
        f"<div class='card kpi'><span>Total P&amp;L</span>"
        f"<b class='{_pnl_class(pnl.get('total_pnl'))}'>"
        f"{_num(pnl.get('total_pnl')):+,.2f} {cur}</b></div>"
        f"<div class='card kpi'><span>Realized</span>"
        f"<b class='{_pnl_class(pnl.get('realized_pnl'))}'>"
        f"{_num(pnl.get('realized_pnl')):+,.2f} {cur}</b></div>"
        f"<div class='card kpi'><span>Unrealized</span>"
        f"<b class='{_pnl_class(pnl.get('unrealized_pnl'))}'>"
        f"{_num(pnl.get('unrealized_pnl')):+,.2f} {cur}</b></div>"
        f"<div class='card kpi'><span>Equity</span>"
        f"<b>{_num(pnl.get('equity')):,.2f} {cur}</b></div>"
        f"<div class='card kpi'><span>Invested</span>"
        f"<b>{_num(pnl.get('invested')):,.2f} {cur}</b></div>"
        f"<div class='card kpi'><span>Fills logged</span>"
        f"<b>{_esc(_int(pnl.get('n_trades'), 0))}</b></div>"
        "</div>"
        "<div class='scroll'><table><thead><tr><th>Coin</th><th>Qty</th>"
        "<th>Avg cost</th><th>Last price</th><th>Value</th><th>UPL</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        "<p class='muted'>Marked at the prices fetched during this run — the same "
        "prices used for the stop/take levels, so equity reflects a snapshot, not a "
        "continuous mark. Realized P&amp;L accrues on sells and closes; unrealized "
        "marks open positions to the latest run.</p></div>"
    )


def _history_section(hist: list[dict], cur: str) -> str:
    head = "<div class='card'><h2>Portfolio value over cycles</h2>"
    if len(hist) < 2:
        state = f"{len(hist)} cycle(s) logged so far." if hist else "No history yet."
        return head + f"<p class='muted'>{state} The chart needs 2+ portfolio cycles.</p></div>"

    series = [_row_worth(h) for h in hist]
    labels = [_cycle_label(h) for h in hist]
    # _load_history windows to HISTORY_WINDOW rows, so hist[0] is NOT the true
    # first cycle once the bot has run longer than that — take the baseline from
    # the earliest logged row instead of silently re-basing.
    start_val = _first_account_size()
    baseline = _num(start_val) if start_val is not None else None
    chart = _axis_chart(
        series,
        labels,
        y_title=f"Total portfolio worth ({cur})",
        x_title="Portfolio cycle",
        baseline=baseline,
    )

    gain = series[-1] - series[0]
    pct = (gain / series[0] * 100) if series[0] else 0.0
    vs_start = (series[-1] - baseline) if baseline else None

    body = (
        "<p class='muted'>One point per portfolio cycle; the dashed line is the "
        "starting account value. Values are the paper ledger's <code>equity</code>.</p>"
        + (chart or "<p class='muted'>Not plottable.</p>")
        + f"<p class='delta'>Cycle-over-cycle <b class='{_pnl_class(gain)}'>"
        f"{gain:+,.2f} {cur} ({pct:+.2f}%)</b>"
        + (
            f" · vs starting account <b class='{_pnl_class(vs_start)}'>"
            f"{vs_start:+,.2f} {cur}</b>"
            if vs_start is not None
            else ""
        )
        + "</p>"
    )

    rows = []
    for h in reversed(hist[-12:]):
        rows.append(
            "<tr><td>#%s</td><td><small>%s</small></td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%.1f%%</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num %s'>%s</td></tr>"
            % (
                _esc(h.get("cycle", "?")),
                _esc(_cycle_label(h).split(" ", 1)[-1]),
                f"{_row_worth(h):,.0f}",
                f"{_num(h.get('total_target')):,.0f}",
                _num(h.get("total_exposure_pct")),
                f"{_num(h.get('cash_remaining')):,.0f}",
                _esc(_int(h.get("n_positions"), "—")),
                _esc(_fmt_dur(h.get("duration_s"))),
                _pnl_class(h.get("total_pnl")),
                f"{_num(h.get('total_pnl')):+,.2f}",
            )
        )
    body += (
        "<div class='scroll'><table><thead><tr><th>Cycle</th><th>Time (UTC)</th>"
        "<th>Worth</th><th>Invested</th><th>Exposure</th><th>Cash</th>"
        "<th>Positions</th><th>Took</th><th>Total P&amp;L</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )
    if len(hist) >= HISTORY_WINDOW:
        body += (
            f"<p class='muted'>Showing the last {len(hist)} cycles — the history "
            f"file is longer, the chart window is {HISTORY_WINDOW}.</p>"
        )
    return head + body + "</div>"


def _signals(opps: list[dict]) -> str:
    head = "<div class='card'><h2>Signal snapshot</h2>"
    if not opps:
        return head + "<p class='muted'>No opportunities in this snapshot.</p></div>"
    top = sorted(opps, key=lambda o: _num(o.get("score")), reverse=True)[:SIGNAL_SLICES]
    rows = "".join(
        "<tr><td><b>%s</b><br><small>%s</small></td><td class='num'>%s</td>"
        "<td>%s</td><td class='reason'>%s</td></tr>"
        % (
            _esc(o.get("symbol") or o.get("coin_id") or "?"),
            _esc(o.get("coin_id", "")),
            _esc(o.get("score", "—")),
            _esc(o.get("risk_tier") or "—"),
            _esc(o.get("reason", "")),
        )
        for o in top
    )
    return (
        head + "<div class='scroll'><table><thead><tr><th>Coin</th><th>Score</th>"
        "<th>Risk tier</th><th>Why it made the list</th></tr></thead><tbody>"
        + rows + "</tbody></table></div>"
        + f"<p class='muted'>Top {len(top)} of {len(opps)} shortlisted coin(s) by "
        "score. Only coins on this list are eligible for the portfolio manager.</p></div>"
    )


def build() -> str:
    snap, problem = _load_snapshot()
    hist = _load_history()
    now = datetime.now(timezone.utc)

    if not snap or not snap.get("plan"):
        if problem == "missing":
            detail = f"No snapshot at <code>{_esc(SNAPSHOT.name)}</code>."
        elif problem:
            detail = f"Snapshot could not be read — {_esc(problem)}."
        else:
            detail = "Snapshot has no <code>plan</code> key."
        return _page(
            "Portfolio — no data",
            "<div class='card empty'><h2>Nothing to show yet</h2>"
            f"<p>{detail}</p>"
            "<p>Run the council flow once, then regenerate the page:</p>"
            "<p><code>python scripts/portfolio_panel.py</code></p>"
            f"<p class='muted'>Built {now.strftime('%Y-%m-%d %H:%M UTC')}</p></div>",
        )

    plan = snap.get("plan") or {}
    actions = plan.get("actions") or []
    cur = str(snap.get("base_currency", "usd")).upper()
    opps = snap.get("opportunities") or []
    score_by_coin = {o.get("coin_id", ""): o.get("score", "") for o in opps}
    pnl = snap.get("pnl") or {}

    body = (
        _strip(snap, now, cur)
        + _kpis(plan, snap, actions, pnl, cur)
        + _allocation(actions, _num(plan.get("cash_remaining")))
        + _positions(actions, score_by_coin, _num(snap.get("account_size")), cur)
        + _pnl_section(pnl, cur)
        + _history_section(hist, cur)
        + _signals(opps)
        + "<div class='card'><h2>Manager rationale</h2>"
        + f"<p>{_esc(plan.get('rationale') or '—')}</p>"
        + "<p class='muted'>Read-only view of the latest portfolio plan — this page "
        "places no orders. Regenerate with "
        "<code>python scripts/portfolio_panel.py</code>.</p></div>"
    )
    return _page(f"Portfolio · cycle #{snap.get('portfolio_cycle', '?')} · {cur}", body)


def _page(title: str, body: str) -> str:
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>""" + _esc(title) + """</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { font-family: inherit; margin: 0; padding: 18px 20px 40px; max-width: 1200px;
  color: var(--foreground, #e8e8e8); background: transparent; line-height: 1.45; }
h1 { font-size: 1.3em; margin: 0 0 14px; font-weight: 700; letter-spacing: -.01em; }
h2 { font-size: .95em; margin: 0 0 12px; font-weight: 600; opacity: .85;
  text-transform: uppercase; letter-spacing: .05em; }
.card { background: var(--card, #161616); border: 1px solid var(--border, #333);
  border-radius: 12px; padding: 16px 18px; margin-bottom: 16px; }
.strip { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px;
  font-size: .78em; opacity: .8; margin: 0 0 14px; padding: 0 2px; }
.strip b { font-variant-numeric: tabular-nums; font-weight: 600; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block;
  margin-right: 6px; }
.dot.live { background: #6fdc8c; box-shadow: 0 0 6px #6fdc8c88; }
.dot.warn { background: #e8c15a; box-shadow: 0 0 6px #e8c15a88; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(155px, 1fr));
  gap: 12px; margin-bottom: 16px; }
.card.kpi { margin: 0; padding: 13px 15px; }
.kpi span { font-size: .7em; opacity: .55; display: block; margin-bottom: 5px;
  text-transform: uppercase; letter-spacing: .06em; }
.kpi b { font-size: 1.18em; font-variant-numeric: tabular-nums; }
.kpi small { display: block; font-weight: normal; opacity: .5; font-size: .72em;
  margin-top: 2px; }
.bar { height: 6px; border-radius: 3px; background: var(--border, #333);
  margin-top: 8px; overflow: hidden; }
.bar div { height: 100%; background: var(--accent, #4da3ff); }
.alloc { display: flex; align-items: center; gap: 26px; flex-wrap: wrap; }
.donut { width: 132px; height: 132px; flex: none; }
.donut circle { fill: var(--card, #161616); }
.legend { list-style: none; padding: 0; margin: 0; font-size: .84em; flex: 1;
  display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 7px 24px; }
.legend li { display: flex; align-items: center; gap: 9px; white-space: nowrap; }
.legend .nm { min-width: 52px; }
.legend li b { font-variant-numeric: tabular-nums; opacity: .9; font-weight: 600;
  min-width: 96px; text-align: right; }
.legend li b small { font-weight: normal; }
.legend .track { flex: 1; height: 7px; border-radius: 4px; min-width: 44px;
  background: var(--border, #2e2e2e); overflow: hidden; }
.legend .track span { display: block; height: 100%; border-radius: 4px; }
.legend .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; }
table { width: 100%; border-collapse: collapse; font-size: .84em; }
th, td { text-align: left; padding: 7px 9px; vertical-align: top;
  border-bottom: 1px solid var(--border, #2a2a2a); }
thead th { position: sticky; top: 0; background: var(--card, #161616);
  opacity: .5; font-weight: 600; font-size: .76em; text-transform: uppercase;
  letter-spacing: .04em; white-space: nowrap; }
thead th.brk { white-space: normal; padding-left: 0; padding-right: 6px; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: #ffffff08; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.reason { opacity: .78; min-width: 220px; }
small { opacity: .55; font-size: .85em; }
.pill { display: inline-block; font-size: .7em; font-weight: 700; padding: 2px 9px;
  border-radius: 20px; text-transform: uppercase; letter-spacing: .04em; }
.pill.buy { background: #123d22; color: #6fdc8c; }
.pill.sell { background: #471818; color: #f08a8a; }
.pill.hold { background: #2a2a2a; color: #aaa; }
.pos { color: #6fdc8c; }
.neg { color: #f08a8a; }
.muted { opacity: .5; font-size: .78em; }
.delta { font-size: .84em; opacity: .8; margin: 4px 0 12px; }
.warn { border-color: #6b5a2a; }
.warn h2 { color: #e8c15a; }
.empty { text-align: center; padding: 48px 24px; }
.empty p { opacity: .8; }
code { background: #00000055; padding: 2px 6px; border-radius: 4px;
  font-size: .9em; }
.scroll { overflow-x: auto; }
.chart { width: 100%; height: auto; margin: 10px 0 14px; display: block; }
.chart .grid { stroke: var(--border, #2a2a2a); stroke-width: 1; stroke-dasharray: 3 4; }
.chart .axis { stroke: var(--border, #555); stroke-width: 1.2; }
.chart .base { stroke: var(--muted-foreground, #8a8a8a); stroke-width: 1.2;
  stroke-dasharray: 6 5; opacity: .7; }
.chart .tick { fill: var(--muted-foreground, #9a9a9a); font-size: 11px; }
.chart .axis-title { fill: var(--muted-foreground, #b0b0b0); font-size: 11.5px;
  font-weight: 600; }
.chart circle { opacity: .9; }
.chart circle.end { opacity: 1; stroke: var(--card, #161616); stroke-width: 2; }
.donut .tick { fill: var(--muted-foreground, #9a9a9a); font-size: 11px; }
</style></head><body>
<h1>""" + _esc(title) + """</h1>
""" + body + """
</body></html>
"""


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    PANEL.write_text(build(), encoding="utf-8")
    snap, problem = _load_snapshot()
    n = len((snap.get("plan") or {}).get("actions") or []) if snap else 0
    state = f"{n} actions" if snap else f"NO SNAPSHOT ({problem})"
    print(f"panel -> {PANEL}  ({state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())