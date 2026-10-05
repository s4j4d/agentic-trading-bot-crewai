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
import os
import re
from pathlib import Path
from typing import Any

from crewai import Agent, Crew, Memory, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from crewai.skills.loader import load_skill
from pydantic import BaseModel, Field, model_validator

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
    VolatilityScreenerTool,
)
from crypto_council_flow.tools.exchange_base import (
    ExchangeMarketsTool,
    ExchangeOHLCTool,
    ExchangeTickerTool,
    ExchangeBatchTickerTool,
)
from crypto_council_flow.tools.portfolio_tools import (
    PortfolioExposureTool,
    RebalanceAllocatorTool,
    RiskLevelsTool,
)

embedder = {
    "provider": "ollama",
    "config": {
        "model": "nomic-embed-text",
        "url": "http://localhost:11434/api/embeddings"
    }
}

memory = Memory(
    llm="ollama/qwen3:14b",

    embedder={
        "provider": "ollama",
        "config": {
            "model_name": "nomic-embed-text",
            "url": "http://localhost:11434/api/embeddings",
        },
    },

    # Don't perform expensive similarity consolidation on every save
    consolidation_threshold=1.0,

    # Don't retrieve excessive amounts of memory
    confidence_threshold_high=0.8,
    exploration_budget=0,

    # Don't let normal queries trigger unnecessary analysis
    query_analysis_threshold=1000,
    storage="./memory"
)

# Task 6: gate CrewAI memory behind env var (default off — generic
# conversation memory adds Ollama embed latency on every agent step with
# no trading value; real-trading memory is Task 9 trade journal).
_USE_MEMORY = os.getenv("COUNCIL_MEMORY", "false").lower() == "true"

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
        ExchangeMarketsTool(),
        ExchangeTickerTool(),
        ExchangeBatchTickerTool(),
        ExchangeOHLCTool(),
        TrendingCoinsTool(),
        MomentumScreenerTool(),
        NewListingsTool(),
        VolatilityScreenerTool()
    ]


def _technical_tools() -> list:
    return [RSITool(), MACDTool(), BollingerBandsTool(), EMACrossTool(), ATRTool()]


def _sentiment_tools() -> list:
    return [FearGreedIndexTool(), CryptoNewsTool(), CommunitySentimentTool(), MarketDominanceTool()]


def _risk_tools() -> list:
    return [PositionSizingTool(), LiquidationPriceTool(), PortfolioVaRTool(), AssetCorrelationTool()]


def _portfolio_tools() -> list:
    return [PortfolioExposureTool(), RebalanceAllocatorTool(), RiskLevelsTool()]


# ---------------------------------------------------------------------------
# Structured scout output — parsed by Flow from result.pydantic, no raw JSON
# parsing and no file roundtrip (agent has no write_file tool).
# ---------------------------------------------------------------------------

class ScoutOpportunity(BaseModel):
    """Single canonical DTO for scout output.

    CrewAI's output validation works against exactly these 8 fields.
    A ``model_validator(mode=\"before\")`` normalises any LLM field-name
    variants into this canonical shape *before* validation, so each record
    always ends up with the single required DTO — two DTOs are never valid.
    """
    rank: int = 0  # positional; defaults when the LLM omits it
    coin_id: str
    symbol: str
    name: str
    score: int
    signals: list[str] = Field(default_factory=list)
    risk_tier: str = "MEDIUM"
    reason: str = ""

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = dict(data)

        # coin_id aliases
        if not d.get("coin_id") and d.get("coingecko_id"):
            d["coin_id"] = d["coingecko_id"]

        # symbol aliases — also extract from "Name (SYM)" combined format
        if not d.get("symbol"):
            if d.get("ticker"):
                d["symbol"] = d["ticker"]
            elif isinstance(d.get("coin"), str) and "(" in d["coin"]:
                inside = d["coin"].split("(")[-1].rstrip(")").strip()
                if inside.isalpha() and len(inside) <= 6:
                    d["symbol"] = inside.upper()

        # name aliases — strip "(SYM)" suffix from combined format
        if not d.get("name") and isinstance(d.get("coin"), str):
            d["name"] = d["coin"].split("(")[0].strip() or d["coin"]

        # score aliases — also handle 0-1 float weighted_score → 0-100 int
        if d.get("score") is None and d.get("weighted_score") is not None:
            raw = d["weighted_score"]
            try:
                raw = float(raw)
                d["score"] = int(round(raw * 100)) if 0 <= raw <= 1 else int(round(raw))
            except (TypeError, ValueError):
                d["score"] = 50

        # rank aliases
        if d.get("rank") is None and d.get("rank_position") is not None:
            d["rank"] = d["rank_position"]
        # default positional rank when the LLM omits numbering entirely
        if d.get("rank") is None:
            d["rank"] = 0

        # coin_id fallback — resolve from symbol via a lightweight lookup
        if not d.get("coin_id"):
            sym = (d.get("symbol") or "").upper()
            d["coin_id"] = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(sym, (d.get("symbol") or "").lower().replace(" ", "-"))

        return d


