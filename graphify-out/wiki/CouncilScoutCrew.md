# CouncilScoutCrew

> God node · 15 connections · `src/crypto_council_flow/crews/council/council_crew.py`

**Community:** [CouncilAnalysisCrew](CouncilAnalysisCrew.md)

## Connections by Relation

### calls
- .run_scout() `EXTRACTED`

### contains
- [council_crew.py](council_crew.py.md) `EXTRACTED`

### imports
- [main.py](main.py.md) `EXTRACTED`
- council/__init__.py `EXTRACTED`

### method
- .market_scout() `EXTRACTED`
- .crew() `EXTRACTED`
- .market_scout_task() `EXTRACTED`

### rationale_for
- Runs the market_scout_task only. Called every 2 hours by the Flow. `EXTRACTED`

### references
- CrewBase `EXTRACTED`

### uses
- [CryptoCouncilFlow](CryptoCouncilFlow.md) `INFERRED`
- test_scout_crews_still_build() `INFERRED`
- test_council_crew_importable() `INFERRED`
- test_scout_crew_has_time_budget_and_narrowed_prompt() `INFERRED`
- test_scout_task_uses_structured_output() `INFERRED`
- test_scout_crew_instantiates() `INFERRED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*