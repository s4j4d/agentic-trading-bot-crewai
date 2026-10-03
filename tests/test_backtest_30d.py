#!/usr/bin/env python
"""
30-Day Backtest for the Crypto Council Flow trading strategy.

Simulates the core signal logic of the four-agent council (scout → technical →
sentiment → risk) over 30 days of historical CoinGecko data, without calling
LLMs — instead replicating the deterministic parts of each agent's decision
pipeline.

Strategy summary (mirrors the council_crew task configs):
  1. Market scout selects coins ranked by momentum/volatility.
  2. Technical analysis computes RSI, MACD, Bollinger Bands, EMA Cross, ATR.
  3. A composite signal score decides long/short/flat.
  4. Risk manager applies 2% max risk per trade with a 2:1 reward:risk target.

Run:
    cd crypto_council_flow
    uv run pytest tests/test_backtest_30d.py -v -s
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest
import requests

# ---------------------------------------------------------------------------
# CoinGecko helpers (reuses the same endpoints as the live tools)
# ---------------------------------------------------------------------------

_CG_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT = 20


def _cg_get(endpoint: str, params: dict) -> Any:
    resp = requests.get(
        f"{_CG_BASE}{endpoint}",
        params=params,
        timeout=_TIMEOUT,
        headers={"accept": "application/json", "user-agent": "backtest/1.0"},
    )
    resp.raise_for_status()
    return resp.json()


def fetch_ohlcv(coin_id: str, days: int = 45) -> dict[str, np.ndarray]:
    """Fetch daily OHLCV and return {open, high, low, close, volume, timestamps}."""
    data = _cg_get(
        f"/coins/{coin_id}/market_chart",
        {"vs_currency": "usd", "days": days, "interval": "daily"},
    )
    prices = data.get("prices", [])
    volumes = data.get("total_volumes", [])

    if not prices:
        raise ValueError(f"No price data for {coin_id}")

    closes = np.array([p[1] for p in prices], dtype=float)
    vols = np.array([v[1] for v in volumes], dtype=float) if volumes else np.zeros_like(closes)
    ts = np.array([p[0] / 1000 for p in prices], dtype=float)  # epoch seconds

    # CoinGecko daily candles aren't true OHLC — we approximate from close series
    # with a simple rolling method.
    highs = closes.copy()
    lows = closes.copy()
    for i in range(1, len(closes)):
        # Simulate intraday range from close-to-close moves
        movement = abs(closes[i] - closes[i - 1])
        highs[i] = closes[i] + movement * 0.3
        lows[i] = closes[i] - movement * 0.3
    highs[0] = closes[0] * 1.01
    lows[0] = closes[0] * 0.99

    return {
        "open": closes.copy(),  # approximate
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": vols,
        "timestamps": ts,
    }


# ---------------------------------------------------------------------------
# Technical indicators (identical math to technical_indicators.py)
# ---------------------------------------------------------------------------

def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder-smoothed RSI, returns array aligned with closes (NaN for warmup)."""
    rsi = np.full_like(closes, np.nan)
    if len(closes) < period + 1:
        return rsi

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def _ema(prices: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    ema = np.empty(len(prices))
    ema[0] = prices[0]
    for i in range(1, len(prices)):
        ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
    return ema


def compute_macd(closes: np.ndarray, fast=12, slow=26, signal=9) -> dict[str, np.ndarray]:
    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)
    macd_line = fast_ema - slow_ema
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def compute_bollinger(closes: np.ndarray, period=20, num_std=2.0) -> dict[str, np.ndarray]:
    middle = np.full_like(closes, np.nan)
    upper = np.full_like(closes, np.nan)
    lower = np.full_like(closes, np.nan)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        m = np.mean(window)
        s = np.std(window, ddof=1)
        middle[i] = m
        upper[i] = m + num_std * s
        lower[i] = m - num_std * s
    return {"upper": upper, "middle": middle, "lower": lower}


def compute_ema_cross(closes: np.ndarray, fast_period=9, slow_period=21) -> dict[str, np.ndarray]:
    fast = _ema(closes, fast_period)
    slow = _ema(closes, slow_period)
    return {"fast": fast, "slow": slow}


