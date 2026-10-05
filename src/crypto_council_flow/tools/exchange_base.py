"""
Abstract exchange interface for market data tools.

All exchange-specific tools implement these interfaces so the scout/analysis
agents can work with any exchange by swapping the implementation.
"""

from __future__ import annotations

import abc
import json
import os
import time
from typing import Any, Type, TypeVar

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

T = TypeVar("T", bound="ExchangeClient")

_DEFAULT_TIMEOUT = 10
_tool_cache: dict[tuple[str, frozenset], str] = {}


def _cache_get(tool_name: str, params: dict) -> str | None:
    return _tool_cache.get((tool_name, frozenset(params.items())))


def _cache_put(tool_name: str, params: dict, result: str) -> None:
    if not result.startswith('{"error"'):
        _tool_cache[(tool_name, frozenset(params.items()))] = result


def _error(message: str) -> str:
    return json.dumps({"error": message})


class ExchangeClient(abc.ABC):
    """Abstract base for exchange HTTP clients."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        quote_currency: str = "usdt",
        timeout: int = _DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.quote_currency = quote_currency.lower()
        self.timeout = timeout
        self._session = requests.Session()
        if api_key:
            self._session.headers.update({"Authorization": f"Token {api_key}"})
        self._session.headers.update(
            {"accept": "application/json", "user-agent": "market-scout/1.0"}
        )

    @abc.abstractmethod
    def list_markets(self) -> list[dict[str, Any]]:
        """Return normalized market list: [{"symbol", "base", "quote", "active"}, ...]"""

    @abc.abstractmethod
    def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        """Return ticker for a symbol: {"last", "volume", "high", "low", "change_pct"}"""

    @abc.abstractmethod
    def get_ohlc(
        self, symbol: str, timeframe: str, from_ts: int, to_ts: int
    ) -> list[list[float]]:
        """Return OHLC candles: [[ts, o, h, l, c, v], ...]"""

    def _get(self, endpoint: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{endpoint}"
        resp = self._session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._session.close()


class NobitexClient(ExchangeClient):
    """Nobitex (Iran) public REST API client."""

    def list_markets(self) -> list[dict[str, Any]]:
        # /market/list 404s on apiv2; /market/stats (no params) returns all
        # pairs keyed "base-quote" (e.g. "btc-usdt"). active = not isClosed.
        data = self._get("/market/stats")
        markets = []
        for key, info in data.get("stats", {}).items():
            if "-" not in key:
                continue
            src, dst = key.split("-", 1)
            markets.append(
                {
                    "symbol": f"{src.upper()}{dst.upper()}",
                    "base": src.lower(),
                    "quote": dst.lower(),
                    "active": not info.get("isClosed", False),
                    "min_order": 0,
                    "max_order": 0,
                    "price_precision": 2,
                }
            )
        return markets

    def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        # Nobitex stats endpoint: /market/stats?srcCurrency=btc&dstCurrency=rls
        parts = self._parse_symbol(symbol)
        if not parts:
            return None
        src, dst = parts
        try:
            data = self._get("/market/stats", {"srcCurrency": src, "dstCurrency": dst})
        except Exception:
            # Unknown pair -> apiv2 returns 400; treat as "symbol not found".
            return None
        # Live keys: "<src>-<dst>" ("btc-usdt") with dayLow/dayHigh/dayChange,
        # latest, volumeSrc/volumeDst (NOT "volume"/"day_high"/"change").
        stats = data.get("stats", {}).get(f"{src}-{dst}", {})
        if not stats:
            return None
        return {
            "last": float(stats.get("latest", 0)),
            "volume": float(stats.get("volumeSrc", 0)),
            "high": float(stats.get("dayHigh", 0)),
            "low": float(stats.get("dayLow", 0)),
            "change_pct": float(stats.get("dayChange", 0)),
        }

    def get_ohlc(
        self, symbol: str, timeframe: str, from_ts: int, to_ts: int
    ) -> list[list[float]]:
        parts = self._parse_symbol(symbol)
        if not parts:
            return []
        src, dst = parts
        nobitex_sym = f"{src.upper()}{dst.upper()}"
        resolution = self._tf_to_resolution(timeframe)
        data = self._get(
            "/market/udf/history",
            {
                "symbol": nobitex_sym,
                "resolution": resolution,
                "from": from_ts,
                "to": to_ts,
            },
        )
        if data.get("s") != "ok":
            return []
        candles = []
        for i in range(len(data.get("t", []))):
            candles.append(
                [
                    int(data["t"][i]) * 1000,  # ms
                    float(data["o"][i]),
                    float(data["h"][i]),
                    float(data["l"][i]),
                    float(data["c"][i]),
                    float(data["v"][i]),
                ]
            )
        return candles

    def _parse_symbol(self, symbol: str) -> tuple[str, str] | None:
        # Expect "BTCIRT" or "BTC-USDT" etc.
        sym = symbol.replace("-", "").replace("_", "").upper()
        if sym.endswith(self.quote_currency.upper()):
            base = sym[: -len(self.quote_currency)].lower()
            return base, self.quote_currency
        # Try common quotes
        for q in ("usdt", "irt", "rls", "btc", "eth"):
            if sym.endswith(q.upper()):
                return sym[: -len(q)].lower(), q
        return None

    def _tf_to_resolution(self, tf: str) -> str:
        # apiv2 supported_resolutions (from /market/udf/config): intraday
        # minutes as plain numbers, daily as 1D/2D/3D (case-sensitive —
        # lowercase "1d" and TradingView-style "D"/"W" are rejected).
        # No native weekly (has_weekly_and_monthly=false), so 1w falls
        # back to the coarsest supported resolution, 3D.
        mapping = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "1D",
            "1w": "3D",
        }
        return mapping.get(tf, "1D")


def get_exchange_client() -> ExchangeClient:
    """Factory: reads env vars and returns the configured exchange client."""
    exchange = os.getenv("EXCHANGE", "nobitex").lower()
    base_url = os.getenv("EXCHANGE_API_BASE", "https://apiv2.nobitex.ir")
    api_key = os.getenv("EXCHANGE_API_KEY")
    quote = os.getenv("EXCHANGE_QUOTE", "rls").lower()

    if exchange == "nobitex":
        return NobitexClient(base_url=base_url, api_key=api_key, quote_currency=quote)
    raise ValueError(f"Unsupported exchange: {exchange}")


# ---------------------------------------------------------------------------
# CrewAI Tool Wrappers (exchange-agnostic)
# ---------------------------------------------------------------------------


class ExchangeMarketsInput(BaseModel):
    quote_currency: str | None = Field(
        default=None,
        description="Filter markets by quote currency (e.g. 'usdt', 'rls').",
    )
    only_active: bool = Field(
        default=True, description="Only return active/enabled markets."
    )
    max_results: int = Field(
        default=50, ge=1, le=200,
        description="Max markets returned; output also includes total_count.",
    )


class ExchangeMarketsTool(BaseTool):
    """Fetch all tradeable markets from the configured exchange."""

    name: str = "exchange_markets"
    description: str = (
        "Returns a compact market summary: total_count plus a sample of up to "
        "max_results markets (symbol, base, quote). Use it ONLY to check whether "
        "a candidate coin is tradeable — do NOT treat the sample as analysis input. "
        "Defaults to quote_currency='usdt', only_active=true, max_results=50. "
        "Confirm specific coins with exchange_batch_ticker, not this list."
    )
    args_schema: Type[BaseModel] = ExchangeMarketsInput

    def _run(
        self,
        quote_currency: str | None = None,
        only_active: bool = True,
        max_results: int = 50,
    ) -> str:
        params = {"quote_currency": quote_currency, "only_active": only_active,
                  "max_results": max_results}
        cached = _cache_get(self.name, params)
        if cached:
            return cached

        try:
            client = get_exchange_client()
            markets = client.list_markets()
            if quote_currency:
                markets = [m for m in markets if m["quote"] == quote_currency.lower()]
            if only_active:
                markets = [m for m in markets if m["active"]]
            total_count = len(markets)
            sample = [
                {"symbol": m["symbol"], "base": m["base"], "quote": m["quote"]}
                for m in markets[:max_results]
            ]
            result = json.dumps(
                {
                    "source": f"{client.__class__.__name__}_markets",
                    "total_count": total_count,
                    "returned": len(sample),
                    "note": "Sample only — verify candidates with exchange_batch_ticker.",
                    "markets": sample,
                }
            )
            _cache_put(self.name, params, result)
            return result
        except Exception as exc:
            return _error(f"exchange_markets failed: {type(exc).__name__}: {exc}")


def _check_symbol(client, symbol: str) -> dict:
    """Fetch one symbol's ticker; returns ticker dict or error info."""
    try:
        ticker = client.get_ticker(symbol)
        if ticker is None:
            return {"symbol": symbol, "error": "symbol not found"}
        ticker["symbol"] = symbol
        return {"symbol": symbol, "ticker": ticker}
    except Exception as exc:
        return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"}


