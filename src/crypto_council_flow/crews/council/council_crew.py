"""
Crypto Council Crew

Orchestrates four specialist agents:
  - market_scout      — scans trending/momentum/catalyst data for the best coins
  - technical_analyst — price chart & indicator analysis for a given coin
  - sentiment_analyst — market psychology & news sentiment for a given coin
  - risk_manager      — position sizing, VaR, and final trade plan

Two crew entry points are exposed:

1. CouncilScoutCrew  — runs the market_scout_task only.
   Call this every 2 hours to refresh the opportunity list.

2. CouncilAnalysisCrew — runs technical → sentiment → risk for a single coin.
   Call this every 5 minutes per coin from the scout list.

Agent definitions live in config/agents.jsonc (JSONC with comments).
A custom _load_jsonc() helper parses the file at module load time.
Tasks are defined in config/scout_tasks.yaml (CouncilScoutCrew) and
config/analysis_tasks.yaml (CouncilAnalysisCrew).
Each crew points at its own file because CrewBase maps the `agent:` field of
EVERY task in tasks_config against that class's @agent methods.
Each agent has a SKILL.md file in skills/<agent-name>/ loaded at INSTRUCTIONS level.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from crewai.skills.loader import load_skill

from crypto_council_flow.tools.technical_indicators import (
    ATRTool,
    BollingerBandsTool,
    EMACrossTool,
    MACDTool,
    RSITool,
)
from crypto_council_flow.tools.sentiment_tools import (
    CommunitySentimentTool,
    CryptoNewsTool,
    FearGreedIndexTool,
    MarketDominanceTool,
)
from crypto_council_flow.tools.risk_tools import (
    AssetCorrelationTool,
    LiquidationPriceTool,
    PortfolioVaRTool,
    PositionSizingTool,
)
from crypto_council_flow.tools.scout_tools import (
    MomentumScreenerTool,
    NewListingsTool,
    TrendingCoinsTool,
    UpcomingCatalystsTool,
)


_CONFIG_DIR = Path(__file__).parent / "config"
_SKILLS_DIR = Path(__file__).parent / "skills"


# ---------------------------------------------------------------------------
# JSONC loader
# ---------------------------------------------------------------------------

def _load_jsonc(path: Path) -> dict[str, Any]:
    """Parse a JSONC file (JSON with // comments and trailing commas).

    The agents.jsonc uses parenthesised multi-line string syntax for readability,
    which is not valid JSON. This function pre-processes those patterns before
    handing off to the standard json parser.
    """
    raw = path.read_text(encoding="utf-8")

    # Remove single-line comments:  // ...
    raw = re.sub(r"//[^\n]*", "", raw)

    # Remove block comments:  /* ... */
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)

    # Collapse parenthesised multi-line strings:
    #   "key": (
    #       "part one "
    #       "part two"
    #   ),
    # → "key": "part one part two",
    raw = re.sub(r':\s*\(\s*\n', ': ', raw)          # remove opening paren
    raw = re.sub(r'"\s*\n\s*"', ' ', raw)            # join adjacent string lines
    raw = re.sub(r'\)\s*,', ',', raw)                 # remove closing paren + comma
    raw = re.sub(r'\)\s*\n', '\n', raw)               # remove trailing closing paren

    # Remove trailing commas before } or ]
    raw = re.sub(r',\s*([}\]])', r'\1', raw)

    return json.loads(raw)


# Load once at module level — before @CrewBase processes the class.
_AGENTS_DICT: dict[str, Any] = _load_jsonc(_CONFIG_DIR / "agents.jsonc")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_agent_skill(skill_dir_name: str) -> list:
    """Load SKILL.md from skills/<skill_dir_name>/ at INSTRUCTIONS level."""
    skills = load_skill(_SKILLS_DIR)
    return [s for s in skills if s.name == skill_dir_name]


def _scout_tools() -> list:
    return [
        TrendingCoinsTool(),
        MomentumScreenerTool(),
        NewListingsTool(),
        UpcomingCatalystsTool(),
    ]


def _technical_tools() -> list:
    return [RSITool(), MACDTool(), BollingerBandsTool(), EMACrossTool(), ATRTool()]


def _sentiment_tools() -> list:
    return [FearGreedIndexTool(), CryptoNewsTool(), CommunitySentimentTool(), MarketDominanceTool()]


def _risk_tools() -> list:
    return [PositionSizingTool(), LiquidationPriceTool(), PortfolioVaRTool(), AssetCorrelationTool()]


# ---------------------------------------------------------------------------
# Scout Crew  (market_scout only)
# ---------------------------------------------------------------------------

@CrewBase
class CouncilScoutCrew:
    """Runs the market_scout_task only. Called every 2 hours by the Flow."""

    agents: list[BaseAgent]
    tasks: list[Task]

    tasks_config: str = "config/scout_tasks.yaml"

    @agent
    def market_scout(self) -> Agent:
        cfg = _AGENTS_DICT["market_scout"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=_scout_tools(),
            skills=_load_agent_skill("market-scout") or None,
            verbose=cfg.get("verbose", True),
            allow_delegation=cfg.get("allow_delegation", False),
            reasoning=cfg.get("reasoning", True),
        )

    @task
    def market_scout_task(self) -> Task:
        return Task(
            config=self.tasks_config["market_scout_task"],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        """Scout-only crew: single agent, single task."""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )


# ---------------------------------------------------------------------------
# Analysis Crew  (technical → sentiment → risk for one coin)
# ---------------------------------------------------------------------------

@CrewBase
class CouncilAnalysisCrew:
    """
    Runs the three-agent analysis pipeline for a single coin.

    Inputs expected at kickoff:
        symbol          — uppercase ticker, e.g. "SOL"
        coin_id         — CoinGecko slug, e.g. "solana"
        vs_currency     — quote currency, e.g. "usd"
        period          — RSI period, e.g. 14
        account_size_usd — account size in USD, e.g. 10000
    """

    agents: list[BaseAgent]
    tasks: list[Task]

    tasks_config: str = "config/analysis_tasks.yaml"

    @agent
    def technical_analyst(self) -> Agent:
        cfg = _AGENTS_DICT["technical_analyst"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=_technical_tools(),
            skills=_load_agent_skill("technical-analyst") or None,
            verbose=cfg.get("verbose", True),
            allow_delegation=cfg.get("allow_delegation", False),
            reasoning=cfg.get("reasoning", True),
        )

    @agent
    def sentiment_analyst(self) -> Agent:
        cfg = _AGENTS_DICT["sentiment_analyst"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=_sentiment_tools(),
            skills=_load_agent_skill("sentiment-analyst") or None,
            verbose=cfg.get("verbose", True),
            allow_delegation=cfg.get("allow_delegation", False),
            reasoning=cfg.get("reasoning", True),
        )

    @agent
    def risk_manager(self) -> Agent:
        cfg = _AGENTS_DICT["risk_manager"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=_risk_tools(),
            skills=_load_agent_skill("risk-manager") or None,
            verbose=cfg.get("verbose", True),
            allow_delegation=cfg.get("allow_delegation", False),
            reasoning=cfg.get("reasoning", True),
        )

    @task
    def technical_analysis_task(self) -> Task:
        return Task(
            config=self.tasks_config["technical_analysis_task"],  # type: ignore[index]
        )

    @task
    def sentiment_analysis_task(self) -> Task:
        return Task(
            config=self.tasks_config["sentiment_analysis_task"],  # type: ignore[index]
        )

    @task
    def risk_assessment_task(self) -> Task:
        return Task(
            config=self.tasks_config["risk_assessment_task"],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        """Sequential pipeline: technical → sentiment → risk."""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
