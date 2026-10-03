"""Both council crews must instantiate.

CrewBase maps the `agent:` field of EVERY task in tasks_config against that
crew class's @agent methods (crewai/project/crew_base.py map_all_task_variables).
A mismatch — e.g. tasks_config containing a task whose agent has no @agent
method on the class — raises KeyError at instantiation, before any kickoff.
"""

from pathlib import Path

import crewai.memory.storage.kickoff_task_outputs_storage as _kts
import crewai_core.paths as _paths


def _workspace_storage() -> str:
    """Redirect CrewAI SQLite storage into the project tree.

    db_storage_path() defaults to the user's AppData dir, which may be
    unwritable in sandboxed CI. Patch both import sites.
    """
    p = str(Path(__file__).resolve().parent.parent / ".tmp_crewai_storage")
    Path(p).mkdir(parents=True, exist_ok=True)
    return p


_paths.db_storage_path = _workspace_storage
_kts.db_storage_path = _workspace_storage


def test_scout_crew_instantiates() -> None:
    from crypto_council_flow.crews.council.council_crew import CouncilScoutCrew

    crew = CouncilScoutCrew().crew()
    assert [t.name for t in crew.tasks] == ["market_scout_task"]
    assert [a.role for a in crew.agents] == ["Crypto Market Scout"]


def test_analysis_crew_instantiates() -> None:
    from crypto_council_flow.crews.council.council_crew import CouncilAnalysisCrew

    crew = CouncilAnalysisCrew().crew()
    assert [t.name for t in crew.tasks] == [
        "technical_analysis_task",
        "sentiment_analysis_task",
        "risk_assessment_task",
    ]
    assert {a.role for a in crew.agents} == {
        "{symbol} Technical Analyst",
        "{symbol} Sentiment Analyst",
        "Crypto Risk Manager",
    }


def test_analysis_and_portfolio_crews_have_time_budgets() -> None:
    """Plan Task 5: per-agent time budgets bound slow-LLM worst case."""
    from crypto_council_flow.crews.council.council_crew import (
        CouncilAnalysisCrew,
        CouncilPortfolioCrew,
    )

    budgets = {a.role: getattr(a, "max_execution_time") for a in CouncilAnalysisCrew().crew().agents}
    assert budgets == {
        "{symbol} Technical Analyst": 420,
        "{symbol} Sentiment Analyst": 300,
        "Crypto Risk Manager": 300,
    }
    (pm,) = CouncilPortfolioCrew().crew().agents
    assert getattr(pm, "max_execution_time") == 300
    assert getattr(pm, "max_iter") == 5
