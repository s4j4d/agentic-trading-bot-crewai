# Graph Report - crypto_council_flow  (2026-10-02)

## Corpus Check
- 32 files · ~39,090 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 646 nodes · 1009 edges · 42 communities (39 shown, 1 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 64 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `77d5baf3`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- council_crew.py
- CouncilAnalysisCrew
- Correctness Properties
- test_backtest_30d.py
- market-scout/SKILL.md
- PortfolioExposureTool
- exchange_base.py
- _load_jsonc
- technical_indicators.py
- TestManagePortfolioStep
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
- CryptoCouncilFlow
- _extract_scout_items
- CryptoCouncilState
- conftest.py
- Deployment to CrewAI AMP
- portfolio-manager/SKILL.md
- ⚠️ Version & Freshness Requirements
- {{crew_name}} Crew
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

## God Nodes (most connected - your core abstractions)
1. `AGENTS.md — CrewAI Reference for AI Coding Assistants` - 25 edges
2. `CoinOpportunity` - 21 edges
3. `_load_jsonc()` - 18 edges
4. `CouncilAnalysisCrew` - 16 edges
5. `Flows` - 15 edges
6. `CouncilPortfolioCrew` - 14 edges
7. `CouncilScoutCrew` - 13 edges
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
- `TestCouncilPortfolioCrew` --uses--> `PortfolioPlan`  [INFERRED]
  tests/test_portfolio.py → src/crypto_council_flow/crews/council/council_crew.py

## Import Cycles
- None detected.

## Communities (42 total, 1 thin omitted)

### Community 0 - "council_crew.py"
Cohesion: 0.06
Nodes (54): field_validator, Crypto Council Crew Orchestrates four specialist agents: - market_scout — scans…, _risk_tools(), _scout_tools(), _sentiment_tools(), _technical_tools(), ExchangeMarketsTool, ExchangeOHLCTool (+46 more)

### Community 1 - "CouncilAnalysisCrew"
Cohesion: 0.07
Nodes (24): agent, crew, CrewBase, CouncilAnalysisCrew, CouncilPortfolioCrew, CouncilScoutCrew, _load_agent_skill(), _portfolio_tools() (+16 more)

### Community 2 - "Correctness Properties"
Cohesion: 0.05
Nodes (42): 1. CryptoCouncilFlow (`main.py`), 2. CryptoCouncilState (`main.py`), 3. CoinOpportunity (`main.py`), 4. CouncilScoutCrew (`council_crew.py`), 5. CouncilAnalysisCrew (`council_crew.py`), 6. Agent Definitions (`config/agents.jsonc`), 7. Task Definitions (`config/tasks.yaml`), 8. Agent Skills (`skills/<agent-name>/SKILL.md`) (+34 more)

### Community 3 - "test_backtest_30d.py"
Cohesion: 0.09
Nodes (30): _cg_get(), composite_signal(), compute_bollinger(), compute_ema_cross(), compute_macd(), compute_rsi(), _ema(), _fetch_all_data() (+22 more)

### Community 4 - "market-scout/SKILL.md"
Cohesion: 0.06
Nodes (35): 1. Trending Coins (`trending_coins`), 2. Momentum Screener (`momentum_screener`), 3. Volatility and Tradeability, 4. New Listings (`new_listings`), 5. Upcoming Catalysts (`upcoming_catalysts`), ATR%, Catalysts & Event Potential: 10%, Composite Opportunity Score (+27 more)

### Community 5 - "PortfolioExposureTool"
Cohesion: 0.10
Nodes (19): AllocatorOpportunity, _coin_id_of(), ExposurePosition, PortfolioExposureInput, PortfolioExposureTool, _position_of(), Any, BaseModel (+11 more)

### Community 6 - "exchange_base.py"
Cohesion: 0.11
Nodes (18): _cache_get(), _cache_put(), _error(), ExchangeClient, ExchangeMarketsInput, ExchangeOHLCInput, ExchangeTickerInput, get_exchange_client() (+10 more)

### Community 7 - "_load_jsonc"
Cohesion: 0.15
Nodes (13): _load_jsonc(), Any, Path, Parse a JSONC file (JSON with // comments and trailing commas). The…, Path, Unit tests for the _load_jsonc() helper in council_crew.py. All tests operate…, The actual agents.jsonc in the project can be loaded without error., Each agent in agents.jsonc has the required role, goal, backstory keys. (+5 more)

### Community 8 - "technical_indicators.py"
Cohesion: 0.13
Nodes (19): ATRInput, BollingerBandsInput, _closes(), _coingecko_id_to_symbol(), EMACrossInput, _fetch_ohlcv(), _fetch_ohlcv_coingecko(), _fetch_ohlcv_exchange() (+11 more)

### Community 9 - "TestManagePortfolioStep"
Cohesion: 0.15
Nodes (10): PortfolioAction, PortfolioPlan, BaseModel, model_validator, Single canonical DTO for scout output. CrewAI's output validation works against…, One per-coin rebalance action. One DTO, never two., Cap-aware rebalance plan. Single canonical DTO for portfolio output., ScoutOpportunity (+2 more)

### Community 10 - "main.py"
Cohesion: 0.12
Nodes (17): _append_to_consolidated(), _build_report(), _extract_portfolio_plan(), kickoff(), _parse_inputs(), plot(), Any, Crypto Council Flow — main entry point. Scheduling model ---------------- The… (+9 more)

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
Cohesion: 0.32
Nodes (11): _action_class(), build(), _esc(), _fmt(), _load_history(), _load_snapshot(), main(), _page() (+3 more)

### Community 17 - "sentiment-analyst/SKILL.md"
Cohesion: 0.17
Nodes (11): 1. Fear & Greed Index (`fear_greed_index`), 2. Crypto News Sentiment (`crypto_news_sentiment`), 3. Community Sentiment (`community_sentiment`), 4. Market Dominance (`market_dominance`), Catalyst Flags, Composite Sentiment Score Calculation, Constraints, Data Sources and How to Use Them (+3 more)

### Community 18 - "technical-analyst/SKILL.md"
Cohesion: 0.17
Nodes (11): 1. EMA Cross — Trend Direction, 2. RSI — Momentum and Overbought/Oversold, 3. MACD — Momentum Crossovers and Acceleration, 4. Bollinger Bands — Volatility Regime and Price Extremes, 5. ATR — Volatility Measurement and Stop Placement, Constraints, Indicator Sequence and Purpose, Key Levels Construction (+3 more)

### Community 19 - "CoinOpportunity"
Cohesion: 0.27
Nodes (4): CoinOpportunity, model_validator, Single canonical DTO for scout output consumed by the Flow. Same shape as…, TestCoinOpportunity

### Community 20 - "CryptoCouncilFlow"
Cohesion: 0.20
Nodes (7): listen, CryptoCouncilFlow, Three-step CrewAI Flow: run_scout — market_scout produces a ranked coin list…, Run CouncilScoutCrew to refresh the opportunity list. Called once at startup…, Run CouncilAnalysisCrew for each coin in the current opportunity list PLUS any…, Run CouncilPortfolioCrew to produce a cap-aware rebalance plan. Called…, start

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

### Community 27 - "{{crew_name}} Crew"
Cohesion: 0.29
Nodes (6): {{crew_name}} Crew, Customizing, Installation, Running the Project, Support, Understanding Your Crew

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

## Knowledge Gaps
- **182 isolated node(s):** `crypto_council_flow`, `Overview`, `High-Level Flow`, `1. CryptoCouncilFlow (`main.py`)`, `2. CryptoCouncilState (`main.py`)` (+177 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 299 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_load_jsonc()` connect `_load_jsonc` to `council_crew.py`, `CouncilAnalysisCrew`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Why does `CouncilAnalysisCrew` connect `CouncilAnalysisCrew` to `council_crew.py`, `main.py`, `CryptoCouncilFlow`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **Why does `CouncilScoutCrew` connect `CouncilAnalysisCrew` to `council_crew.py`, `main.py`, `CryptoCouncilFlow`, `_extract_scout_items`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `CoinOpportunity` (e.g. with `TestCoinOpportunity` and `TestCryptoCouncilState`) actually correct?**
  _`CoinOpportunity` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `_load_jsonc()` (e.g. with `test_council_crew_importable()` and `.test_comment_only_lines_do_not_break_parse()`) actually correct?**
  _`_load_jsonc()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `CouncilAnalysisCrew` (e.g. with `CryptoCouncilFlow` and `test_analysis_crew_instantiates()`) actually correct?**
  _`CouncilAnalysisCrew` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `crypto_council_flow`, `Overview`, `High-Level Flow` to the rest of the system?**
  _182 weakly-connected nodes found - possible documentation gaps or missing edges._