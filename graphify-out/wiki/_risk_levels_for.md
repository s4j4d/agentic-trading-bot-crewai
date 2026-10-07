# _risk_levels_for

> 14 nodes

## Key Concepts

- **_risk_levels_for()** (7 connections) — `src/crypto_council_flow/main.py`
- **TestAtrSnapshotFor** (5 connections) — `tests/test_risk_levels.py`
- **TestRiskLevelsFor** (5 connections) — `tests/test_risk_levels.py`
- **test_risk_levels.py** (5 connections) — `tests/test_risk_levels.py`
- **.test_invalid_returns_none()** (2 connections) — `tests/test_risk_levels.py`
- **.test_standard_levels()** (2 connections) — `tests/test_risk_levels.py`
- **.test_tiny_atr_floored_at_1pct()** (2 connections) — `tests/test_risk_levels.py`
- **.test_snapshot_from_candles()** (1 connections) — `tests/test_risk_levels.py`
- **.test_snapshot_none_on_fetch_error()** (1 connections) — `tests/test_risk_levels.py`
- **.test_snapshot_none_when_no_candles()** (1 connections) — `tests/test_risk_levels.py`
- **Deterministic ATR-based stop/take (pure math, no network). 0.5x ATR stop and 1x…** (1 connections) — `src/crypto_council_flow/main.py`
- **RiskLevelsTool — deterministic stop-loss / take-profit math (no network, no…** (1 connections) — `tests/test_risk_levels.py`
- **Deterministic backfill helper in main.py (pure math, no network).** (1 connections) — `tests/test_risk_levels.py`
- **Retry-then-None: cached OHLC -> (price, atr_pct), failure -> (None, None).** (1 connections) — `tests/test_risk_levels.py`

## Relationships

- [_backfill_risk_levels](_backfill_risk_levels.md) (2 shared connections)
- [main.py](main.py.md) (2 shared connections)
- [portfolio_tools.py](portfolio_tools.py.md) (1 shared connections)

## Source Files

- `src/crypto_council_flow/main.py`
- `tests/test_risk_levels.py`

## Audit Trail

- EXTRACTED: 17 (85%)
- INFERRED: 3 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*