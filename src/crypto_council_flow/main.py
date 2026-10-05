#!/usr/bin/env python
"""
Crypto Council Flow — main entry point.

Scheduling model
----------------
The flow implements two nested loops inside a single long-running process:

  Scout loop  (every 2 hours)
    CouncilScoutCrew scans trending / momentum / catalyst data and produces
    a ranked list of 3-7 coin opportunities stored in flow state.

  Analysis loop  (every 5 minutes, operates on the current scout list)
    For each coin in the scout list, CouncilAnalysisCrew runs the three-agent
      sequential pipeline (technical → sentiment → risk) and writes a report to
      output/<coin_id>_report.md.  All results are also appended to a single
      consolidated file at output/all_analysis_results.txt.

Cycle locking
-------------
No new cycle (scout or analysis) starts while any previous cycle is still
running. A single asyncio.Lock prevents overlapping execution: if the scout
cycle is in progress when the analysis timer fires, the analysis is skipped
and retried on the next scheduler tick (and vice-versa). This ensures mutual
exclusion between all cycles regardless of type.

Flow state is a Pydantic model so it is typed and serialisable.

Usage
-----
    crewai run                   # uses kickoff() entry point
    python -m crypto_council_flow.main            # same
    python -m crypto_council_flow.main --once     # run one full cycle and exit
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time
os.environ["OTEL_SDK_DISABLED"] = "true"
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from crewai.flow import Flow, listen, start

from crypto_council_flow.crews.council.council_crew import (
    CouncilAnalysisCrew,
    CouncilPortfolioCrew,
    CouncilScoutCrew,
    PortfolioAction,
    PortfolioPlan,
)
from crypto_council_flow.tools.technical_indicators import _fetch_ohlcv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOUT_INTERVAL_SECONDS: int = 2 * 60 * 60   # 2 hours
ANALYSIS_INTERVAL_SECONDS: int = 5 * 60     # 5 minutes
DEFAULT_VS_CURRENCY: str = "usd"
DEFAULT_BASE_CURRENCY: str = "usd"
DEFAULT_RSI_PERIOD: int = 14
OUTPUT_DIR: Path = Path("output")
CONSOLIDATED_RESULTS: Path = OUTPUT_DIR / "all_analysis_results.txt"


# ---------------------------------------------------------------------------
# Pydantic state model
# ---------------------------------------------------------------------------

class CoinOpportunity(BaseModel):
    """Single canonical DTO for scout output consumed by the Flow.

    Same shape as ScoutOpportunity in council_crew.py — one DTO, never two.
    The before-validator normalises LLM field-name variants so structured
    output and the raw-JSON fallback both converge on this shape.
    """
    rank: int = 0
    coin_id: str = ""
    symbol: str = ""
    name: str = ""
    score: int = 50
    signals: list[str] = Field(default_factory=list)
    risk_tier: str = "MEDIUM"
    reason: str = ""

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = dict(data)

        if not d.get("coin_id") and d.get("coingecko_id"):
            d["coin_id"] = d["coingecko_id"]

        if not d.get("symbol"):
            if d.get("ticker"):
                d["symbol"] = d["ticker"]
            elif isinstance(d.get("coin"), str) and "(" in d["coin"]:
                inside = d["coin"].split("(")[-1].rstrip(")").strip()
                if inside.isalpha() and len(inside) <= 6:
                    d["symbol"] = inside.upper()

        if not d.get("name") and isinstance(d.get("coin"), str):
            d["name"] = d["coin"].split("(")[0].strip() or d["coin"]

        if d.get("score") is None and d.get("weighted_score") is not None:
            raw = d["weighted_score"]
            try:
                raw = float(raw)
                d["score"] = int(round(raw * 100)) if 0 <= raw <= 1 else int(round(raw))
            except (TypeError, ValueError):
                d["score"] = 50

        if d.get("rank") is None and d.get("rank_position") is not None:
            d["rank"] = d["rank_position"]

        # coin_id fallback — derive a slug from the symbol
        if not d.get("coin_id"):
            sym = (d.get("symbol") or "").upper()
            d["coin_id"] = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(sym, (d.get("symbol") or "").lower().replace(" ", "-"))

        return d


class CryptoCouncilState(BaseModel):
    """Persistent Flow state passed between steps."""

    # User-configurable inputs (supplied at kickoff)
    account_size: float = 10_000.0
    base_currency: str = DEFAULT_BASE_CURRENCY
    period: int = DEFAULT_RSI_PERIOD

    # Configurable caps (also supplied at kickoff or via CLI)
    max_total_exposure_pct: float = 30.0
    max_single_position_pct: float = 5.0

    # Analysis scope (plan Task 2; CLI flags are Task 1)
    max_coins: int = 10
    fast: bool = False

    # Scout outputs — refreshed every 2 hours
    coin_opportunities: list[CoinOpportunity] = Field(default_factory=list)
    last_scout_utc: str = ""

    # Analysis tracking — refreshed every 5 minutes
    last_analysis_utc: str = ""
    analysis_reports: dict[str, str] = Field(default_factory=dict)  # coin_id → report path
    analysis_verdicts: dict[str, str] = Field(default_factory=dict)  # coin_id → short excerpt of the analysis conclusion
    analysed_coins: list[str] = Field(default_factory=list)  # coin_ids with completed analysis this cycle
    last_risk_levels: dict[str, dict[str, float]] = Field(default_factory=dict)  # ATR snapshot {current_price, atr_pct} per coin for deterministic SL/TP backfill
    max_hold_days: int = 4  # force-close positions held longer than this
    position_opened_utc: dict[str, str] = Field(default_factory=dict)  # coin_id → ISO ts first opened

    # Portfolio management — refreshed after each analysis cycle
    portfolio_plan: dict[str, Any] = Field(default_factory=dict)  # last PortfolioPlan dump
    last_portfolio_utc: str = ""

    # Internal timing state
    scout_cycle: int = 0
    analysis_cycle: int = 0
    portfolio_cycle: int = 0

    # Per-cycle durations (seconds, wall-clock)
    last_scout_duration_s: float = 0.0
    last_analysis_duration_s: float = 0.0
    last_portfolio_duration_s: float = 0.0
    last_coin_durations_s: dict[str, float] = Field(default_factory=dict)  # coin_id → seconds


def _analyse_one_coin(
    opportunity: CoinOpportunity, period: int, account_size: float, base_currency: str
) -> tuple[CoinOpportunity, str | None, Exception | None, float]:
    """Run the analysis crew for one coin (worker thread).

    Pure worker: never touches flow state or the filesystem. Returns
    (opportunity, raw_report_or_None, error_or_None, duration_s) so the
    main thread can do all state mutation and file writes.
    """
    coin_start = time.perf_counter()
    try:
        result = CouncilAnalysisCrew().crew().kickoff(
            inputs={
                "symbol": opportunity.symbol,
                "coin_id": opportunity.coin_id,
                "vs_currency": DEFAULT_VS_CURRENCY,
                "period": period,
                "account_size": account_size,
                "base_currency": base_currency,
            }
        )
        return (opportunity, result.raw, None, time.perf_counter() - coin_start)
    except Exception as exc:  # noqa: BLE001 — surfaced to the merge loop
        return (opportunity, None, exc, time.perf_counter() - coin_start)


def _analysis_workers(n_coins: int) -> int:
    """Thread-pool size for per-coin analysis (env-overridable)."""
    try:
        workers = int(os.getenv("COUNCIL_ANALYSIS_WORKERS", "3"))
    except ValueError:
        workers = 3
    return max(1, min(n_coins, workers))


def _fmt_dur(seconds: float) -> str:
    """Format seconds as '45s', '1m 23s', or '1h 02m'."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s:02d}s"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"


