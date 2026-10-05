import json
import math
import os
from datetime import datetime, timezone
from typing import Type

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from crypto_council_flow.tools.exchange_base import get_exchange_client


_DEFAULT_TIMEOUT = 10
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Put your NewsData key in an environment variable instead of the source code.
_NEWSDATA_URL = "https://newsdata.io/api/1/latest"
_NEWSDATA_API_KEY = os.getenv("NEWSDATA_API_KEY")

_tool_cache: dict[tuple[str, frozenset], str] = {}


def _cache_get(tool_name: str, params: dict) -> str | None:
    return _tool_cache.get((tool_name, frozenset(params.items())))


def _cache_put(tool_name: str, params: dict, result: str) -> None:
    if not result.startswith('{"error"'):
        _tool_cache[(tool_name, frozenset(params.items()))] = result


def _error(message: str) -> str:
    return json.dumps({
        "error": message
    })


def _get_coingecko(endpoint: str, params: dict) -> dict | list:
    response = requests.get(
        f"{_COINGECKO_BASE}{endpoint}",
        params=params,
        timeout=_DEFAULT_TIMEOUT,
        headers={
            "accept": "application/json",
            "user-agent": "market-scout/1.0",
        },
    )

    response.raise_for_status()
    return response.json()


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _log_return(previous: float, current: float) -> float | None:
    if previous <= 0 or current <= 0:
        return None

    return math.log(current / previous)


# ---------------------------------------------------------------------------
# Trending Coins
# ---------------------------------------------------------------------------

class TrendingCoinsInput(BaseModel):
    top_n: int = Field(
        default=10,
        ge=1,
        le=15,
        description="Number of trending coins to return.",
    )


class TrendingCoinsTool(BaseTool):
    name: str = "trending_coins"
    description: str = (
        "Find coins currently receiving unusually high market attention on "
        "CoinGecko. This measures attention/trending activity, not volatility."
    )
    args_schema: Type[BaseModel] = TrendingCoinsInput

    def _run(self, top_n: int = 10) -> str:
        params = {"top_n": top_n}

        cached = _cache_get(self.name, params)
        if cached:
            return cached

        try:
            data = _get_coingecko("/search/trending", {})

            coins = []

            for item in data.get("coins", [])[:top_n]:
                coin = item.get("item", {})

                # CoinGecko's score is zero-based:
                # score 0 = rank 1
                trending_rank = int(coin.get("score", 0)) + 1

                coins.append({
                    "coin_id": coin.get("id"),
                    "symbol": (coin.get("symbol") or "").upper(),
                    "name": coin.get("name"),
                    "trending_rank": trending_rank,
                    "market_cap_rank": coin.get("market_cap_rank"),
                    "price_btc": _safe_float(coin.get("price_btc")),
                })

            result = json.dumps({
                "source": "coingecko_trending",
                "coins": coins,
            })

            _cache_put(self.name, params, result)
            return result

        except Exception as exc:
            return _error(
                f"trending_coins failed: {type(exc).__name__}: {exc}"
            )


# ---------------------------------------------------------------------------
# Momentum Screener
# ---------------------------------------------------------------------------

class MomentumScreenerInput(BaseModel):
    top_n: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of momentum candidates.",
    )

    min_volume_usd: float = Field(
        default=5_000_000,
        ge=0,
        description="Minimum 24h trading volume in USD.",
    )

    max_market_cap_rank: int = Field(
        default=500,
        ge=1,
        le=1000,
        description="Maximum CoinGecko market-cap rank.",
    )


