"""
Market-scouting tools for the market_scout agent.

All tools use free public APIs only:
  - CoinGecko (trending, top gainers, new coins, market data)
  - CryptoPanic (news headlines with vote counts)

No API keys required.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Type

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


_DEFAULT_TIMEOUT = 20
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_CRYPTOPANIC_URL = "https://newsdata.io/api/1/latest?apikey=pub_c111157bd75c4a9394c43bf276c0362a"

# Per-run result cache — keyed by (tool_name, frozenset of sorted arg items).
# Prevents re-calling a tool that already succeeded within one agent execution.
# ponytail: no TTL; clear_tool_cache() resets between flow cycles.
_tool_cache: dict[tuple[str, frozenset], str] = {}


def clear_tool_cache() -> None:
    """Reset between flow cycles."""
    _tool_cache.clear()


def _cache_get(name: str, kwargs: dict) -> str | None:
    key = (name, frozenset(kwargs.items()))
    return _tool_cache.get(key)


def _cache_put(name: str, kwargs: dict, result: str) -> None:
    if not result.startswith('{"error"'):
        _tool_cache[(name, frozenset(kwargs.items()))] = result


# ---------------------------------------------------------------------------
# Trending Coins Tool
# ---------------------------------------------------------------------------

class TrendingCoinsInput(BaseModel):
    top_n: int = Field(
        default=10,
        ge=1,
        le=15,
        description="Number of trending coins to return (CoinGecko returns up to 15).",
    )


class TrendingCoinsTool(BaseTool):
    name: str = "trending_coins"
    description: str = (
        "Fetches the top trending cryptocurrencies on CoinGecko in the last 24 hours, "
        "ranked by search volume. Returns the coin ID, symbol, market-cap rank, "
        "price in BTC, and 24-hour price change. Use this to identify coins with "
        "rising retail and investor interest."
    )
    args_schema: Type[BaseModel] = TrendingCoinsInput

    def _run(self, top_n: int = 10) -> str:
        cached = _cache_get(self.name, {"top_n": top_n})
        if cached is not None:
            return cached
        try:
            resp = requests.get(
                f"{_COINGECKO_BASE}/search/trending",
                timeout=_DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            coins = resp.json().get("coins", [])[:top_n]

            results = []
            for entry in coins:
                item = entry.get("item", {})
                results.append(
                    {
                        "coin_id": item.get("id", ""),
                        "symbol": item.get("symbol", "").upper(),
                        "name": item.get("name", ""),
                        "market_cap_rank": item.get("market_cap_rank"),
                        "price_btc": item.get("price_btc"),
                        "score": item.get("score"),  # CoinGecko trending rank (0 = #1)
                    }
                )

            result = json.dumps(
                {
                    "source": "coingecko_trending",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "count": len(results),
                    "coins": results,
                }
            )
            _cache_put(self.name, {"top_n": top_n}, result)
            return result
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# New Listings Tool
# ---------------------------------------------------------------------------

class NewListingsInput(BaseModel):
    top_n: int = Field(
        default=20,
        ge=5,
        le=50,
        description="Number of recently added coins to return.",
    )


class NewListingsTool(BaseTool):
    name: str = "new_listings"
    description: str = (
        "Fetches the most recently listed coins on CoinGecko. New listings often "
        "experience high short-term volatility and momentum. Returns the coin ID, "
        "symbol, name, and date it was first added to CoinGecko. Filter out coins "
        "with no price data (likely scams or illiquid tokens)."
    )
    args_schema: Type[BaseModel] = NewListingsInput

    def _run(self, top_n: int = 20) -> str:
        cached = _cache_get(self.name, {"top_n": top_n})
        if cached is not None:
            return cached
        try:
            params = {
                "vs_currency": "usd",
                "order": "id_desc",  # newest first by internal ID
                "per_page": top_n,
                "page": 1,
                "price_change_percentage": "24h",
                "sparkline": "false",
            }
            resp = requests.get(
                f"{_COINGECKO_BASE}/coins/markets",
                params=params,
                timeout=_DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json()

            results = []
            for coin in raw:
                market_cap = coin.get("market_cap") or 0
                price = coin.get("current_price") or 0
                if price <= 0:
                    continue  # skip coins with no live price
                results.append(
                    {
                        "coin_id": coin.get("id", ""),
                        "symbol": (coin.get("symbol") or "").upper(),
                        "name": coin.get("name", ""),
                        "current_price_usd": price,
                        "market_cap_usd": market_cap,
                        "market_cap_rank": coin.get("market_cap_rank"),
                        "price_change_24h_pct": coin.get("price_change_percentage_24h"),
                        "volume_24h_usd": coin.get("total_volume"),
                    }
                )

            result = json.dumps(
                {
                    "source": "coingecko_new_listings",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "count": len(results),
                    "coins": results,
                }
            )
            _cache_put(self.name, {"top_n": top_n}, result)
            return result
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Momentum Screener Tool
# ---------------------------------------------------------------------------

class MomentumScreenerInput(BaseModel):
    top_n: int = Field(
        default=20,
        ge=5,
        le=100,
        description="Number of top-gaining coins to return.",
    )
    min_volume_usd: float = Field(
        default=5_000_000.0,
        ge=0,
        description="Minimum 24-hour volume in USD to filter out illiquid coins.",
    )
    max_market_cap_rank: int = Field(
        default=500,
        ge=1,
        le=2000,
        description="Only consider coins within this market-cap rank (e.g. top 500).",
    )


class MomentumScreenerTool(BaseTool):
    name: str = "momentum_screener"
    description: str = (
        "Screens the top cryptocurrencies by 24-hour price gain, filtered by minimum "
        "volume and market-cap rank. Returns coins showing the strongest positive "
        "short-term momentum. Use this to find breakout candidates and coins with "
        "unusual volume spikes relative to their typical trading activity."
    )
    args_schema: Type[BaseModel] = MomentumScreenerInput

    def _run(
        self,
        top_n: int = 20,
        min_volume_usd: float = 5_000_000.0,
        max_market_cap_rank: int = 500,
    ) -> str:
        args = {"top_n": top_n, "min_volume_usd": min_volume_usd, "max_market_cap_rank": max_market_cap_rank}
        cached = _cache_get(self.name, args)
        if cached is not None:
            return cached
        try:
            # Fetch top coins by market cap to stay within free-tier rate limits
            params = {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": max_market_cap_rank,
                "page": 1,
                "price_change_percentage": "1h,24h,7d",
                "sparkline": "false",
            }
            resp = requests.get(
                f"{_COINGECKO_BASE}/coins/markets",
                params=params,
                timeout=_DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json()

            # Filter and sort
            candidates = []
            for coin in raw:
                volume = coin.get("total_volume") or 0
                rank = coin.get("market_cap_rank") or 9999
                change_24h = coin.get("price_change_percentage_24h") or 0.0
                if volume < min_volume_usd or rank > max_market_cap_rank:
                    continue
                candidates.append(
                    {
                        "coin_id": coin.get("id", ""),
                        "symbol": (coin.get("symbol") or "").upper(),
                        "name": coin.get("name", ""),
                        "current_price_usd": coin.get("current_price"),
                        "market_cap_rank": rank,
                        "price_change_1h_pct": coin.get(
                            "price_change_percentage_1h_in_currency"
                        ),
                        "price_change_24h_pct": change_24h,
                        "price_change_7d_pct": coin.get(
                            "price_change_percentage_7d_in_currency"
                        ),
                        "volume_24h_usd": volume,
                        "market_cap_usd": coin.get("market_cap"),
                    }
                )

            # Sort by 24h gain descending, take top_n
            candidates.sort(
                key=lambda c: c["price_change_24h_pct"] or 0.0, reverse=True
            )
            top = candidates[:top_n]

            result = json.dumps(
                {
                    "source": "coingecko_momentum_screener",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "filters": {
                        "min_volume_usd": min_volume_usd,
                        "max_market_cap_rank": max_market_cap_rank,
                    },
                    "count": len(top),
                    "coins": top,
                }
            )
            _cache_put(self.name, args, result)
            return result
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Upcoming Catalysts Tool  (CryptoPanic news scan)
# ---------------------------------------------------------------------------

class UpcomingCatalystsInput(BaseModel):
    limit: int = Field(
        default=20,
        ge=5,
        le=50,
        description="Number of news items to scan for catalyst signals.",
    )


class UpcomingCatalystsTool(BaseTool):
    name: str = "upcoming_catalysts"
    description: str = (
        "Scans CryptoPanic's 'important' and 'rising' news feeds to surface coins "
        "with significant upcoming or recent catalysts: ETF filings, exchange listings, "
        "protocol upgrades, token unlocks, regulatory news, and major partnerships. "
        "Returns the coin tickers mentioned, headline snippets, and vote counts. "
        "Use this to identify narrative-driven opportunities that precede price moves."
    )
    args_schema: Type[BaseModel] = UpcomingCatalystsInput

    # Keywords that indicate a meaningful catalyst
    _BULLISH_KEYWORDS = [
        "etf", "listing", "upgrade", "partnership", "integration", "launch",
        "mainnet", "approval", "institutional", "adoption", "grant", "audit passed",
        "staking", "airdrop", "burn", "buyback",
    ]
    _BEARISH_KEYWORDS = [
        "hack", "exploit", "breach", "sec", "lawsuit", "ban", "regulation",
        "delist", "rug", "exit scam", "fraud", "penalty", "unlock", "dump",
        "vulnerability", "attack",
    ]

    def _run(self, limit: int = 20) -> str:
        args = {"limit": limit}
        cached = _cache_get(self.name, args)
        if cached is not None:
            return cached

        # Outer try/except: any failure returns a valid empty result so the
        # agent can continue with the other three tools.
        try:
            # Endpoint accepts only the apikey (embedded in URL), no query params.
            resp = requests.get(_CRYPTOPANIC_URL, timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
            items: list[dict] = resp.json().get("results", [])
        except Exception:
            # API down, timeout, bad JSON, rate limit — return empty valid shape
            # so the scout task does not crash.
            return json.dumps(
                {
                    "source": "cryptopanic_catalyst_scan",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "articles_scanned": 0,
                    "catalyst_articles_found": 0,
                    "coins_with_catalysts": [],
                }
            )

        try:
            # Deduplicate by URL
            seen: set[str] = set()
            unique: list[dict] = []
            for item in items:
                url = item.get("url", "")
                if url not in seen:
                    seen.add(url)
                    unique.append(item)

            results = []
            for item in unique[:limit]:
                title = item.get("title", "").lower()
                votes = item.get("votes", {}) or {}
                bull = votes.get("positive", 0) or 0
                bear = votes.get("negative", 0) or 0

                # Detect catalyst type from title text
                bullish_flags = [kw for kw in self._BULLISH_KEYWORDS if kw in title]
                bearish_flags = [kw for kw in self._BEARISH_KEYWORDS if kw in title]

                if not bullish_flags and not bearish_flags:
                    continue  # Skip generic news

                # Extract mentioned currencies from the API metadata
                currencies = [
                    c.get("code", "").upper()
                    for c in (item.get("currencies") or [])
                    if c.get("code")
                ]

                results.append(
                    {
                        "title": item.get("title", ""),
                        "published_at": item.get("published_at", ""),
                        "currencies": currencies,
                        "bullish_flags": bullish_flags,
                        "bearish_flags": bearish_flags,
                        "catalyst_direction": (
                            "bearish"
                            if bearish_flags and not bullish_flags
                            else "bullish"
                            if bullish_flags and not bearish_flags
                            else "mixed"
                        ),
                        "bullish_votes": bull,
                        "bearish_votes": bear,
                        "url": item.get("url", ""),
                    }
                )

            # Group catalysts by coin
            coin_catalysts: dict[str, list] = {}
            for r in results:
                for symbol in r["currencies"]:
                    coin_catalysts.setdefault(symbol, []).append(
                        {
                            "title": r["title"],
                            "direction": r["catalyst_direction"],
                            "flags": r["bullish_flags"] + r["bearish_flags"],
                            "votes": r["bullish_votes"] - r["bearish_votes"],
                        }
                    )

            # Sort coins by net vote score
            coin_summary = [
                {
                    "symbol": sym,
                    "catalyst_count": len(cats),
                    "net_vote_score": sum(c["votes"] for c in cats),
                    "direction": (
                        "bullish"
                        if sum(1 for c in cats if c["direction"] == "bullish")
                        > sum(1 for c in cats if c["direction"] == "bearish")
                        else "bearish"
                    ),
                    "catalysts": cats[:3],  # top 3 per coin
                }
                for sym, cats in coin_catalysts.items()
            ]
            coin_summary.sort(key=lambda x: x["net_vote_score"], reverse=True)

            result = json.dumps(
                {
                    "source": "cryptopanic_catalyst_scan",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "articles_scanned": len(unique),
                    "catalyst_articles_found": len(results),
                    "coins_with_catalysts": coin_summary[:15],
                }
            )
            _cache_put(self.name, args, result)
            return result
        except Exception:
            # Processing error after successful fetch — return empty valid shape
            return json.dumps(
                {
                    "source": "cryptopanic_catalyst_scan",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "articles_scanned": 0,
                    "catalyst_articles_found": 0,
                    "coins_with_catalysts": [],
                }
            )
