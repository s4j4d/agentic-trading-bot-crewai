# CouncilPortfolioCrew

> God node · 16 connections · `src/crypto_council_flow/crews/council/council_crew.py`

**Community:** [CouncilAnalysisCrew](CouncilAnalysisCrew.md)

## Connections by Relation

### calls
- .manage_portfolio() `EXTRACTED`
- .test_portfolio_agent_has_both_tools() `INFERRED`
- .test_portfolio_crew_instantiates() `INFERRED`
- .test_portfolio_task_config_renders() `INFERRED`

### contains
- [council_crew.py](council_crew.py.md) `EXTRACTED`

### imports
- [main.py](main.py.md) `EXTRACTED`
- council/__init__.py `EXTRACTED`

### method
- .portfolio_manager() `EXTRACTED`
- .crew() `EXTRACTED`
- .portfolio_management_task() `EXTRACTED`

### rationale_for
- Runs the portfolio_management_task only. Called after each analysis cycle. `EXTRACTED`

### references
- CrewBase `EXTRACTED`

### uses
- [CryptoCouncilFlow](CryptoCouncilFlow.md) `INFERRED`
- TestCouncilPortfolioCrew `INFERRED`
- test_scout_crews_still_build() `INFERRED`
- test_analysis_and_portfolio_crews_have_time_budgets() `INFERRED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*