class MomentumScreenerTool(BaseTool):
    name: str = "momentum_screener"
    description: str = (
        "Find liquid coins with significant recent price momentum and trading "
        "activity. This is a momentum/volume signal, NOT a volatility measure."
    )
    args_schema: Type[BaseModel] = MomentumScreenerInput

    def _run(
        self,
        top_n: int = 20,
        min_volume_usd: float = 5_000_000,
        max_market_cap_rank: int = 500,
    ) -> str:

        params = {
            "top_n": top_n,
            "min_volume_usd": min_volume_usd,
            "max_market_cap_rank": max_market_cap_rank,
        }

        cached = _cache_get(self.name, params)
        if cached:
            return cached

        try:
            # Fetch enough coins to cover the requested market-cap range.
            per_page = min(max_market_cap_rank, 250)

            data = _get_coingecko(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": per_page,
                    "page": 1,
                    "price_change_percentage": "1h,24h,7d",
                    "sparkline": "false",
                },
            )

            stablecoins = {
                "usdt",
                "usdc",
                "dai",
                "busd",
                "tusd",
            }

            wrapped_assets = {
                "wbtc",
                "weth",
            }

            candidates = []

            for coin in data:
                coin_id = (coin.get("id") or "").lower()
                symbol = (coin.get("symbol") or "").lower()

                market_cap_rank = coin.get("market_cap_rank")
                volume = _safe_float(coin.get("total_volume"))

                if symbol in stablecoins or symbol in wrapped_assets:
                    continue

                if market_cap_rank is None:
                    continue

                if market_cap_rank > max_market_cap_rank:
                    continue

                if volume < min_volume_usd:
                    continue

                candidates.append({
                    "coin_id": coin_id,
                    "symbol": symbol.upper(),
                    "name": coin.get("name"),
                    "current_price": _safe_float(
                        coin.get("current_price")
                    ),
                    "market_cap_rank": market_cap_rank,
                    "price_change_1h_pct": _safe_float(
                        coin.get("price_change_percentage_1h_in_currency")
                    ),
                    "price_change_24h_pct": _safe_float(
                        coin.get("price_change_percentage_24h_in_currency")
                    ),
                    "price_change_7d_pct": _safe_float(
                        coin.get("price_change_percentage_7d_in_currency")
                    ),
                    "volume_24h_usd": volume,
                    "market_cap_usd": _safe_float(
                        coin.get("market_cap")
                    ),
                })

            # Momentum score deliberately combines multiple timeframes.
            # It is NOT simply sorted by 24h gain anymore.
            def momentum_score(coin: dict) -> float:
                one_h = coin["price_change_1h_pct"]
                one_d = coin["price_change_24h_pct"]
                seven_d = coin["price_change_7d_pct"]

                return (
                    abs(one_h) * 0.25
                    + abs(one_d) * 0.50
                    + abs(seven_d) * 0.25
                )

            candidates.sort(
                key=momentum_score,
                reverse=True,
            )

            result = json.dumps({
                "source": "coingecko_momentum_screener",
                "criteria": {
                    "min_volume_usd": min_volume_usd,
                    "max_market_cap_rank": max_market_cap_rank,
                },
                "coins": candidates[:top_n],
            })

            _cache_put(self.name, params, result)
            return result

        except Exception as exc:
            return _error(
                f"momentum_screener failed: {type(exc).__name__}: {exc}"
            )


# ---------------------------------------------------------------------------
# Volatility Screener
# ---------------------------------------------------------------------------

class VolatilityScreenerInput(BaseModel):
    top_n: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of volatility candidates.",
    )

    min_volume_usd: float = Field(
        default=1_000_000,
        ge=0,
        description="Minimum 24h trading volume in USD.",
    )

    max_market_cap_rank: int = Field(
        default=500,
        ge=1,
        le=1000,
        description="Maximum CoinGecko market-cap rank.",
    )

    require_exchange_listing: bool = Field(
        default=True,
        description=(
            "Whether to keep only coins that are listed/active on the configured "
            "exchange (EXCHANGE_QUOTE pair). If the exchange list cannot be "
            "fetched, returns the unfiltered ranking rather than nothing."
        ),
    )


