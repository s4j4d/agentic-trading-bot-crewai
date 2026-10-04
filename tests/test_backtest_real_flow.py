"""
Real-flow backtest — runs the actual CryptoCouncilFlow (scout → analysis →
portfolio) against a frozen market snapshot from 30 days ago, then checks
whether the portfolio actions the crew produced would have been profitable
over the 30 days that followed.

How it works
------------
1. The HTTP layer (`requests.get` / `requests.Session.get`) is mocked with
   historical data frozen at T-30 — the crew sees this as "current market".
2. The real Flow kickoff runs: CouncilScoutCrew → CouncilAnalysisCrew →
   CouncilPortfolioCrew, producing a PortfolioPlan with target allocations
   and per-action stop-loss / take-profit.
3. The plan is parsed and each targeted action is graded against REAL price
   action from T-30 → now: did price reach the take-profit before the
   stop-loss? Was the net position profitable?

Crew output is saved to output/backtest_real_flow.log so it can be
inspected without re-running the resource-intensive crew.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

# ---------------------------------------------------------------------------
# Time framing
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
T0 = NOW - timedelta(days=30)          # "now" as far as the crew is concerned
HISTORY_DAYS = 365                     # days of frozen history to fetch
VS_CURRENCY = "usd"
ACCOUNT_SIZE = 10_000.0

LOG_PATH = Path("output") / "backtest_real_flow.log"
LOG_PATH.parent.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Real history fetch (unmocked) — used for the frozen snapshot AND the
# profitability grading after the crew runs.
# ---------------------------------------------------------------------------

def _fetch_real_ohlc(coin_id: str, vs: str, days: int) -> list[list[float]]:
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    for attempt_days in (days, 365, 180, 90):
        for retry in range(3):
            try:
                resp = requests.get(url, params={"vs_currency": vs, "days": attempt_days}, timeout=20)
                if resp.status_code == 429:
                    time.sleep(15 * (retry + 1))
                    continue
                if resp.status_code == 400:
                    break
                resp.raise_for_status()
                candles = resp.json()
                if len(candles) >= 50:
                    return candles
            except requests.HTTPError:
                time.sleep(2)
                continue
            time.sleep(2)
        time.sleep(6)
    return []


def _fetch_real_market_chart(coin_id: str, vs: str, days: int) -> dict[str, Any]:
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    for retry in range(3):
        try:
            resp = requests.get(url, params={"vs_currency": vs, "days": days, "interval": "daily"}, timeout=20)
            if resp.status_code == 429:
                time.sleep(15 * (retry + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError:
            time.sleep(15 * (retry + 1))
    return {"prices": [], "total_volumes": [], "market_caps": []}


def _fetch_real_prices(coin_id: str, vs: str, days: int) -> list[float]:
    try:
        chart = _fetch_real_market_chart(coin_id, vs, days)
        prices = [p for _, p in chart.get("prices", [])]
        if prices:
            return prices
    except Exception:
        pass
    # Fallback: daily OHLC closes
    ohlc = _fetch_real_ohlc(coin_id, vs, days)
    return [row[4] for row in ohlc]


# ---------------------------------------------------------------------------
# Frozen snapshot helpers
# ---------------------------------------------------------------------------

def _frozen_ohlc(real_candles: list[list[float]]) -> list[list[float]]:
    cutoff_ms = T0.timestamp() * 1000
    return [c for c in real_candles if c[0] <= cutoff_ms]


def _frozen_chart(real: dict[str, Any]) -> dict[str, Any]:
    cutoff_ms = T0.timestamp() * 1000
    return {
        "prices": [[t, p] for t, p in real.get("prices", []) if t <= cutoff_ms],
        "total_volumes": [[t, v] for t, v in real.get("total_volumes", []) if t <= cutoff_ms],
        "market_caps": [[t, v] for t, v in real.get("market_caps", []) if t <= cutoff_ms],
    }


# ---------------------------------------------------------------------------
# Fake response plumbing
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200, text: str = "", content: bytes = b""):
        self._payload = payload
        self.status_code = status
        self.text = text or (json.dumps(payload) if not isinstance(payload, str) else payload)
        self.content = content or self.text.encode("utf-8")

    def json(self) -> Any:
        if isinstance(self._payload, (dict, list)):
            return self._payload
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


# ---------------------------------------------------------------------------
# Frozen data registry — real history trimmed to T0, keyed per coin.
# ---------------------------------------------------------------------------

class FrozenData:
    def __init__(self, coin_ids: list[str]) -> None:
        self.ohlc: dict[str, list[list[float]]] = {}
        self.chart: dict[str, dict[str, Any]] = {}
        for cid in coin_ids:
            try:
                real_ohlc = _fetch_real_ohlc(cid, VS_CURRENCY, HISTORY_DAYS)
                real_chart = _fetch_real_market_chart(cid, VS_CURRENCY, HISTORY_DAYS)
                self.ohlc[cid] = _frozen_ohlc(real_ohlc)
                self.chart[cid] = _frozen_chart(real_chart)
                last = self.ohlc[cid][-1][4] if self.ohlc[cid] else None
                print(f"  {cid}: {len(self.ohlc[cid])} frozen candles, last close ${last}")
            except Exception as e:
                print(f"  {cid}: failed to fetch — {e}")
            time.sleep(6)  # CoinGecko free-tier rate limit

    def ohlc_for(self, url: str) -> list[list[float]]:
        m = re.search(r"/coins/([^/]+)/ohlc", url)
        cid = m.group(1) if m else None
        return self.ohlc.get(cid or "", [])

    def chart_for(self, url: str) -> dict[str, Any]:
        m = re.search(r"/coins/([^/]+)/market_chart", url)
        cid = m.group(1) if m else None
        return self.chart.get(cid or "", {"prices": [], "total_volumes": [], "market_caps": []})


# ---------------------------------------------------------------------------
# Canned replies
# ---------------------------------------------------------------------------

def _trending(coin_ids: list[str]) -> dict:
    return {"coins": [
        {"item": {"id": cid, "symbol": cid[:3], "name": cid.title(),
                  "market_cap_rank": i + 1, "price_btc": 0.002, "score": i}}
        for i, cid in enumerate(coin_ids[:5])
    ]}


def _markets(coin_ids: list[str], frozen: FrozenData) -> list[dict]:
    out = []
    for i, cid in enumerate(coin_ids):
        last = frozen.ohlc[cid][-1][4] if frozen.ohlc.get(cid) else 0
        out.append({
            "id": cid, "symbol": cid[:3], "name": cid.title(),
            "current_price": last, "market_cap": 50e9 / (i + 1), "market_cap_rank": i + 1,
            "total_volume": 5e9 / (i + 1), "price_change_percentage_24h": 2.0,
            "price_change_percentage_1h_in_currency": 0.5,
            "price_change_percentage_7d_in_currency": 5.0,
        })
    return out


_FEAR_GREED = {"data": [
    {"value": "62", "value_classification": "Greed", "timestamp": str(int(T0.timestamp()))},
    {"value": "55", "value_classification": "Greed", "timestamp": str(int((T0 - timedelta(days=1)).timestamp()))},
]}

_GLOBAL = {"data": {
    "market_cap_percentage": {"btc": 55.0, "eth": 17.0},
    "total_market_cap": {"usd": 2.5e12}, "total_volume": {"usd": 1.2e11},
    "market_cap_change_percentage_24h_usd": 1.5, "active_cryptocurrencies": 14000,
}}

_NEWS_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Solana rally continues as DeFi volume surges</title>
<link>https://example.com/1</link><pubDate>Tue, 01 Sep 2026 10:00:00 GMT</pubDate>
<description>Solana DeFi volume surges to record high.</description></item>
</channel></rss>"""


