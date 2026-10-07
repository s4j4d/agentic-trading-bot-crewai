# _backfill_risk_levels

> 14 nodes

## Key Concepts

- **_backfill_risk_levels()** (13 connections) — `src/crypto_council_flow/main.py`
- **TestBackfillRiskLevels** (7 connections) — `tests/test_risk_levels.py`
- **_sane_levels()** (3 connections) — `src/crypto_council_flow/main.py`
- **_within_pct_tolerance()** (3 connections) — `src/crypto_council_flow/main.py`
- **_within_pct()** (2 connections) — `src/crypto_council_flow/main.py`
- **.test_fills_missing_levels_from_snapshot()** (2 connections) — `tests/test_risk_levels.py`
- **.test_keeps_crew_levels_within_20pct()** (2 connections) — `tests/test_risk_levels.py`
- **.test_none_without_snapshot()** (2 connections) — `tests/test_risk_levels.py`
- **.test_rejects_far_from_computed_and_replaces()** (2 connections) — `tests/test_risk_levels.py`
- **.test_rejects_insane_and_replaces_with_computed()** (2 connections) — `tests/test_risk_levels.py`
- **Crew SL/TP tolerance around the computed ATR levels (env-overridable).** (1 connections) — `src/crypto_council_flow/main.py`
- **Structural check: stop must sit on the loss side of entry and take on the…** (1 connections) — `src/crypto_council_flow/main.py`
- **Anchor each action's stop/take to the deterministic ATR set. Per action, the…** (1 connections) — `src/crypto_council_flow/main.py`
- **Deterministic backfill with crew-level validation: sane + within 20% of…** (1 connections) — `tests/test_risk_levels.py`

## Relationships

- [main.py](main.py.md) (4 shared connections)
- [_risk_levels_for](_risk_levels_for.md) (2 shared connections)
- [.manage_portfolio](manage_portfolio.md) (2 shared connections)

## Source Files

- `src/crypto_council_flow/main.py`
- `tests/test_risk_levels.py`

## Audit Trail

- EXTRACTED: 20 (80%)
- INFERRED: 5 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*