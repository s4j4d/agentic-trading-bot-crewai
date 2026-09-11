# Design: Crypto Council Flow — Automatic Coin Selection Architecture

## Overview

The Crypto Council Flow is a long-running CrewAI-based trading intelligence system
that continuously identifies short-term cryptocurrency opportunities and produces
risk-adjusted trade plans — with no manual coin selection required. The user
provides only `account_size_usd` and the RSI `period`; everything else is automated.

The system is split into two independently scheduled crews wired together by a
CrewAI Flow. A scout crew refreshes the opportunity list every two hours. An
analysis crew processes each coin from that list every five minutes via a sequential
pipeline of three specialist agents.

---

## Architecture

### High-Level Flow

```
CryptoCouncilFlow (async scheduler, 60s tick)
│
├── run_scout()  [every 2h — @start]
│   └── CouncilScoutCrew
│       └── market_scout agent
│           ├── TrendingCoinsTool     (CoinGecko)
│           ├── MomentumScreenerTool  (CoinGecko)
│           ├── NewListingsTool       (CoinGecko)
│           └── UpcomingCatalystsTool (CryptoPanic)
│           → JSON array → list[CoinOpportunity] → state.coin_opportunities
│
└── analyse_coins()  [every 5min — @listen(run_scout)]
    └── CouncilAnalysisCrew (per coin, sequential)
        ├── technical_analyst agent
        │   ├── RSITool, MACDTool, BollingerBandsTool
        │   ├── EMACrossTool, ATRTool
        │   └── → structured markdown report
        ├── sentiment_analyst agent  [context: technical task output]
        │   ├── FearGreedIndexTool, CryptoNewsTool
        │   ├── CommunitySentimentTool, MarketDominanceTool
        │   └── → structured markdown report
        └── risk_manager agent  [context: technical + sentiment outputs]
            ├── PositionSizingTool, LiquidationPriceTool
            ├── PortfolioVaRTool, AssetCorrelationTool
            └── → final trade plan → output/<coin_id>_report.md
```

---

## Components

### 1. CryptoCouncilFlow (`main.py`)

The top-level orchestrator. It is a typed `Flow[CryptoCouncilState]` subclass with
two methods wired via CrewAI's event decorators.

```python
@start()
def run_scout(self) -> None: ...

@listen(run_scout)
def analyse_coins(self) -> None: ...
```

`run_scout` and `analyse_coins` are called from an async outer scheduler
(`_run_async`) that drives both cadences independently via monotonic time tracking:

- Scout interval: `SCOUT_INTERVAL_SECONDS = 7200` (2 hours)
- Analysis interval: `ANALYSIS_INTERVAL_SECONDS = 300` (5 minutes)
- Scheduler tick: `asyncio.sleep(60)` (1 minute granularity)

Blocking crew calls are delegated to a thread pool via `asyncio.to_thread(...)` so
the event loop is never blocked.

**Entry points:**

| Name | Called by | Description |
|------|-----------|-------------|
| `kickoff()` | `crewai run` / pyproject script | Starts the continuous scheduler |
| `plot()` | `crewai flow plot` | Generates flow diagram HTML |
| `_run_async(...)` | `kickoff()` | Core async loop |

**CLI flags:**

| Flag | Default | Effect |
|------|---------|--------|
| `--account-size <float>` | 10000.0 | Account size in USD |
| `--period <int>` | 14 | RSI look-back period |
| `--once` | False | Run one full cycle and exit |

---

### 2. CryptoCouncilState (`main.py`)

A Pydantic `BaseModel` that carries all flow state between steps.

```python
class CryptoCouncilState(BaseModel):
    account_size_usd: float = 10_000.0
    period: int = 14
    coin_opportunities: list[CoinOpportunity] = []
    last_scout_utc: str = ""
    last_analysis_utc: str = ""
    analysis_reports: dict[str, str] = {}   # coin_id → report file path
    scout_cycle: int = 0
    analysis_cycle: int = 0
```

`coin_opportunities` is the handoff contract between the scout crew and the
analysis crew — the scout writes it, the analysis crew reads it.

---

### 3. CoinOpportunity (`main.py`)

A Pydantic model representing a single entry in the scout output.