class VolatilityScreenerTool(BaseTool):
    name: str = "volatility_screener"
    description: str = (
        "Find liquid cryptocurrencies with high short-term tradable "
        "volatility. Uses CoinGecko's 7-day hourly price series to calculate "
        "realized volatility, maximum rolling 24h range, movement frequency, "
        "and recent price movement. This is the primary volatility signal."
    )
    args_schema: Type[BaseModel] = VolatilityScreenerInput

    def _run(
        self,
        top_n: int = 20,
        min_volume_usd: float = 1_000_000,
        max_market_cap_rank: int = 500,
        require_exchange_listing: bool = True,
    ) -> str:

        params = {
            "top_n": top_n,
            "min_volume_usd": min_volume_usd,
            "max_market_cap_rank": max_market_cap_rank,
            "require_exchange_listing": require_exchange_listing,
        }

        cached = _cache_get(self.name, params)
        if cached:
            return cached

        try:
            # Keep this bounded because sparkline data is substantially larger
            # than ordinary /coins/markets responses.
            per_page = min(max_market_cap_rank, 100)

            data = _get_coingecko(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": per_page,
                    "page": 1,
                    "price_change_percentage": "1h,24h,7d",
                    "sparkline": "true",
                },
            )

            stablecoins = {
                "usdt",
                "usdc",
                "dai",
                "busd",
                "tusd",
            }

            wrapped_assets = {
                "wbtc",
                "weth",
            }

            candidates = []

            for coin in data:
                coin_id = (coin.get("id") or "").lower()
                symbol = (coin.get("symbol") or "").lower()

                if symbol in stablecoins or symbol in wrapped_assets:
                    continue

                market_cap_rank = coin.get("market_cap_rank")
                volume = _safe_float(coin.get("total_volume"))

                if market_cap_rank is None:
                    continue

                if market_cap_rank > max_market_cap_rank:
                    continue

                if volume < min_volume_usd:
                    continue

                prices = (
                    coin.get("sparkline_in_7d", {})
                    .get("price", [])
                )

                if len(prices) < 24:
                    continue

                prices = [
                    _safe_float(price)
                    for price in prices
                    if _safe_float(price) > 0
                ]

                if len(prices) < 24:
                    continue

                # -------------------------------------------------------
                # Hourly log returns
                # -------------------------------------------------------

                returns = []

                for previous, current in zip(
                    prices[:-1],
                    prices[1:],
                ):
                    value = _log_return(previous, current)

                    if value is not None and math.isfinite(value):
                        returns.append(value)

                if len(returns) < 12:
                    continue

                # Realized hourly volatility.
                mean_return = sum(returns) / len(returns)

                variance = sum(
                    (r - mean_return) ** 2
                    for r in returns
                ) / len(returns)

                hourly_volatility = math.sqrt(variance)

                # Scale hourly volatility to a 24h horizon.
                realized_volatility_24h_pct = (
                    hourly_volatility
                    * math.sqrt(24)
                    * 100
                )

                # -------------------------------------------------------
                # Rolling 24h price range
                # -------------------------------------------------------

                rolling_ranges = []

                for i in range(23, len(prices)):
                    window = prices[i - 23:i + 1]

                    low = min(window)
                    high = max(window)

                    if low > 0:
                        rolling_range = (
                            (high - low) / low
                        ) * 100

                        rolling_ranges.append(rolling_range)

                max_rolling_24h_range_pct = (
                    max(rolling_ranges)
                    if rolling_ranges
                    else 0.0
                )

                # -------------------------------------------------------
                # Movement frequency
                # -------------------------------------------------------
                #
                # Count hourly moves >= 1%.
                # This is intentionally separate from net 24h change.
                # A coin that oscillates +4%, -3%, +5%, -4% can have
                # enormous trading volatility despite finishing near 0%.
                # -------------------------------------------------------

                significant_move_count = sum(
                    1
                    for r in returns
                    if abs(r) >= math.log(1.01)
                )

                movement_frequency_pct = (
                    significant_move_count / len(returns)
                ) * 100

                # -------------------------------------------------------
                # Recent 24h realized volatility
                # -------------------------------------------------------

                recent_returns = returns[-24:]

                if len(recent_returns) >= 6:
                    recent_mean = (
                        sum(recent_returns)
                        / len(recent_returns)
                    )

                    recent_variance = sum(
                        (r - recent_mean) ** 2
                        for r in recent_returns
                    ) / len(recent_returns)

                    recent_realized_volatility_24h_pct = (
                        math.sqrt(recent_variance)
                        * math.sqrt(24)
                        * 100
                    )
                else:
                    recent_realized_volatility_24h_pct = (
                        realized_volatility_24h_pct
                    )

                current_price = _safe_float(
                    coin.get("current_price")
                )

                price_change_24h = _safe_float(
                    coin.get(
                        "price_change_percentage_24h_in_currency"
                    )
                )

                price_change_7d = _safe_float(
                    coin.get(
                        "price_change_percentage_7d_in_currency"
                    )
                )

                candidates.append({
                    "coin_id": coin_id,
                    "symbol": symbol.upper(),
                    "name": coin.get("name"),
                    "current_price": current_price,
                    "market_cap_rank": market_cap_rank,
                    "volume_24h_usd": volume,
                    "market_cap_usd": _safe_float(
                        coin.get("market_cap")
                    ),

                    # Primary volatility signals
                    "realized_volatility_7d_pct": round(
                        realized_volatility_24h_pct,
                        4,
                    ),
                    "recent_realized_volatility_24h_pct": round(
                        recent_realized_volatility_24h_pct,
                        4,
                    ),
                    "max_rolling_24h_range_pct": round(
                        max_rolling_24h_range_pct,
                        4,
                    ),
                    "movement_frequency_pct": round(
                        movement_frequency_pct,
                        2,
                    ),

                    # Context, NOT volatility itself
                    "price_change_24h_pct": price_change_24h,
                    "price_change_7d_pct": price_change_7d,
                })

            # -----------------------------------------------------------
            # Exchange-listing filter
            # -----------------------------------------------------------
            # Keep only coins that are actually listed/active on the
            # configured exchange (EXCHANGE_QUOTE pair). Otherwise the
            # volatility ranking can surface coins we cannot trade, and
            # the scout's shortlist would silently mix tradable and
            # non-tradable names. If the exchange list is unavailable,
            # fall back to the unfiltered ranking.
            # -----------------------------------------------------------

            exchange_filter_applied = False
            exchange_filter_note = "disabled"

            if require_exchange_listing:
                try:
                    client = get_exchange_client()
                    markets = client.list_markets()
                    quote = (os.getenv("EXCHANGE_QUOTE", "rls") or "rls").lower()
                    listed_bases = {
                        (m.get("base") or "").lower()
                        for m in markets
                        if m.get("active") and (m.get("quote") or "").lower() == quote
                    }
                    if listed_bases:
                        before = len(candidates)
                        candidates = [
                            c for c in candidates
                            if c["symbol"].lower() in listed_bases
                        ]
                        exchange_filter_applied = True
                        exchange_filter_note = (
                            f"kept {len(candidates)}/{before} coins listed on "
                            f"{client.__class__.__name__} ({quote} pairs)"
                        )
                    else:
                        exchange_filter_note = (
                            f"exchange returned no active {quote} markets; "
                            "ranking left unfiltered"
                        )
                except Exception as exc:
                    exchange_filter_note = (
                        f"exchange list unavailable ({type(exc).__name__}: {exc}); "
                        "ranking left unfiltered"
                    )

            # -----------------------------------------------------------
            # Volatility score
            # -----------------------------------------------------------
            #
            # Normalize each metric relative to the candidates in this
            # scan. This prevents raw values such as 0.04 vs 12.0 from
            # being combined directly.
            # -----------------------------------------------------------

            def percentile_rank(values: list[float], value: float) -> float:
                if not values:
                    return 0.0

                below_or_equal = sum(
                    1 for item in values
                    if item <= value
                )

                return below_or_equal / len(values)

            volatility_values = [
                c["recent_realized_volatility_24h_pct"]
                for c in candidates
            ]

            range_values = [
                c["max_rolling_24h_range_pct"]
                for c in candidates
            ]

            movement_values = [
                c["movement_frequency_pct"]
                for c in candidates
            ]

            volume_values = [
                math.log10(max(c["volume_24h_usd"], 1))
                for c in candidates
            ]

            for coin in candidates:
                volatility_rank = percentile_rank(
                    volatility_values,
                    coin["recent_realized_volatility_24h_pct"],
                )

                range_rank = percentile_rank(
                    range_values,
                    coin["max_rolling_24h_range_pct"],
                )

                movement_rank = percentile_rank(
                    movement_values,
                    coin["movement_frequency_pct"],
                )

                liquidity_rank = percentile_rank(
                    volume_values,
                    math.log10(
                        max(coin["volume_24h_usd"], 1)
                    ),
                )

                # Volatility is deliberately dominant.
                coin["volatility_score"] = round(
                    (
                        volatility_rank * 0.40
                        + range_rank * 0.30
                        + movement_rank * 0.20
                        + liquidity_rank * 0.10
                    ) * 100,
                    2,
                )

            candidates.sort(
                key=lambda c: (
                    c["volatility_score"],
                    c["volume_24h_usd"],
                ),
                reverse=True,
            )

            result = json.dumps({
                "source": "coingecko_volatility_screener",
                "methodology": {
                    "price_series": "7d hourly sparkline",
                    "realized_volatility": (
                        "hourly log-return standard deviation "
                        "scaled to 24h"
                    ),
                    "range": "maximum rolling 24h high-low range",
                    "movement_frequency": (
                        "percentage of hourly moves >= 1%"
                    ),
                    "liquidity": "24h USD volume",
                },
                "criteria": {
                    "min_volume_usd": min_volume_usd,
                    "max_market_cap_rank": max_market_cap_rank,
                },
                "exchange_filter": {
                    "applied": exchange_filter_applied,
                    "note": exchange_filter_note,
                },
                "coins": candidates[:top_n],
            })

            _cache_put(self.name, params, result)
            return result

        except Exception as exc:
            return _error(
                f"volatility_screener failed: {type(exc).__name__}: {exc}"
            )


