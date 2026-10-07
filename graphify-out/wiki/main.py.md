# main.py

> 16 nodes

## Key Concepts

- **main.py** (42 connections) — `src/crypto_council_flow/main.py`
- **_apply_max_hold()** (7 connections) — `src/crypto_council_flow/main.py`
- **datetime** (6 connections)
- **test_short_term_caps.py** (6 connections) — `tests/test_short_term_caps.py`
- **_parse_inputs()** (5 connections) — `src/crypto_council_flow/main.py`
- **kickoff()** (4 connections) — `src/crypto_council_flow/main.py`
- **_env_seconds()** (2 connections) — `src/crypto_council_flow/main.py`
- **test_defaults_are_small_and_short()** (2 connections) — `tests/test_short_term_caps.py`
- **test_max_hold_days_cli_flag()** (2 connections) — `tests/test_short_term_caps.py`
- **test_max_hold_forces_close()** (2 connections) — `tests/test_short_term_caps.py`
- **test_tighter_atr_levels()** (2 connections) — `tests/test_short_term_caps.py`
- **Force ``close`` on any positioned action held > max_hold_days. Age is derived…** (1 connections) — `src/crypto_council_flow/main.py`
- **Parse CLI arguments into Flow inputs. Accepted flags: --account-size <float>…** (1 connections) — `src/crypto_council_flow/main.py`
- **Primary entry point — starts the continuous Flow scheduler.** (1 connections) — `src/crypto_council_flow/main.py`
- **Crypto Council Flow — main entry point. Scheduling model ---------------- The…** (1 connections) — `src/crypto_council_flow/main.py`
- **Read a cadence env var as seconds. Accepts bare seconds (``7200``) or a…** (1 connections) — `src/crypto_council_flow/main.py`

## Relationships

- [.manage_portfolio](manage_portfolio.md) (10 shared connections)
- [_fmt_dur](_fmt_dur.md) (6 shared connections)
- [CoinOpportunity](CoinOpportunity.md) (4 shared connections)
- [CryptoCouncilFlow](CryptoCouncilFlow.md) (4 shared connections)
- [_backfill_risk_levels](_backfill_risk_levels.md) (4 shared connections)
- [CouncilAnalysisCrew](CouncilAnalysisCrew.md) (3 shared connections)
- [.analyse_coins](analyse_coins.md) (3 shared connections)
- [_risk_levels_for](_risk_levels_for.md) (2 shared connections)
- [technical_indicators.py](technical_indicators.py.md) (2 shared connections)
- [_extract_scout_items](_extract_scout_items.md) (2 shared connections)
- [portfolio_panel.py](portfolio_panel.py.md) (1 shared connections)
- [scout_tools.py](scout_tools.py.md) (1 shared connections)

## Source Files

- `src/crypto_council_flow/main.py`
- `tests/test_short_term_caps.py`

## Audit Trail

- EXTRACTED: 64 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*