```python
class CoinOpportunity(BaseModel):
    rank: int
    coin_id: str           # CoinGecko slug — used as API key by downstream agents
    symbol: str            # Uppercase ticker
    name: str
    score: int             # 0-100 composite opportunity score
    signals: list[str]
    risk_tier: str         # LOW | MEDIUM | HIGH | EXTREME
    reason: str
```

`coin_id` is the critical field — it is used directly as the CoinGecko API
identifier by all four downstream tools (technical indicators, sentiment, risk).

---

### 4. CouncilScoutCrew (`council_crew.py`)

A `@CrewBase` class containing only the `market_scout` agent and
`market_scout_task`. It is instantiated fresh on every scout cycle.

```python
@CrewBase
class CouncilScoutCrew:
    tasks_config: str = "config/tasks.yaml"

    @agent
    def market_scout(self) -> Agent: ...

    @task
    def market_scout_task(self) -> Task: ...

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks,
                    process=Process.sequential, verbose=True)
```

The scout agent has `reasoning=True` so it plans its four-tool scan sequence
before acting, which reduces out-of-order tool usage.

---

### 5. CouncilAnalysisCrew (`council_crew.py`)

A `@CrewBase` class with three agents (`technical_analyst`, `sentiment_analyst`,
`risk_manager`) running in a sequential pipeline. Context chaining ensures each
agent receives prior agents' outputs:

- `sentiment_analysis_task` has `context: [technical_analysis_task]`
- `risk_assessment_task` has `context: [technical_analysis_task, sentiment_analysis_task]`

The crew is instantiated once per coin per analysis cycle.

**Inputs expected at kickoff:**

| Input | Example |
|-------|---------|
| `symbol` | `"SOL"` |
| `coin_id` | `"solana"` |
| `vs_currency` | `"usd"` |
| `period` | `14` |
| `account_size_usd` | `10000.0` |

---

### 6. Agent Definitions (`config/agents.jsonc`)

Agents are defined in JSONC format (JSON with `//` comments and parenthesised
multi-line strings). A custom `_load_jsonc()` function parses the file at module
load time before the `@CrewBase` decorator runs.

Each agent is instantiated from the parsed dict with explicit field mapping
rather than `config=self.agents_config[...]` so that tools and skills can be
attached programmatically.

| Agent | Role | Tools |
|-------|------|-------|
| `market_scout` | Crypto Market Scout | TrendingCoins, MomentumScreener, NewListings, UpcomingCatalysts |
| `technical_analyst` | `{symbol}` Technical Analyst | RSI, MACD, BollingerBands, EMACross, ATR |
| `sentiment_analyst` | `{symbol}` Sentiment Analyst | FearGreed, CryptoNews, CommunitySentiment, MarketDominance |
| `risk_manager` | Crypto Risk Manager | PositionSizing, LiquidationPrice, PortfolioVaR, AssetCorrelation |

All agents have `reasoning=True` (reflect-then-act) and `allow_delegation=False`.

---

### 7. Task Definitions (`config/tasks.yaml`)

| Task | Agent | Context |
|------|-------|---------|
| `market_scout_task` | `market_scout` | — |
| `technical_analysis_task` | `technical_analyst` | — |
| `sentiment_analysis_task` | `sentiment_analyst` | `technical_analysis_task` |
| `risk_assessment_task` | `risk_manager` | `technical_analysis_task`, `sentiment_analysis_task` |

`market_scout_task.expected_output` specifies a pure JSON array with no prose or
markdown — this is required for the deterministic JSON parsing in `run_scout()`.

---

### 8. Agent Skills (`skills/<agent-name>/SKILL.md`)

Each agent has a `SKILL.md` loaded at the `INSTRUCTIONS` level via
`crewai.skills.loader.load_skill()`. Skills encode domain methodology that would
otherwise be buried in task descriptions.

| Agent | Skill | Key Content |
|-------|-------|-------------|
| `market_scout` | `market-scout` | Four-signal scoring model, exclusion criteria, output format |
| `technical_analyst` | `technical-analyst` | Five-indicator sequence, synthesis rules, key level construction |
| `sentiment_analyst` | `sentiment-analyst` | Composite score formula, divergence detection, catalyst flag override table |
| `risk_manager` | `risk-manager` | Kelly + fixed-risk sizing, leverage rules, sentiment alignment decision tree |

