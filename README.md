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
   3 `CoinOpportunity` records (score 0–100, risk tier, one-sentence reason).
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

### Trading horizon: hourly candles, max-hold in days

The council is configured for short-horizon trading, not swing trades:

- **Indicators are computed from 1-hour exchange candles** (`exchange_ohlc`
  `timeframe="1h"`), with a short default history window (`days=3`).
- **`--max-hold-days` (default 1, env `COUNCIL_MAX_HOLD_DAYS`)** is the
  deterministic ceiling on how long a position may be held, and is mirrored
  into every crew kickoff (`max_hold_days`) plus all task prompts and
  `SKILL.md` files so the LLM reasons about the same intraday horizon.
- **`risk_tick` runs every 60s** (`RISK_TICK_INTERVAL_SECONDS`) with **no LLM
  call**: it force-closes on stop-loss / take-profit hits, closes positions
  past `max_hold_days`, and appends a paper P&L row to
  `output/portfolio_history.jsonl`.
- Cadence: scout 2h → analysis 5m → risk tick 60s, all gated by the cycle lock.

### Scout tool-call discipline

The scout is prompted to emit independent tool calls concurrently but with
**one tool per call**: a single standalone JSON argument object per call.
`exchange_batch_ticker` (`{"symbols": [...]}`) and `exchange_ohlc`
(`{"symbol": ..., "timeframe": ..., "days": ...}`) take different shapes and
must never be merged into one call — CrewAI rejects merged payloads with
`json_decode_error: Extra data`.

`VolatilityScreenerTool` intersects its CoinGecko volatility ranking with the
exchange's active markets on `EXCHANGE_QUOTE` before scoring
(`require_exchange_listing=True`, default), so untradable coins can never
top the volatility ranking; it reports `exchange_filter.{applied,note}` and
falls back to the unfiltered ranking if the exchange list is unavailable.

## Agents & tools

| Agent | Role | Key tools |
|---|---|---|
| `market_scout` | Find 3 tradable short-term opportunities | `ExchangeMarketsTool`, `ExchangeBatchTickerTool`, `ExchangeOHLCTool`, `TrendingCoinsTool`, `MomentumScreenerTool`, `VolatilityScreenerTool`, `NewListingsTool` |
| `technical_analyst` | Chart + indicator read per coin | `RSI`, `MACD`, `BollingerBands`, `EMA-Cross`, `ATR` (exchange 1h OHLC, shared cache via `COUNCIL_OHLC_TTL_S`, 6s CoinGecko throttle) |
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
python -m crypto_council_flow.main --once --max-total-exposure 60 --max-single-position 20 --rsi-period 14 --atr-period 14
```

## Outputs (`output/`, git-ignored)

- `<coin_id>_report.md` — per-coin full analysis (with `**Analysis time:**` header)
- `all_analysis_results.txt` — consolidated per-cycle log
- `portfolio_plan.json` — latest rebalance snapshot (actions, totals, `duration_s`)
- `portfolio_history.jsonl` — one row per portfolio cycle (trend log)
- `paper_ledger.json` — paper fills (entry price/qty) feeding the P&L section
- `scripts/portfolio_panel.py` — stdlib-only HTML panel generator
  (`python scripts/portfolio_panel.py` → `output/portfolio_panel.html`;
  allocation state plus a P&L section computed from the paper ledger)

Console prints per-cycle timings (`⏱ Scout/Analysis/Portfolio`) and a
`CYCLE SUMMARY` with total wall-clock.

## Configuration

| Knob | Where | Default |
|---|---|---|
| Scout / analysis cadence | `main.py` `SCOUT_INTERVAL_SECONDS` / `ANALYSIS_INTERVAL_SECONDS` | 2h / 5m |
| Deterministic risk tick | `main.py` `RISK_TICK_INTERVAL_SECONDS` | 60s |
| Max hold (days) | `--max-hold-days` / `COUNCIL_MAX_HOLD_DAYS` env | 1 |
| RSI / ATR look-back (candles) | `--rsi-period` / `--atr-period`, or `COUNCIL_RSI_PERIOD` / `COUNCIL_ATR_PERIOD` env | 14 / 14 |
| Candle timeframe | `exchange_ohlc` `timeframe` (technical analysis) | `1h` |
| Analysis parallelism | `COUNCIL_ANALYSIS_WORKERS` env | 3 |
| Tool HTTP timeouts | `_DEFAULT_TIMEOUT` per tool module | 10s |
| OHLC cache / throttle | `tools/technical_indicators.py`, `COUNCIL_OHLC_TTL_S` env | 5-min TTL, 6s min gap |
| Exposure caps | `--max-total-exposure` / `--max-single-position` | 60% / 20% |
| Exchange backend | `EXCHANGE*` env (`EXCHANGE_API_BASE`, `EXCHANGE_QUOTE`) | Nobitex apiv2, usdt quote |
| CrewAI generic memory | `COUNCIL_MEMORY` env | `false` (off; trade journal is separate) |
| Volatility screener exchange filter | `require_exchange_listing` on `VolatilityScreenerTool` | `true` |

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