# ---------------------------------------------------------------------------
# Flow definition
# ---------------------------------------------------------------------------

class CryptoCouncilFlow(Flow[CryptoCouncilState]):
    """
    Three-step CrewAI Flow:

      run_scout       — market_scout produces a ranked coin list
      analyse_coins   — three analysts process each coin sequentially
      manage_portfolio — portfolio_manager reconciles the book with fresh signals
    """

    @start()
    def run_scout(self) -> None:
        """
        Run CouncilScoutCrew to refresh the opportunity list.
        Called once at startup and then every 2 hours by the outer scheduler.
        """
        self.state.scout_cycle += 1
        scout_start = time.perf_counter()
        print(
            f"\n{'='*60}\n"
            f"SCOUT CYCLE #{self.state.scout_cycle}  "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"{'='*60}"
        )

        try:
            result = CouncilScoutCrew().crew().kickoff()
        except Exception as exc:
            self.state.last_scout_duration_s = time.perf_counter() - scout_start
            print(f"ERROR: Scout crew failed: {exc}. Keeping previous coin list.")
            print(f"⏱ Scout took {_fmt_dur(self.state.last_scout_duration_s)} (failed)")
            return

        # Preferred path: structured output validated by CrewAI against
        # ScoutShortlist (no JSON parsing, no file roundtrip).
        opportunities: list[CoinOpportunity] = []
        shortlist = result.pydantic
        items: list[dict[str, Any]] = []
        if shortlist is not None:
            raw_items = shortlist.opportunities
            items = [
                op.model_dump() if hasattr(op, "model_dump") else dict(op)
                for op in raw_items
            ]
        else:
            items = _extract_scout_items(result.raw)

        for item in items:
            try:
                opportunities.append(CoinOpportunity(**item))
            except Exception:
                # Skip malformed entries rather than crashing
                pass

        if not opportunities:
            self.state.last_scout_duration_s = time.perf_counter() - scout_start
            print(
                "WARNING: Scout returned no parseable opportunities. "
                "Keeping previous coin list."
            )
            print(f"⏱ Scout took {_fmt_dur(self.state.last_scout_duration_s)} (no results)")
        else:
            self.state.coin_opportunities = opportunities
            self.state.last_scout_utc = datetime.now(timezone.utc).isoformat()
            self.state.last_scout_duration_s = time.perf_counter() - scout_start
            print(
                f"Scout found {len(opportunities)} opportunity(ies):\n"
                + "\n".join(
                    f"  #{op.rank} {op.symbol} ({op.coin_id}) — score {op.score} — {op.reason}"
                    for op in opportunities
                )
            )
            print(f"⏱ Scout took {_fmt_dur(self.state.last_scout_duration_s)}")

    @listen(run_scout)
    def analyse_coins(self) -> None:
        """
        Run CouncilAnalysisCrew for each coin in the current opportunity list
        PLUS any coins currently in the portfolio (open positions from previous plan).
        Called immediately after run_scout and then every 5 minutes by the scheduler.
        """
        if not self.state.coin_opportunities and not self.state.portfolio_plan:
            print("No coins to analyse. Waiting for scout results.")
            return

        self.state.analysis_cycle += 1
        self.state.analysed_coins = []  # reset; manage_portfolio consumes this cycle's list
        self.state.last_coin_durations_s = {}
        analysis_start = time.perf_counter()
        OUTPUT_DIR.mkdir(exist_ok=True)

        # Task 2 cap: sort scout by score desc, slice to max_coins, then
        # fill spare slots with portfolio holds (normal mode only).
        cap = max(1, self.state.max_coins or 10)
        scout_sorted = sorted(
            self.state.coin_opportunities, key=lambda o: o.score, reverse=True
        )[:cap]
        to_analyse: list[CoinOpportunity] = list(scout_sorted)

        # Add portfolio coins that aren't already in scout list — only while
        # slots remain. Fast mode skips extras entirely (cap is all scout).
        if not getattr(self.state, "fast", False):
            if self.state.portfolio_plan and isinstance(self.state.portfolio_plan.get("actions"), list):
                for action in self.state.portfolio_plan["actions"]:
                    if len(to_analyse) >= cap:
                        break
                    if action.get("target", 0) > 0:
                        coin_id = action.get("coin_id", "")
                        if coin_id and not any(op.coin_id == coin_id for op in to_analyse):
                            # Create a minimal opportunity for portfolio coin (score 0, will be vetted)
                            to_analyse.append(
                                CoinOpportunity(
                                    coin_id=coin_id,
                                    symbol=action.get("symbol", "").upper() or coin_id.upper(),
                                    name=action.get("symbol", "").upper() or coin_id.upper(),
                                    score=0,
                                    signals=["portfolio_hold"],
                                    risk_tier="MEDIUM",
                                    reason="Portfolio position — ensure fresh analysis for rebalance",
                                )
                            )

        print(
            f"\n{'-'*60}\n"
            f"ANALYSIS CYCLE #{self.state.analysis_cycle}  "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Analysing {len(to_analyse)} coin(s) (scout: {len(self.state.coin_opportunities)} + portfolio: {len(to_analyse) - len(self.state.coin_opportunities)})...\n"
            f"{'-'*60}"
        )

        failed_coins: list[str] = []
        for opportunity in to_analyse:
            print(f"\n  ▶ Queued {opportunity.symbol} ({opportunity.coin_id})...")

        # Parallel fan-out: workers run the LLM crews; the main thread below
        # does ALL state mutation and file writes (no shared-state races).
        with ThreadPoolExecutor(max_workers=_analysis_workers(len(to_analyse))) as pool:
            futures = [
                pool.submit(
                    _analyse_one_coin,
                    opportunity,
                    self.state.period,
                    self.state.account_size,
                    self.state.base_currency,
                )
                for opportunity in to_analyse
            ]
            worker_results = [fut.result() for fut in futures]

        # Merge on the main thread, in scout-score order for stable output.
        worker_results.sort(key=lambda r: getattr(r[0], "score", 0), reverse=True)
        for opportunity, raw_report, error, duration_s in worker_results:
            coin_id = opportunity.coin_id
            symbol = opportunity.symbol
            self.state.last_coin_durations_s[coin_id] = duration_s

            if error is not None or raw_report is None:
                failed_coins.append(coin_id)
                print(f"    ✗ Analysis failed for {symbol}: {error}")
                continue

            report_path = OUTPUT_DIR / f"{coin_id}_report.md"
            report_content = _build_report(
                opportunity, raw_report,
                duration_s=duration_s,
            )
            report_path.write_text(report_content, encoding="utf-8")
            self.state.analysis_reports[coin_id] = str(report_path)
            self.state.analysis_verdicts[coin_id] = _extract_verdict(raw_report)
            self.state.analysed_coins.append(coin_id)
            print(
                f"    ✓ Report saved → {report_path} "
                f"(⏱ {_fmt_dur(duration_s)})"
            )

            # Append to consolidated results file
            _append_to_consolidated(
                self.state.analysis_cycle, opportunity, raw_report,
                duration_s=duration_s,
            )

        self.state.last_analysis_utc = datetime.now(timezone.utc).isoformat()
        self.state.last_analysis_duration_s = time.perf_counter() - analysis_start
        print(
            f"\nAnalysis cycle #{self.state.analysis_cycle} complete "
            f"(⏱ {_fmt_dur(self.state.last_analysis_duration_s)}). "
            f"Analysed: {self.state.analysed_coins} | "
            f"Failed: {failed_coins} | "
            f"Reports: {list(self.state.analysis_reports.keys())}"
        )
        if self.state.last_coin_durations_s:
            per_coin = ", ".join(
                f"{coin} {_fmt_dur(d)}"
                for coin, d in self.state.last_coin_durations_s.items()
            )
            print(f"Per-coin times: {per_coin}")
        print(f"Consolidated results: {CONSOLIDATED_RESULTS.resolve()}")

    @listen(analyse_coins)
    def manage_portfolio(self) -> None:
        """
        Run CouncilPortfolioCrew to produce a cap-aware rebalance plan.
        Called immediately after analyse_coins completes.

        The opportunities sent to the allocator are the scout list MERGED
        with current book holdings missing from it (neutral carry score 50,
        judged on fresh analysis rather than auto-exited).
        """
        # Prepare open_positions from previous portfolio plan (or empty for first run)
        open_positions: list[dict[str, Any]] = []
        plan_symbols: dict[str, str] = {}
        if self.state.portfolio_plan and isinstance(self.state.portfolio_plan.get("actions"), list):
            for action in self.state.portfolio_plan["actions"]:
                if action.get("target", 0) > 0:
                    coin = action.get("coin_id", "")
                    if coin:
                        open_positions.append({
                            "coin_id": coin,
                            "position": action.get("target", 0),
                        })
                        if action.get("symbol"):
                            plan_symbols[coin] = action.get("symbol", "")

        if not self.state.coin_opportunities and not open_positions:
            print(
                "No opportunities to manage. Skipping portfolio step. "
                f"(analysed this cycle: {self.state.analysed_coins} — "
                "empty means every analysis failed or scout returned nothing)"
            )
            return

        # Record when positions were first opened so max_hold_days can age them.
        now_utc = datetime.now(timezone.utc)
        current_ids: set[str] = {
            str(a.get("coin_id")) for a in open_positions if a.get("coin_id")
        }
        # Drop open-time entries for positions no longer held.
        self.state.position_opened_utc = {
            cid: iso for cid, iso in self.state.position_opened_utc.items() if cid in current_ids
        }
        for cid in current_ids:
            self.state.position_opened_utc.setdefault(cid, now_utc.isoformat())

        self.state.portfolio_cycle += 1
        portfolio_start = time.perf_counter()
        print(
            f"\n{'~'*60}\n"
            f"PORTFOLIO CYCLE #{self.state.portfolio_cycle}  "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"{'~'*60}"
        )

        # Prepare opportunities for the allocator: scout list MERGED with
        # current book holdings that the scout dropped. Those get a neutral
        # carry score (50) and the "portfolio_hold" marker so the manager
        # judges them on their fresh analysis instead of auto-exiting them.
        seen_ids = {op.coin_id for op in self.state.coin_opportunities}
        opportunities_json: list[dict[str, Any]] = [
            {
                "coin_id": op.coin_id,
                "symbol": op.symbol,
                "score": op.score,
                "risk_tier": op.risk_tier,
                "reason": op.reason,
            }
            for op in self.state.coin_opportunities
        ]
        for pos in open_positions:
            if pos["coin_id"] not in seen_ids:
                opportunities_json.append({
                    "coin_id": pos["coin_id"],
                    "symbol": plan_symbols.get(pos["coin_id"], pos["coin_id"].upper()),
                    "score": 50,
                    "risk_tier": "MEDIUM",
                    "reason": "Portfolio hold not in scout list — carried with neutral score, judged on fresh analysis.",
                })

        analysed_coins_json = self.state.analysed_coins.copy()
        analysis_verdicts_json = {
            cid: self.state.analysis_verdicts.get(cid, "")
            for cid in analysed_coins_json
        }

        # ATR snapshot (choice A: cache reuse) feeding risk_levels_json and
        # the deterministic backfill below. Same (coin, vs, days) key the
        # analysis ATR tool already fetched -> cache hit, zero new calls.
        risk_snapshot: dict[str, dict[str, float]] = {}
        for entry in opportunities_json + [
            {"coin_id": pos["coin_id"]} for pos in open_positions
        ]:
            coin = entry.get("coin_id", "")
            if coin and coin not in risk_snapshot:
                price, atr = _atr_snapshot_for(coin)
                if price is not None and atr is not None:
                    risk_snapshot[coin] = {"current_price": price, "atr_pct": atr}
        self.state.last_risk_levels = risk_snapshot

        try:
            result = CouncilPortfolioCrew().crew().kickoff(
                inputs={
                    "account_size": self.state.account_size,
                    "base_currency": self.state.base_currency,
                    "max_total_exposure_pct": self.state.max_total_exposure_pct,
                    "max_single_position_pct": self.state.max_single_position_pct,
                    "open_positions_json": json.dumps(open_positions),
                    "opportunities_json": json.dumps(opportunities_json),
                    "analysed_coins_json": json.dumps(analysed_coins_json),
                    "analysis_verdicts_json": json.dumps(analysis_verdicts_json),
                    "risk_levels_json": json.dumps(risk_snapshot),
                }
            )

            plan = result.pydantic
            if plan is not None:
                self.state.portfolio_plan = plan.model_dump()
            else:
                self.state.portfolio_plan = _extract_portfolio_plan(result.raw)

            # Deterministic totals: never trust the crew's arithmetic.
            self.state.portfolio_plan = _recompute_portfolio_totals(
                self.state.portfolio_plan, self.state.account_size
            )

            # Deterministic SL/TP backfill from the ATR snapshot (close
            # actions included, informational). Totals pass through untouched.
            self.state.portfolio_plan = _backfill_risk_levels(
                self.state.portfolio_plan, risk_snapshot
            )

            # Force-close positions that exceeded max_hold_days.
            self.state.portfolio_plan = _apply_max_hold(
                self.state.portfolio_plan,
                self.state.position_opened_utc,
                self.state.max_hold_days,
                datetime.now(timezone.utc),
            )
            self.state.portfolio_plan = _recompute_portfolio_totals(
                self.state.portfolio_plan, self.state.account_size
            )

            self.state.last_portfolio_utc = datetime.now(timezone.utc).isoformat()
            self.state.last_portfolio_duration_s = time.perf_counter() - portfolio_start

            OUTPUT_DIR.mkdir(exist_ok=True)
            snapshot = {
                "saved_utc": self.state.last_portfolio_utc,
                "portfolio_cycle": self.state.portfolio_cycle,
                "duration_s": round(self.state.last_portfolio_duration_s, 1),
                "account_size": self.state.account_size,
                "base_currency": self.state.base_currency,
                "max_total_exposure_pct": self.state.max_total_exposure_pct,
                "max_single_position_pct": self.state.max_single_position_pct,
                "plan": self.state.portfolio_plan,
                "opportunities": opportunities_json,
                "analysed_coins": analysed_coins_json,
            }
            (OUTPUT_DIR / "portfolio_plan.json").write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with (OUTPUT_DIR / "portfolio_history.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": self.state.last_portfolio_utc,
                    "cycle": self.state.portfolio_cycle,
                    "duration_s": round(self.state.last_portfolio_duration_s, 1),
                    "account_size": self.state.account_size,
                    "base_currency": self.state.base_currency,
                    "total_target": self.state.portfolio_plan.get("total_target", 0.0),
                    "total_exposure_pct": self.state.portfolio_plan.get("total_exposure_pct", 0.0),
                    "cash_remaining": self.state.portfolio_plan.get("cash_remaining", 0.0),
                    "n_positions": sum(
                        1 for a in self.state.portfolio_plan.get("actions", [])
                        if a.get("target", 0) > 0
                    ),
                }, ensure_ascii=False) + "\n")

            total_target = self.state.portfolio_plan.get("total_target", 0.0)
            total_pct = self.state.portfolio_plan.get("total_exposure_pct", 0.0)
            cash = self.state.portfolio_plan.get("cash_remaining", 0.0)
            actions = self.state.portfolio_plan.get("actions", [])
            open_actions = [a for a in actions if a.get("target", 0) > 0]
            currency = self.state.base_currency.upper()
            print(
                f"Portfolio plan: {len(open_actions)} open position(s), "
                f"{total_target:,.0f} {currency} total ({total_pct:.1f}% exposure), "
                f"{cash:,.0f} {currency} cash remaining"
            )
            for a in actions:
                print(f"  {a.get('action','hold'):>8} {a.get('symbol','?'):>6} "
                      f"{a.get('current',0):>8,.0f} → {a.get('target',0):>8,.0f} {currency}  ({a.get('reason','')})")
            print(f"⏱ Portfolio took {_fmt_dur(self.state.last_portfolio_duration_s)}")

        except Exception as exc:
            self.state.last_portfolio_duration_s = time.perf_counter() - portfolio_start
            print(f"ERROR: Portfolio crew failed: {exc}. Keeping previous plan.")
            print(f"⏱ Portfolio took {_fmt_dur(self.state.last_portfolio_duration_s)} (failed)")
            # Do not clear previous plan; just log the failure

        # Reset analysed_coins for next cycle
        self.state.analysed_coins = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_portfolio_plan(raw_output: str) -> dict[str, Any]:
    """Best-effort extraction of portfolio plan dict from raw text."""
    import re
    text = raw_output.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines()
            if not line.strip().startswith("```")
        )
    for pattern in (r'\{[^{}]*"actions"\s*:.*\}', r'\{.*"actions".*\}'):
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "actions" in parsed:
            return parsed
    return {}


