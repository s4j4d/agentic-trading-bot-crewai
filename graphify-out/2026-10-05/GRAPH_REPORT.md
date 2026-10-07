# Graph Report - crypto_council_flow  (2026-10-03)

## Corpus Check
- 36 files · ~44,676 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 735 nodes · 1141 edges · 46 communities (43 shown, 1 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 75 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `dc87b6bd`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- council_crew.py
- CouncilAnalysisCrew
- Correctness Properties
- test_backtest_30d.py
- market-scout/SKILL.md
- portfolio_tools.py
- ExchangeClient
- _load_jsonc
- technical_indicators.py
- CryptoCouncilFlow
- main.py
- scout_tools.py
- test_cycle_lock.py
- Flows
- AGENTS.md — CrewAI Reference for AI Coding Assistants
- risk-manager/SKILL.md
- portfolio_panel.py
- sentiment-analyst/SKILL.md
- technical-analyst/SKILL.md
- CoinOpportunity
- .analyse_coins
- _extract_scout_items
- CryptoCouncilState
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
- _backfill_risk_levels
- Step-by-step tasks
- test_fast_mode.py
- test_panel_levels.py

## God Nodes (most connected - your core abstractions)
1. `AGENTS.md — CrewAI Reference for AI Coding Assistants` - 25 edges
2. `CoinOpportunity` - 23 edges
3. `_load_jsonc()` - 18 edges
4. `CouncilAnalysisCrew` - 16 edges
5. `CouncilPortfolioCrew` - 15 edges
6. `Flows` - 15 edges
7. `CouncilScoutCrew` - 14 edges
8. `CryptoCouncilFlow` - 13 edges
9. `PortfolioExposureTool` - 13 edges
10. `RebalanceAllocatorTool` - 13 edges

## Surprising Connections (you probably didn't know these)
- `test_council_crew_importable()` --indirect_call--> `_load_jsonc()`  [INFERRED]
  tests/test_imports.py → src/crypto_council_flow/crews/council/council_crew.py
- `test_scout_crew_instantiates()` --uses--> `CouncilScoutCrew`  [INFERRED]
  tests/test_crew_configs.py → src/crypto_council_flow/crews/council/council_crew.py
- `test_scout_task_uses_structured_output()` --uses--> `CouncilScoutCrew`  [INFERRED]
  tests/test_scout_parse.py → src/crypto_council_flow/crews/council/council_crew.py
- `test_analysis_crew_instantiates()` --uses--> `CouncilAnalysisCrew`  [INFERRED]
  tests/test_crew_configs.py → src/crypto_council_flow/crews/council/council_crew.py
- `TestPortfolioPlanDTO` --uses--> `PortfolioAction`  [INFERRED]
  tests/test_portfolio.py → src/crypto_council_flow/crews/council/council_crew.py

## Import Cycles
- None detected.

## Communities (46 total, 1 thin omitted)

### Community 0 - "council_crew.py"
Cohesion: 0.06
Nodes (59): field_validator, Crypto Council Crew Orchestrates four specialist agents: - market_scout — scans…, _risk_tools(), _scout_tools(), _sentiment_tools(), _technical_tools(), ExchangeMarketsInput, ExchangeMarketsTool (+51 more)

### Community 1 - "CouncilAnalysisCrew"
Cohesion: 0.06
Nodes (27): agent, crew, CrewBase, CouncilAnalysisCrew, CouncilPortfolioCrew, CouncilScoutCrew, _load_agent_skill(), Load SKILL.md from skills/<skill_dir_name>/ at INSTRUCTIONS level. (+19 more)

### Community 2 - "Correctness Properties"
Cohesion: 0.05
Nodes (42): 1. CryptoCouncilFlow (`main.py`), 2. CryptoCouncilState (`main.py`), 3. CoinOpportunity (`main.py`), 4. CouncilScoutCrew (`council_crew.py`), 5. CouncilAnalysisCrew (`council_crew.py`), 6. Agent Definitions (`config/agents.jsonc`), 7. Task Definitions (`config/tasks.yaml`), 8. Agent Skills (`skills/<agent-name>/SKILL.md`) (+34 more)

### Community 3 - "test_backtest_30d.py"
Cohesion: 0.09
Nodes (30): _cg_get(), composite_signal(), compute_bollinger(), compute_ema_cross(), compute_macd(), compute_rsi(), _ema(), _fetch_all_data() (+22 more)

### Community 4 - "market-scout/SKILL.md"
Cohesion: 0.06
Nodes (35): 1. Trending Coins (`trending_coins`), 2. Momentum Screener (`momentum_screener`), 3. Volatility and Tradeability, 4. New Listings (`new_listings`), 5. Upcoming Catalysts (`upcoming_catalysts`), ATR%, Catalysts & Event Potential: 10%, Composite Opportunity Score (+27 more)

### Community 5 - "portfolio_tools.py"
Cohesion: 0.07
Nodes (24): _portfolio_tools(), AllocatorOpportunity, _coin_id_of(), ExposurePosition, PortfolioExposureInput, PortfolioExposureTool, _position_of(), Any (+16 more)

### Community 6 - "ExchangeClient"
Cohesion: 0.12
Nodes (13): _cache_get(), _cache_put(), _error(), ExchangeClient, get_exchange_client(), NobitexClient, Any, Factory: reads env vars and returns the configured exchange client. (+5 more)

### Community 7 - "_load_jsonc"
Cohesion: 0.15
Nodes (13): _load_jsonc(), Any, Path, Parse a JSONC file (JSON with // comments and trailing commas). The…, Path, Unit tests for the _load_jsonc() helper in council_crew.py. All tests operate…, The actual agents.jsonc in the project can be loaded without error., Each agent in agents.jsonc has the required role, goal, backstory keys. (+5 more)

### Community 8 - "technical_indicators.py"
Cohesion: 0.11
Nodes (23): Response, ATRInput, BollingerBandsInput, _closes(), _coingecko_id_to_symbol(), EMACrossInput, _fetch_ohlcv(), _fetch_ohlcv_coingecko() (+15 more)

### Community 9 - "CryptoCouncilFlow"
Cohesion: 0.16
Nodes (11): PortfolioAction, PortfolioPlan, BaseModel, model_validator, Single canonical DTO for scout output. CrewAI's output validation works against…, One per-coin rebalance action. One DTO, never two., Cap-aware rebalance plan. Single canonical DTO for portfolio output., ScoutOpportunity (+3 more)

### Community 10 - "main.py"
Cohesion: 0.11
Nodes (19): listen, _atr_snapshot_for(), _extract_portfolio_plan(), kickoff(), _parse_inputs(), plot(), Any, Crypto Council Flow — main entry point. Scheduling model ---------------- The… (+11 more)

### Community 11 - "scout_tools.py"
Cohesion: 0.24
Nodes (13): _cache_get(), _cache_put(), _error(), _get_coingecko(), _log_return(), MomentumScreenerInput, NewListingsInput, BaseModel (+5 more)

### Community 12 - "test_cycle_lock.py"
Cohesion: 0.12
Nodes (15): Tests for the cycle locking mechanism in the scheduler. Verifies that: 1. No…, Lock must be released even if the cycle raises an exception., Timer should update after a successful cycle., Timer should update even after a failed cycle (to avoid immediate retry)., When cycle A is running, cycle B must be skipped., Simulate concurrent access: if scout is running (from another caller), analysis…, Cycles that run sequentially should both complete., When a cycle is skipped because the lock is held, the timer must NOT advance. (+7 more)

### Community 13 - "Flows"
Cohesion: 0.13
Nodes (15): Basic Flow, Conditional Routing, Flow Decorators, Flow Execution, Flow Streaming (v1.8.0+), Flow Visualization, Flows, Human-in-the-Loop (v1.8.0+) (+7 more)

### Community 14 - "AGENTS.md — CrewAI Reference for AI Coding Assistants"
Cohesion: 0.14
Nodes (13): Agent Collaboration, AGENTS.md — CrewAI Reference for AI Coding Assistants, Architecture Overview, Common Pitfalls, Crew Execution, Crew Options, Custom Embedding Provider, Development Best Practices (+5 more)

### Community 15 - "risk-manager/SKILL.md"
Cohesion: 0.14
Nodes (13): Constraints, Core Principle: Risk First, Reward Second, Final Recommendation Decision Tree, Output Structure, Role, Sentiment Alignment Check, Step 1: Determine Entry and Stop-Loss, Step 2: Compute Position Size (`position_sizing`) (+5 more)

### Community 16 - "portfolio_panel.py"
Cohesion: 0.23
Nodes (15): _action_class(), build(), _esc(), _fmt(), _fmt_dur(), _load_history(), _load_snapshot(), _lvl() (+7 more)

### Community 17 - "sentiment-analyst/SKILL.md"
Cohesion: 0.17
Nodes (11): 1. Fear & Greed Index (`fear_greed_index`), 2. Crypto News Sentiment (`crypto_news_sentiment`), 3. Community Sentiment (`community_sentiment`), 4. Market Dominance (`market_dominance`), Catalyst Flags, Composite Sentiment Score Calculation, Constraints, Data Sources and How to Use Them (+3 more)

### Community 18 - "technical-analyst/SKILL.md"
Cohesion: 0.17
Nodes (11): 1. EMA Cross — Trend Direction, 2. RSI — Momentum and Overbought/Oversold, 3. MACD — Momentum Crossovers and Acceleration, 4. Bollinger Bands — Volatility Regime and Price Extremes, 5. ATR — Volatility Measurement and Stop Placement, Constraints, Indicator Sequence and Purpose, Key Levels Construction (+3 more)

### Community 19 - "CoinOpportunity"
Cohesion: 0.27
Nodes (4): CoinOpportunity, model_validator, Single canonical DTO for scout output consumed by the Flow. Same shape as…, TestCoinOpportunity

### Community 20 - ".analyse_coins"
Cohesion: 0.14
Nodes (14): Exception, _analyse_one_coin(), _analysis_workers(), _append_to_consolidated(), _build_report(), _fmt_dur(), Run the analysis crew for one coin (worker thread). Pure worker: never touches…, Thread-pool size for per-coin analysis (env-overridable). (+6 more)

### Community 21 - "_extract_scout_items"
Cohesion: 0.24
Nodes (10): ScoutShortlist, _extract_scout_items(), Best-effort extraction of opportunity dicts from raw scout text. Handles the…, Scout structured-output wiring: pydantic path + raw fallback extractor., The zcash-shaped item from the failed run must validate (int score)., test_extractor_handles_envelope(), test_extractor_handles_fenced_bare_array(), test_extractor_returns_empty_on_prose() (+2 more)

### Community 22 - "CryptoCouncilState"
Cohesion: 0.31
Nodes (4): CryptoCouncilState, BaseModel, Persistent Flow state passed between steps., TestCryptoCouncilState

### Community 23 - "conftest.py"
Cohesion: 0.24
Nodes (10): cryptopanic_api_response(), markets_api_response(), fixture, Shared pytest fixtures for the crypto_council_flow test suite., A valid JSON array that the scout task would produce., Minimal valid CoinGecko /search/trending payload., Two-coin /coins/markets payload., Minimal CryptoPanic /posts/ payload with one bullish and one bearish article. (+2 more)

### Community 24 - "Deployment to CrewAI AMP"
Cohesion: 0.20
Nodes (10): AMP Dashboard Tabs, CI/CD API Deployment, CLI Deployment, Deployed Automation REST API, Deployment to CrewAI AMP, Deployment Troubleshooting, GitHub Actions Example, Prerequisites (+2 more)

### Community 25 - "portfolio-manager/SKILL.md"
Cohesion: 0.22
Nodes (8): Constraints, Core Principle: Caps First, Rotation Second, Output Structure, Role, Step 1: Measure current exposure (`portfolio_exposure`), Step 2: Compute score-weighted targets (`rebalance_allocator`), Step 3: Reconcile into actions, Step-by-Step Rebalance Process

### Community 26 - "⚠️ Version & Freshness Requirements"
Cohesion: 0.29
Nodes (7): Deprecated CLI flag aliases (still supported), Deprecated CLI scaffolding aliases (still supported), How to verify you're using current patterns:, Mandatory: Research before writing CrewAI code, Patterns to NEVER use (outdated/removed):, ⚠️ Version & Freshness Requirements, What changed since older versions:

### Community 27 - "Crypto Council Flow — agentic paper-trading council (CrewAI)"
Cohesion: 0.18
Nodes (10): Agents & tools, Configuration, Crypto Council Flow — agentic paper-trading council (CrewAI), How it works, Installation, Layout, Outputs (`output/`, git-ignored), Running the Project (+2 more)

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

### Community 42 - "_backfill_risk_levels"
Cohesion: 0.12
Nodes (11): _backfill_risk_levels(), Deterministic 1x-ATR stop + 2:1 take-profit (pure math, no network). Floor:…, Fill missing stop_loss/take_profit from the ATR snapshot (deterministic).…, _risk_levels_for(), RiskLevelsTool — deterministic stop-loss / take-profit math (no network, no…, Deterministic backfill helper in main.py (pure math, no network)., Retry-then-None: cached OHLC -> (price, atr_pct), failure -> (None, None)., Deterministic backfill: missing levels filled from snapshot, close included. (+3 more)

### Community 43 - "Step-by-step tasks"
Cohesion: 0.12
Nodes (16): Architecture / proposed approach, Current context / assumptions, Goal, Risks, tradeoffs, and open questions, Step-by-step tasks, Sub-15-minute full-cycle plan — crypto_council_flow `--once`, Task 0 — Confirm the time budget split (no code, 3 min), Task 1 — Add `--max-coins` and `--fast` CLI flags (TDD) (+8 more)

### Community 44 - "test_fast_mode.py"
Cohesion: 0.33
Nodes (8): _flow_with(), _install_fake_crew(), Parallel per-coin analysis (plan Task 7: sub-15-min flow). Workers run…, Replace CouncilAnalysisCrew with a fake; redirect output files to tmp., Every coin's report lands in state + on disk, even with a failure., 3 coins x 0.4s of work must finish well under serial time, on >1 thread., test_parallel_analysis_runs_concurrently(), test_parallel_merge_keeps_all_coins()

### Community 45 - "test_panel_levels.py"
Cohesion: 0.48
Nodes (4): _load_panel_mod(), Panel SL/TP columns — every action row renders stop_loss/take_profit. RED…, TestPanelRiskColumns, _write_snapshot()

## Knowledge Gaps
- **201 isolated node(s):** `crypto_council_flow`, `Goal`, `Current context / assumptions`, `Architecture / proposed approach`, `Task 0 — Confirm the time budget split (no code, 3 min)` (+196 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 350 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_load_jsonc()` connect `_load_jsonc` to `council_crew.py`, `CouncilAnalysisCrew`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Why does `CouncilScoutCrew` connect `CouncilAnalysisCrew` to `council_crew.py`, `CryptoCouncilFlow`, `main.py`, `.analyse_coins`, `_extract_scout_items`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Why does `CouncilAnalysisCrew` connect `CouncilAnalysisCrew` to `council_crew.py`, `main.py`, `.analyse_coins`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `CoinOpportunity` (e.g. with `_flow_with()` and `TestCoinOpportunity`) actually correct?**
  _`CoinOpportunity` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `_load_jsonc()` (e.g. with `test_council_crew_importable()` and `.test_comment_only_lines_do_not_break_parse()`) actually correct?**
  _`_load_jsonc()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `CouncilAnalysisCrew` (e.g. with `test_analysis_and_portfolio_crews_have_time_budgets()` and `test_analysis_crew_instantiates()`) actually correct?**
  _`CouncilAnalysisCrew` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `CouncilPortfolioCrew` (e.g. with `CryptoCouncilFlow` and `test_analysis_and_portfolio_crews_have_time_budgets()`) actually correct?**
  _`CouncilPortfolioCrew` has 6 INFERRED edges - model-reasoned connections that need verification._