class ExchangeBatchTickerInput(BaseModel):
    symbols: list[str] = Field(
        ..., description="List of exchange symbols, e.g. ['BTCUSDT', 'ETHUSDT']."
    )


class ExchangeBatchTickerTool(BaseTool):
    """Fetch 24h tickers for multiple symbols concurrently. Coins not listed
    on the exchange are returned with an error field instead of a ticker."""

    name: str = "exchange_batch_ticker"
    description: str = (
        "Fetches 24h tickers for a list of symbols in one shot (concurrently). "
        "This is the ONLY ticker tool — use it for one symbol or many "
        "(pass a 1-element array for a single coin). Symbols not traded on "
        "the configured exchange are reported as errors — treat them as "
        "ineligible."
    )
    args_schema: Type[BaseModel] = ExchangeBatchTickerInput

    def _run(self, symbols: list[str]) -> str:
        if not symbols:
            return _error("symbols list is empty")
        client = get_exchange_client()
        # Fetch each symbol concurrently so N coins take ~one round-trip,
        # not N sequential ones. One bad/unknown symbol never blocks the rest.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as pool:
            results = list(pool.map(lambda s: _check_symbol(client, s), symbols))
        return json.dumps(
            {
                "source": f"{client.__class__.__name__}_batch_ticker",
                "count": len(results),
                "results": results,
            }
        )


