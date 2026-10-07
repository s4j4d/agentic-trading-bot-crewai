# _fmt_dur

> 13 nodes

## Key Concepts

- **_fmt_dur()** (9 connections) — `src/crypto_council_flow/main.py`
- **.run_scout()** (7 connections) — `src/crypto_council_flow/main.py`
- **_run_async()** (6 connections) — `src/crypto_council_flow/main.py`
- **_append_to_consolidated()** (5 connections) — `src/crypto_council_flow/main.py`
- **_build_report()** (5 connections) — `src/crypto_council_flow/main.py`
- **_fmt_next_runs()** (4 connections) — `src/crypto_council_flow/main.py`
- **start** (1 connections)
- **Wrap the crew's raw analysis output with a scout context header.** (1 connections) — `src/crypto_council_flow/main.py`
- **Append a single coin's analysis to the consolidated results file.** (1 connections) — `src/crypto_council_flow/main.py`
- **Outer async scheduler. Scout cadence: every SCOUT_INTERVAL_SECONDS (2 hours)…** (1 connections) — `src/crypto_council_flow/main.py`
- **Format seconds as '45s', '1m 23s', or '1h 02m'.** (1 connections) — `src/crypto_council_flow/main.py`
- **Per-step 'due in X' summary for the scheduler tick log. The scheduler wakes…** (1 connections) — `src/crypto_council_flow/main.py`
- **Run CouncilScoutCrew to refresh the opportunity list. Called once at startup…** (1 connections) — `src/crypto_council_flow/main.py`

## Relationships

- [main.py](main.py.md) (6 shared connections)
- [.analyse_coins](analyse_coins.md) (3 shared connections)
- [CoinOpportunity](CoinOpportunity.md) (3 shared connections)
- [CryptoCouncilFlow](CryptoCouncilFlow.md) (2 shared connections)
- [CouncilAnalysisCrew](CouncilAnalysisCrew.md) (1 shared connections)
- [_extract_scout_items](_extract_scout_items.md) (1 shared connections)
- [.manage_portfolio](manage_portfolio.md) (1 shared connections)

## Source Files

- `src/crypto_council_flow/main.py`

## Audit Trail

- EXTRACTED: 30 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*