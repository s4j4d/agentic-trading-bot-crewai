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
    output/<coin_id>_report.md.

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
os.environ["OTEL_SDK_DISABLED"] = "true"
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from crewai.flow import Flow, listen, start

from crypto_council_flow.crews.council.council_crew import (
    CouncilAnalysisCrew,
    CouncilScoutCrew,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOUT_INTERVAL_SECONDS: int = 2 * 60 * 60   # 2 hours
ANALYSIS_INTERVAL_SECONDS: int = 5 * 60     # 5 minutes
DEFAULT_VS_CURRENCY: str = "usd"
DEFAULT_RSI_PERIOD: int = 14
OUTPUT_DIR: Path = Path("output")


# ---------------------------------------------------------------------------
# Pydantic state model
# ---------------------------------------------------------------------------

class CoinOpportunity(BaseModel):
    """A single coin opportunity as returned by the market_scout."""

    rank: int
    coin_id: str
    symbol: str
    name: str
    score: int
    signals: list[str] = Field(default_factory=list)
    risk_tier: str = "MEDIUM"
    reason: str = ""


class CryptoCouncilState(BaseModel):
    """Persistent Flow state passed between steps."""

    # User-configurable inputs (supplied at kickoff)
    account_size_usd: float = 10_000.0
    period: int = DEFAULT_RSI_PERIOD

    # Scout outputs — refreshed every 2 hours
    coin_opportunities: list[CoinOpportunity] = Field(default_factory=list)
    last_scout_utc: str = ""

    # Analysis tracking — refreshed every 5 minutes
    last_analysis_utc: str = ""
    analysis_reports: dict[str, str] = Field(default_factory=dict)  # coin_id → report path

    # Internal timing state
    scout_cycle: int = 0
    analysis_cycle: int = 0


# ---------------------------------------------------------------------------
# Flow definition
# ---------------------------------------------------------------------------

class CryptoCouncilFlow(Flow[CryptoCouncilState]):
    """
    Two-step CrewAI Flow:

      run_scout     — market_scout produces a ranked coin list
      analyse_coins — three analysts process each coin sequentially
    """

    @start()
    def run_scout(self) -> None:
        """
        Run CouncilScoutCrew to refresh the opportunity list.
        Called once at startup and then every 2 hours by the outer scheduler.
        """
        self.state.scout_cycle += 1
        print(
            f"\n{'='*60}\n"
            f"SCOUT CYCLE #{self.state.scout_cycle}  "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"{'='*60}"
        )

        result = CouncilScoutCrew().crew().kickoff()
        raw_output = result.raw.strip()

        # Parse the JSON array from the scout output
        opportunities: list[CoinOpportunity] = []
        try:
            # Strip markdown code fences if the LLM wrapped the JSON
            if raw_output.startswith("```"):
                raw_output = "\n".join(
                    line for line in raw_output.splitlines()
                    if not line.strip().startswith("```")
                )
            data: list[dict[str, Any]] = json.loads(raw_output)
            for item in data:
                try:
                    opportunities.append(CoinOpportunity(**item))
                except Exception:
                    # Skip malformed entries rather than crashing
                    pass
        except json.JSONDecodeError:
            # LLM didn't return pure JSON — try to extract the array
            import re
            match = re.search(r"\[.*\]", raw_output, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                    for item in data:
                        try:
                            opportunities.append(CoinOpportunity(**item))
                        except Exception:
                            pass
                except json.JSONDecodeError:
                    pass

        if not opportunities:
            print(
                "WARNING: Scout returned no parseable opportunities. "
                "Keeping previous coin list."
            )
        else:
            self.state.coin_opportunities = opportunities
            self.state.last_scout_utc = datetime.now(timezone.utc).isoformat()
            print(
                f"Scout found {len(opportunities)} opportunity(ies):\n"
                + "\n".join(
                    f"  #{op.rank} {op.symbol} ({op.coin_id}) — score {op.score} — {op.reason}"
                    for op in opportunities
                )
            )

    @listen(run_scout)
    def analyse_coins(self) -> None:
        """
        Run CouncilAnalysisCrew for each coin in the current opportunity list.
        Called immediately after run_scout and then every 5 minutes by the scheduler.
        """
        if not self.state.coin_opportunities:
            print("No coins to analyse. Waiting for scout results.")
            return

        self.state.analysis_cycle += 1
        OUTPUT_DIR.mkdir(exist_ok=True)

        print(
            f"\n{'-'*60}\n"
            f"ANALYSIS CYCLE #{self.state.analysis_cycle}  "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Analysing {len(self.state.coin_opportunities)} coin(s)...\n"
            f"{'-'*60}"
        )

        for opportunity in self.state.coin_opportunities:
            coin_id = opportunity.coin_id
            symbol = opportunity.symbol

            print(f"\n  ▶ Analysing {symbol} ({coin_id})...")

            try:
                result = CouncilAnalysisCrew().crew().kickoff(
                    inputs={
                        "symbol": symbol,
                        "coin_id": coin_id,
                        "vs_currency": DEFAULT_VS_CURRENCY,
                        "period": self.state.period,
                        "account_size_usd": self.state.account_size_usd,
                    }
                )

                report_path = OUTPUT_DIR / f"{coin_id}_report.md"
                report_content = _build_report(opportunity, result.raw)
                report_path.write_text(report_content, encoding="utf-8")
                self.state.analysis_reports[coin_id] = str(report_path)
                print(f"    ✓ Report saved → {report_path}")

            except Exception as exc:
                print(f"    ✗ Analysis failed for {symbol}: {exc}")

        self.state.last_analysis_utc = datetime.now(timezone.utc).isoformat()
        print(
            f"\nAnalysis cycle #{self.state.analysis_cycle} complete. "
            f"Reports: {list(self.state.analysis_reports.keys())}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_report(opportunity: CoinOpportunity, analysis_raw: str) -> str:
    """Wrap the crew's raw analysis output with a scout context header."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"# Crypto Council Report: {opportunity.symbol}\n\n"
        f"**Generated:** {ts}  \n"
        f"**Scout Score:** {opportunity.score}/100 | "
        f"**Risk Tier:** {opportunity.risk_tier}  \n"
        f"**Scout Reason:** {opportunity.reason}  \n"
        f"**Signals:** {', '.join(opportunity.signals)}  \n\n"
        f"---\n\n"
    )
    return header + analysis_raw


def _parse_inputs(argv: list[str]) -> dict[str, Any]:
    """Parse CLI arguments into Flow inputs.

    Accepted flags:
      --account-size <float>   account size in USD (default: 10000)
      --period <int>           RSI period (default: 14)
      --once                   run one full cycle and exit
    """
    inputs: dict[str, Any] = {
        "account_size_usd": 10_000.0,
        "period": DEFAULT_RSI_PERIOD,
    }
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("--account-size", "--account_size") and i + 1 < len(argv):
            try:
                inputs["account_size_usd"] = float(argv[i + 1])
            except ValueError:
                pass
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
        else:
            i += 1
    return inputs


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------

async def _run_async(
    account_size_usd: float,
    period: int,
    run_once: bool = False,
) -> None:
    """
    Outer async scheduler.

    Scout cadence:   every SCOUT_INTERVAL_SECONDS (2 hours)
    Analysis cadence: every ANALYSIS_INTERVAL_SECONDS (5 minutes)

    On the first iteration both scout and analysis run immediately.
    After that, the analysis loop runs every 5 minutes, and the scout
    refreshes its coin list every 2 hours.
    """
    flow = CryptoCouncilFlow()
    flow.state.account_size_usd = account_size_usd
    flow.state.period = period

    last_scout_time: float = 0.0        # force scout on first iteration
    last_analysis_time: float = 0.0     # force analysis on first iteration
    print('######################',last_scout_time,"\n")
    import time

    iteration = 0
    while True:
        now = time.monotonic()
        iteration += 1

        # --- Scout (every 2 hours) ---
        if now - last_scout_time >= SCOUT_INTERVAL_SECONDS:
            await asyncio.to_thread(flow.run_scout)
            last_scout_time = time.monotonic()

        # --- Analysis (every 5 minutes) ---
        if now - last_analysis_time >= ANALYSIS_INTERVAL_SECONDS:
            await asyncio.to_thread(flow.analyse_coins)
            last_analysis_time = time.monotonic()

        if run_once:
            print("\nRan one full cycle (--once flag). Exiting.")
            break

        # Sleep for 60 s between scheduler ticks (granularity: 1 min)
        print(
            f"\n[Scheduler] Cycle {iteration} done. "
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
            account_size_usd=inputs.get("account_size_usd", 10_000.0),
            period=inputs.get("period", DEFAULT_RSI_PERIOD),
            run_once=run_once,
        )
    )


def plot() -> None:
    """Generate a flow diagram HTML file."""
    flow = CryptoCouncilFlow()
    flow.plot("crypto_council_flow")
    print("Flow diagram saved to crypto_council_flow.html")


if __name__ == "__main__":
    kickoff()