def _risk_levels_for(
    current_price: object,
    atr_pct: object,
    stop_atr_mult: float = 0.5,
    take_atr_mult: float = 1.0,
) -> tuple[float | None, float | None]:
    """Deterministic ATR-based stop/take (pure math, no network).

    0.5x ATR stop and 1x ATR take (2:1 reward-risk).
    Floor: tiny ATR (<1%) uses 1% so stablecoins still get a guardrail.
    Invalid (non-positive price or ATR) returns (None, None).
    Close actions use identical levels — informational guardrails, not orders.
    """
    try:
        price = float(current_price or 0.0)
        atr = float(atr_pct or 0.0)
    except (TypeError, ValueError):
        return (None, None)
    if price <= 0 or atr <= 0:
        return (None, None)
    eff_pct = max(atr, 1.0)
    stop = round(price * (1 - (eff_pct * stop_atr_mult) / 100), 6)
    take = round(price * (1 + (eff_pct * take_atr_mult) / 100), 6)
    return (stop, take)


_ATR_SNAPSHOT_DAYS = 30  # same key the analysis ATR tool fetches -> cache hit
_ATR_SNAPSHOT_PERIOD = 14  # Wilder smoothing period, mirrors ATRTool


def _atr_snapshot_for(coin_id: str) -> tuple[float | None, float | None]:
    """Retry-then-None ATR snapshot from cached OHLC (pure read, no LLM).

    Uses the shared `_fetch_ohlcv` cache (choice A: cache reuse) with the
    same (coin, vs, days) key the analysis ATR tool already fetched, so a
    fresh cycle costs zero new network calls. Returns
    (current_price, atr_pct); on empty data or fetch error (e.g. 429)
    returns (None, None) and the backfill leaves levels null.
    """
    try:
        ohlcv = _fetch_ohlcv(coin_id, DEFAULT_VS_CURRENCY, _ATR_SNAPSHOT_DAYS)
    except Exception:
        return (None, None)
    if not ohlcv or len(ohlcv) < _ATR_SNAPSHOT_PERIOD + 1:
        return (None, None)
    try:
        highs = [float(r[2]) for r in ohlcv]
        lows = [float(r[3]) for r in ohlcv]
        closes = [float(r[4]) for r in ohlcv]
        trs = [
            max(highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]))
            for i in range(1, len(closes))
        ]
        atr = sum(trs[:_ATR_SNAPSHOT_PERIOD]) / _ATR_SNAPSHOT_PERIOD
        for v in trs[_ATR_SNAPSHOT_PERIOD:]:
            atr = (atr * (_ATR_SNAPSHOT_PERIOD - 1) + v) / _ATR_SNAPSHOT_PERIOD
        price = closes[-1]
        if price <= 0:
            return (None, None)
        return (round(price, 6), round(atr / price * 100, 4))
    except (TypeError, ValueError, IndexError):
        return (None, None)


