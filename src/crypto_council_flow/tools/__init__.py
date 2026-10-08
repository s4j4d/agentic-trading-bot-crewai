"""
Tools package for the Crypto Council flow.

Exports all tool classes organised by agent domain.
"""

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
    ExchangeBatchTickerTool,
    ExchangeOHLCTool,
)
from crypto_council_flow.tools.portfolio_tools import (
    PortfolioExposureTool,
    RebalanceAllocatorTool,
)
from crypto_council_flow.tools.execution_base import (
    ExecutionClient,
    ExecutionError,
    ExecutionHalted,
    Balance,
    OrderRequest,
    OrderResult,
    Fill,
    get_execution_client,
    get_execution_mode,
)
from crypto_council_flow.tools.execution_paper import PaperExecutionClient
from crypto_council_flow.tools.execution_nobitex import NobitexExecutionClient

__all__ = [
    # Technical analysis
    "RSITool",
    "MACDTool",
    "BollingerBandsTool",
    "EMACrossTool",
    "ATRTool",
    # Sentiment analysis
    "FearGreedIndexTool",
    "CryptoNewsTool",
    "CommunitySentimentTool",
    "MarketDominanceTool",
    # Risk management
    "PositionSizingTool",
    "LiquidationPriceTool",
    "PortfolioVaRTool",
    "AssetCorrelationTool",
    # Market scouting
    "TrendingCoinsTool",
    "MomentumScreenerTool",
    "NewListingsTool",
    "UpcomingCatalystsTool",
    "VolatilityScreenerTool",
    # Exchange market data (abstract, exchange-agnostic)
    "ExchangeMarketsTool",
    "ExchangeBatchTickerTool",
    "ExchangeOHLCTool",
    # Portfolio management (paper)
    "PortfolioExposureTool",
    "RebalanceAllocatorTool",
    # Execution (abstract, exchange-agnostic)
    "ExecutionClient",
    "ExecutionError",
    "ExecutionHalted",
    "Balance",
    "OrderRequest",
    "OrderResult",
    "Fill",
    "get_execution_client",
    "get_execution_mode",
    "PaperExecutionClient",
    "NobitexExecutionClient",
]
