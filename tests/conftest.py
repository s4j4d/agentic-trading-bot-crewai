"""
Shared pytest fixtures for the crypto_council_flow test suite.
"""

import json
import pytest


# ---------------------------------------------------------------------------
# Minimal valid CoinGecko trending response
# ---------------------------------------------------------------------------

@pytest.fixture()
def trending_api_response():
    """Minimal valid CoinGecko /search/trending payload."""
    return {
        "coins": [
            {
                "item": {
                    "id": "solana",
                    "symbol": "sol",
                    "name": "Solana",
                    "market_cap_rank": 5,
                    "price_btc": 0.00234,
                    "score": 0,
                }
            },
            {
                "item": {
                    "id": "ethereum",
                    "symbol": "eth",
                    "name": "Ethereum",
                    "market_cap_rank": 2,
                    "price_btc": 0.05,
                    "score": 1,
                }
            },
        ]
    }


# ---------------------------------------------------------------------------
# Minimal valid CoinGecko /coins/markets response (reused by multiple tools)
# ---------------------------------------------------------------------------

@pytest.fixture()
def markets_api_response():
    """Two-coin /coins/markets payload."""
    return [
        {
            "id": "solana",
            "symbol": "sol",
            "name": "Solana",
            "current_price": 150.0,
            "market_cap": 65_000_000_000,
            "market_cap_rank": 5,
            "total_volume": 3_000_000_000,
            "price_change_percentage_24h": 8.5,
            "price_change_percentage_1h_in_currency": 1.2,
            "price_change_percentage_7d_in_currency": 12.0,
        },
        {
            "id": "avalanche-2",
            "symbol": "avax",
            "name": "Avalanche",
            "current_price": 35.0,
            "market_cap": 14_000_000_000,
            "market_cap_rank": 12,
            "total_volume": 600_000_000,
            "price_change_percentage_24h": 5.1,
            "price_change_percentage_1h_in_currency": 0.3,
            "price_change_percentage_7d_in_currency": -2.0,
        },
    ]


# ---------------------------------------------------------------------------
# Minimal valid CryptoPanic response
# ---------------------------------------------------------------------------

@pytest.fixture()
def cryptopanic_api_response():
    """Minimal CryptoPanic /posts/ payload with one bullish and one bearish article."""
    return {
        "results": [
            {
                "title": "Solana ETF approval expected next month",
                "url": "https://example.com/1",
                "published_at": "2024-01-15T10:00:00Z",
                "currencies": [{"code": "SOL"}],
                "votes": {"positive": 20, "negative": 2},
            },
            {
                "title": "Exchange hack drains funds",
                "url": "https://example.com/2",
                "published_at": "2024-01-15T09:00:00Z",
                "currencies": [{"code": "XYZ"}],
                "votes": {"positive": 1, "negative": 15},
            },
            {
                "title": "Bitcoin mainnet upgrade announced",
                "url": "https://example.com/3",
                "published_at": "2024-01-15T08:00:00Z",
                "currencies": [{"code": "BTC"}],
                "votes": {"positive": 30, "negative": 0},
            },
        ]
    }


# ---------------------------------------------------------------------------
# Minimal scout output used by run_scout parsing tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def valid_scout_json():
    """A valid JSON array that the scout task would produce."""
    return [
        {
            "rank": 1,
            "coin_id": "solana",
            "symbol": "SOL",
            "name": "Solana",
            "score": 82,
            "signals": ["trending_rank_1", "momentum_+8.5%_24h"],
            "risk_tier": "MEDIUM",
            "reason": "Top trending with strong 24h momentum.",
        },
        {
            "rank": 2,
            "coin_id": "avalanche-2",
            "symbol": "AVAX",
            "name": "Avalanche",
            "score": 65,
            "signals": ["momentum_+5.1%_24h"],
            "risk_tier": "LOW",
            "reason": "Solid momentum in the top-20 market cap.",
        },
    ]