class ExchangeOHLCInput(BaseModel):
    symbol: str = Field(..., description="Exchange symbol, e.g. 'BTCUSDT'.")
    timeframe: str = Field(default="1d", description="Timeframe: 1m,5m,15m,1h,4h,1d,1w.")
    days: int = Field(default=30, ge=1, le=365, description="Days of history to fetch.")


class ExchangeOHLCTool(BaseTool):
    """Fetch OHLC candles for a symbol (replaces CoinGecko OHLC for technical indicators)."""

    name: str = "exchange_ohlc"
    description: str = (
        "Returns OHLCV candles for a market from the configured exchange. "
        "Use this instead of CoinGecko OHLC when the exchange provides better data. "
        "Output format: [[ts_ms, open, high, low, close, volume], ...]"
    )
    args_schema: Type[BaseModel] = ExchangeOHLCInput

    def _run(
        self, symbol: str, timeframe: str = "1d", days: int = 30
    ) -> str:
        import time

        to_ts = int(time.time())
        from_ts = to_ts - days * 86400
        params = {"symbol": symbol, "timeframe": timeframe, "from": from_ts, "to": to_ts}
        cached = _cache_get(self.name, params)
        if cached:
            return cached

        try:
            client = get_exchange_client()
            candles = client.get_ohlc(symbol, timeframe, from_ts, to_ts)
            result = json.dumps(
                {
                    "source": f"{client.__class__.__name__}_ohlc",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "candles": candles,
                }
            )
            _cache_put(self.name, params, result)
            return result
        except Exception as exc:
            return _error(f"exchange_ohlc failed: {type(exc).__name__}: {exc}")


# Re-export for tool registry
__all__ = [
    "ExchangeClient",
    "NobitexClient",
    "get_exchange_client",
    "ExchangeMarketsTool",
    "ExchangeBatchTickerTool",
    "ExchangeOHLCTool",
]