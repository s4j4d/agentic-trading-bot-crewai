# .analyse_coins

> 10 nodes

## Key Concepts

- **.analyse_coins()** (10 connections) — `src/crypto_council_flow/main.py`
- **_analyse_one_coin()** (6 connections) — `src/crypto_council_flow/main.py`
- **_analysis_workers()** (3 connections) — `src/crypto_council_flow/main.py`
- **_extract_verdict()** (3 connections) — `src/crypto_council_flow/main.py`
- **listen** (2 connections)
- **Exception** (1 connections)
- **Pull a short verdict excerpt from the tail of an analysis report.…** (1 connections) — `src/crypto_council_flow/main.py`
- **Run the analysis crew for one coin (worker thread). Pure worker: never touches…** (1 connections) — `src/crypto_council_flow/main.py`
- **Thread-pool size for per-coin analysis (env-overridable).** (1 connections) — `src/crypto_council_flow/main.py`
- **Run CouncilAnalysisCrew for each coin in the current opportunity list PLUS any…** (1 connections) — `src/crypto_council_flow/main.py`

## Relationships

- [main.py](main.py.md) (3 shared connections)
- [_fmt_dur](_fmt_dur.md) (3 shared connections)
- [CoinOpportunity](CoinOpportunity.md) (2 shared connections)
- [CouncilAnalysisCrew](CouncilAnalysisCrew.md) (1 shared connections)
- [CryptoCouncilFlow](CryptoCouncilFlow.md) (1 shared connections)
- [.manage_portfolio](manage_portfolio.md) (1 shared connections)

## Source Files

- `src/crypto_council_flow/main.py`

## Audit Trail

- EXTRACTED: 19 (95%)
- INFERRED: 1 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*