# ---------------------------------------------------------------------------
# Composite signal logic (mirrors the council's combined output)
# ---------------------------------------------------------------------------

def composite_signal(
    rsi: float,
    macd_hist: float,
    macd_prev_hist: float,
    bb_pct_b: float,
    ema_spread_pct: float,
    ema_prev_spread_pct: float,
) -> tuple[str, float]:
    """
    Returns (direction, confidence) where direction is 'long' | 'short' | 'flat'
    and confidence is 0-1.

    Combines the same signals the council agents evaluate:
    - RSI overbought/oversold
    - MACD crossover direction
    - Bollinger Band position (%B)
    - EMA Cross trend & crossover
    """
    score = 0.0

    # RSI signal (weight: 0.25)
    if not np.isnan(rsi):
        if rsi < 30:
            score += 0.25  # oversold → long
        elif rsi > 70:
            score -= 0.25  # overbought → short
        elif rsi < 45:
            score += 0.10
        elif rsi > 55:
            score -= 0.10

    # MACD signal (weight: 0.25)
    if macd_hist > 0 and macd_prev_hist < 0:
        score += 0.25  # bullish crossover
    elif macd_hist < 0 and macd_prev_hist > 0:
        score -= 0.25  # bearish crossover
    elif macd_hist > 0:
        score += 0.10
    elif macd_hist < 0:
        score -= 0.10

    # Bollinger %B signal (weight: 0.20)
    if not np.isnan(bb_pct_b):
        if bb_pct_b < 20:
            score += 0.20  # near lower band → long
        elif bb_pct_b > 80:
            score -= 0.20  # near upper band → short

    # EMA Cross signal (weight: 0.30)
    if ema_spread_pct > 0 and ema_prev_spread_pct <= 0:
        score += 0.30  # golden cross
    elif ema_spread_pct < 0 and ema_prev_spread_pct >= 0:
        score -= 0.30  # death cross
    elif ema_spread_pct > 0:
        score += 0.10
    elif ema_spread_pct < 0:
        score -= 0.10

    confidence = min(abs(score), 1.0)

    if score > 0.25:
        return "long", confidence
    elif score < -0.25:
        return "short", confidence
    else:
        return "flat", confidence


# ---------------------------------------------------------------------------
# Position and portfolio tracking
# ---------------------------------------------------------------------------

@dataclass
class Position:
    coin_id: str
    direction: str  # "long" or "short"
    entry_price: float
    size_usd: float
    stop_loss: float
    take_profit: float
    entry_day: int


@dataclass
class TradeResult:
    coin_id: str
    direction: str
    entry_price: float
    exit_price: float
    size_usd: float
    pnl_usd: float
    pnl_pct: float
    entry_day: int
    exit_day: int
    exit_reason: str  # "take_profit" | "stop_loss" | "end_of_period"


