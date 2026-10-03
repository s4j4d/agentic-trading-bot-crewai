"""
Risk management tools for the risk_manager agent.

Tools cover:
- Position sizing via Kelly criterion and fixed-risk models
- Liquidation price calculation for leveraged positions
- Portfolio volatility (historical VaR and CVaR)
- Correlation between two crypto assets
- Exchange order-book depth / liquidity check via CoinGecko tickers
"""

from __future__ import annotations

import json
import math
from typing import Type

import numpy as np
import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator, model_validator


_DEFAULT_TIMEOUT = 10
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _fetch_daily_closes(coin_id: str, vs_currency: str, days: int) -> np.ndarray:
    url = f"{_COINGECKO_BASE}/coins/{coin_id}/market_chart"
    params = {"vs_currency": vs_currency, "days": days, "interval": "daily"}
    resp = requests.get(url, params=params, timeout=_DEFAULT_TIMEOUT)
    resp.raise_for_status()
    prices = resp.json().get("prices", [])
    return np.array([p[1] for p in prices], dtype=float)


# ---------------------------------------------------------------------------
# Position Sizing Tool (Kelly + fixed-risk)
# ---------------------------------------------------------------------------

class PositionSizingInput(BaseModel):
    account_size: float = Field(..., gt=0, description="Total trading account size.")
    entry_price: float = Field(..., gt=0, description="Planned entry price for the asset.")
    stop_loss_price: float = Field(..., gt=0, description="Stop-loss price level.")
    win_rate: float = Field(
        default=0.55,
        ge=0.01,
        le=0.99,
        description="Historical or estimated win rate (0.0-1.0). Default 0.55.",
    )
    reward_risk_ratio: float = Field(
        default=2.0,
        ge=0.1,
        le=20.0,
        description="Average reward-to-risk ratio (e.g. 2.0 means 2:1 R/R). Default 2.0.",
    )
    max_risk_pct: float = Field(
        default=2.0,
        ge=0.1,
        le=20.0,
        description="Maximum percentage of account to risk per trade (e.g. 2.0 = 2%). Default 2.0.",
    )


