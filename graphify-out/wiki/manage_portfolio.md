# .manage_portfolio

> 18 nodes

## Key Concepts

- **.manage_portfolio()** (12 connections) — `src/crypto_council_flow/main.py`
- **.risk_tick()** (8 connections) — `src/crypto_council_flow/main.py`
- **Any** (8 connections)
- **_atr_snapshot_for()** (5 connections) — `src/crypto_council_flow/main.py`
- **_extract_portfolio_plan()** (5 connections) — `src/crypto_council_flow/main.py`
- **_recompute_portfolio_totals()** (5 connections) — `src/crypto_council_flow/main.py`
- **_update_paper_ledger()** (5 connections) — `src/crypto_council_flow/main.py`
- **_apply_sl_tp_hits()** (4 connections) — `src/crypto_council_flow/main.py`
- **_regenerate_portfolio_panel()** (4 connections) — `src/crypto_council_flow/main.py`
- **.test_extract_portfolio_plan_fallback()** (2 connections) — `tests/test_portfolio.py`
- **Retry-then-None ATR snapshot from cached OHLC (pure read, no LLM). Uses the…** (1 connections) — `src/crypto_council_flow/main.py`
- **Force ``close`` on positions whose stop/take was hit. Deterministic, no LLM:…** (1 connections) — `src/crypto_council_flow/main.py`
- **Recompute totals deterministically from action targets. The crew's arithmetic…** (1 connections) — `src/crypto_council_flow/main.py`
- **Run CouncilPortfolioCrew to produce a cap-aware rebalance plan. Called…** (1 connections) — `src/crypto_council_flow/main.py`
- **Deterministic fast risk check — NO LLM call. Runs between analysis cycles to…** (1 connections) — `src/crypto_council_flow/main.py`
- **Best-effort extraction of portfolio plan dict from raw text.** (1 connections) — `src/crypto_council_flow/main.py`
- **Fill the plan against the paper ledger using current prices. Prices come from…** (1 connections) — `src/crypto_council_flow/main.py`
- **Run the stdlib panel script after each portfolio cycle. Best-effort.** (1 connections) — `src/crypto_council_flow/main.py`

## Relationships

- [main.py](main.py.md) (10 shared connections)
- [CryptoCouncilFlow](CryptoCouncilFlow.md) (3 shared connections)
- [_backfill_risk_levels](_backfill_risk_levels.md) (2 shared connections)
- [technical_indicators.py](technical_indicators.py.md) (1 shared connections)
- [CouncilAnalysisCrew](CouncilAnalysisCrew.md) (1 shared connections)
- [_fmt_dur](_fmt_dur.md) (1 shared connections)
- [.analyse_coins](analyse_coins.md) (1 shared connections)
- [_extract_scout_items](_extract_scout_items.md) (1 shared connections)

## Source Files

- `src/crypto_council_flow/main.py`
- `tests/test_portfolio.py`

## Audit Trail

- EXTRACTED: 42 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*