# ---------------------------------------------------------------------------
# New Listings
# ---------------------------------------------------------------------------

class NewListingsInput(BaseModel):
    top_n: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Number of recently added market entries to inspect.",
    )


class NewListingsTool(BaseTool):
    name: str = "new_listings"
    description: str = (
        "Identify newer CoinGecko market entries that may be undergoing "
        "active price discovery. Treat these as potential volatility sources, "
        "but verify volume and liquidity before selecting them."
    )
    args_schema: Type[BaseModel] = NewListingsInput

    def _run(self, top_n: int = 20) -> str:
        params = {"top_n": top_n}

        cached = _cache_get(self.name, params)
        if cached:
            return cached

        try:
            data = _get_coingecko(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "id_desc",
                    "per_page": top_n,
                    "page": 1,
                    "price_change_percentage": "24h",
                    "sparkline": "false",
                },
            )

            coins = []

            for coin in data:
                current_price = _safe_float(
                    coin.get("current_price")
                )

                if current_price <= 0:
                    continue

                coins.append({
                    "coin_id": coin.get("id"),
                    "symbol": (
                        coin.get("symbol") or ""
                    ).upper(),
                    "name": coin.get("name"),
                    "current_price": current_price,
                    "market_cap_usd": _safe_float(
                        coin.get("market_cap")
                    ),
                    "market_cap_rank": coin.get(
                        "market_cap_rank"
                    ),
                    "price_change_24h_pct": _safe_float(
                        coin.get(
                            "price_change_percentage_24h_in_currency"
                        )
                    ),
                    "volume_24h_usd": _safe_float(
                        coin.get("total_volume")
                    ),
                })

            result = json.dumps({
                "source": "coingecko_new_listing_candidates",
                "note": (
                    "CoinGecko's id_desc ordering is used as a candidate "
                    "source; it should not be interpreted as an exact "
                    "listing-date ranking."
                ),
                "coins": coins,
            })

            _cache_put(self.name, params, result)
            return result

        except Exception as exc:
            return _error(
                f"new_listings failed: {type(exc).__name__}: {exc}"
            )