---

### 9. Tools (`tools/`)

All 16 tools are `BaseTool` subclasses using free public APIs with no API keys.

#### Scout Tools (`scout_tools.py`)

| Class | Tool Name | API | Input Schema |
|-------|-----------|-----|--------------|
| `TrendingCoinsTool` | `trending_coins` | CoinGecko `/search/trending` | `top_n: int = 10` |
| `MomentumScreenerTool` | `momentum_screener` | CoinGecko `/coins/markets` | `top_n`, `min_volume_usd`, `max_market_cap_rank` |
| `NewListingsTool` | `new_listings` | CoinGecko `/coins/markets` (order=id_desc) | `top_n: int = 20` |
| `UpcomingCatalystsTool` | `upcoming_catalysts` | CryptoPanic free API | `limit: int = 20` |

`UpcomingCatalystsTool` has hardcoded keyword lists (`_BULLISH_KEYWORDS`,
`_BEARISH_KEYWORDS`) for catalyst classification and groups results by coin symbol.

#### Technical Indicator Tools (`technical_indicators.py`)

All five tools fetch OHLCV from CoinGecko `/coins/{id}/ohlc` using a shared
`_fetch_ohlcv()` helper.

| Class | Tool Name | Algorithm |
|-------|-----------|-----------|
| `RSITool` | `rsi_indicator` | Wilder smoothing on gains/losses |
| `MACDTool` | `macd_indicator` | EMA difference + signal EMA + histogram |
| `BollingerBandsTool` | `bollinger_bands_indicator` | SMA ± n×std, %B, band width% |
| `EMACrossTool` | `ema_cross_indicator` | Fast/slow EMA, golden/death cross detection |
| `ATRTool` | `atr_indicator` | True Range Wilder smoothing, ATR% of price |

#### Sentiment Tools (`sentiment_tools.py`)

| Class | Tool Name | API |
|-------|-----------|-----|
| `FearGreedIndexTool` | `fear_greed_index` | alternative.me `/fng/` |
| `CryptoNewsTool` | `crypto_news_sentiment` | CryptoPanic free API |
| `CommunitySentimentTool` | `community_sentiment` | CoinGecko `/coins/{id}` |
| `MarketDominanceTool` | `market_dominance` | CoinGecko `/global` |

#### Risk Tools (`risk_tools.py`)

All four tools share `_fetch_daily_closes()` for price history retrieval.

| Class | Tool Name | Key Algorithm |
|-------|-----------|---------------|
| `PositionSizingTool` | `position_sizing` | Kelly criterion (half-Kelly) + fixed-risk; returns min of both |
| `LiquidationPriceTool` | `liquidation_price` | `entry × (1 ± (1/leverage − maintenance_margin_rate))` |
| `PortfolioVaRTool` | `portfolio_var` | Historical VaR + CVaR from 90-day log-return distribution |
| `AssetCorrelationTool` | `asset_correlation` | Pearson correlation of aligned daily log returns |

---

## Data Models

### CoinOpportunity (scout output / state contract)

```python
CoinOpportunity(
    rank=1,
    coin_id="solana",          # CoinGecko slug
    symbol="SOL",
    name="Solana",
    score=82,                  # 0–100 composite
    signals=["trending_rank_3", "momentum_+7.2%_24h", "catalyst:mainnet_upgrade"],
    risk_tier="MEDIUM",        # LOW | MEDIUM | HIGH | EXTREME
    reason="Trending top 3 with strong 24h momentum and mainnet upgrade catalyst.",
)
```

### Opportunity Scoring Formula

```
score = (momentum_signal × 0.40)
      + (trending_signal × 0.30)
      + (catalyst_signal × 0.15)
      + (listing_signal × 0.15)

where:
  momentum_signal  = clamp(gain_24h / 15% × 100, 0, 100)
  trending_signal  = (11 - rank) × 10          [rank 1→100, rank 10→10]
  catalyst_signal  = min(net_votes × 5, 100)
  listing_signal   = 60 if new_listing else 40
```

### Analysis Report Structure

Each analysis cycle writes `output/<coin_id>_report.md` via `_build_report()`:

```
# Crypto Council Report: {SYMBOL}
Generated: {timestamp}
Scout Score: {score}/100 | Risk Tier: {risk_tier}
Scout Reason: {reason}
Signals: {signals}

---

## Technical Analysis: {SYMBOL}/{vs_currency}
[structured markdown from technical_analysis_task]

## Sentiment Analysis: {SYMBOL}
[structured markdown from sentiment_analysis_task]

## Risk Assessment: {SYMBOL} Trade Plan
[structured markdown from risk_assessment_task]
```

---

## Key Design Decisions

### JSONC Agent Configuration

Agents are defined in `config/agents.jsonc` rather than the standard
`agents.yaml`. This enables `//` comments and parenthesised multi-line strings
in the backstory/goal fields for readability. The `_load_jsonc()` function
applies four regex transformations before JSON parsing:
1. Strip `//` single-line comments
2. Strip `/* */` block comments
3. Collapse parenthesised multi-line strings (join adjacent quoted lines)
4. Remove trailing commas before `}` or `]`

### Defensive Scout Output Parsing

The scout task's `expected_output` demands a pure JSON array, but LLMs
sometimes wrap output in markdown fences. `run_scout()` applies two layers
of defence:
1. Strip code fences before parsing
2. If `json.loads` fails, use regex `\[.*\]` extraction on the raw string

Individual malformed `CoinOpportunity` entries are silently skipped; only
structurally valid objects are added to `state.coin_opportunities`.

### Two @CrewBase Classes vs. One

A single `@CrewBase` class with all four agents would run all four tasks on
every invocation. Splitting into `CouncilScoutCrew` and `CouncilAnalysisCrew`
allows independent scheduling cadences (2h scout vs. 5min analysis) and avoids
re-running the expensive three-agent pipeline just to refresh the coin list.

### Context Chaining for Agent Communication

Rather than passing structured Pydantic objects between tasks, context chaining
(`context: [task_name]`) in `tasks.yaml` passes the full textual output of prior
tasks into the next agent's context window. This preserves the LLM's ability to
reason over the narrative, not just extract fields — the risk manager, for
example, needs the entry zone and invalidation level expressed as prose by the
technical analyst to derive its stop-loss distance.

### All Tools Use Free Public APIs

No API keys are required. This eliminates secrets management complexity and
makes the project runnable immediately after `uv sync`. The trade-off is
CoinGecko rate limiting on free tier (10–30 requests/minute). The 60-second
scheduler tick and sequential per-coin analysis naturally spread requests
across time.

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Scout returns unparseable JSON | Warning printed; previous `coin_opportunities` retained |
| Individual malformed opportunity entry | Silently skipped; valid entries still processed |
| CoinGecko rate limit (`429`) / network error | Tool returns `{"error": "..."}` JSON; agent reports the error in its output rather than crashing |
| Analysis crew exception for one coin | `print(f"✗ Analysis failed for {symbol}: {exc}")`; other coins continue |
| Scout returns empty list after parsing | Warning printed; previous list retained; no analysis attempted |
| `asyncio.to_thread` failure | Exception propagates to `_run_async`; loop continues on next tick |

---

## File Structure