@dataclass
class Portfolio:
    initial_capital: float = 10_000.0
    cash: float = 10_000.0
    max_risk_pct: float = 0.02  # 2% max risk per trade
    reward_risk_ratio: float = 2.0
    positions: list[Position] = field(default_factory=list)
    closed_trades: list[TradeResult] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def open_position(self, coin_id: str, direction: str, entry_price: float,
                      atr_pct: float, day: int) -> None:
        """Open a new position using ATR-based stop-loss sizing."""
        if len(self.positions) >= 3:
            return  # max 3 concurrent positions

        # Stop-loss at 1x ATR distance
        stop_distance_pct = max(atr_pct, 1.0)  # at least 1%
        if direction == "long":
            stop_loss = entry_price * (1 - stop_distance_pct / 100)
            take_profit = entry_price * (1 + stop_distance_pct * self.reward_risk_ratio / 100)
        else:
            stop_loss = entry_price * (1 + stop_distance_pct / 100)
            take_profit = entry_price * (1 - stop_distance_pct * self.reward_risk_ratio / 100)

        # Position sizing: risk = max_risk_pct of portfolio
        risk_usd = self.portfolio_value_at_day(day) * self.max_risk_pct
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit <= 0:
            return

        units = risk_usd / risk_per_unit
        size_usd = min(units * entry_price, self.cash * 0.4)  # max 40% of cash per trade
        if size_usd < 10:
            return

        self.cash -= size_usd
        self.positions.append(Position(
            coin_id=coin_id,
            direction=direction,
            entry_price=entry_price,
            size_usd=size_usd,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_day=day,
        ))

    def check_exits(self, day: int, prices: dict[str, float]) -> None:
        """Check stop-loss and take-profit for all open positions."""
        still_open = []
        for pos in self.positions:
            price = prices.get(pos.coin_id, pos.entry_price)
            hit_sl = (pos.direction == "long" and price <= pos.stop_loss) or \
                     (pos.direction == "short" and price >= pos.stop_loss)
            hit_tp = (pos.direction == "long" and price >= pos.take_profit) or \
                     (pos.direction == "short" and price <= pos.take_profit)

            if hit_sl or hit_tp:
                exit_price = pos.stop_loss if hit_sl else pos.take_profit
                reason = "stop_loss" if hit_sl else "take_profit"
                if pos.direction == "long":
                    pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
                else:
                    pnl_pct = (pos.entry_price - exit_price) / pos.entry_price

                pnl_usd = pos.size_usd * pnl_pct
                units = pos.size_usd / pos.entry_price
                self.cash += pos.size_usd + pnl_usd

                self.closed_trades.append(TradeResult(
                    coin_id=pos.coin_id,
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    size_usd=pos.size_usd,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct,
                    entry_day=pos.entry_day,
                    exit_day=day,
                    exit_reason=reason,
                ))
            else:
                still_open.append(pos)
        self.positions = still_open

    def close_all(self, day: int, prices: dict[str, float]) -> None:
        """Close all positions at market price (end of period)."""
        for pos in self.positions:
            price = prices.get(pos.coin_id, pos.entry_price)
            if pos.direction == "long":
                pnl_pct = (price - pos.entry_price) / pos.entry_price
            else:
                pnl_pct = (pos.entry_price - price) / pos.entry_price

            pnl_usd = pos.size_usd * pnl_pct
            self.cash += pos.size_usd + pnl_usd

            self.closed_trades.append(TradeResult(
                coin_id=pos.coin_id,
                direction=pos.direction,
                entry_price=pos.entry_price,
                exit_price=price,
                size_usd=pos.size_usd,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                entry_day=pos.entry_day,
                exit_day=day,
                exit_reason="end_of_period",
            ))
        self.positions = []

    def portfolio_value_at_day(self, day_idx: int) -> float:
        """Approximate portfolio value (cash + unrealised PnL)."""
        return self.cash + sum(p.size_usd for p in self.positions)

    def record_equity(self, day_idx: int, prices: dict[str, float]) -> None:
        """Record total equity including unrealised positions."""
        unrealised = 0.0
        for pos in self.positions:
            price = prices.get(pos.coin_id, pos.entry_price)
            if pos.direction == "long":
                pnl_pct = (price - pos.entry_price) / pos.entry_price
            else:
                pnl_pct = (pos.entry_price - price) / pos.entry_price
            unrealised += pos.size_usd * pnl_pct

        self.equity_curve.append(self.cash + unrealised)


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

# Coins to test — major + mid-cap for diversity
BACKTEST_COINS = ["bitcoin", "ethereum", "solana", "ripple"]
BACKTEST_DAYS = 30
WARMUP_DAYS = 30  # extra days for indicator warmup


def _fetch_all_data() -> dict[str, dict[str, np.ndarray]]:
    """Fetch OHLCV data for all coins with rate-limit handling."""
    data = {}
    for i, coin in enumerate(BACKTEST_COINS):
        if i > 0:
            time.sleep(6)  # CoinGecko rate limit ~10 req/min for free tier
        try:
            data[coin] = fetch_ohlcv(coin, days=BACKTEST_DAYS + WARMUP_DAYS + 5)
            print(f"  ✓ {coin}: {len(data[coin]['close'])} candles")
        except Exception as e:
            print(f"  ✗ {coin}: {e}")
    return data


