"""
Technical analysis indicator tools for the technical_analyst agent.

All indicators are computed from raw OHLCV data fetched from the
configured source (CoinGecko by default, or the configured exchange via
USE_EXCHANGE_OHLC=true).
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Type

import numpy as np
import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from crypto_council_flow.tools.exchange_base import get_exchange_client


_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_DEFAULT_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MIN_CANDLES = 50  # minimum candles we want for indicators to work reliably
_USE_EXCHANGE = os.getenv("USE_EXCHANGE_OHLC", "false").lower() == "true"

# In-memory OHLC cache: one fetch per (coin, vs, days) shared by all
# indicator tools. Without this, a single coin analysis fires 5-7 identical
# CoinGecko requests back-to-back and trips the free-tier rate limit (429).
_OHLC_TTL_S = float(os.getenv("COUNCIL_OHLC_TTL_S", "300"))  # 5 min; hourly candles update within a cycle
_ohlc_cache: dict[tuple[str, str, int, bool], tuple[float, list[list[float]]]] = {}
_ohlc_lock = threading.Lock()
_last_coingecko_call: float = 0.0
_COINGECKO_MIN_GAP_S = 6.0  # ~10 req/min ceiling for the free tier


def _throttled_get(url: str, params: dict[str, Any]) -> requests.Response:
    """GET with a minimum gap between CoinGecko calls (rate-limit guard)."""
    global _last_coingecko_call
    with _ohlc_lock:
        wait = _COINGECKO_MIN_GAP_S - (time.monotonic() - _last_coingecko_call)
        if wait > 0:
            time.sleep(wait)
        resp = requests.get(url, params=params, timeout=_DEFAULT_TIMEOUT)
        _last_coingecko_call = time.monotonic()
    return resp


def _fetch_ohlcv(coin_id: str, vs_currency: str, days: int) -> list[list[float]]:
    """Return OHLCV data as [[timestamp, open, high, low, close, volume], ...].

    If USE_EXCHANGE_OHLC=true, uses the configured exchange client.
    Otherwise falls back to CoinGecko.

    Results are cached per (coin, vs, days, source) for _OHLC_TTL_S so the
    5-7 indicator tools analysing one coin share a single fetch.
    """
    key = (coin_id.lower(), vs_currency.lower(), days, _USE_EXCHANGE)
    with _ohlc_lock:
        hit = _ohlc_cache.get(key)
        if hit and (time.monotonic() - hit[0]) < _OHLC_TTL_S:
            return [list(row) for row in hit[1]]
    data = _fetch_ohlcv_exchange(coin_id, vs_currency, days) if _USE_EXCHANGE else _fetch_ohlcv_coingecko(coin_id, vs_currency, days)
    with _ohlc_lock:
        _ohlc_cache[key] = (time.monotonic(), [list(row) for row in data])
    return data


def _fetch_ohlcv_coingecko(coin_id: str, vs_currency: str, days: int) -> list[list[float]]:
    """Fetch OHLCV from CoinGecko."""
    ohlc_url = f"{_COINGECKO_BASE}/coins/{coin_id}/ohlc"
    escalation = [days, 90, 180, 365, 730]

    for attempt_days in escalation:
        params = {"vs_currency": vs_currency, "days": attempt_days}
        resp = _throttled_get(ohlc_url, params)
        resp.raise_for_status()
        candles = resp.json()  # [[ts, o, h, l, c], ...]
        if len(candles) >= _MIN_CANDLES:
            return [[c[0] * 1000, c[1], c[2], c[3], c[4], 0] for c in candles]

    # Fallback: return whatever we got from the last attempt
    params = {"vs_currency": vs_currency, "days": escalation[-1]}
    resp = _throttled_get(ohlc_url, params)
    resp.raise_for_status()
    candles = resp.json()
    return [[c[0] * 1000, c[1], c[2], c[3], c[4], 0] for c in candles]


def _fetch_ohlcv_exchange(coin_id: str, vs_currency: str, days: int) -> list[list[float]]:
    """Fetch OHLCV from the configured exchange."""
    import time

    client = get_exchange_client()

    # Map CoinGecko coin_id to exchange symbol (e.g., bitcoin -> BTCUSDT)
    symbol = _coingecko_id_to_symbol(coin_id, vs_currency)
    if not symbol:
        return []

    to_ts = int(time.time())
    from_ts = to_ts - days * 86400

    # Use hourly timeframe for indicator calculations (intraday trading)
    candles = client.get_ohlc(symbol, "1h", from_ts, to_ts)

    # Ensure minimum candles
    if len(candles) < _MIN_CANDLES and days < 730:
        return _fetch_ohlcv_exchange(coin_id, vs_currency, min(days * 2, 730))

    return candles


def _coingecko_id_to_symbol(coin_id: str, vs_currency: str) -> str | None:
    """Map CoinGecko ID to exchange symbol (e.g., bitcoin + usdt -> BTCUSDT)."""
    # Common mappings - in production this would query the exchange's market list
    common_map = {
        "bitcoin": "BTC",
        "ethereum": "ETH",
        "solana": "SOL",
        "ripple": "XRP",
        "cardano": "ADA",
        "dogecoin": "DOGE",
        "avalanche-2": "AVAX",
        "polkadot": "DOT",
        "polygon": "MATIC",
        "chainlink": "LINK",
        "uniswap": "UNI",
        "litecoin": "LTC",
        "bitcoin-cash": "BCH",
        "stellar": "XLM",
        "cosmos": "ATOM",
        "vechain": "VET",
        "tron": "TRX",
        "ethereum-classic": "ETC",
        "filecoin": "FIL",
        "internet-computer": "ICP",
        "near": "NEAR",
        "algorand": "ALGO",
        "aave": "AAVE",
        "maker": "MKR",
        "compound": "COMP",
        "sushi": "SUSHI",
        "curve-dao-token": "CRV",
        "yearn-finance": "YFI",
        "synthetix": "SNX",
        "1inch": "1INCH",
        "decentraland": "MANA",
        "the-sandbox": "SAND",
        "axie-infinity": "AXS",
        "flow": "FLOW",
        "theta": "THETA",
        "elrond": "EGLD",
        "hedera": "HBAR",
        "harmony": "ONE",
        "kava": "KAVA",
        "band-protocol": "BAND",
        "ocean-protocol": "OCEAN",
        "fetch-ai": "FET",
        "render": "RNDR",
        "arweave": "AR",
        "helium": "HNT",
        "kadena": "KDA",
        "secret": "SCRT",
        "thorchain": "RUNE",
        "zcash": "ZEC",
        "dash": "DASH",
        "monero": "XMR",
        "nano": "NANO",
        "bitcoin-gold": "BTG",
        "ravencoin": "RVN",
        "digibyte": "DGB",
        "horizen": "ZEN",
        "siacoin": "SC",
        "decred": "DCR",
        "quant": "QNT",
        "lido-dao": "LDO",
        "rocket-pool": "RPL",
        "staked-ether": "STETH",
        "wrapped-bitcoin": "WBTC",
        "tether": "USDT",
        "usd-coin": "USDC",
        "binance-usd": "BUSD",
        "true-usd": "TUSD",
        "dai": "DAI",
        "frax": "FRAX",
        "magic-internet-money": "MIM",
        "liquity-usd": "LUSD",
    }

    base = common_map.get(coin_id.lower())
    if not base:
        # Fallback: use coin_id uppercased (works for simple symbols)
        base = coin_id.upper().replace("-", "")
        if len(base) > 6:
            return None

    quote = vs_currency.upper()
    return f"{base}{quote}"


def _closes(ohlcv: list[list[float]]) -> np.ndarray:
    return np.array([row[4] for row in ohlcv], dtype=float)


def _highs(ohlcv: list[list[float]]) -> np.ndarray:
    return np.array([row[2] for row in ohlcv], dtype=float)


def _lows(ohlcv: list[list[float]]) -> np.ndarray:
    return np.array([row[3] for row in ohlcv], dtype=float)


# ---------------------------------------------------------------------------
# RSI Tool
# ---------------------------------------------------------------------------

class RSIInput(BaseModel):
    coin_id: str = Field(
        ...,
        description=(
            "CoinGecko coin ID (e.g. 'bitcoin', 'ethereum', 'solana'). "
            "Use the exact slug from https://api.coingecko.com/api/v3/coins/list."
        ),
    )
    vs_currency: str = Field(default="usd", description="Quote currency (e.g. 'usd', 'btc').")
    days: int = Field(default=3, ge=1, le=14, description="Number of days of hourly OHLC history to fetch.")
    period: int = Field(default=int(os.getenv("COUNCIL_RSI_PERIOD", "14")), ge=2, le=50, description="RSI look-back period.")


class RSITool(BaseTool):
    name: str = "rsi_indicator"
    description: str = (
        "Computes the Relative Strength Index (RSI) for a cryptocurrency. "
        "RSI oscillates between 0 and 100; values above 70 suggest overbought "
        "conditions, below 30 suggest oversold. Returns the most recent RSI value "
        "and the last 5 readings for trend context."
    )
    args_schema: Type[BaseModel] = RSIInput

    def _run(self, coin_id: str, vs_currency: str = "usd", days: int = 3, period: int = 14) -> str:
        period = int(os.getenv("COUNCIL_RSI_PERIOD", str(period)))
        try:
            ohlcv = _fetch_ohlcv(coin_id, vs_currency, days)
            if len(ohlcv) < period + 1:
                return json.dumps({"error": f"Not enough data: got {len(ohlcv)} candles, need at least {period + 1}."})

            closes = _closes(ohlcv)
            deltas = np.diff(closes)
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)

            # Wilder smoothing
            avg_gain = np.mean(gains[:period])
            avg_loss = np.mean(losses[:period])
            rsi_values: list[float] = []
            for i in range(period, len(deltas)):
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
                rsi_values.append(round(100.0 - (100.0 / (1.0 + rs)), 2))

            current_rsi = rsi_values[-1]
            signal = "overbought" if current_rsi > 70 else "oversold" if current_rsi < 30 else "neutral"

            return json.dumps({
                "coin": coin_id,
                "vs_currency": vs_currency,
                "period": period,
                "current_rsi": current_rsi,
                "signal": signal,
                "last_5_readings": rsi_values[-5:],
                "current_close": round(closes[-1], 6),
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# MACD Tool
# ---------------------------------------------------------------------------

class MACDInput(BaseModel):
    coin_id: str = Field(..., description="CoinGecko coin ID (e.g. 'bitcoin').")
    vs_currency: str = Field(default="usd", description="Quote currency.")
    days: int = Field(default=3, ge=1, le=14, description="Days of hourly history.")
    fast_period: int = Field(default=12, ge=2, le=50, description="Fast EMA period.")
    slow_period: int = Field(default=26, ge=5, le=100, description="Slow EMA period.")
    signal_period: int = Field(default=9, ge=2, le=30, description="Signal line EMA period.")


class MACDTool(BaseTool):
    name: str = "macd_indicator"
    description: str = (
        "Computes the Moving Average Convergence Divergence (MACD) for a cryptocurrency. "
        "Returns MACD line, signal line, and histogram. A bullish crossover (MACD crosses "
        "above signal) indicates upward momentum; bearish crossover indicates downward momentum."
    )
    args_schema: Type[BaseModel] = MACDInput

    @staticmethod
    def _ema(prices: np.ndarray, period: int) -> np.ndarray:
        k = 2.0 / (period + 1)
        ema = np.empty(len(prices))
        ema[0] = prices[0]
        for i in range(1, len(prices)):
            ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
        return ema

    def _run(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 3,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> str:
        try:
            ohlcv = _fetch_ohlcv(coin_id, vs_currency, days)
            closes = _closes(ohlcv)
            min_required = slow_period + signal_period
            if len(closes) < min_required:
                return json.dumps({"error": f"Need at least {min_required} candles, got {len(closes)}."})

            fast_ema = self._ema(closes, fast_period)
            slow_ema = self._ema(closes, slow_period)
            macd_line = fast_ema - slow_ema
            signal_line = self._ema(macd_line, signal_period)
            histogram = macd_line - signal_line

            current_macd = round(float(macd_line[-1]), 6)
            current_signal = round(float(signal_line[-1]), 6)
            current_hist = round(float(histogram[-1]), 6)
            prev_hist = round(float(histogram[-2]), 6)

            if current_hist > 0 and prev_hist < 0:
                crossover = "bullish_crossover"
            elif current_hist < 0 and prev_hist > 0:
                crossover = "bearish_crossover"
            elif current_hist > prev_hist:
                crossover = "bullish_momentum"
            else:
                crossover = "bearish_momentum"

            return json.dumps({
                "coin": coin_id,
                "vs_currency": vs_currency,
                "macd_line": current_macd,
                "signal_line": current_signal,
                "histogram": current_hist,
                "crossover_signal": crossover,
                "current_close": round(float(closes[-1]), 6),
                "last_5_histograms": [round(float(h), 6) for h in histogram[-5:]],
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Bollinger Bands Tool
# ---------------------------------------------------------------------------

class BollingerBandsInput(BaseModel):
    coin_id: str = Field(..., description="CoinGecko coin ID (e.g. 'bitcoin').")
    vs_currency: str = Field(default="usd", description="Quote currency.")
    days: int = Field(default=3, ge=1, le=14, description="Days of hourly history.")
    period: int = Field(default=20, ge=5, le=100, description="SMA look-back period.")
    num_std: float = Field(default=2.0, ge=0.5, le=4.0, description="Number of standard deviations for bands.")


class BollingerBandsTool(BaseTool):
    name: str = "bollinger_bands_indicator"
    description: str = (
        "Computes Bollinger Bands for a cryptocurrency. Returns upper band, middle band (SMA), "
        "and lower band. Price near the upper band signals potential overbought; near the lower "
        "band signals potential oversold. Band width indicates volatility — narrow bands precede "
        "large moves (the squeeze)."
    )
    args_schema: Type[BaseModel] = BollingerBandsInput

    def _run(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 3,
        period: int = 20,
        num_std: float = 2.0,
    ) -> str:
        try:
            ohlcv = _fetch_ohlcv(coin_id, vs_currency, days)
            closes = _closes(ohlcv)
            if len(closes) < period:
                return json.dumps({"error": f"Need at least {period} candles, got {len(closes)}."})

            window = closes[-period:]
            sma = float(np.mean(window))
            std = float(np.std(window, ddof=1))
            upper = round(sma + num_std * std, 6)
            lower = round(sma - num_std * std, 6)
            middle = round(sma, 6)
            current_price = round(float(closes[-1]), 6)

            band_width = round((upper - lower) / middle * 100, 2)  # as % of middle
            pct_b = round((current_price - lower) / (upper - lower) * 100, 2) if upper != lower else 50.0

            if pct_b > 100:
                position = "above_upper_band"
            elif pct_b > 80:
                position = "near_upper_band"
            elif pct_b < 0:
                position = "below_lower_band"
            elif pct_b < 20:
                position = "near_lower_band"
            else:
                position = "within_bands"

            return json.dumps({
                "coin": coin_id,
                "vs_currency": vs_currency,
                "period": period,
                "num_std": num_std,
                "upper_band": upper,
                "middle_band": middle,
                "lower_band": lower,
                "current_price": current_price,
                "pct_b": pct_b,
                "band_width_pct": band_width,
                "position": position,
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# EMA Cross Tool
# ---------------------------------------------------------------------------

class EMACrossInput(BaseModel):
    coin_id: str = Field(..., description="CoinGecko coin ID (e.g. 'bitcoin').")
    vs_currency: str = Field(default="usd", description="Quote currency.")
    days: int = Field(default=3, ge=1, le=14, description="Days of hourly history.")
    fast_period: int = Field(default=9, ge=2, le=50, description="Fast EMA period.")
    slow_period: int = Field(default=21, ge=5, le=200, description="Slow EMA period.")


class EMACrossTool(BaseTool):
    name: str = "ema_cross_indicator"
    description: str = (
        "Computes two Exponential Moving Averages (EMA) and detects crossovers. "
        "A golden cross (fast EMA crosses above slow EMA) is a bullish trend signal; "
        "a death cross (fast crosses below slow) is bearish. Returns both EMA values, "
        "the spread, and the crossover direction."
    )
    args_schema: Type[BaseModel] = EMACrossInput

    @staticmethod
    def _ema(prices: np.ndarray, period: int) -> np.ndarray:
        k = 2.0 / (period + 1)
        ema = np.empty(len(prices))
        ema[0] = prices[0]
        for i in range(1, len(prices)):
            ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
        return ema

    def _run(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 3,
        fast_period: int = 9,
        slow_period: int = 21,
    ) -> str:
        try:
            ohlcv = _fetch_ohlcv(coin_id, vs_currency, days)
            closes = _closes(ohlcv)
            if len(closes) < slow_period + 1:
                return json.dumps({"error": f"Need at least {slow_period + 1} candles, got {len(closes)}."})

            fast_ema = self._ema(closes, fast_period)
            slow_ema = self._ema(closes, slow_period)

            current_fast = round(float(fast_ema[-1]), 6)
            current_slow = round(float(slow_ema[-1]), 6)
            prev_fast = float(fast_ema[-2])
            prev_slow = float(slow_ema[-2])

            spread_pct = round((current_fast - current_slow) / current_slow * 100, 4)

            if prev_fast < prev_slow and current_fast > current_slow:
                crossover = "golden_cross"
                trend = "bullish"
            elif prev_fast > prev_slow and current_fast < current_slow:
                crossover = "death_cross"
                trend = "bearish"
            elif current_fast > current_slow:
                crossover = "fast_above_slow"
                trend = "bullish"
            else:
                crossover = "fast_below_slow"
                trend = "bearish"

            return json.dumps({
                "coin": coin_id,
                "vs_currency": vs_currency,
                "fast_period": fast_period,
                "slow_period": slow_period,
                "fast_ema": current_fast,
                "slow_ema": current_slow,
                "spread_pct": spread_pct,
                "crossover": crossover,
                "trend": trend,
                "current_close": round(float(closes[-1]), 6),
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# ATR Tool
# ---------------------------------------------------------------------------

class ATRInput(BaseModel):
    coin_id: str = Field(..., description="CoinGecko coin ID (e.g. 'bitcoin').")
    vs_currency: str = Field(default="usd", description="Quote currency.")
    days: int = Field(default=3, ge=1, le=14, description="Days of hourly history.")
    period: int = Field(default=int(os.getenv("COUNCIL_ATR_PERIOD", "14")), ge=2, le=50, description="ATR look-back period.")


class ATRTool(BaseTool):
    name: str = "atr_indicator"
    description: str = (
        "Computes the Average True Range (ATR) for a cryptocurrency. "
        "ATR measures market volatility; higher ATR means more volatile price action. "
        "Returns the raw ATR value and the ATR as a percentage of the current price "
        "(ATR%), which is useful for setting stop-loss distances and position sizing."
    )
    args_schema: Type[BaseModel] = ATRInput

    def _run(self, coin_id: str, vs_currency: str = "usd", days: int = 3, period: int = 14) -> str:
        period = int(os.getenv("COUNCIL_ATR_PERIOD", str(period)))
        try:
            ohlcv = _fetch_ohlcv(coin_id, vs_currency, days)
            if len(ohlcv) < period + 1:
                return json.dumps({"error": f"Need at least {period + 1} candles, got {len(ohlcv)}."})

            highs = _highs(ohlcv)
            lows = _lows(ohlcv)
            closes = _closes(ohlcv)

            # True Range: max of (H-L), |H-prevC|, |L-prevC|
            tr_values = []
            for i in range(1, len(closes)):
                hl = highs[i] - lows[i]
                hc = abs(highs[i] - closes[i - 1])
                lc = abs(lows[i] - closes[i - 1])
                tr_values.append(max(hl, hc, lc))

            tr = np.array(tr_values)

            # Wilder smoothing
            atr = float(np.mean(tr[:period]))
            for i in range(period, len(tr)):
                atr = (atr * (period - 1) + tr[i]) / period

            current_price = float(closes[-1])
            atr_pct = round(atr / current_price * 100, 4) if current_price > 0 else 0.0

            # Volatility assessment
            if atr_pct > 10:
                volatility_level = "extreme"
            elif atr_pct > 5:
                volatility_level = "high"
            elif atr_pct > 2:
                volatility_level = "moderate"
            else:
                volatility_level = "low"

            return json.dumps({
                "coin": coin_id,
                "vs_currency": vs_currency,
                "period": period,
                "atr": round(atr, 6),
                "atr_pct": atr_pct,
                "current_price": round(current_price, 6),
                "volatility_level": volatility_level,
                "suggested_stop_loss_1x_atr_pct": round(atr_pct, 2),
                "suggested_stop_loss_2x_atr_pct": round(atr_pct * 2, 2),
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})