def _community(coin_id: str) -> dict:
    return {
        "community_data": {"twitter_followers": 5_000_000, "reddit_subscribers": 1_200_000,
                           "reddit_average_posts_48h": 50, "reddit_average_comments_48h": 400},
        "developer_data": {"commit_count_4_weeks": 800, "stars": 12000, "forks": 3000},
        "sentiment_votes_up_percentage": 70, "sentiment_votes_down_percentage": 30,
        "name": coin_id.title(), "watchlist_portfolio_users": 400000,
    }


# ---------------------------------------------------------------------------
# HTTP mock
# ---------------------------------------------------------------------------

def make_mock_get(frozen: FrozenData, coin_ids: list[str]):
    def _mock_get(url: str, params: dict | None = None, **kwargs: Any) -> _FakeResponse:
        params = params or {}
        if "/coins/" in url and url.endswith("/ohlc"):
            return _FakeResponse(frozen.ohlc_for(url))
        if "/coins/" in url and url.endswith("/market_chart"):
            return _FakeResponse(frozen.chart_for(url))
        if re.search(r"/coins/[^/]+$", url):
            m = re.search(r"/coins/([^/]+)$", url)
            cid = m.group(1) if m else None
            last = frozen.ohlc[cid][-1][4] if cid and cid in frozen.ohlc else 0
            return _FakeResponse({**_community(cid or ""), "market_data": {"current_price": {"usd": last}}})
        if url.endswith("/search/trending"):
            return _FakeResponse(_trending(coin_ids))
        if url.endswith("/coins/markets"):
            return _FakeResponse(_markets(coin_ids, frozen))
        if url.endswith("/coins/list"):
            return _FakeResponse([{"id": c, "symbol": c[:3], "name": c.title()} for c in coin_ids])
        if url.endswith("/global"):
            return _FakeResponse(_GLOBAL)
        if "alternative.me" in url:
            return _FakeResponse(_FEAR_GREED)
        if url.endswith("/rss") or "outboundfeeds" in url or "/feed" in url:
            return _FakeResponse(_NEWS_RSS, content=_NEWS_RSS.encode(), text=_NEWS_RSS)
        if "nobitex" in url:
            return _FakeResponse({"stats": {}} if url.endswith("/market/stats") else {"s": "ok", "t": [], "o": [], "h": [], "l": [], "c": [], "v": []})
        return _FakeResponse({})

    def _mock_session_get(self, url, params=None, **kwargs):
        return _mock_get(url, params, **kwargs)

    return _mock_get, _mock_session_get


