"""
Sentiment analysis tools for the sentiment_analyst agent.

Data sources:
- CoinGecko public API (coin metadata, community data)
- Alternative.me Fear & Greed Index API (free, no key)
- Crypto news via RSS feeds (free, no key required)
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Type

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


_DEFAULT_TIMEOUT = 10  # seconds
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_FEAR_GREED_URL = "https://api.alternative.me/fng/"

# News endpoint(s).  Override with the CRYPTO_NEWS_SENTIMENT env var to swap
# providers (e.g. a CryptoPanic/NewsData URL with your own key) without a code
# change.  Defaults to free, key-less RSS feeds from major crypto outlets.
_DEFAULT_NEWS_FEEDS = (
    "https://cointelegraph.com/rss"
    ",https://www.coindesk.com/arc/outboundfeeds/rss/"
    ",https://decrypt.co/feed"
)
CRYPTO_NEWS_SENTIMENT = os.getenv("CRYPTO_NEWS_SENTIMENT", _DEFAULT_NEWS_FEEDS)


# ---------------------------------------------------------------------------
# Coin ID resolution — the agent often passes a symbol or short name that
# doesn't match CoinGecko's slug.  This helper resolves it.
# ---------------------------------------------------------------------------
_COIN_LIST_CACHE: list[dict] | None = None


def _resolve_coin_id(raw_id: str) -> str:
    """Try ``raw_id`` as a CoinGecko slug first; if 404, resolve via /coins/list.

    Returns the best-matching CoinGecko ``id`` slug, or the original ``raw_id``
    if resolution fails (so the caller still gets a meaningful error).
    """
    global _COIN_LIST_CACHE

    # 1. Quick check — if the raw_id already works, just return it.
    try:
        resp = requests.get(
            f"{_COINGECKO_BASE}/coins/{raw_id}",
            params={"localization": "false", "tickers": "false",
                    "market_data": "false", "community_data": "false",
                    "developer_data": "false", "sparkline": "false"},
            timeout=_DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            return raw_id
    except requests.RequestException:
        pass

    # 2. Fetch the full coin list (cached in module global).
    if _COIN_LIST_CACHE is None:
        try:
            resp = requests.get(f"{_COINGECKO_BASE}/coins/list", timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
            _COIN_LIST_CACHE = resp.json()
        except Exception:
            return raw_id  # give up — let caller handle the error

    lower = raw_id.lower()

    # 3. Exact id match (case-insensitive).
    for coin in _COIN_LIST_CACHE:
        if coin.get("id", "").lower() == lower:
            return coin["id"]

    # 4. Exact symbol match — take the one with the shortest id (usually canonical).
    symbol_matches = [c for c in _COIN_LIST_CACHE if c.get("symbol", "").lower() == lower]
    if symbol_matches:
        symbol_matches.sort(key=lambda c: len(c.get("id", "")))
        return symbol_matches[0]["id"]

    # 5. Name contains the query.
    for coin in _COIN_LIST_CACHE:
        if lower in (coin.get("name", "").lower()):
            return coin["id"]

    return raw_id  # fallback


# ---------------------------------------------------------------------------
# Fear & Greed Index Tool
# ---------------------------------------------------------------------------

class FearGreedInput(BaseModel):
    limit: int = Field(default=7, ge=1, le=30, description="Number of days of history to return.")


class FearGreedIndexTool(BaseTool):
    name: str = "fear_greed_index"
    description: str = (
        "Fetches the Crypto Fear & Greed Index from alternative.me. "
        "The index ranges from 0 (extreme fear) to 100 (extreme greed). "
        "Extreme fear can signal buying opportunities; extreme greed may signal "
        "the market is due for a correction. Returns the current value plus recent history."
    )
    args_schema: Type[BaseModel] = FearGreedInput

    _CLASSIFICATIONS = {
        (0, 25): "extreme_fear",
        (25, 47): "fear",
        (47, 54): "neutral",
        (54, 75): "greed",
        (75, 101): "extreme_greed",
    }

    def _classify(self, value: int) -> str:
        for (lo, hi), label in self._CLASSIFICATIONS.items():
            if lo <= value < hi:
                return label
        return "unknown"

    def _run(self, limit: int = 7) -> str:
        try:
            params = {"limit": limit, "format": "json"}
            resp = requests.get(_FEAR_GREED_URL, params=params, timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            entries = data.get("data", [])
            if not entries:
                return json.dumps({"error": "No data returned from Fear & Greed API."})

            current = entries[0]
            current_value = int(current.get("value", 0))
            history = [
                {
                    "date": e.get("timestamp", ""),
                    "value": int(e.get("value", 0)),
                    "classification": e.get("value_classification", self._classify(int(e.get("value", 0)))),
                }
                for e in entries
            ]

            # Compute 7-day trend
            if len(history) >= 2:
                first_val = history[-1]["value"]
                last_val = history[0]["value"]
                trend = "improving" if last_val > first_val else "deteriorating" if last_val < first_val else "stable"
            else:
                trend = "insufficient_data"

            return json.dumps({
                "current_value": current_value,
                "current_classification": self._classify(current_value),
                "trend": trend,
                "history": history,
                "interpretation": (
                    "Market is in extreme fear — contrarian buy signal possible."
                    if current_value < 25
                    else "Market is in extreme greed — consider profit-taking."
                    if current_value > 75
                    else "Market sentiment is moderate."
                ),
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Crypto News Sentiment Tool
# ---------------------------------------------------------------------------

_COIN_ALIASES = {
    "BTC": ["BTC", "BITCOIN"],
    "ETH": ["ETH", "ETHEREUM"],
    "SOL": ["SOL", "SOLANA"],
    "BNB": ["BNB", "BINANCE"],
    "XRP": ["XRP", "RIPPLE"],
    "ADA": ["ADA", "CARDANO"],
    "DOGE": ["DOGE", "DOGECOIN"],
    "DOT": ["DOT", "POLKADOT"],
    "AVAX": ["AVAX", "AVALANCHE"],
    "MATIC": ["MATIC", "POLYGON"],
    "LINK": ["LINK", "CHAINLINK"],
    "LTC": ["LTC", "LITECOIN"],
    "TRX": ["TRX", "TRON"],
    "ATOM": ["ATOM", "COSMOS"],
}

_BULLISH_WORDS = re.compile(
    r"\b(surge|soar|rally|gain|bull|bullish|record high|all-time high|adoption|"
    r"approval|breakthrough|partnership|upgrade|launch|raise|pump)\b", re.I)
_BEARISH_WORDS = re.compile(
    r"\b(crash|plunge|drop|fall|bear|bearish|hack|exploit|ban|lawsuit|fraud|"
    r"scam|investigation|bankrupt|liquidation|selloff|dump|decline|drop)\b", re.I)


def _fetch_rss_items(feed_url: str) -> list[dict]:
    """Fetch and parse a single RSS feed; returns a list of article dicts."""
    resp = requests.get(
        feed_url,
        headers={"User-Agent": "crypto-council-flow/1.0"},
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    out = []
    for item in root.findall(".//item"):
        try:
            published = item.findtext("pubDate", "")
            try:
                published_iso = parsedate_to_datetime(published).isoformat()
            except Exception:
                published_iso = published
            link = item.findtext("link", "") or ""
            domain = re.sub(r"^https?://(www\.)?", "", link).split("/")[0] if link else ""
            out.append({
                "title": (item.findtext("title") or "").strip(),
                "description": re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip(),
                "published_at": published_iso,
                "domain": domain,
                "url": link,
            })
        except Exception:
            continue
    return out


def _currency_matches(symbol: str, article: dict) -> bool:
    """True if the article mentions the symbol or one of its known aliases."""
    haystack = f"{article['title']} {article['description']}".upper()
    aliases = _COIN_ALIASES.get(symbol.upper(), [symbol.upper()])
    return any(re.search(rf"\b{re.escape(a)}\b", haystack) for a in aliases)


def _headline_sentiment(text: str) -> str:
    """Very lightweight keyword sentiment for a headline."""
    bull = len(_BULLISH_WORDS.findall(text))
    bear = len(_BEARISH_WORDS.findall(text))
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


class CryptoNewsInput(BaseModel):
    currencies: str = Field(
        default="BTC",
        description=(
            "Comma-separated list of currency symbols to filter news (e.g. 'BTC', 'ETH,SOL'). "
            "Use standard uppercase ticker symbols."
        ),
    )
    filter_type: str = Field(
        default="hot",
        description="Filter type: 'hot' (trending), 'rising', 'bullish', 'bearish', 'important', or 'saved'.",
    )
    limit: int = Field(default=10, ge=1, le=50, description="Number of news items to return.")


class CryptoNewsTool(BaseTool):
    name: str = "crypto_news_sentiment"
    description: str = (
        "Fetches recent crypto news headlines from RSS feeds (CoinTelegraph, "
        "CoinDesk, Decrypt — configurable via CRYPTO_NEWS_SENTIMENT env var). "
        "Returns news titles, publication times, source domains, and a "
        "headline-keyword sentiment score. Useful for understanding recent "
        "narrative shifts and market-moving events."
    )
    args_schema: Type[BaseModel] = CryptoNewsInput

    def _run(self, currencies: str = "BTC", filter_type: str = "hot", limit: int = 10) -> str:
        try:
            feeds = [u.strip() for u in CRYPTO_NEWS_SENTIMENT.split(",") if u.strip()]
            articles: list[dict] = []
            for feed_url in feeds:
                articles.extend(_fetch_rss_items(feed_url))

            # Filter by requested currencies (symbol or known name alias).
            if currencies:
                wanted = {c.strip().upper() for c in currencies.split(",") if c.strip()}
                matched = [
                    a for a in articles
                    if any(_currency_matches(w, a) for w in wanted)
                ]
                # Fall back to the full headline set so the tool still returns
                # context when a ticker isn't mentioned in recent headlines.
                results = matched[:limit] if matched else articles[:limit]
                matched_any = bool(matched)
            else:
                results = articles[:limit]
                matched_any = True

            if not results:
                return json.dumps({
                    "currencies": currencies,
                    "message": "No news results found. Try different currencies or filter.",
                    "items": [],
                })

            items = []
            bullish_count = 0
            bearish_count = 0

            for item in results:
                polarity = _headline_sentiment(item["title"] + " " + item["description"])
                bull = 1 if polarity == "bullish" else 0
                bear = 1 if polarity == "bearish" else 0
                bullish_count += bull
                bearish_count += bear
                items.append({
                    "title": item["title"],
                    "published_at": item["published_at"],
                    "domain": item["domain"],
                    "url": item["url"],
                    "bullish_votes": bull,
                    "bearish_votes": bear,
                    "kind": "news",
                })

            total_votes = bullish_count + bearish_count
            sentiment_ratio = round(bullish_count / total_votes, 3) if total_votes > 0 else 0.5
            aggregate_sentiment = (
                "bullish" if sentiment_ratio > 0.6
                else "bearish" if sentiment_ratio < 0.4
                else "mixed"
            )

            return json.dumps({
                "currencies": currencies,
                "filter": filter_type,
                "total_articles": len(items),
                "aggregate_sentiment": aggregate_sentiment,
                "bullish_votes": bullish_count,
                "bearish_votes": bearish_count,
                "sentiment_ratio": sentiment_ratio,
                "matched_currency_filter": matched_any,
                "items": items,
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# CoinGecko Community & Social Data Tool
# ---------------------------------------------------------------------------

class CommunitySentimentInput(BaseModel):
    coin_id: str = Field(
        ...,
        description=(
            "CoinGecko coin ID (e.g. 'bitcoin', 'ethereum', 'solana'). "
            "Use the exact slug from https://api.coingecko.com/api/v3/coins/list."
        ),
    )


class CommunitySentimentTool(BaseTool):
    name: str = "community_sentiment"
    description: str = (
        "Fetches social and community metrics for a cryptocurrency from CoinGecko. "
        "Includes Reddit subscribers, Twitter/X followers, GitHub activity, "
        "developer commits, and the sentiment_votes_up/down ratio from the CoinGecko community. "
        "Useful for gauging community strength and developer activity."
    )
    args_schema: Type[BaseModel] = CommunitySentimentInput

    def _run(self, coin_id: str) -> str:
        try:
            coin_id = _resolve_coin_id(coin_id)
            url = f"{_COINGECKO_BASE}/coins/{coin_id}"
            params = {
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "true",
                "developer_data": "true",
                "sparkline": "false",
            }
            resp = requests.get(url, params=params, timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            community = data.get("community_data", {}) or {}
            developer = data.get("developer_data", {}) or {}

            # Sentiment votes
            votes_up = data.get("sentiment_votes_up_percentage", 0) or 0
            votes_down = data.get("sentiment_votes_down_percentage", 0) or 0
            if votes_up + votes_down > 0:
                community_sentiment = (
                    "bullish" if votes_up > 60
                    else "bearish" if votes_down > 60
                    else "mixed"
                )
            else:
                community_sentiment = "no_data"

            # GitHub activity score (rough)
            commits_4w = developer.get("commit_count_4_weeks", 0) or 0
            stars = developer.get("stars", 0) or 0
            dev_activity = (
                "high" if commits_4w > 100
                else "moderate" if commits_4w > 20
                else "low" if commits_4w > 0
                else "none"
            )

            return json.dumps({
                "coin": coin_id,
                "name": data.get("name", coin_id),
                "community_sentiment": community_sentiment,
                "sentiment_votes_up_pct": votes_up,
                "sentiment_votes_down_pct": votes_down,
                "twitter_followers": community.get("twitter_followers", 0),
                "reddit_subscribers": community.get("reddit_subscribers", 0),
                "reddit_average_posts_48h": community.get("reddit_average_posts_48h", 0),
                "reddit_average_comments_48h": community.get("reddit_average_comments_48h", 0),
                "github_stars": stars,
                "github_commits_4w": commits_4w,
                "developer_activity": dev_activity,
                "watchlist_portfolio_users": data.get("watchlist_portfolio_users", 0),
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Market Dominance & Trend Tool
# ---------------------------------------------------------------------------

class MarketDominanceInput(BaseModel):
    pass  # No inputs — returns global market data


class MarketDominanceTool(BaseTool):
    name: str = "market_dominance"
    description: str = (
        "Fetches global crypto market data including BTC dominance, total market cap, "
        "24h volume, and market cap change. High BTC dominance (>60%) usually signals "
        "risk-off / altcoin bear. Low dominance with rising total market cap signals "
        "altcoin season. Returns macro market context."
    )
    args_schema: Type[BaseModel] = MarketDominanceInput

    def _run(self) -> str:
        try:
            resp = requests.get(f"{_COINGECKO_BASE}/global", timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json().get("data", {})

            btc_dom = data.get("market_cap_percentage", {}).get("btc", 0)
            eth_dom = data.get("market_cap_percentage", {}).get("eth", 0)
            total_mcap = data.get("total_market_cap", {}).get("usd", 0)
            total_volume = data.get("total_volume", {}).get("usd", 0)
            mcap_change_pct = data.get("market_cap_change_percentage_24h_usd", 0)
            active_coins = data.get("active_cryptocurrencies", 0)

            # Volume/mcap ratio (>5% can signal speculative activity)
            vol_mcap_ratio = round(total_volume / total_mcap * 100, 2) if total_mcap > 0 else 0

            # Market regime
            if btc_dom > 60:
                regime = "btc_dominance_high_risk_off"
            elif btc_dom < 40:
                regime = "altcoin_season"
            else:
                regime = "balanced"

            return json.dumps({
                "btc_dominance_pct": round(btc_dom, 2),
                "eth_dominance_pct": round(eth_dom, 2),
                "total_market_cap_usd": total_mcap,
                "total_volume_24h_usd": total_volume,
                "volume_to_mcap_ratio_pct": vol_mcap_ratio,
                "market_cap_change_24h_pct": round(mcap_change_pct, 3),
                "active_cryptocurrencies": active_coins,
                "market_regime": regime,
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})
