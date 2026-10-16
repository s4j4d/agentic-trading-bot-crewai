"""Both council crews must instantiate.

CrewBase maps the `agent:` field of EVERY task in tasks_config against that
crew class's @agent methods (crewai/project/crew_base.py map_all_task_variables).
A mismatch — e.g. tasks_config containing a task whose agent has no @agent
method on the class — raises KeyError at instantiation, before any kickoff.
"""

import importlib
import os
from pathlib import Path

import crewai.memory.storage.kickoff_task_outputs_storage as _kts
import crewai_core.paths as _paths

import crypto_council_flow.crews.council.council_crew as _scout_module


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


def _reload_scout_module() -> None:
    """Re-read the scout module so a monkeypatched env var takes effect."""
    importlib.reload(_scout_module)


def test_scout_crew_has_time_budget_and_narrowed_prompt(monkeypatch) -> None:
    """Scout is budgeted and pinned to exactly 3 finalists.

    The budget was raised from 300s to 400s in 868cd7c (per-coin analysis verdicts
    plus max_hold_days) and the prompt wording moved from "max 3" to "exactly 3";
    this assertion was left behind and has failed on a clean tree ever since.
    """
    from crypto_council_flow.crews.council.council_crew import CouncilScoutCrew

    monkeypatch.delenv("COUNCIL_SCOUT_MAX_EXECUTION_TIME", raising=False)
    _reload_scout_module()
    try:
        (scout,) = CouncilScoutCrew().crew().agents
        assert getattr(scout, "max_iter") == 8
        assert getattr(scout, "max_execution_time") == 500
    finally:
        monkeypatch.undo()
        _reload_scout_module()

    crew = CouncilScoutCrew().crew()
    (t,) = [t for t in crew.tasks if t.name == "market_scout_task"]
    assert "3-7" not in t.description
    assert "exactly 3" in t.description


def test_scout_budget_is_env_tunable(monkeypatch) -> None:
    """COUNCIL_SCOUT_MAX_EXECUTION_TIME overrides the 500s default."""
    from crypto_council_flow.crews.council.council_crew import CouncilScoutCrew

    for raw, expected in (("900", 900), ("", 500), ("junk", 500), ("0", 500), ("-30", 500)):
        monkeypatch.setenv("COUNCIL_SCOUT_MAX_EXECUTION_TIME", raw)
        _reload_scout_module()
        try:
            (scout,) = CouncilScoutCrew().crew().agents
            assert getattr(scout, "max_execution_time") == expected, raw
        finally:
            monkeypatch.undo()
    _reload_scout_module()
