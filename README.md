# Crypto Council Flow — agentic paper-trading council (CrewAI)

A multi-agent crypto research + paper-portfolio pipeline built on [CrewAI Flows](https://docs.crewai.com).
A council of specialist LLM agents scouts the market, analyses each candidate coin
(technical → sentiment → risk), and reconciles everything into a cap-aware
**paper** rebalance plan. For now it places no real orders — all positions are
simulated on paper; live order execution is planned for the future.

> ⚠️ Not financial advice. Research/experimentation tooling only.

## How it works

Three-step `CryptoCouncilFlow` (`src/crypto_council_flow/main.py`):

1. **`run_scout`** (every 2h) — `CouncilScoutCrew` (single `market_scout` agent)
   scans CoinGecko screeners + exchange data and emits a ranked shortlist of
   3–7 `CoinOpportunity` records (score 0–100, risk tier, one-sentence reason).
2. **`analyse_coins`** (every 5m, over the current scout list) — for each coin,
   `CouncilAnalysisCrew` runs a sequential pipeline:
   `technical_analyst` → `sentiment_analyst` → `risk_manager`.
   Coins are processed **in parallel** via `ThreadPoolExecutor`
   (default 3 workers, override with `COUNCIL_ANALYSIS_WORKERS`);
   the main thread merges results, writes `output/<coin_id>_report.md`,
   and appends to `output/all_analysis_results.txt`.
3. **`manage_portfolio`** (after each analysis) — `CouncilPortfolioCrew`
   (`portfolio_manager` agent + deterministic allocator tools) reconciles open
   paper positions with fresh signals and writes `output/portfolio_plan.json`
   plus a `output/portfolio_history.jsonl` trend row.

Cycle locking: a single `asyncio.Lock` guarantees no two cycles (scout /
analysis / portfolio) overlap; a timer that fires mid-cycle is skipped and
retried on the next tick.

## Agents & tools

| Agent | Role | Key tools |
|---|---|---|
| `market_scout` | Find 3–7 tradable short-term opportunities | `ExchangeMarkets/Ticker/OHLCTool`, `TrendingCoins`, `MomentumScreener`, `VolatilityScreener`, `NewListings`, `UpcomingCatalysts` |
| `technical_analyst` | Chart + indicator read per coin | `RSI`, `MACD`, `BollingerBands`, `EMA-Cross`, `ATR` (shared 15-min OHLC cache, 6s CoinGecko throttle) |
| `sentiment_analyst` | Market psychology per coin | `FearGreedIndex`, `CryptoNews`, `CommunitySentiment`, `MarketDominance` |
| `risk_manager` | Position sizing, VaR, trade plan | `PositionSizing` (Kelly + fixed-risk), `LiquidationPrice`, `PortfolioVaR`, `AssetCorrelation` |
| `portfolio_manager` | Cap-aware rebalance plan (paper) | `PortfolioExposureTool`, `RebalanceAllocatorTool` (score-weighted pro-rata, single/total caps, $10 dust → hold, exit candidates → close), `RiskLevelsTool` (1×-ATR stop-loss + 2:1 take-profit price levels, close actions included, informational — no orders placed) |

Design rules: one canonical Pydantic DTO per domain with `before`-validators
that normalise LLM field drift; deterministic math lives in tools (no network,
no LLM); LLM totals in the portfolio plan are overwritten by a deterministic
recompute (`total_target`, exposure %, cash) before anything is persisted.

Currency-neutral: all amounts carry a `base_currency` label (default `usd`;
e.g. run with `--base-currency toman`). No FX conversion is performed.

## Installation

Requires Python >=3.10 <3.14. This project uses [UV](https://docs.astral.sh/uv/)
for dependency management. Install dependencies:

```bash
pip install uv
crewai install          # or: pip install -e .
```

Configure the LLM backend in `.env` (keys only — values are yours):

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=...    # OpenAI-compatible endpoint; Ollama works
MODEL=...              # e.g. an ollama or openai model id
```

## Running the Project

One full cycle and exit:

```bash
python -m crypto_council_flow.main --once
```

Continuous scheduler (scout 2h / analysis 5m):

```bash
crewai run
# or: python -m crypto_council_flow.main
```

Useful flags for `--once`:

```bash
python -m crypto_council_flow.main --once --account-size 5000000 --base-currency toman
python -m crypto_council_flow.main --once --max-total-exposure 60 --max-single-position 20 --period 14
```

## Outputs (`output/`, git-ignored)

- `<coin_id>_report.md` — per-coin full analysis (with `**Analysis time:**` header)
- `all_analysis_results.txt` — consolidated per-cycle log
- `portfolio_plan.json` — latest rebalance snapshot (actions, totals, `duration_s`)
- `portfolio_history.jsonl` — one row per portfolio cycle (trend log)
- `scripts/portfolio_panel.py` — stdlib-only HTML panel generator
  (`python scripts/portfolio_panel.py` → `output/portfolio_panel.html`;
  allocation state only, no P&L — there are no entry prices or fills)

Console prints per-cycle timings (`⏱ Scout/Analysis/Portfolio`) and a
`CYCLE SUMMARY` with total wall-clock.

## Configuration

| Knob | Where | Default |
|---|---|---|
| Scout / analysis cadence | `main.py` `SCOUT_INTERVAL_SECONDS` / `ANALYSIS_INTERVAL_SECONDS` | 2h / 5m |
| Analysis parallelism | `COUNCIL_ANALYSIS_WORKERS` env | 3 |
| Tool HTTP timeouts | `_DEFAULT_TIMEOUT` per tool module | 10s |
| OHLC cache / throttle | `tools/technical_indicators.py` | 15-min TTL, 6s min gap |
| Exposure caps | `--max-total-exposure` / `--max-single-position` | 60% / 20% |
| Exchange backend | `EXCHANGE*` env (`EXCHANGE_API_BASE`, `EXCHANGE_QUOTE`) | Nobitex apiv2, usdt quote |

## Tests

```bash
.venv/Scripts/python -m pytest tests/ -q
```

Covers DTO/model validation, crew config, cycle locking, portfolio
allocator/exposure math, parallel-analysis merge + concurrency, scout parsing,
and a 30-day backtest harness.

## Layout

```
src/crypto_council_flow/
  main.py                  # Flow: scout → analyse (parallel) → portfolio + scheduler
  crews/council/
    council_crew.py        # 3 crews, 5 agents, canonical DTOs
    config/                # agents.jsonc, *_tasks.yaml, agents.yaml placeholder
    skills/                # SKILL.md per agent (market-scout, risk-manager, …)
  tools/                   # scout / technical / sentiment / risk / exchange / portfolio
scripts/portfolio_panel.py # HTML allocation panel (stdlib only)
tests/                     # pytest suite
.hermes/plans/             # design plans (e.g. sub-15-min flow plan)
```

## Support

For CrewAI questions: [docs](https://docs.crewai.com) · [GitHub](https://github.com/joaomdmoura/crewai) · [Discord](https://discord.com/invite/X4JWnZnxPb).