@pytest.fixture(scope="module")
def price_data() -> dict[str, dict[str, np.ndarray]]:
    """Module-scoped fixture to avoid re-fetching."""
    return _fetch_all_data()


class TestBacktest30Day:
    """30-day backtest of the Crypto Council strategy."""

    def test_profitability(self, price_data: dict[str, dict[str, np.ndarray]]):
        """
        Run the backtest and check if the strategy is profitable over 30 days.
        """
        if not price_data:
            pytest.skip("No price data available (CoinGecko API may be rate-limited)")

        portfolio = Portfolio(initial_capital=10_000.0)
        warmup = WARMUP_DAYS

        # Verify we have enough data
        min_candles = min(len(d["close"]) for d in price_data.values())
        if min_candles < warmup + BACKTEST_DAYS:
            pytest.skip(f"Insufficient data: need {warmup + BACKTEST_DAYS}, got {min_candles}")

        # Pre-compute indicators for all coins
        indicators: dict[str, dict[str, np.ndarray]] = {}
        for coin, d in price_data.items():
            closes = d["close"]
            indicators[coin] = {
                "rsi": compute_rsi(closes, period=14),
                "macd": compute_macd(closes),
                "bb": compute_bollinger(closes),
                "ema": compute_ema_cross(closes),
            }

        # Simulate day-by-day
        daily_log = []
        for day in range(warmup, warmup + BACKTEST_DAYS):
            # Current prices
            current_prices = {coin: price_data[coin]["close"][day] for coin in price_data}

            # 1) Check exits first
            portfolio.check_exits(day, current_prices)

            # 2) Generate signals for each coin
            for coin in price_data:
                rsi_val = indicators[coin]["rsi"][day]
                macd_h = indicators[coin]["macd"]["histogram"][day]
                macd_prev_h = indicators[coin]["macd"]["histogram"][day - 1]

                bb_upper = indicators[coin]["bb"]["upper"][day]
                bb_lower = indicators[coin]["bb"]["lower"][day]
                bb_middle = indicators[coin]["bb"]["middle"][day]
                if not np.isnan(bb_upper) and not np.isnan(bb_lower) and bb_upper != bb_lower:
                    pct_b = (current_prices[coin] - bb_lower) / (bb_upper - bb_lower) * 100
                else:
                    pct_b = 50.0

                ema_spread = indicators[coin]["ema"]["fast"][day] - indicators[coin]["ema"]["slow"][day]
                ema_spread_pct = ema_spread / indicators[coin]["ema"]["slow"][day] * 100 if indicators[coin]["ema"]["slow"][day] > 0 else 0
                ema_prev_spread = indicators[coin]["ema"]["fast"][day - 1] - indicators[coin]["ema"]["slow"][day - 1]
                ema_prev_spread_pct = ema_prev_spread / indicators[coin]["ema"]["slow"][day - 1] * 100 if indicators[coin]["ema"]["slow"][day - 1] > 0 else 0

                direction, confidence = composite_signal(
                    rsi=rsi_val if not np.isnan(rsi_val) else 50.0,
                    macd_hist=macd_h,
                    macd_prev_hist=macd_prev_h,
                    bb_pct_b=pct_b,
                    ema_spread_pct=ema_spread_pct,
                    ema_prev_spread_pct=ema_prev_spread_pct,
                )

                # Estimate ATR% for stop-loss sizing (from recent close moves)
                if day >= 2:
                    recent_closes = price_data[coin]["close"][day - 14:day + 1]
                    atr_est = float(np.mean(np.abs(np.diff(recent_closes)))) / current_prices[coin] * 100
                else:
                    atr_est = 2.0

                # Open position if signal is strong and we have no position in this coin
                existing_coins = {p.coin_id for p in portfolio.positions}
                if direction != "flat" and coin not in existing_coins and confidence >= 0.3:
                    portfolio.open_position(
                        coin_id=coin,
                        direction=direction,
                        entry_price=current_prices[coin],
                        atr_pct=atr_est,
                        day=day,
                    )

            # Record equity
            portfolio.record_equity(day, current_prices)

            # Log
            total_value = portfolio.cash + sum(
                p.size_usd * (
                    (current_prices[p.coin_id] - p.entry_price) / p.entry_price
                    if p.direction == "long"
                    else (p.entry_price - current_prices[p.coin_id]) / p.entry_price
                )
                for p in portfolio.positions
            )
            daily_log.append({
                "day": day - warmup,
                "equity": round(total_value, 2),
                "open_positions": len(portfolio.positions),
            })

        # Close remaining positions at end
        final_prices = {coin: price_data[coin]["close"][warmup + BACKTEST_DAYS - 1]
                       for coin in price_data}
        portfolio.close_all(warmup + BACKTEST_DAYS, final_prices)

        # ---- Assertions & Report ----
        initial = portfolio.initial_capital
        final_equity = portfolio.equity_curve[-1] if portfolio.equity_curve else initial
        total_pnl = final_equity - initial
        total_return_pct = (total_pnl / initial) * 100

        win_trades = [t for t in portfolio.closed_trades if t.pnl_usd > 0]
        loss_trades = [t for t in portfolio.closed_trades if t.pnl_usd <= 0]
        total_trades = len(portfolio.closed_trades)
        win_rate = len(win_trades) / total_trades * 100 if total_trades > 0 else 0

        # Max drawdown
        eq = np.array(portfolio.equity_curve) if portfolio.equity_curve else np.array([initial])
        running_max = np.maximum.accumulate(eq)
        drawdowns = (eq - running_max) / running_max
        max_dd = float(np.min(drawdowns)) * 100 if len(drawdowns) > 0 else 0.0

        # Print report
        print("\n" + "=" * 70)
        print("  30-DAY BACKTEST REPORT — Crypto Council Strategy")
        print("=" * 70)
        print(f"  Coins tested:    {', '.join(BACKTEST_COINS)}")
        print(f"  Period:          {BACKTEST_DAYS} days")
        print(f"  Initial capital: ${initial:,.2f}")
        print(f"  Final equity:    ${final_equity:,.2f}")
        print(f"  Total P&L:       ${total_pnl:,.2f} ({total_return_pct:+.2f}%)")
        print(f"  Max drawdown:    {max_dd:.2f}%")
        print(f"  Total trades:    {total_trades}")
        print(f"  Wins / Losses:   {len(win_trades)} / {len(loss_trades)}")
        print(f"  Win rate:        {win_rate:.1f}%")
        print()

        if portfolio.closed_trades:
            print("  Trade details:")
            print(f"  {'Coin':<12} {'Dir':<6} {'Entry':>12} {'Exit':>12} {'PnL':>12} {'PnL%':>8} {'Reason':<16}")
            print("  " + "-" * 78)
            for t in portfolio.closed_trades:
                print(
                    f"  {t.coin_id:<12} {t.direction:<6} "
                    f"${t.entry_price:>10,.2f} ${t.exit_price:>10,.2f} "
                    f"${t.pnl_usd:>+10,.2f} {t.pnl_pct:>+7.2%} {t.exit_reason:<16}"
                )

        print()
        if portfolio.equity_curve:
            print("  Equity curve (daily):")
            for entry in daily_log:
                bar_len = int(max(0, (entry["equity"] - initial) / initial * 50 + 25))
                bar = "█" * bar_len
                print(f"    Day {entry['day']:>2}: ${entry['equity']:>10,.2f}  {bar}")

        print("=" * 70)
        print(f"  RESULT: {'PROFITABLE ✓' if total_pnl > 0 else 'NOT PROFITABLE ✗'}")
        print("=" * 70)

        # The test assertion — strategy must be profitable
        assert total_pnl > 0, (
            f"Strategy lost ${abs(total_pnl):,.2f} over {BACKTEST_DAYS} days. "
            f"Final equity ${final_equity:,.2f} vs initial ${initial:,.2f}. "
            f"Win rate: {win_rate:.1f}% over {total_trades} trades."
        )