class PositionSizingTool(BaseTool):
    name: str = "position_sizing"
    description: str = (
        "Calculates optimal position size using both the Kelly Criterion and a "
        "fixed-risk model. Returns the number of units to buy, value of the "
        "position, risk amount, and Kelly fraction. Helps prevent over-sizing that "
        "leads to account blow-up."
    )
    args_schema: Type[BaseModel] = PositionSizingInput

    def _run(
        self,
        account_size: float,
        entry_price: float,
        stop_loss_price: float,
        win_rate: float = 0.55,
        reward_risk_ratio: float = 2.0,
        max_risk_pct: float = 2.0,
    ) -> str:
        try:
            if entry_price <= 0 or stop_loss_price <= 0:
                return json.dumps({"error": "Prices must be positive."})

            risk_per_unit = abs(entry_price - stop_loss_price)
            if risk_per_unit == 0:
                return json.dumps({"error": "Entry price and stop-loss price cannot be equal."})

            # Fixed-risk model
            max_risk = account_size * (max_risk_pct / 100)
            fixed_units = max_risk / risk_per_unit
            fixed_position = fixed_units * entry_price

            # Kelly Criterion: f* = W - (1-W)/R where R = reward/risk ratio
            kelly_fraction = win_rate - (1 - win_rate) / reward_risk_ratio
            half_kelly = kelly_fraction / 2  # half-Kelly for safety
            kelly_position = max(0.0, account_size * half_kelly)
            kelly_units = kelly_position / entry_price

            # Use the more conservative of the two
            recommended_units = min(fixed_units, kelly_units)
            recommended_position = recommended_units * entry_price
            actual_risk = recommended_units * risk_per_unit
            actual_risk_pct = actual_risk / account_size * 100

            return json.dumps({
                "account_size": account_size,
                "entry_price": entry_price,
                "stop_loss_price": stop_loss_price,
                "risk_per_unit": round(risk_per_unit, 6),
                "win_rate": win_rate,
                "reward_risk_ratio": reward_risk_ratio,
                "fixed_risk_model": {
                    "units": round(fixed_units, 6),
                    "position": round(fixed_position, 2),
                    "risk": round(max_risk, 2),
                },
                "kelly_criterion": {
                    "full_kelly_fraction": round(kelly_fraction, 4),
                    "half_kelly_fraction": round(half_kelly, 4),
                    "units": round(kelly_units, 6),
                    "position": round(kelly_position, 2),
                },
                "recommended": {
                    "units": round(recommended_units, 6),
                    "position": round(recommended_position, 2),
                    "risk": round(actual_risk, 2),
                    "risk_pct_of_account": round(actual_risk_pct, 2),
                    "basis": "more_conservative_of_fixed_risk_and_half_kelly",
                },
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Liquidation Price Calculator
# ---------------------------------------------------------------------------

class LiquidationPriceInput(BaseModel):
    entry_price: float = Field(..., gt=0, description="Entry price of the leveraged position.")
    leverage_levels: list[float] = Field(
        default=[2.0, 5.0],
        description="Leverage levels to evaluate, e.g. [2, 5] for 2x and 5x scenarios.",
    )
    position_side: str = Field(
        default="long",
        description="Position side: 'long' or 'short'.",
    )
    maintenance_margin_rate: float = Field(
        default=0.005,
        ge=0.001,
        le=0.5,
        description=(
            "Maintenance margin rate as a decimal (e.g. 0.005 = 0.5%). "
            "Check your exchange for the exact value. Default 0.5%."
        ),
    )

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_singular_leverage(cls, data: object) -> object:
        # Single canonical DTO: always end up with leverage_levels.
        # If the LLM passes the old singular `leverage`, fold it in.
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if d.get("leverage_levels") is None and d.get("leverage") is not None:
            raw = d.pop("leverage")
            d["leverage_levels"] = [raw] if not isinstance(raw, list) else raw
        return d

    @field_validator("leverage_levels")
    @classmethod
    def _check_levels(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("leverage_levels must contain at least one level")
        for level in v:
            if not 1.0 <= float(level) <= 200.0:
                raise ValueError(f"leverage level {level} out of range [1, 200]")
        return [float(x) for x in v]


class LiquidationPriceTool(BaseTool):
    name: str = "liquidation_price"
    description: str = (
        "Calculates the liquidation price for a leveraged crypto futures or margin position. "
        "Pass leverage_levels as a list (e.g. [2, 5]) to evaluate multiple leverage scenarios "
        "in a single call. Returns the liquidation price, the percentage drop/rise from entry "
        "to liquidation, and a safety buffer recommendation per level. "
        "Essential for managing leveraged trade risk."
    )
    args_schema: Type[BaseModel] = LiquidationPriceInput

    def _run(
        self,
        entry_price: float,
        leverage_levels: list[float] | None = None,
        position_side: str = "long",
        maintenance_margin_rate: float = 0.005,
    ) -> str:
        try:
            levels = leverage_levels or [2.0, 5.0]
            side = position_side.lower().strip()
            if side not in ("long", "short"):
                return json.dumps({"error": "position_side must be 'long' or 'short'."})

            scenarios = []
            for leverage in levels:
                initial_margin_rate = 1.0 / leverage

                # Standard formula: liq_price = entry * (1 ± (initial_margin - maintenance_margin))
                # Long: liq = entry * (1 - (1/leverage - maintenance_margin_rate))
                # Short: liq = entry * (1 + (1/leverage - maintenance_margin_rate))
                margin_diff = initial_margin_rate - maintenance_margin_rate

                if side == "long":
                    liquidation_price = entry_price * (1 - margin_diff)
                    distance_pct = round((entry_price - liquidation_price) / entry_price * 100, 3)
                    direction = "price_drops_by"
                else:
                    liquidation_price = entry_price * (1 + margin_diff)
                    distance_pct = round((liquidation_price - entry_price) / entry_price * 100, 3)
                    direction = "price_rises_by"

                liquidation_price = round(liquidation_price, 6)

                # Safety buffer: suggest stop-loss at 50% of distance to liq
                safe_stop_pct = distance_pct * 0.5
                if side == "long":
                    suggested_stop = round(entry_price * (1 - safe_stop_pct / 100), 6)
                else:
                    suggested_stop = round(entry_price * (1 + safe_stop_pct / 100), 6)

                risk_level = (
                    "extreme" if distance_pct < 5
                    else "high" if distance_pct < 15
                    else "moderate" if distance_pct < 30
                    else "low"
                )

                scenarios.append({
                    "leverage": leverage,
                    "liquidation_price": liquidation_price,
                    "distance_to_liquidation_pct": distance_pct,
                    "distance_direction": direction,
                    "risk_level": risk_level,
                    "suggested_stop_loss": suggested_stop,
                })

            max_liq = max(scenarios, key=lambda s: s["distance_to_liquidation_pct"])
            return json.dumps({
                "entry_price": entry_price,
                "position_side": side,
                "maintenance_margin_rate": maintenance_margin_rate,
                "scenarios": scenarios,
                "warning": (
                    f"Highest leverage evaluated ({max_liq['leverage']}x): a "
                    f"{max_liq['distance_to_liquidation_pct']:.1f}% adverse move triggers "
                    f"liquidation. Stop-loss at {max_liq['suggested_stop_loss']} is recommended."
                ),
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Historical VaR / CVaR Tool
# ---------------------------------------------------------------------------

class VaRInput(BaseModel):
    coin_id: str = Field(..., description="CoinGecko coin ID (e.g. 'bitcoin').")
    vs_currency: str = Field(default="usd", description="Quote currency.")
    days: int = Field(default=90, ge=30, le=365, description="Days of price history.")
    confidence_level: float = Field(
        default=0.95,
        ge=0.90,
        le=0.99,
        description="Confidence level for VaR (e.g. 0.95 = 95%).",
    )
    position_size: float = Field(
        default=10000.0,
        gt=0,
        description="Hypothetical position size for risk figures in the base currency.",
    )


class PortfolioVaRTool(BaseTool):
    name: str = "portfolio_var"
    description: str = (
        "Computes Historical Value at Risk (VaR) and Conditional VaR (CVaR / Expected Shortfall) "
        "for a cryptocurrency position. VaR estimates the maximum expected loss at a given "
        "confidence level over a 1-day horizon. CVaR shows the average loss beyond VaR, "
        "capturing tail risk. Returns both percentage and dollar figures."
    )
    args_schema: Type[BaseModel] = VaRInput

    def _run(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 90,
        confidence_level: float = 0.95,
        position_size: float = 10000.0,
    ) -> str:
        try:
            closes = _fetch_daily_closes(coin_id, vs_currency, days)
            if len(closes) < 10:
                return json.dumps({"error": "Insufficient price data."})

            # Daily log returns
            log_returns = np.diff(np.log(closes))
            n = len(log_returns)

            # Historical VaR: percentile of loss distribution
            sorted_returns = np.sort(log_returns)
            var_idx = int(np.floor((1 - confidence_level) * n))
            var_idx = max(0, var_idx)
            var_return = float(sorted_returns[var_idx])  # negative = loss
            cvar_return = float(np.mean(sorted_returns[:var_idx + 1]))  # avg of tail

            var_pct = round(abs(var_return) * 100, 3)
            cvar_pct = round(abs(cvar_return) * 100, 3)
            var_amount = round(position_size * abs(var_return), 2)
            cvar_amount = round(position_size * abs(cvar_return), 2)

            # Annualised volatility
            ann_vol = round(float(np.std(log_returns, ddof=1)) * math.sqrt(365) * 100, 2)

            # Max drawdown
            cumulative = np.exp(np.cumsum(log_returns))
            running_max = np.maximum.accumulate(cumulative)
            drawdowns = (cumulative - running_max) / running_max
            max_drawdown_pct = round(float(np.min(drawdowns)) * 100, 2)

            return json.dumps({
                "coin": coin_id,
                "vs_currency": vs_currency,
                "days_analysed": n,
                "confidence_level": confidence_level,
                "position_size": position_size,
                "historical_var": {
                    "daily_loss_pct": var_pct,
                    "daily_loss": var_amount,
                    "interpretation": f"With {confidence_level*100:.0f}% confidence, max 1-day loss ≤ {var_pct}% ({var_amount:,.2f})",
                },
                "cvar_expected_shortfall": {
                    "daily_loss_pct": cvar_pct,
                    "daily_loss": cvar_amount,
                    "interpretation": f"In the worst {(1-confidence_level)*100:.0f}% of days, average loss is {cvar_pct}% ({cvar_amount:,.2f})",
                },
                "annualised_volatility_pct": ann_vol,
                "max_drawdown_pct": max_drawdown_pct,
                "risk_tier": (
                    "very_high" if ann_vol > 150
                    else "high" if ann_vol > 80
                    else "moderate" if ann_vol > 40
                    else "low"
                ),
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Asset Correlation Tool
# ---------------------------------------------------------------------------

class CorrelationInput(BaseModel):
    coin_id_a: str = Field(..., description="First CoinGecko coin ID (e.g. 'bitcoin').")
    coin_id_b: str = Field(..., description="Second CoinGecko coin ID (e.g. 'ethereum').")
    vs_currency: str = Field(default="usd", description="Quote currency.")
    days: int = Field(default=90, ge=14, le=365, description="Days of price history.")


class AssetCorrelationTool(BaseTool):
    name: str = "asset_correlation"
    description: str = (
        "Computes Pearson correlation between daily returns of two cryptocurrencies. "
        "Correlation near +1 means assets move together (no diversification). "
        "Near 0 means independent; near -1 means inverse. Essential for portfolio "
        "construction and avoiding concentrated correlated risk."
    )
    args_schema: Type[BaseModel] = CorrelationInput

    def _run(
        self,
        coin_id_a: str,
        coin_id_b: str,
        vs_currency: str = "usd",
        days: int = 90,
    ) -> str:
        try:
            closes_a = _fetch_daily_closes(coin_id_a, vs_currency, days)
            closes_b = _fetch_daily_closes(coin_id_b, vs_currency, days)

            # Align series length
            min_len = min(len(closes_a), len(closes_b))
            if min_len < 10:
                return json.dumps({"error": "Not enough overlapping data points."})

            returns_a = np.diff(np.log(closes_a[-min_len:]))
            returns_b = np.diff(np.log(closes_b[-min_len:]))

            correlation = float(np.corrcoef(returns_a, returns_b)[0, 1])
            correlation = round(correlation, 4)

            if abs(correlation) > 0.85:
                relationship = "highly_correlated"
                diversification = "poor"
            elif abs(correlation) > 0.5:
                relationship = "moderately_correlated"
                diversification = "partial"
            elif abs(correlation) > 0.2:
                relationship = "weakly_correlated"
                diversification = "good"
            else:
                relationship = "uncorrelated"
                diversification = "excellent"

            if correlation < -0.5:
                relationship = "negatively_correlated"
                diversification = "excellent_hedge"

            return json.dumps({
                "coin_a": coin_id_a,
                "coin_b": coin_id_b,
                "vs_currency": vs_currency,
                "days": days,
                "pearson_correlation": correlation,
                "relationship": relationship,
                "diversification_benefit": diversification,
                "interpretation": (
                    f"{coin_id_a} and {coin_id_b} have a {correlation:.2f} correlation over {days} days. "
                    f"Diversification benefit: {diversification}."
                ),
            })
        except requests.RequestException as exc:
            return json.dumps({"error": f"API request failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})