def _apply_max_hold(
    plan: dict[str, Any],
    opened_utc: dict[str, str],
    max_hold_days: int,
    now: datetime,
) -> dict[str, Any]:
    """Force ``close`` on any positioned action held > max_hold_days.

    Age is derived from ``opened_utc`` (ISO timestamp recorded when the
    position was first opened). Actions with an unknown open time are
    left alone — better to keep exposure than guess at age.
    """
    if not isinstance(plan, dict) or max_hold_days <= 0:
        return plan
    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        return plan
    for a in actions:
        if not isinstance(a, dict):
            continue
        if a.get("action") == "close":
            continue
        target = a.get("target", 0) or 0
        if target <= 0:
            continue
        opened_iso = opened_utc.get(a.get("coin_id", ""))
        if not opened_iso:
            continue
        try:
            opened = datetime.fromisoformat(opened_iso)
        except ValueError:
            continue
        age_days = (now - opened).total_seconds() / 86400.0
        if age_days > max_hold_days:
            a["action"] = "close"
            a["target"] = 0
            a["current"] = a.get("current", 0)
            a["notes"] = (a.get("notes", "") + f" [max_hold_days={max_hold_days} force-close]").strip()
            a["stop_loss"] = None
            a["take_profit"] = None
    return plan


def _within_pct(value: object, ref: float | None, pct: float) -> bool:
    if ref is None or ref <= 0:
        return False
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return abs(v - ref) <= (pct / 100.0) * ref


