"""
Import sanity checks — verifies that every public symbol can be imported
without errors. No external services are called.
"""

import importlib


def test_main_module_importable():
    mod = importlib.import_module("crypto_council_flow.main")
    assert hasattr(mod, "CryptoCouncilFlow")
    assert hasattr(mod, "CoinOpportunity")
    assert hasattr(mod, "CryptoCouncilState")
    assert hasattr(mod, "kickoff")
    assert hasattr(mod, "plot")
    assert hasattr(mod, "_build_report")


def test_tools_init_importable():
    mod = importlib.import_module("crypto_council_flow.tools")
    expected = [
        "RSITool", "MACDTool", "BollingerBandsTool", "EMACrossTool", "ATRTool",
        "FearGreedIndexTool", "CryptoNewsTool", "CommunitySentimentTool",
        "MarketDominanceTool",
        "PositionSizingTool", "LiquidationPriceTool", "PortfolioVaRTool",
        "AssetCorrelationTool",
        "TrendingCoinsTool", "MomentumScreenerTool", "NewListingsTool",
        "UpcomingCatalystsTool",
    ]
    for name in expected:
        assert hasattr(mod, name), f"tools.__init__ is missing {name}"


def test_scout_tools_importable():
    from crypto_council_flow.tools.scout_tools import (
        TrendingCoinsTool,
        MomentumScreenerTool,
        NewListingsTool,
        UpcomingCatalystsTool,
    )
    for cls in (TrendingCoinsTool, MomentumScreenerTool, NewListingsTool,
                UpcomingCatalystsTool):
        assert callable(cls)


def test_council_crew_importable():
    from crypto_council_flow.crews.council.council_crew import (
        CouncilScoutCrew,
        CouncilAnalysisCrew,
        _load_jsonc,
    )
    assert callable(CouncilScoutCrew)
    assert callable(CouncilAnalysisCrew)
    assert callable(_load_jsonc)