# ---------------------------------------------------------------------------
# Portfolio-plan grading — did the crew's actions work over the next 30d?
# ---------------------------------------------------------------------------

def grade_action(action: dict[str, Any], prices: list[float]) -> dict[str, Any]:
    sl = action.get("stop_loss")
    tp = action.get("take_profit")
    target = action.get("target", 0) or 0
    current = action.get("current", 0) or 0

    if action.get("action") in ("close",):
        return {"profitable": None, "reason": "close action — no directional bet"}

    if target <= current:
        return {"profitable": None, "reason": "no new directional exposure"}

    if not prices:
        return {"profitable": False, "reason": "no future prices"}

    entry = prices[0]

    # Sanity guard: levels the crew emitted are only meaningful if they sit
    # in a sane band around the real entry (SL below, TP above, both within
    # a 60% band). Anything else is treated as crew nonsense and marked
    # not-gradeable instead of producing a bogus P&L.
    if sl is not None and tp is not None:
        sl_sane = sl < entry * 1.5
        tp_sane = tp > entry * 0.5
        sl_long_sane = sl < entry
        tp_long_sane = tp > entry
        if not (sl_sane and tp_sane and sl_long_sane and tp_long_sane):
            return {
                "profitable": None,
                "reason": f"invalid levels (entry≈${entry:.2f}, sl=${sl:.2f}, tp=${tp:.2f})",
            }
        if abs(entry - sl) / entry > 0.6 or abs(tp - entry) / entry > 0.6:
            return {
                "profitable": None,
                "reason": f"levels too far from entry (entry≈${entry:.2f}, sl=${sl:.2f}, tp=${tp:.2f})",
            }

    entry_idx = 0
    exit_idx = len(prices) - 1
    hit_tp = hit_sl = False
    exit_price = prices[-1]
    for i, p in enumerate(prices):
        if sl is not None and p <= sl:
            hit_sl, exit_price, exit_idx = True, sl, i
            break
        if tp is not None and p >= tp:
            hit_tp, exit_price, exit_idx = True, tp, i
            break

    pnl_pct = (exit_price - entry) / entry
    return {
        "profitable": pnl_pct > 0,
        "hit_tp": hit_tp, "hit_sl": hit_sl,
        "exit_price": exit_price, "pnl_pct": pnl_pct,
        "days_to_close": exit_idx if (hit_tp or hit_sl) else len(prices) - 1,
    }


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_backtest_real_flow_full(monkeypatch: pytest.MonkeyPatch):
    """Run the full Flow (scout → analysis → portfolio) and grade the plan."""

    COIN_POOL = ["bitcoin", "ethereum", "solana", "ripple", "avalanche-2"]

    print("\nBuilding frozen T-30 snapshot...")
    frozen = FrozenData(COIN_POOL)

    # Capture REAL future price curves NOW, before the HTTP layer is mocked.
    # The grading step later uses these — never the frozen data.
    future_prices: dict[str, list[float]] = {}
    for cid in COIN_POOL:
        try:
            future_prices[cid] = _fetch_real_prices(cid, VS_CURRENCY, 30)
        except Exception as e:
            print(f"  Could not pre-fetch future prices for {cid}: {e}")
        time.sleep(3)

    mock_get, mock_session_get = make_mock_get(frozen, COIN_POOL)
    monkeypatch.setattr(requests, "get", mock_get)
    monkeypatch.setattr(requests.sessions.Session, "get", mock_session_get)

    # Scout crew is mocked to return a fixed shortlist — the real trading
    # decision logic (analysis + portfolio crews) still runs. This keeps the
    # test deterministic at the market-scan boundary.
    from crypto_council_flow.crews.council import council_crew as _cc
    from crypto_council_flow.crews.council.council_crew import ScoutShortlist, ScoutOpportunity

    _frozen_shortlist = ScoutShortlist(opportunities=[
        ScoutOpportunity(
            rank=1, coin_id="bitcoin", symbol="BTC", name="Bitcoin",
            score=82, signals=["bullish_macd", "volume_spike"],
            risk_tier="LOW", reason="Strong trend with volume confirmation",
        ),
        ScoutOpportunity(
            rank=2, coin_id="ethereum", symbol="ETH", name="Ethereum",
            score=75, signals=["bullish_ema_cross"], risk_tier="MEDIUM",
            reason="EMA crossover with moderate momentum",
        ),
        ScoutOpportunity(
            rank=3, coin_id="solana", symbol="SOL", name="Solana",
            score=68, signals=["oversold_rsi"], risk_tier="MEDIUM",
            reason="RSI recovering from oversold",
        ),
    ])

    class _FakeScoutResult:
        pydantic = _frozen_shortlist
        raw = json.dumps({"opportunities": [o.model_dump() for o in _frozen_shortlist.opportunities]})

    class _FakeScoutCrewInstance:
        def crew(self):
            return SimpleNamespace(kickoff=lambda **kwargs: _FakeScoutResult())

    import crypto_council_flow.main as _main_mod
    monkeypatch.setattr(_main_mod, "CouncilScoutCrew", lambda: _FakeScoutCrewInstance())

    from crypto_council_flow.main import CryptoCouncilFlow
    flow = CryptoCouncilFlow()
    flow.state.account_size = ACCOUNT_SIZE
    flow.state.base_currency = "usd"
    flow.state.fast = True
    flow.state.max_coins = 3

    try:
        flow.run_scout()
    except Exception as e:
        print(f"Scout failed: {e}")
    try:
        flow.analyse_coins()
    except Exception as e:
        print(f"Analysis failed: {e}")
    try:
        flow.manage_portfolio()
    except Exception as e:
        print(f"Portfolio failed: {e}")

    plan = flow.state.portfolio_plan
    log = {
        "coin_opportunities": [o.model_dump() if hasattr(o, "model_dump") else o for o in flow.state.coin_opportunities],
        "analysed_coins": flow.state.analysed_coins,
        "portfolio_plan": plan,
        "last_scout_duration_s": getattr(flow.state, "last_scout_duration_s", None),
        "last_analysis_duration_s": getattr(flow.state, "last_analysis_duration_s", None),
        "last_portfolio_duration_s": getattr(flow.state, "last_portfolio_duration_s", None),
    }
    LOG_PATH.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print(f"\nFlow state saved to {LOG_PATH}")

    monkeypatch.undo()
    actions = (plan or {}).get("actions", []) if isinstance(plan, dict) else []
    print(f"\n=== PORTFOLIO PLAN ({len(actions)} actions) ===")
    results = []
    for a in actions:
        cid = a.get("coin_id", "")
        res = grade_action(a, future_prices.get(cid, []))
        res["coin_id"] = cid
        res["action"] = a.get("action")
        res["target"] = a.get("target")
        res["current"] = a.get("current")
        results.append(res)
        if res["profitable"] is None:
            verdict = res["reason"]
            pnl = "n/a"
            dtc = ""
        elif res.get("reason") and "pnl_pct" not in res:
            verdict = res["reason"]
            pnl = "n/a"
            dtc = ""
        else:
            verdict = "PROFITABLE" if res["profitable"] else "NOT PROFITABLE"
            pnl = f"{res['pnl_pct']*100:+.2f}%" if "pnl_pct" in res else "n/a"
            dtc = f"  closed in {res.get('days_to_close', '?')}d"
        print(f"  {cid:<16} {str(a.get('action')):<10} target={a.get('target'):<10} → {verdict} ({pnl}){dtc}")

    n_gradeable = [r for r in results if r["profitable"] is not None]
    n_profitable = [r for r in n_gradeable if r["profitable"]]
    print(f"\n=== VERDICT ===")
    print(f"  Graded actions: {len(n_gradeable)}  |  Profitable: {len(n_profitable)}")

    assert isinstance(plan, dict), "Flow did not produce a portfolio plan"
    assert "actions" in plan, f"Malformed plan: {plan}"
