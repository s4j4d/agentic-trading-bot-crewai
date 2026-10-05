"""Portfolio tools for the portfolio_manager agent.

Deterministic, no network, no LLM:
- PortfolioExposureTool    -- exposure math + cap-breach flags
- RebalanceAllocatorTool   -- cap-aware target allocation weighted by score
- RiskLevelsTool           -- ATR-based stop-loss / take-profit price levels

All amounts are in the account's base currency (USD, toman, EUR, ...).
The tools do no conversion -- they treat numbers as plain amounts.
``base_currency`` is a display label only, echoed back so the agent
can name the currency in its reasons.
"""

from __future__ import annotations

import json
from typing import Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _safe_amount(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _coin_id_of(entry: Any) -> str:
    if isinstance(entry, BaseModel):
        data = entry.model_dump()
    elif isinstance(entry, dict):
        data = entry
    else:
        return ""
    for key in ("coin_id", "coingecko_id", "coin", "id"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _position_of(entry: Any) -> float:
    """Extract a monetary amount from a position dict/model.

    Accepts both the new canonical keys (position, current, target, size,
    value) and the legacy *_usd keys so old payloads still parse.
    """
    if isinstance(entry, BaseModel):
        data = entry.model_dump()
    elif isinstance(entry, dict):
        data = entry
    else:
        return 0.0
    for key in (
        "position", "current", "target", "size", "value",
        "position_usd", "current_usd", "target_usd", "size_usd", "value_usd",
    ):
        if data.get(key) is not None:
            return _safe_amount(data.get(key))
    return 0.0


# ---------------------------------------------------------------------------
# Portfolio exposure
# ---------------------------------------------------------------------------

class ExposurePosition(BaseModel):
    coin_id: str = ""
    position: float = 0.0

    model_config = {"extra": "ignore"}


class PortfolioExposureInput(BaseModel):
    positions: list[ExposurePosition] = Field(
        default_factory=list,
        description="Open paper positions as [{coin_id, position}].",
    )
    account_size: float = Field(..., gt=0, description="Total account size.")
    max_total_exposure_pct: float = Field(
        default=60.0, ge=1, le=100,
        description="Max total invested across all positions as % of account.",
    )
    max_single_position_pct: float = Field(
        default=20.0, ge=1, le=100,
        description="Max single-coin position as % of account.",
    )
    base_currency: str = Field(
        default="usd",
        description="Display label for the account currency (e.g. usd, toman).",
    )


class PortfolioExposureTool(BaseTool):
    name: str = "portfolio_exposure"
    description: str = (
        "Computes total and per-coin portfolio exposure from open paper "
        "positions. Flags total-cap breaches and coins over the single-position "
        "cap. Pure math, no network calls."
    )
    args_schema: Type[BaseModel] = PortfolioExposureInput

    def _run(
        self,
        positions: list[dict[str, Any]] | list[ExposurePosition] | None = None,
        account_size: float = 10_000.0,
        max_total_exposure_pct: float = 60.0,
        max_single_position_pct: float = 20.0,
        base_currency: str = "usd",
    ) -> str:
        try:
            if account_size <= 0:
                return json.dumps({"error": "account_size must be positive."})
            rows: list[dict[str, Any]] = []
            total = 0.0
            for entry in positions or []:
                coin = _coin_id_of(entry)
                amount = round(_position_of(entry), 2)
                if not coin:
                    continue
                total += amount
                rows.append({"coin_id": coin, "position": amount})

            total = round(total, 2)
            total_pct = round(total / account_size * 100, 2) if account_size else 0.0
            for row in rows:
                row["exposure_pct"] = (
                    round(row["position"] / account_size * 100, 2)
                    if account_size else 0.0
                )

            single_cap = account_size * max_single_position_pct / 100
            over_single = sorted(
                row["coin_id"] for row in rows if row["position"] > single_cap
            )
            return json.dumps({
                "account_size": account_size,
                "base_currency": base_currency,
                "total_position": total,
                "total_exposure_pct": total_pct,
                "cash_remaining": round(account_size - total, 2),
                "positions": rows,
                "breaches": {
                    "total_breached": total_pct > max_total_exposure_pct,
                    "total_exposure_pct": total_pct,
                    "max_total_exposure_pct": max_total_exposure_pct,
                    "coins_over_single_cap": over_single,
                    "max_single_position_pct": max_single_position_pct,
                },
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Rebalance allocator
# ---------------------------------------------------------------------------

class AllocatorOpportunity(BaseModel):
    coin_id: str = ""
    score: float = 50.0

    model_config = {"extra": "ignore"}


class RebalanceAllocatorInput(BaseModel):
    opportunities: list[AllocatorOpportunity] = Field(
        default_factory=list,
        description="Ranked opportunities as [{coin_id, score}]. Score 0-100.",
    )
    positions: list[ExposurePosition] = Field(
        default_factory=list,
        description="Current open positions as [{coin_id, position}].",
    )
    account_size: float = Field(..., gt=0, description="Total account size.")
    max_total_exposure_pct: float = Field(default=60.0, ge=1, le=100)
    max_single_position_pct: float = Field(default=20.0, ge=1, le=100)
    min_trade: float = Field(
        default=10.0, ge=0,
        description="Deltas under this amount become hold.",
    )
    base_currency: str = Field(
        default="usd",
        description="Display label for the account currency (e.g. usd, toman).",
    )


class RebalanceAllocatorTool(BaseTool):
    name: str = "rebalance_allocator"
    description: str = (
        "Produces cap-aware target allocations weighted by opportunity "
        "score. Total targets never exceed max_total_exposure_pct of the "
        "account and no single coin exceeds max_single_position_pct. "
        "Pure math, no network calls."
    )
    args_schema: Type[BaseModel] = RebalanceAllocatorInput

    def _run(
        self,
        opportunities: list[dict[str, Any]] | list[AllocatorOpportunity] | None = None,
        positions: list[dict[str, Any]] | list[ExposurePosition] | None = None,
        account_size: float = 10_000.0,
        max_total_exposure_pct: float = 60.0,
        max_single_position_pct: float = 20.0,
        min_trade: float = 10.0,
        base_currency: str = "usd",
    ) -> str:
        try:
            if account_size <= 0:
                return json.dumps({"error": "account_size must be positive."})

            # Normalise opportunities: keep entries with a coin_id, default score 50.
            cands: list[dict[str, Any]] = []
            for entry in opportunities or []:
                if isinstance(entry, BaseModel):
                    data = entry.model_dump()
                elif isinstance(entry, dict):
                    data = dict(entry)
                else:
                    continue
                coin = data.get("coin_id") or data.get("coingecko_id") or ""
                if not isinstance(coin, str) or not coin.strip():
                    continue
                try:
                    score = float(data.get("score", 50.0))
                except (TypeError, ValueError):
                    score = 50.0
                score = max(0.0, score)
                cands.append({"coin_id": coin.strip(), "score": score})
            # Highest score first for deterministic rounding-drift assignment.
            cands.sort(key=lambda c: c["score"], reverse=True)

            current: dict[str, float] = {}
            for entry in positions or []:
                coin = _coin_id_of(entry)
                if coin:
                    current[coin] = round(current.get(coin, 0.0) + _position_of(entry), 2)

            total_budget = round(account_size * max_total_exposure_pct / 100, 2)
            single_cap = round(account_size * max_single_position_pct / 100, 2)

            targets: dict[str, float] = {}
            if cands:
                weights: dict[str, float] = {}
                score_sum = sum(c["score"] for c in cands)
                if score_sum > 0:
                    for c in cands:
                        weights[c["coin_id"]] = c["score"] / score_sum
                else:
                    equal = 1.0 / len(cands)
                    for c in cands:
                        weights[c["coin_id"]] = equal
                # Initial pro-rata targets, hard-capped per coin.
                uncapped = set(weights)
                for coin, w in weights.items():
                    targets[coin] = min(round(w * total_budget, 2), single_cap)
                # Redistribute leftover budget to uncapped coins (cap-aware).
                for _ in range(len(cands) + 1):
                    allocated = round(sum(targets.values()), 2)
                    leftover = round(total_budget - allocated, 2)
                    if leftover <= 0.01:
                        break
                    eligible = [c for c in cands if targets[c["coin_id"]] < single_cap - 0.005]
                    if not eligible:
                        break
                    w_sum = sum(weights[c["coin_id"]] for c in eligible)
                    if w_sum <= 0:
                        break
                    moved = False
                    for c in eligible:
                        coin = c["coin_id"]
                        add = min(
                            round(leftover * weights[coin] / w_sum, 2),
                            round(single_cap - targets[coin], 2),
                        )
                        if add > 0:
                            targets[coin] = round(targets[coin] + add, 2)
                            moved = True
                    if not moved:
                        break
                # Fix penny rounding drift on the largest target.
                drift = round(total_budget - sum(targets.values()), 2)
                if cands and abs(drift) >= 0.01 and abs(drift) <= len(cands) * 0.05:
                    top = cands[0]["coin_id"]
                    candidate = round(targets[top] + drift, 2)
                    if 0 <= candidate <= single_cap:
                        targets[top] = candidate

            allocations: list[dict[str, Any]] = []
            score_by_coin = {c["coin_id"]: c["score"] for c in cands}
            for coin in list(targets.keys()) + [k for k in current if k not in targets]:
                target = round(targets.get(coin, 0.0), 2)
                cur = round(current.get(coin, 0.0), 2)
                delta = round(target - cur, 2)
                if cur <= 0 and target > 0:
                    action = "open"
                elif target <= 0 and cur > 0:
                    action = "close"
                elif delta > min_trade:
                    action = "increase"
                elif delta < -min_trade:
                    action = "decrease"
                else:
                    action = "hold"
                    target = cur  # avoid dust trades
                    delta = 0.0
                allocations.append({
                    "coin_id": coin,
                    "score": score_by_coin.get(coin, 0.0),
                    "current": cur,
                    "target": target,
                    "target_pct": round(target / account_size * 100, 2),
                    "delta": delta,
                    "action": action,
                })
            allocations.sort(key=lambda a: a["target"], reverse=True)

            total_target = round(sum(a["target"] for a in allocations), 2)
            return json.dumps({
                "account_size": account_size,
                "base_currency": base_currency,
                "max_total_exposure_pct": max_total_exposure_pct,
                "max_single_position_pct": max_single_position_pct,
                "total_budget": total_budget,
                "single_cap": single_cap,
                "total_target": total_target,
                "total_exposure_pct": round(total_target / account_size * 100, 2),
                "unallocated": round(total_budget - total_target, 2),
                "allocations": allocations,
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Risk levels (stop-loss / take-profit)
# ---------------------------------------------------------------------------

class RiskLevelsInput(BaseModel):
    coin_id: str = Field(..., description="CoinGecko coin ID.")
    current_price: float = Field(..., gt=0, description="Current price in vs currency.")
    atr_pct: float = Field(..., description="ATR as % of price (from atr_indicator).")
    action: str = Field(
        default="hold",
        description="Portfolio action (open/increase/decrease/close/hold). Levels are identical for all actions (long spot).",
    )


class RiskLevelsTool(BaseTool):
    name: str = "risk_levels"
    description: str = (
        "Computes deterministic stop-loss / take-profit price levels from ATR. "
        "Stop is 0.5x ATR below price, take-profit is 1x ATR above (2:1 reward-risk). "
        "Pure math, no network calls. Close actions get identical levels."
    )
    args_schema: Type[BaseModel] = RiskLevelsInput

    def _run(
        self,
        coin_id: str = "",
        current_price: float = 0.0,
        atr_pct: float = 0.0,
        action: str = "hold",
    ) -> str:
        try:
            price = _safe_amount(current_price)
            atr = _safe_amount(atr_pct)
            if price <= 0 or atr <= 0:
                return json.dumps({
                    "coin_id": coin_id,
                    "current_price": price,
                    "atr_pct": atr,
                    "stop_loss": None,
                    "take_profit": None,
                    "reward_risk": 2.0,
                })
            # Floor tiny ATR at 1% so stablecoins still get a guardrail.
            eff_pct = max(atr, 1.0)
            stop = round(price * (1 - 0.5 * eff_pct / 100), 6)
            take = round(price * (1 + eff_pct / 100), 6)
            return json.dumps({
                "coin_id": coin_id,
                "current_price": price,
                "atr_pct": atr,
                "stop_loss": stop,
                "take_profit": take,
                "reward_risk": 2.0,
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})
