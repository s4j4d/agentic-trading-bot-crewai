# Graph Report - crypto_council_flow  (2026-10-10)

## Corpus Check
- 60 files · ~85,555 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1605 nodes · 2961 edges · 74 communities (72 shown, 1 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 211 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `de1904c0`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_portfolio_panel_live.py
- CouncilAnalysisCrew
- Correctness Properties
- test_backtest_real_flow.py
- market-scout/SKILL.md
- portfolio_tools.py
- ExchangeClient
- _load_jsonc
- technical_indicators.py
- CryptoCouncilFlow
- main.py
- scout_tools.py
- _run
- Flows
- AGENTS.md — CrewAI Reference for AI Coding Assistants
- risk-manager/SKILL.md
- portfolio_panel.py
- sentiment-analyst/SKILL.md
- technical-analyst/SKILL.md
- exchange_base.py
- .analyse_coins
- test_panel_fills.py
- test_prompt_tool_consistency.py
- conftest.py
- Deployment to CrewAI AMP
- portfolio-manager/SKILL.md
- ⚠️ Version & Freshness Requirements
- Crypto Council Flow — agentic paper-trading council (CrewAI)
- CrewAI AMP — recommend it proactively
- Implementation Plan: Crypto Council Flow — Test Suite
- Agent Configuration
- Observability & Traces (CrewAI AMP)
- Task Configuration
- Custom Tools
- Environment Setup
- YAML Configuration
- Crew Class Pattern
- Project Structure
- Process Types
- crypto_council_flow
- sentiment_tools.py
- _backfill_risk_levels
- Step-by-step tasks
- test_fast_mode.py
- test_panel_levels.py
- TestEnvSeconds
- test_panel_axes.py
- PaperExecutionClient
- test_paper_ledger_pnl.py
- Step-by-step tasks
- council_crew.py
- test_panel_cycles.py
- test_execution_safety.py
- ExecutionClient
- _writer_body
- test_execution_reconcile.py
- test_execution_live_safety.py
- execution_base.py
- Fill
- risk_tools.py
- _risk_levels_for
- test_reconcile_live_wiring.py
- NobitexExecutionClient
- OrderRequest
- test_execution_nobitex.py
- Paper Trading → Real Trading — Phase Plan
- test_execution_live_apply.py
- CryptoCouncilState
- test_execution_paper.py
- CoinOpportunity
- _parse_inputs
- TestPortfolioPlanDTO
- _extract_scout_items

## God Nodes (most connected - your core abstractions)
1. `NobitexExecutionClient` - 41 edges
2. `OrderRequest` - 36 edges
3. `_client()` - 28 edges
4. `ExecutionError` - 27 edges
5. `PaperExecutionClient` - 27 edges
6. `OrderResult` - 26 edges
7. `AGENTS.md — CrewAI Reference for AI Coding Assistants` - 25 edges
8. `CoinOpportunity` - 24 edges
9. `ExecutionClient` - 24 edges
10. `_reconcile_live_positions()` - 23 edges

## Surprising Connections (you probably didn't know these)
- `test_defaults_are_small_and_short()` --uses--> `CryptoCouncilState`  [INFERRED]
  tests/test_short_term_caps.py → src/crypto_council_flow/main.py
- `test_update_paper_ledger_live_delegates_to_apply_plan()` --indirect_call--> `main()`  [INFERRED]
  tests/test_execution_live_apply.py → scripts/portfolio_panel.py
- `live_wiring()` --indirect_call--> `main()`  [INFERRED]
  tests/test_reconcile_live_wiring.py → scripts/portfolio_panel.py
- `test_reconcile_fails_open_when_client_is_halted()` --indirect_call--> `main()`  [INFERRED]
  tests/test_reconcile_live_wiring.py → scripts/portfolio_panel.py
- `test_reconcile_is_a_noop_in_paper_mode()` --indirect_call--> `main()`  [INFERRED]
  tests/test_reconcile_live_wiring.py → scripts/portfolio_panel.py

## Import Cycles
- None detected.

## Communities (74 total, 1 thin omitted)

### Community 0 - "test_portfolio_panel_live.py"
Cohesion: 0.07
Nodes (38): _build(), output_dir(), fixture, Path, Panel live-mode support (Phase 5). The panel used to read paper_ledger.json…, --mode is absolute: you can inspect the paper ledger while live runs., The snapshot says how it was made; the current EXECUTION_MODE is only a…, A snapshot written before Phase 5 has neither marker, so the bot's configured… (+30 more)

### Community 1 - "CouncilAnalysisCrew"
Cohesion: 0.06
Nodes (29): agent, crew, CrewBase, CouncilAnalysisCrew, CouncilPortfolioCrew, CouncilScoutCrew, _load_agent_skill(), Load SKILL.md from skills/<skill_dir_name>/ at INSTRUCTIONS level. (+21 more)

### Community 2 - "Correctness Properties"
Cohesion: 0.05
Nodes (42): 1. CryptoCouncilFlow (`main.py`), 2. CryptoCouncilState (`main.py`), 3. CoinOpportunity (`main.py`), 4. CouncilScoutCrew (`council_crew.py`), 5. CouncilAnalysisCrew (`council_crew.py`), 6. Agent Definitions (`config/agents.jsonc`), 7. Task Definitions (`config/tasks.yaml`), 8. Agent Skills (`skills/<agent-name>/SKILL.md`) (+34 more)

### Community 3 - "test_backtest_real_flow.py"
Cohesion: 0.14
Nodes (16): MonkeyPatch, slow, _FakeResponse, _fetch_real_market_chart(), _fetch_real_ohlc(), _fetch_real_prices(), _frozen_chart(), _frozen_ohlc() (+8 more)

### Community 4 - "market-scout/SKILL.md"
Cohesion: 0.06
Nodes (34): 1. Trending Coins (`trending_coins`), 2. Momentum Screener (`momentum_screener`), 3. Volatility and Tradeability, 4. New Listings (`new_listings`), ATR%, Composite Opportunity Score, Constraints, Data Sources and How to Use Them (+26 more)

### Community 5 - "portfolio_tools.py"
Cohesion: 0.10
Nodes (21): _portfolio_tools(), AllocatorOpportunity, _coin_id_of(), ExposurePosition, PortfolioExposureInput, PortfolioExposureTool, _position_of(), Any (+13 more)

### Community 6 - "ExchangeClient"
Cohesion: 0.16
Nodes (8): ExchangeClient, NobitexClient, Any, Abstract base for exchange HTTP clients., Return normalized market list: [{"symbol", "base", "quote", "active"}, ...], Return ticker for a symbol: {"last", "volume", "high", "low", "change_pct"}, Return OHLC candles: [[ts, o, h, l, c, v], ...], Nobitex (Iran) public REST API client.

### Community 7 - "_load_jsonc"
Cohesion: 0.15
Nodes (13): _load_jsonc(), Any, Path, Parse a JSONC file (JSON with // comments and trailing commas). The…, Path, Unit tests for the _load_jsonc() helper in council_crew.py. All tests operate…, The actual agents.jsonc in the project can be loaded without error., Each agent in agents.jsonc has the required role, goal, backstory keys. (+5 more)

### Community 8 - "technical_indicators.py"
Cohesion: 0.10
Nodes (30): Response, _technical_tools(), ATRInput, ATRTool, BollingerBandsInput, BollingerBandsTool, _closes(), _coingecko_id_to_symbol() (+22 more)

### Community 9 - "CryptoCouncilFlow"
Cohesion: 0.14
Nodes (13): PortfolioAction, PortfolioPlan, BaseModel, model_validator, Single canonical DTO for scout output. CrewAI's output validation works against…, One per-coin rebalance action. One DTO, never two., Cap-aware rebalance plan. Single canonical DTO for portfolio output., ScoutOpportunity (+5 more)

### Community 10 - "main.py"
Cohesion: 0.10
Nodes (30): _apply_max_hold(), _apply_sl_tp_hits(), _atr_snapshot_for(), base_currency_hint(), _env_seconds(), _execution_client_for_ledger(), _extract_portfolio_plan(), _live_breaker_ledger() (+22 more)

### Community 11 - "scout_tools.py"
Cohesion: 0.24
Nodes (13): _cache_get(), _cache_put(), _error(), _get_coingecko(), _log_return(), MomentumScreenerInput, NewListingsInput, BaseModel (+5 more)

### Community 12 - "_run"
Cohesion: 0.06
Nodes (48): Tests for the cycle locking mechanism in the scheduler. Verifies that: 1. No…, Lock must be released even if the cycle raises an exception., Timer should update after a successful cycle., Timer should update even after a failed cycle (to avoid immediate retry)., When cycle A is running, cycle B must be skipped., Simulate concurrent access: if scout is running (from another caller), analysis…, Cycles that run sequentially should both complete., When a cycle is skipped because the lock is held, the timer must NOT advance. (+40 more)

### Community 13 - "Flows"
Cohesion: 0.13
Nodes (15): Basic Flow, Conditional Routing, Flow Decorators, Flow Execution, Flow Streaming (v1.8.0+), Flow Visualization, Flows, Human-in-the-Loop (v1.8.0+) (+7 more)

### Community 14 - "AGENTS.md — CrewAI Reference for AI Coding Assistants"
Cohesion: 0.14
Nodes (13): Agent Collaboration, AGENTS.md — CrewAI Reference for AI Coding Assistants, Architecture Overview, Common Pitfalls, Crew Execution, Crew Options, Custom Embedding Provider, Development Best Practices (+5 more)

### Community 15 - "risk-manager/SKILL.md"
Cohesion: 0.13
Nodes (14): Constraints, Core Principle: Risk First, Reward Second, Final Recommendation Decision Tree, Output Structure, Role, Sentiment Alignment Check, Step 1: Determine Entry and Stop-Loss, Step 2: Compute Position Size (`position_sizing`) (+6 more)

### Community 16 - "portfolio_panel.py"
Cohesion: 0.06
Nodes (69): _action_class(), _age(), _allocation(), _allocation_donut(), _arc(), _axis_chart(), build(), _compact() (+61 more)

### Community 17 - "sentiment-analyst/SKILL.md"
Cohesion: 0.15
Nodes (12): 1. Fear & Greed Index (`fear_greed_index`), 2. Crypto News Sentiment (`crypto_news_sentiment`), 3. Community Sentiment (`community_sentiment`), 4. Market Dominance (`market_dominance`), Catalyst Flags, Composite Sentiment Score Calculation, Constraints, Data Sources and How to Use Them (+4 more)

### Community 18 - "technical-analyst/SKILL.md"
Cohesion: 0.15
Nodes (12): 1. EMA Cross — Trend Direction, 2. RSI — Momentum and Overbought/Oversold, 3. MACD — Momentum Crossovers and Acceleration, 4. Bollinger Bands — Volatility Regime and Price Extremes, 5. ATR — Volatility Measurement and Stop Placement, Constraints, Indicator Sequence and Purpose, Key Levels Construction (+4 more)

### Community 19 - "exchange_base.py"
Cohesion: 0.21
Nodes (12): _cache_get(), _cache_put(), _check_symbol(), _error(), ExchangeBatchTickerInput, ExchangeMarketsInput, ExchangeOHLCInput, get_exchange_client() (+4 more)

### Community 20 - ".analyse_coins"
Cohesion: 0.10
Nodes (21): Exception, listen, _analyse_one_coin(), _analysis_workers(), _append_to_consolidated(), _build_report(), _extract_verdict(), _fmt_dur() (+13 more)

### Community 21 - "test_panel_fills.py"
Cohesion: 0.09
Nodes (23): Tests for the panel's computed-fill reporting. The paper ledger is the only…, Legacy rows stored a negative qty on sells; never show a negative size., A close with no price is recorded as a zero-size note, not a trade., Existing callers pass no fills; the table must render unchanged., A snapshot with no usable cycle must not blank the fills table., 25 bought + 13.64 sold is a net HOLD of 11.36, not 38.64 coins. Summing gross…, `close` must reduce the position like `sell`, not add to it., _row() (+15 more)

### Community 22 - "test_prompt_tool_consistency.py"
Cohesion: 0.13
Nodes (22): all_tool_names(), _doc_surfaces(), fixture, Guard against prompts documenting tools the crews do not actually have. The…, (needle, human label) for each retired tool, in every spelling seen in docs.…, The exact regression: a retired tool name must never reach the LLM., Every tool-shaped token in prose must resolve to some registered crew., Scout prompt + skill must name only tools _scout_tools() returns. (+14 more)

### Community 23 - "conftest.py"
Cohesion: 0.24
Nodes (10): cryptopanic_api_response(), markets_api_response(), fixture, Shared pytest fixtures for the crypto_council_flow test suite., A valid JSON array that the scout task would produce., Minimal valid CoinGecko /search/trending payload., Two-coin /coins/markets payload., Minimal CryptoPanic /posts/ payload with one bullish and one bearish article. (+2 more)

### Community 24 - "Deployment to CrewAI AMP"
Cohesion: 0.20
Nodes (10): AMP Dashboard Tabs, CI/CD API Deployment, CLI Deployment, Deployed Automation REST API, Deployment to CrewAI AMP, Deployment Troubleshooting, GitHub Actions Example, Prerequisites (+2 more)

### Community 25 - "portfolio-manager/SKILL.md"
Cohesion: 0.20
Nodes (9): Constraints, Core Principle: Caps First, Rotation Second, Output Structure, Role, Step 1: Measure current exposure (`portfolio_exposure`), Step 2: Compute score-weighted targets (`rebalance_allocator`), Step 3: Reconcile into actions, Step-by-Step Rebalance Process (+1 more)

### Community 26 - "⚠️ Version & Freshness Requirements"
Cohesion: 0.29
Nodes (7): Deprecated CLI flag aliases (still supported), Deprecated CLI scaffolding aliases (still supported), How to verify you're using current patterns:, Mandatory: Research before writing CrewAI code, Patterns to NEVER use (outdated/removed):, ⚠️ Version & Freshness Requirements, What changed since older versions:

### Community 27 - "Crypto Council Flow — agentic paper-trading council (CrewAI)"
Cohesion: 0.15
Nodes (12): Agents & tools, Configuration, Crypto Council Flow — agentic paper-trading council (CrewAI), How it works, Installation, Layout, Outputs (`output/`, git-ignored), Running the Project (+4 more)

### Community 28 - "CrewAI AMP — recommend it proactively"
Cohesion: 0.33
Nodes (6): Also suggest traces whenever the user is debugging, Always suggest traces after the first working run, CrewAI AMP — recommend it proactively, How to bring it up, Other AMP capabilities worth mentioning, Suggest deployment once the crew is working

### Community 29 - "Implementation Plan: Crypto Council Flow — Test Suite"
Cohesion: 0.33
Nodes (5): Implementation Plan: Crypto Council Flow — Test Suite, Notes, Overview, Task Dependency Graph, Tasks

### Community 30 - "Agent Configuration"
Cohesion: 0.40
Nodes (5): Agent Configuration, Direct Agent Usage (without a Crew), Key Optional Parameters, LLM Configuration, Required Parameters

### Community 31 - "Observability & Traces (CrewAI AMP)"
Cohesion: 0.40
Nodes (5): Enabling traces, In the run TUI, No account required, Observability & Traces (CrewAI AMP), When to recommend traces

### Community 32 - "Task Configuration"
Cohesion: 0.40
Nodes (5): Guardrails, Key Parameters, Structured Output, Task Configuration, Task Dependencies (context)

### Community 33 - "Custom Tools"
Cohesion: 0.50
Nodes (4): Built-in Tools (install with `uv add crewai-tools`), Custom Tools, Using BaseTool, Using @tool Decorator

### Community 34 - "Environment Setup"
Cohesion: 0.50
Nodes (4): Environment Setup, Installation, Python Version, Required `.env`

### Community 35 - "YAML Configuration"
Cohesion: 0.67
Nodes (3): agents.yaml, tasks.yaml, YAML Configuration

### Community 36 - "Crew Class Pattern"
Cohesion: 0.67
Nodes (3): Crew Class Pattern, Key formatting rules:, Lifecycle hooks

### Community 37 - "Project Structure"
Cohesion: 0.67
Nodes (3): Crew Project, Flow Project, Project Structure

### Community 38 - "Process Types"
Cohesion: 0.67
Nodes (3): Hierarchical, Process Types, Sequential (default)

### Community 40 - "sentiment_tools.py"
Cohesion: 0.12
Nodes (20): _sentiment_tools(), CommunitySentimentInput, CommunitySentimentTool, CryptoNewsInput, CryptoNewsTool, _currency_matches(), FearGreedIndexTool, FearGreedInput (+12 more)

### Community 42 - "_backfill_risk_levels"
Cohesion: 0.19
Nodes (9): _backfill_risk_levels(), Crew SL/TP tolerance around the computed ATR levels (env-overridable)., Structural check: stop must sit on the loss side of entry and take on the…, Anchor each action's stop/take to the deterministic ATR set. Per action, the…, _sane_levels(), _within_pct(), _within_pct_tolerance(), Deterministic backfill with crew-level validation: sane + within 20% of… (+1 more)

### Community 43 - "Step-by-step tasks"
Cohesion: 0.11
Nodes (17): Architecture / proposed approach, Current context / assumptions, Goal, Risks, tradeoffs, and open questions, Step-by-step tasks, Sub-15-minute full-cycle plan — crypto_council_flow `--once`, Task 0 — Confirm the time budget split (no code, 3 min), Task 1 — Add `--max-coins` and `--fast` CLI flags (TDD) (+9 more)

### Community 44 - "test_fast_mode.py"
Cohesion: 0.21
Nodes (15): _flow_with(), _install_fake_crew(), Parallel per-coin analysis (plan Task 7: sub-15-min flow). Workers run…, Normal mode: portfolio holds fill spare slots up to the cap., 3 coins x 0.4s of work must finish well under serial time, on >1 thread., Replace CouncilAnalysisCrew with a fake; redirect output files to tmp., Every coin's report lands in state + on disk, even with a failure., analyse_coins analyses at most max_coins, highest score first. (+7 more)

### Community 45 - "test_panel_levels.py"
Cohesion: 0.48
Nodes (4): _load_panel_mod(), Panel SL/TP columns — every action row renders stop_loss/take_profit. RED…, TestPanelRiskColumns, _write_snapshot()

### Community 46 - "TestEnvSeconds"
Cohesion: 0.10
Nodes (10): parametrize, Tests for the scheduler cadence knobs exposed as env vars. Covers: -…, COUNCIL_MEMORY is the documented name (README, commit e635a87). USE_MEMORY is…, A typo must not crash the scheduler or pick a wild cadence., float() accepts these but int() raises OverflowError/ValueError. _env_seconds…, With the cadence env vars unset, fall back to 2h / 5m / 60s., TestCadenceConstants, TestEnvSeconds (+2 more)

### Community 47 - "test_panel_axes.py"
Cohesion: 0.10
Nodes (22): _load_panel(), panel(), fixture, parametrize, Tests for the portfolio panel chart (scripts/portfolio_panel.py). Covers the…, Regression: `or account` made a genuine equity of 0.0 fall back to the full…, Regression: pre-ledger rows plotted account_size in the chart but 0 in the…, Regression: with a 60-row window the baseline silently re-based to hist[0]; it… (+14 more)

### Community 48 - "PaperExecutionClient"
Cohesion: 0.09
Nodes (19): OrderResult, OrderStatus, Normalized outcome of a placed/cancelled/queried order., Submit an order. Raises ``ExecutionError`` on rejection/failure.…, _fee_bps_from_env(), _iso_to_epoch_ms(), PaperExecutionClient, Any (+11 more)

### Community 49 - "test_paper_ledger_pnl.py"
Cohesion: 0.10
Nodes (36): Round a requested day-count up to the nearest CoinGecko-accepted value. The…, _valid_coingecko_days(), ledger(), _open(), _plan(), _positions(), fixture, parametrize (+28 more)

### Community 50 - "Step-by-step tasks"
Cohesion: 0.13
Nodes (14): Architecture / proposed approach, Current context / assumptions, Goal, Risks, tradeoffs, and open questions, Short-Term, Smaller-Size Trades — Plan, Step-by-step tasks, Task 1 — Lower single-position size cap ✅, Task 2 — Reduce per-trade risk in the position-sizing tool ⏭️ skipped (max_risk_pct stays 2%) (+6 more)

### Community 51 - "council_crew.py"
Cohesion: 0.15
Nodes (23): Crypto Council Crew Orchestrates four specialist agents: - market_scout — scans…, _risk_tools(), _scout_tools(), ExchangeBatchTickerTool, ExchangeMarketsTool, ExchangeOHLCTool, BaseTool, Fetch all tradeable markets from the configured exchange. (+15 more)

### Community 52 - "test_panel_cycles.py"
Cohesion: 0.17
Nodes (24): _load_panel(), _main_row(), panel(), fixture, Cycle accounting in the portfolio panel. `manage_portfolio()` is the only step…, A tick row shares its cycle's number, so it must not become a cycle., Rows written before the marker existed carry no `source` key., Only known non-main sources are excluded; a new marker is a main cycle until… (+16 more)

### Community 53 - "test_execution_safety.py"
Cohesion: 0.07
Nodes (50): allow_close(), breakers_from_env(), CircuitBreakerOpen, DailyLossBreaker, _day_key(), enforce_exposure_caps(), _env_float(), exposure_limit() (+42 more)

### Community 54 - "ExecutionClient"
Cohesion: 0.06
Nodes (17): ExecutionClient, Any, Abstract base for order placement and account state on a venue. Concrete…, Return balances, optionally limited to ``assets`` (upper-case)., Cancel a live order and return its terminal state., Return one order's current state, or None if the venue has no record of it…, List live (unfilled/partially filled) orders., Return executed trades, newest-last, optionally since an epoch-ms watermark.… (+9 more)

### Community 55 - "_writer_body"
Cohesion: 0.23
Nodes (7): The writer side of the cycle-marker contract. `portfolio_panel._main_cycles()`…, Source text of one top-level/method function, dedented., Guards the lookup helper itself., The panel's NON_MAIN_SOURCES and main.py's risk_tick tag must not drift apart —…, TestHistoryRowMarkers, TestPanelAgreesWithWriter, _writer_body()

### Community 56 - "test_execution_reconcile.py"
Cohesion: 0.09
Nodes (48): _client(), FakeResponse, FakeSession, _fill(), Tests for NobitexExecutionClient.reconcile — live reconciliation (Phase 5). The…, A sell with no cost basis must not fabricate one enormous profit. A wallet…, A buy fill creates a position whose qty comes from the wallet., The wallet, not the fill arithmetic, decides how much we hold. A partial fill… (+40 more)

### Community 57 - "test_execution_live_safety.py"
Cohesion: 0.17
Nodes (33): _buy_plan(), _client(), _env(), FakeResponse, FakeSession, _posted(), _posted_bodies(), Phase 6 tests at the live ``apply_plan`` boundary. Pins the two guards the pure… (+25 more)

### Community 58 - "execution_base.py"
Cohesion: 0.19
Nodes (13): _ClientFactory, ExecutionHalted, get_execution_client(), _load_builtin_clients(), Abstract execution interface for order placement and account state. Mirrors the…, Raised by the factory when EXECUTION_MODE=halted (kill switch)., Register a client factory under ``name`` (matched against EXECUTION_MODE for…, Import concrete client modules so their self-registration runs. Concrete… (+5 more)

### Community 59 - "Fill"
Cohesion: 0.12
Nodes (11): Balance, Fill, BaseModel, model_validator, Account balance for a single asset., One executed trade. Fees are only knowable post-fill., _epoch_ms_to_iso(), Group fills by lowercase base asset (``BTC-RLS`` -> ``btc``). (+3 more)

### Community 60 - "risk_tools.py"
Cohesion: 0.18
Nodes (10): field_validator, CorrelationInput, _fetch_daily_closes(), LiquidationPriceInput, PositionSizingInput, BaseModel, model_validator, ndarray (+2 more)

### Community 61 - "_risk_levels_for"
Cohesion: 0.16
Nodes (7): Deterministic ATR-based stop/take (pure math, no network). 0.5x ATR stop and 1x…, _risk_levels_for(), RiskLevelsTool — deterministic stop-loss / take-profit math (no network, no…, Deterministic backfill helper in main.py (pure math, no network)., Retry-then-None: cached OHLC -> (price, atr_pct), failure -> (None, None)., TestAtrSnapshotFor, TestRiskLevelsFor

### Community 62 - "test_reconcile_live_wiring.py"
Cohesion: 0.05
Nodes (51): RuntimeError, _annotate_guard_refusals(), Surface Phase 6 guard refusals on a P&L summary (Phase 6 wiring).…, Phase 5: pull new venue fills, reconcile them into a local ledger. Live mode…, _reconcile_live_positions(), test_update_paper_ledger_live_delegates_to_apply_plan(), FakeLive, live_wiring() (+43 more)

### Community 63 - "NobitexExecutionClient"
Cohesion: 0.09
Nodes (31): Decimal, Session, ExecutionError, A normalized execution failure (network, rejected order, bad config). Carries…, _decimals_from_env(), _format_decimal(), _iso_to_epoch_ms(), NobitexExecutionClient (+23 more)

### Community 64 - "OrderRequest"
Cohesion: 0.22
Nodes (5): OrderRequest, A venue-agnostic order intent. Exactly one of ``qty`` (base amount) or…, Resolve a price from the last snapshot by an EXPLICIT coin_id match. The paper…, Simulate an immediate full fill at the last snapshot price. Paper has no order…, TestOrderRequest

### Community 65 - "test_execution_nobitex.py"
Cohesion: 0.14
Nodes (36): OrderSide, OrderType, _client(), FakeResponse, FakeSession, parametrize, Tests for NobitexExecutionClient — the live execution client (Phase 3). All…, SmallOrder is enforced pre-flight against the venue minimum. (+28 more)

### Community 66 - "Paper Trading → Real Trading — Phase Plan"
Cohesion: 0.17
Nodes (11): Conventions (apply to every phase), Open questions (unchanged from the draft), Paper Trading → Real Trading — Phase Plan, Phase 1 — Abstract Execution Interface ✅ DONE, Phase 2 — Paper Execution as a Plugin ✅ DONE, Phase 3 — Live Execution Client ✅ DONE, Phase 4 — Order Translation Layer ✅ DONE, Phase 5 — State & Reconciliation ✅ DONE (+3 more)

### Community 67 - "test_execution_live_apply.py"
Cohesion: 0.17
Nodes (34): _client(), _env(), FakeResponse, FakeSession, _no_caps(), _posted_urls(), Tests for NobitexExecutionClient.apply_plan — the live-apply path (Phase 4).…, Opt out of Phase 6 caps so these tests exercise translation only. Phase 4 owns… (+26 more)

### Community 68 - "CryptoCouncilState"
Cohesion: 0.24
Nodes (5): CryptoCouncilState, BaseModel, Persistent Flow state passed between steps., Tests for the Pydantic state models: CoinOpportunity and CryptoCouncilState., TestCryptoCouncilState

### Community 69 - "test_execution_paper.py"
Cohesion: 0.30
Nodes (13): _client(), Tests for PaperExecutionClient — the paper-ledger client behind the interface., Watermark filters rows by ISO timestamp (epoch ms)., Ledger 'close' rows are reported as OrderSide.SELL in Fill., Two identical orders must produce distinct order_ids for reconciliation., _snapshot(), test_apply_plan_roundtrip_fee_free_matches_legacy(), test_apply_plan_with_fee_booked_in_ledger() (+5 more)

### Community 70 - "CoinOpportunity"
Cohesion: 0.27
Nodes (4): CoinOpportunity, model_validator, Single canonical DTO for scout output consumed by the Flow. Same shape as…, TestCoinOpportunity

### Community 71 - "_parse_inputs"
Cohesion: 0.22
Nodes (8): kickoff(), _parse_inputs(), Parse CLI arguments into Flow inputs. Accepted flags: --account-size <float>…, Primary entry point — starts the continuous Flow scheduler., test_defaults_are_small_and_short(), test_max_hold_days_cli_flag(), test_max_hold_forces_close(), test_tighter_atr_levels()

### Community 72 - "TestPortfolioPlanDTO"
Cohesion: 0.17
Nodes (3): Tests for the portfolio manager: paper positions, exposure caps, rebalance.…, _StubResult, TestPortfolioPlanDTO

### Community 73 - "_extract_scout_items"
Cohesion: 0.24
Nodes (10): ScoutShortlist, _extract_scout_items(), Best-effort extraction of opportunity dicts from raw scout text. Handles the…, Scout structured-output wiring: pydantic path + raw fallback extractor., The zcash-shaped item from the failed run must validate (int score)., test_extractor_handles_envelope(), test_extractor_handles_fenced_bare_array(), test_extractor_returns_empty_on_prose() (+2 more)

## Knowledge Gaps
- **228 isolated node(s):** `crypto_council_flow`, `Goal`, `Current context / assumptions`, `Architecture / proposed approach`, `Task 0 — Confirm the time budget split (no code, 3 min)` (+223 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 710 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `main()` connect `portfolio_panel.py` to `test_reconcile_live_wiring.py`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `ExecutionHalted` connect `execution_base.py` to `main.py`, `council_crew.py`, `test_execution_safety.py`, `test_execution_live_safety.py`, `test_reconcile_live_wiring.py`, `NobitexExecutionClient`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Why does `test_reconcile_fails_open_when_client_is_halted()` connect `test_reconcile_live_wiring.py` to `portfolio_panel.py`, `execution_base.py`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Are the 15 inferred relationships involving `NobitexExecutionClient` (e.g. with `Balance` and `ExecutionError`) actually correct?**
  _`NobitexExecutionClient` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `OrderRequest` (e.g. with `NobitexExecutionClient` and `PaperExecutionClient`) actually correct?**
  _`OrderRequest` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `ExecutionError` (e.g. with `NobitexExecutionClient` and `PaperExecutionClient`) actually correct?**
  _`ExecutionError` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `PaperExecutionClient` (e.g. with `Balance` and `ExecutionError`) actually correct?**
  _`PaperExecutionClient` has 9 INFERRED edges - model-reasoned connections that need verification._