def _within_pct_tolerance() -> float:
    """Crew SL/TP tolerance around the computed ATR levels (env-overridable)."""
    try:
        return float(os.getenv("COUNCIL_RISK_LEVEL_TOLERANCE_PCT", "20"))
    except ValueError:
        return 20.0


def _sane_levels(
    stop: object, take: object, entry: float | None
) -> bool:
    """Structural check: stop must sit on the loss side of entry and take on
    the profit side. The flow is long-only, so loss side = below entry and
    profit side = above entry. Non-numeric or non-positive -> insane."""
    if entry is None or entry <= 0:
        return False
    try:
        s = float(stop)  # type: ignore[arg-type]
        t = float(take)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if s <= 0 or t <= 0:
        return False
    return s < entry and t > entry


def _backfill_risk_levels(
    plan: dict[str, Any], candidates: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Anchor each action's stop/take to the deterministic ATR set.

    Per action, the crew's explicit numbers are kept only if BOTH hold:
      1. structure: stop below entry and take above entry (long-only flow),
      2. proximity: each within 20% of the computed ATR stop/take.
    Otherwise the computed ATR values are used. Any remaining unknowns
    (missing entry price, missing candidate) fall back to the computed set.
    Close actions get identical informational levels; their target stays 0.
    """
    actions = plan.get("actions", []) if isinstance(plan, dict) else []
    if not isinstance(actions, list):
        return plan
    for a in actions:
        if not isinstance(a, dict):
            continue
        cand = candidates.get(a.get("coin_id", "")) if isinstance(candidates, dict) else None
        if not isinstance(cand, dict):
            a.setdefault("stop_loss", None)
            a.setdefault("take_profit", None)
            continue

        computed_stop, computed_take = _risk_levels_for(
            cand.get("current_price"), cand.get("atr_pct")
        )

        try:
            entry = float(cand.get("current_price"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            entry = None

        crew_stop = a.get("stop_loss")
        crew_take = a.get("take_profit")

        # No computed level at all -> keep crew values if sane, else None.
        if computed_stop is None or computed_take is None:
            if _sane_levels(crew_stop, crew_take, entry):
                a["stop_loss"], a["take_profit"] = crew_stop, crew_take
            else:
                a.setdefault("stop_loss", None)
                a.setdefault("take_profit", None)
            continue

        keep_crew = (
            _sane_levels(crew_stop, crew_take, entry)
            and _within_pct(crew_stop, computed_stop, _within_pct_tolerance())
            and _within_pct(crew_take, computed_take, _within_pct_tolerance())
        )
        if not keep_crew:
            a["stop_loss"] = computed_stop
            a["take_profit"] = computed_take
    return plan


def _recompute_portfolio_totals(plan: dict[str, Any], account_size: float) -> dict[str, Any]:
    """Recompute totals deterministically from action targets.

    The crew's arithmetic is unreliable (it has divided by the exposure
    budget instead of the account size). Totals are pure math: sum the
    action targets, divide by the account for exposure %, subtract from
    the account for cash. Never trust the LLM's numbers.
    """
    actions = plan.get("actions", []) if isinstance(plan, dict) else []
    total = 0.0
    if isinstance(actions, list):
        for a in actions:
            if isinstance(a, dict):
                try:
                    total += max(0.0, float(a.get("target", 0) or 0))
                except (TypeError, ValueError):
                    continue
    total = round(total, 2)
    plan["total_target"] = total
    plan["total_exposure_pct"] = round(total / account_size * 100, 2) if account_size > 0 else 0.0
    plan["cash_remaining"] = round(account_size - total, 2)
    return plan


def _extract_scout_items(raw_output: str) -> list[dict[str, Any]]:
    """Best-effort extraction of opportunity dicts from raw scout text.

    Handles the {"opportunities": [...]} envelope, a bare [...] array,
    and markdown code fences. Returns [] when nothing parses.
    """
    import re

    text = raw_output.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines()
            if not line.strip().startswith("```")
        )
    for pattern in (r'\{[^{}]*"opportunities"\s*:.*\}', r"\[.*\]"):
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed = parsed.get("opportunities", [])
        if isinstance(parsed, list) and all(isinstance(i, dict) for i in parsed):
            return parsed
    return []


def _extract_verdict(raw_report: str, max_chars: int = 600) -> str:
    """Pull a short verdict excerpt from the tail of an analysis report.

    Risk/portfolio conclusions are normally stated at the end of the
    report, so the tail is the most decision-relevant slice. Bounded to
    max_chars to keep the portfolio kickoff payload small.
    """
    if not raw_report:
        return ""
    tail = raw_report.strip().splitlines()
    kept: list[str] = []
    total = 0
    for line in reversed(tail):
        if total + len(line) + 1 > max_chars:
            break
        kept.insert(0, line)
        total += len(line) + 1
    if kept:
        return "\n".join(kept).strip()
    # Degenerate case: one giant line — fall back to the raw tail slice.
    return raw_report.strip()[-max_chars:]


def _build_report(opportunity: CoinOpportunity, analysis_raw: str, duration_s: float = 0.0) -> str:
    """Wrap the crew's raw analysis output with a scout context header."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"# Crypto Council Report: {opportunity.symbol}\n\n"
        f"**Generated:** {ts}  \n"
        f"**Analysis time:** {_fmt_dur(duration_s)}  \n"
        f"**Scout Score:** {opportunity.score}/100 | "
        f"**Risk Tier:** {opportunity.risk_tier}  \n"
        f"**Scout Reason:** {opportunity.reason}  \n"
        f"**Signals:** {', '.join(opportunity.signals)}  \n\n"
        f"---\n\n"
    )
    return header + analysis_raw


