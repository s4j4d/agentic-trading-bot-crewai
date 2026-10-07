# test_fast_mode.py

> 16 nodes

## Key Concepts

- **test_fast_mode.py** (10 connections) — `tests/test_fast_mode.py`
- **_flow_with()** (8 connections) — `tests/test_fast_mode.py`
- **_install_fake_crew()** (7 connections) — `tests/test_fast_mode.py`
- **test_analyse_coins_fast_skips_portfolio_extras()** (4 connections) — `tests/test_fast_mode.py`
- **test_analyse_coins_fills_portfolio_extras_within_cap()** (4 connections) — `tests/test_fast_mode.py`
- **test_analyse_coins_respects_max_coins()** (4 connections) — `tests/test_fast_mode.py`
- **test_parallel_analysis_runs_concurrently()** (4 connections) — `tests/test_fast_mode.py`
- **test_parallel_merge_keeps_all_coins()** (4 connections) — `tests/test_fast_mode.py`
- **test_to_analyse_cap()** (3 connections) — `tests/test_fast_mode.py`
- **Parallel per-coin analysis (plan Task 7: sub-15-min flow). Workers run…** (1 connections) — `tests/test_fast_mode.py`
- **Normal mode: portfolio holds fill spare slots up to the cap.** (1 connections) — `tests/test_fast_mode.py`
- **3 coins x 0.4s of work must finish well under serial time, on >1 thread.** (1 connections) — `tests/test_fast_mode.py`
- **Replace CouncilAnalysisCrew with a fake; redirect output files to tmp.** (1 connections) — `tests/test_fast_mode.py`
- **Every coin's report lands in state + on disk, even with a failure.** (1 connections) — `tests/test_fast_mode.py`
- **analyse_coins analyses at most max_coins, highest score first.** (1 connections) — `tests/test_fast_mode.py`
- **Fast mode: portfolio holds outside the cap are not pulled in.** (1 connections) — `tests/test_fast_mode.py`

## Relationships

- [CryptoCouncilFlow](CryptoCouncilFlow.md) (2 shared connections)
- [CoinOpportunity](CoinOpportunity.md) (2 shared connections)
- [main.py](main.py.md) (1 shared connections)

## Source Files

- `tests/test_fast_mode.py`

## Audit Trail

- EXTRACTED: 28 (93%)
- INFERRED: 2 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*