class ScoutShortlist(BaseModel):
    opportunities: list[ScoutOpportunity] = Field(default_factory=list)


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
            max_iter=8,
            max_retry_limit=2,
            max_rpm=1,
            max_execution_time=400
        )

    @task
    def market_scout_task(self) -> Task:
        return Task(
            config=self.tasks_config["market_scout_task"],  # type: ignore[index]
            output_pydantic=ScoutShortlist,
        )

    

    @crew
    def crew(self) -> Crew:
        """Scout-only crew: single agent, single task."""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            memory=memory if _USE_MEMORY else None,
            verbose=True,
            embedder=embedder if _USE_MEMORY else None,
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
        atr_period      — ATR period, e.g. 14
        account_size    — account size, e.g. 10000
        base_currency   — currency label, e.g. "toman"
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
            max_execution_time=420,
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
            reasoning=cfg.get("reasoning", True),
            max_execution_time=300,
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
            max_iter=3,
            max_retry_limit=2,
            max_execution_time=300,
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
            memory=memory if _USE_MEMORY else None,
            embedder=embedder if _USE_MEMORY else None,
        )


# ---------------------------------------------------------------------------
# Portfolio DTOs — single canonical shape, alias normalisation
# ---------------------------------------------------------------------------

class PortfolioAction(BaseModel):
    """One per-coin rebalance action. One DTO, never two."""

    coin_id: str = ""
    symbol: str = ""
    action: str = "hold"
    current: float = 0.0
    target: float = 0.0
    reason: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None
    sl_tp_source: str = "computed"  # "explicit" (crew numbers kept) | "computed" (ATR values used) | "none"

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = dict(data)

        if not d.get("coin_id") and d.get("coingecko_id"):
            d["coin_id"] = d["coingecko_id"]

        if not d.get("symbol"):
            if d.get("ticker"):
                d["symbol"] = d["ticker"]
            elif isinstance(d.get("coin"), str) and "(" in d["coin"]:
                inside = d["coin"].split("(")[-1].rstrip(")").strip()
                if inside.isalpha() and len(inside) <= 6:
                    d["symbol"] = inside.upper()
        if isinstance(d.get("symbol"), str):
            d["symbol"] = d["symbol"].upper()

        # action aliases — converge on the five canonical verbs
        raw_action = str(d.get("action") or d.get("side") or "hold").lower().strip()
        action_map = {
            "buy": "open", "enter": "open", "long": "open",
            "add": "increase", "scale_in": "increase",
            "trim": "decrease", "reduce": "decrease", "scale_out": "decrease",
            "sell": "close", "exit": "close", "flat": "close",
        }
        d["action"] = action_map.get(raw_action, raw_action)
        if d["action"] not in ("open", "increase", "decrease", "close", "hold"):
            d["action"] = "hold"

        # amount aliases — canonical keys are current/target; legacy *_usd
        # keys still parse so old LLM payloads and stored plans keep working
        for key, fallbacks in (
            ("current", ("current_usd", "current_base")),
            ("target", ("target_usd", "target_base")),
        ):
            if d.get(key) is None:
                for fb in fallbacks:
                    if d.get(fb) is not None:
                        d[key] = d[fb]
                        break
        if d.get("action") == "close":
            d["target"] = 0.0

        # risk-level aliases — junk ("" / "n/a" / non-numeric) becomes None
        # so LLM free-text never breaks PortfolioAction validation
        for key in ("stop_loss", "take_profit"):
            val = d.get(key)
            if val is None:
                continue
            try:
                d[key] = float(val)
            except (TypeError, ValueError):
                d[key] = None

        return d


class PortfolioPlan(BaseModel):
    """Cap-aware rebalance plan. Single canonical DTO for portfolio output."""

    actions: list[PortfolioAction] = Field(default_factory=list)
    total_target: float = 0.0
    total_exposure_pct: float = 0.0
    cash_remaining: float = 0.0
    rationale: str = ""

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        # envelope aliases
        if d.get("actions") is None:
            for alt in ("plan", "allocations", "positions", "trades"):
                if isinstance(d.get(alt), list):
                    d["actions"] = d[alt]
                    break
        if isinstance(d.get("actions"), dict):
            d["actions"] = list(d["actions"].values())
        # amount aliases — legacy *_usd keys converge on the canonical shape
        for key, fallbacks in (
            ("total_target", ("total_target_usd",)),
            ("cash_remaining", ("cash_remaining_usd",)),
        ):
            if d.get(key) is None:
                for fb in fallbacks:
                    if d.get(fb) is not None:
                        d[key] = d[fb]
                        break
        return d


# ---------------------------------------------------------------------------
# Portfolio Crew  (portfolio_manager only)
# ---------------------------------------------------------------------------

@CrewBase
class CouncilPortfolioCrew:
    """Runs the portfolio_management_task only. Called after each analysis cycle."""

    agents: list[BaseAgent]
    tasks: list[Task]

    tasks_config: str = "config/portfolio_tasks.yaml"

    @agent
    def portfolio_manager(self) -> Agent:
        cfg = _AGENTS_DICT["portfolio_manager"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=_portfolio_tools(),
            skills=_load_agent_skill("portfolio-manager") or None,
            verbose=cfg.get("verbose", True),
            allow_delegation=cfg.get("allow_delegation", False),
            reasoning=cfg.get("reasoning", True),
            max_iter=5,
            max_retry_limit=0,
            max_rpm=1,
            max_execution_time=300,
        )

    @task
    def portfolio_management_task(self) -> Task:
        return Task(
            config=self.tasks_config["portfolio_management_task"],  # type: ignore[index]
            output_pydantic=PortfolioPlan,
        )

    @crew
    def crew(self) -> Crew:
        """Portfolio-only crew: single agent, single task."""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            memory=memory if _USE_MEMORY else None,
            verbose=True,
            embedder=embedder if _USE_MEMORY else None,
        )