# ---------------------------------------------------------------------------
# Upcoming Catalysts
# ---------------------------------------------------------------------------

class UpcomingCatalystsInput(BaseModel):
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of catalyst entries.",
    )


class UpcomingCatalystsTool(BaseTool):
    name: str = "upcoming_catalysts"
    description: str = (
        "Scan NewsData.io cryptocurrency news for recent or imminent "
        "events that may cause short-term price movement. Positive and "
        "negative catalysts are reported as signals. Negative news is "
        "not automatically an exclusion unless it indicates the asset "
        "is unsafe or effectively untradeable."
    )
    args_schema: Type[BaseModel] = UpcomingCatalystsInput

    _BULLISH_KEYWORDS = {
        "etf",
        "listing",
        "upgrade",
        "partnership",
        "integration",
        "launch",
        "mainnet",
        "approval",
        "institutional",
        "adoption",
        "grant",
        "audit passed",
        "staking",
        "airdrop",
        "burn",
        "buyback",
    }

    _BEARISH_KEYWORDS = {
        "hack",
        "exploit",
        "breach",
        "sec",
        "lawsuit",
        "ban",
        "regulation",
        "delist",
        "rug",
        "exit scam",
        "fraud",
        "penalty",
        "unlock",
        "dump",
        "vulnerability",
        "attack",
    }

    def _run(self, limit: int = 20) -> str:
        params = {"limit": limit}

        cached = _cache_get(self.name, params)
        if cached:
            return cached

        if not _NEWSDATA_API_KEY:
            return _error(
                "upcoming_catalysts failed: NEWSDATA_API_KEY "
                "environment variable is not set"
            )

        try:
            response = requests.get(
                _NEWSDATA_URL,
                params={
                    "apikey": _NEWSDATA_API_KEY,
                    "q": "crypto OR cryptocurrency OR bitcoin OR ethereum",
                    "language": "en",
                },
                timeout=_DEFAULT_TIMEOUT,
                headers={
                    "accept": "application/json",
                    "user-agent": "market-scout/1.0",
                },
            )

            response.raise_for_status()
            data = response.json()

            articles = data.get("results", [])

            catalyst_articles = []

            for article in articles:
                title = (
                    article.get("title")
                    or ""
                ).strip()

                description = (
                    article.get("description")
                    or ""
                ).strip()

                text_blob = (
                    f"{title} {description}"
                ).lower()

                bullish_matches = [
                    keyword
                    for keyword in self._BULLISH_KEYWORDS
                    if keyword in text_blob
                ]

                bearish_matches = [
                    keyword
                    for keyword in self._BEARISH_KEYWORDS
                    if keyword in text_blob
                ]

                if not bullish_matches and not bearish_matches:
                    continue

                # NewsData uses different fields depending on the feed.
                # Keep extraction defensive.
                symbols = []

                for symbol in (
                    article.get("symbols")
                    or article.get("crypto")
                    or []
                ):
                    if isinstance(symbol, str):
                        symbols.append(symbol.upper())

                catalyst_articles.append({
                    "title": title,
                    "url": article.get("link"),
                    "published_at": article.get("pubDate"),
                    "source": article.get("source_name"),
                    "bullish_keywords": bullish_matches,
                    "bearish_keywords": bearish_matches,
                    "symbols": symbols,
                })

            # Deduplicate URLs.
            unique_articles = []
            seen_urls = set()

            for article in catalyst_articles:
                url = article.get("url")

                if url and url in seen_urls:
                    continue

                if url:
                    seen_urls.add(url)

                unique_articles.append(article)

            result = json.dumps({
                "source": "newsdata_catalyst_scan",
                "articles_scanned": len(articles),
                "catalyst_articles_found": len(unique_articles),
                "articles": unique_articles[:limit],
            })

            _cache_put(self.name, params, result)
            return result

        except Exception as exc:
            # IMPORTANT:
            # Do not silently turn an API failure into "no catalysts".
            return _error(
                f"upcoming_catalysts failed: {type(exc).__name__}: {exc}"
            )


# ---------------------------------------------------------------------------
# Tool exports
# ---------------------------------------------------------------------------

trending_coins = TrendingCoinsTool()
momentum_screener = MomentumScreenerTool()
volatility_screener = VolatilityScreenerTool()
new_listings = NewListingsTool()
upcoming_catalysts = UpcomingCatalystsTool()