def _append_to_consolidated(
    cycle: int, opportunity: CoinOpportunity, analysis_raw: str,
    duration_s: float = 0.0,
) -> None:
    """Append a single coin's analysis to the consolidated results file."""
    CONSOLIDATED_RESULTS.parent.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    separator = "=" * 72

    entry = (
        f"\n{separator}\n"
        f"  CYCLE #{cycle}  |  {ts}  |  ⏱ {_fmt_dur(duration_s)}\n"
        f"  {opportunity.symbol} ({opportunity.coin_id})\n"
        f"  Score: {opportunity.score}/100  |  Risk: {opportunity.risk_tier}\n"
        f"  Signals: {', '.join(opportunity.signals) or '—'}\n"
        f"  Reason: {opportunity.reason or '—'}\n"
        f"{separator}\n\n"
        f"{analysis_raw}\n"
    )

    with open(CONSOLIDATED_RESULTS, "a", encoding="utf-8") as fh:
        fh.write(entry)


def _parse_inputs(argv: list[str]) -> dict[str, Any]:
    """Parse CLI arguments into Flow inputs.

    Accepted flags:
      --account-size <float>   account size (default: 10000)
      --base-currency <str>    currency label (default: usd, e.g. toman, eur)
      --period <int>           RSI period (default: 14)
      --max-total-exposure <float>  max total invested % (default: 60)
      --max-single-position <float> max single-coin % (default: 20)
      --once                   run one full cycle and exit
    """
    inputs: dict[str, Any] = {
        "account_size": 10_000.0,
        "base_currency": "usd",
        "period": DEFAULT_RSI_PERIOD,
        "max_total_exposure_pct": 30.0,
        "max_single_position_pct": 5.0,
        "max_hold_days": 4,
    }
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("--account-size", "--account_size") and i + 1 < len(argv):
            try:
                inputs["account_size"] = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ("--base-currency", "--base_currency") and i + 1 < len(argv):
            inputs["base_currency"] = argv[i + 1].lower().strip()
            i += 2
        elif arg in ("--period",) and i + 1 < len(argv):
            try:
                inputs["period"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg == "--once":
            inputs["_once"] = True
            i += 1
        elif arg in ("--max-total-exposure", "--max_total_exposure") and i + 1 < len(argv):
            try:
                inputs["max_total_exposure_pct"] = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ("--max-single-position", "--max_single_position") and i + 1 < len(argv):
            try:
                inputs["max_single_position_pct"] = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ("--max-hold-days", "--max_hold_days") and i + 1 < len(argv):
            try:
                inputs["max_hold_days"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 2
        else:
            i += 1
    return inputs


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------

async def _run_async(
    account_size: float,
    period: int,
    run_once: bool = False,
    max_total_exposure_pct: float = 30.0,
    max_single_position_pct: float = 5.0,
    max_hold_days: int = 4,
    base_currency: str = "usd",
) -> None:
    """
    Outer async scheduler.

    Scout cadence:   every SCOUT_INTERVAL_SECONDS (2 hours)
    Analysis cadence: every ANALYSIS_INTERVAL_SECONDS (5 minutes)
    Portfolio step runs immediately after each analysis cycle.

    On the first iteration both scout and analysis run immediately.
    After that, the analysis loop runs every 5 minutes, and the scout
    refreshes its coin list every 2 hours.

    Cycle locking: No new cycle (scout or analysis) starts if any
    older cycle is still running. This prevents overlapping execution.
    """
    flow = CryptoCouncilFlow()
    flow.state.account_size = account_size
    flow.state.base_currency = base_currency
    flow.state.period = period
    flow.state.max_total_exposure_pct = max_total_exposure_pct
    flow.state.max_single_position_pct = max_single_position_pct
    flow.state.max_hold_days = max_hold_days

    last_scout_time: float = 0.0        # force scout on first iteration
    last_analysis_time: float = 0.0     # force analysis on first iteration
    run_started = time.perf_counter()

    # Cycle locking: ensure no overlapping cycles
    # A single lock prevents any new cycle from starting if any cycle is running
    cycle_lock = asyncio.Lock()

    iteration = 0
    while True:
        now = time.time()
        iteration += 1

        # --- Scout (every 2 hours) ---
        if (now - last_scout_time >= SCOUT_INTERVAL_SECONDS):
            if cycle_lock.locked():
                print(f"[Scheduler] Scout cycle skipped: previous cycle still running")
            else:
                async with cycle_lock:
                    try:
                        await asyncio.to_thread(flow.run_scout)
                    except Exception as exc:
                        print(f"ERROR: Scout cycle failed: {exc}. Continuing.")
                    last_scout_time = time.time()

        # --- Analysis (every 5 minutes) ---
        if (now - last_analysis_time >= ANALYSIS_INTERVAL_SECONDS):
            if cycle_lock.locked():
                print(f"[Scheduler] Analysis cycle skipped: previous cycle still running")
            else:
                async with cycle_lock:
                    try:
                        await asyncio.to_thread(flow.analyse_coins)
                        await asyncio.to_thread(flow.manage_portfolio)
                    except Exception as exc:
                        print(f"ERROR: Analysis cycle failed: {exc}. Continuing.")
                    last_analysis_time = time.time()

        if run_once:
            print(
                f"\n{'='*60}\n"
                f"CYCLE SUMMARY  (total wall-clock {_fmt_dur(time.perf_counter() - run_started)})\n"
                f"  Scout:     {_fmt_dur(flow.state.last_scout_duration_s)}\n"
                f"  Analysis:  {_fmt_dur(flow.state.last_analysis_duration_s)}"
                + (
                    f"  [{', '.join(f'{c} {_fmt_dur(d)}' for c, d in flow.state.last_coin_durations_s.items())}]"
                    if flow.state.last_coin_durations_s else ""
                ) + "\n"
                f"  Portfolio: {_fmt_dur(flow.state.last_portfolio_duration_s)}\n"
                f"{'='*60}"
            )
            print("Ran one full cycle (--once flag). Exiting.")
            break

        # Sleep for 60 s between scheduler ticks (granularity: 1 min)
        print(
            f"\n[Scheduler] Cycle {iteration} done "
            f"(scout {_fmt_dur(flow.state.last_scout_duration_s)} | "
            f"analysis {_fmt_dur(flow.state.last_analysis_duration_s)} | "
            f"portfolio {_fmt_dur(flow.state.last_portfolio_duration_s)}). "
            f"Next analysis check in ~60 s."
        )
        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def kickoff() -> None:
    """Primary entry point — starts the continuous Flow scheduler."""
    inputs = _parse_inputs(sys.argv)
    run_once = inputs.pop("_once", False)
    asyncio.run(
        _run_async(
            account_size=inputs.get("account_size", 10_000.0),
            base_currency=inputs.get("base_currency", "usd"),
            period=inputs.get("period", DEFAULT_RSI_PERIOD),
            run_once=run_once,
            max_total_exposure_pct=inputs.get("max_total_exposure_pct", 30.0),
            max_single_position_pct=inputs.get("max_single_position_pct", 5.0),
            max_hold_days=inputs.get("max_hold_days", 4),
        )
    )


def plot() -> None:
    """Generate a flow diagram HTML file."""
    flow = CryptoCouncilFlow()
    flow.plot("crypto_council_flow")
    print("Flow diagram saved to crypto_council_flow.html")


if __name__ == "__main__":
    kickoff()