```
src/crypto_council_flow/
├── main.py                          # CryptoCouncilFlow, CryptoCouncilState, CoinOpportunity
├── tools/
│   ├── __init__.py                  # Exports all 16 tool classes
│   ├── scout_tools.py               # TrendingCoins, MomentumScreener, NewListings, UpcomingCatalysts
│   ├── technical_indicators.py      # RSI, MACD, BollingerBands, EMACross, ATR
│   ├── sentiment_tools.py           # FearGreed, CryptoNews, CommunitySentiment, MarketDominance
│   └── risk_tools.py                # PositionSizing, LiquidationPrice, PortfolioVaR, AssetCorrelation
└── crews/council/
    ├── council_crew.py              # CouncilScoutCrew, CouncilAnalysisCrew, _load_jsonc
    ├── config/
    │   ├── agents.jsonc             # 4 agent definitions (JSONC with comments)
    │   └── tasks.yaml               # 4 task definitions
    └── skills/
        ├── market-scout/SKILL.md
        ├── technical-analyst/SKILL.md
        ├── sentiment-analyst/SKILL.md
        └── risk-manager/SKILL.md
output/                              # Generated reports: <coin_id>_report.md
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid
executions of a system — essentially, a formal statement about what the system
should do. Properties serve as the bridge between human-readable specifications
and machine-verifiable correctness guarantees.*

### Property 1: Scout output always contains valid CoinOpportunity objects

*For any* JSON array returned by the scout agent that contains a mix of valid and
malformed entries, the parsed `coin_opportunities` list must contain only objects
that satisfy the full `CoinOpportunity` Pydantic schema, and no exception is
raised during parsing.

**Validates: Requirements 1.4, 5.5**

---

### Property 2: Exclusion criteria always remove disqualified coins

*For any* set of candidate coins where at least one coin satisfies an exclusion
criterion (volume < $1M, no live price, bearish catalyst flag, gain > 30% without
catalyst, stablecoin/wrapped asset), that coin must not appear in the final
opportunity output.

**Validates: Requirements 1.2**

---

### Property 3: Opportunity score formula is applied consistently

*For any* valid combination of `(momentum_signal, trending_signal, catalyst_signal,
listing_signal)`, the composite opportunity score must equal
`momentum × 0.40 + trending × 0.30 + catalyst × 0.15 + listing × 0.15`, rounded
to an integer, and must be in the range [0, 100].

**Validates: Requirements 1.3**

---

### Property 4: RSI is always in [0, 100] and signal classification is correct

*For any* price series of length ≥ `period + 1` with all positive values, the RSI
output must be in the closed interval [0, 100], and the `signal` field must be
`"overbought"` iff RSI > 70, `"oversold"` iff RSI < 30, and `"neutral"` otherwise.

**Validates: Requirements 2.2**

---

### Property 5: ATR is non-negative and ATR% equals ATR/price × 100

*For any* valid OHLCV input where all highs, lows, and closes are positive and
highs ≥ lows, the returned `atr` must be ≥ 0 and `atr_pct` must equal
`round(atr / current_price * 100, 4)`.

**Validates: Requirements 2.3**

---

### Property 6: Position sizing always returns the more conservative of fixed-risk and half-Kelly

*For any* valid `(account_size_usd, entry_price, stop_loss_price, win_rate,
reward_risk_ratio, max_risk_pct)`, the `recommended.units` must equal
`min(fixed_risk_units, kelly_units)` where both are computed from the same inputs.

**Validates: Requirements 4.1**

---

### Property 7: Liquidation price formula is correct for both long and short positions

*For any* valid `(entry_price, leverage, maintenance_margin_rate)`:
- For a **long** position: `liquidation_price == entry × (1 − (1/leverage − mmr))`
- For a **short** position: `liquidation_price == entry × (1 + (1/leverage − mmr))`

**Validates: Requirements 4.2**

---

### Property 8: Asset correlation is symmetric

*For any* two coin price series A and B with sufficient data overlap, the Pearson
correlation of (A, B) must equal the Pearson correlation of (B, A) within floating-
point epsilon (1e-10).

**Validates: Requirements 4.4**

---

### Property 9: Report header contains all CoinOpportunity fields

*For any* valid `CoinOpportunity` object and any non-empty `analysis_raw` string,
the report produced by `_build_report()` must contain the `symbol`, `score`,
`risk_tier`, `reason`, and all entries from `signals` as substrings in the header
section.

**Validates: Requirements 5.2**

---

### Property 10: JSONC loader produces valid JSON for any well-formed JSONC input

*For any* string that is valid JSONC (JSON extended with `//` comments, `/* */` block
comments, parenthesised multi-line strings, and trailing commas), the output of
`_load_jsonc()` must parse as valid JSON without raising an exception, and all
non-comment content must be preserved correctly.

**Validates: Requirements 5.3**

---

### Property 11: Catalyst direction detection is consistent with keyword presence

*For any* news article title that contains at least one bearish keyword (`hack`,
`exploit`, `breach`, `lawsuit`, `ban`, `delist`, `rug`, `fraud`, `vulnerability`,
`attack`) and no bullish keywords, the `catalyst_direction` must be `"bearish"`.
*For any* title with only bullish keywords and no bearish keywords, the direction
must be `"bullish"`.

**Validates: Requirements 3.3**
