"""Phase 6 — safety and go-live controls for the execution layer.

Two deterministic guards live here, both free of network and LLM calls so
they can be unit-tested offline exactly like the rest of the flow:

* **Pre-order capital caps.** The allocator shapes the plan, but a plan is
  priced on the *last known* price while an order is filled at the
  *current* price, and the two can drift enough to breach exposure.
  ``enforce_exposure_caps`` re-checks every order at fill time against
  what the wallet *holds* and the current price, and downsizes/rejects
  it. Free cash is deliberately not modelled: the venue rejects an order
  bigger than the spendable balance, so affordability is the venue's
  job while the caps are this guard's.
* **Daily realized-loss circuit breaker.** ``DailyLossBreaker`` accumulates
  realized P&L per UTC day (from the reconciled ledger) and refuses new
  risk once the day's loss passes ``EXECUTION_MAX_DAILY_LOSS_PCT`` of the
  account. It fails *closed* (raises
  :class:`~crypto_council_flow.tools.execution_base.ExecutionHalted`), never
  falling back to paper — a circuit breaker that demotes you to simulated
  profits is worse than no breaker.

Both take normalized inputs, so nothing venue-specific leaks in and no
client knows about them: apply the guards from ``NobitexExecutionClient``
(or any future client) by calling them with plain floats/dicts.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Iterable

from .execution_base import ExecutionHalted

__all__ = [
    "DEFAULT_MAX_DAILY_LOSS_PCT",
    "CircuitBreakerOpen",
    "DailyLossBreaker",
    "allow_close",
    "breakers_from_env",
    "enforce_exposure_caps",
    "exposure_limit",
]

# The breaker's default budget. Set to 0 it refuses *every* entry (useful
# for a dry "never open anything new" posture); negative disables it.
DEFAULT_MAX_DAILY_LOSS_PCT = 5.0


class CircuitBreakerOpen(ExecutionHalted):
    """Raised when the daily realized-loss limit has been breached.

    Subclasses ``ExecutionHalted`` so existing kill-switch handling catches
    it too: from a caller's perspective both mean "do not trade right now".
    """


def _env_float(name: str, default: float | None) -> float | None:
    """Read an optional float env var; blank/invalid means ``default``."""
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val


def exposure_limit(account_size: float, pct: float | None) -> float:
    """Quote-currency cap implied by ``pct`` of ``account_size``.

    ``pct`` is a *fraction* of the account (0.05 = 5%); ``None`` or a
    negative ``pct`` means uncapped. Returns 0.0 for a non-positive
    account so a misconfigured size can never authorize more risk.

    Raises :class:`ValueError` for ``pct > 1.0``: a fraction above 100%
    of the account is a units error (a percent written where a fraction
    belongs, e.g. ``30`` for 30%), and silently accepting it would hand
    back an inert cap of 3000% — the guard would appear armed while
    authorizing unlimited risk. Fail loudly instead.
    """
    try:
        account = float(account_size or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(account) or account <= 0 or pct is None or pct < 0:
        return 0.0
    if pct > 1.0:
        raise ValueError(
            f"cap {pct!r} is not a fraction of the account (0.05 = 5%); "
            "a value above 1.0 looks like a percent written where a "
            "fraction belongs — refusing to arm an inert cap"
        )
    return account * pct


def allow_close(side: Any) -> bool:
    """True when an order only *reduces* risk (never blocked by caps).

    Closes are how a position gets out of trouble, so the exposure caps
    must never block them — the live client enforces the same rule.
    """
    return str(side or "").lower() == "sell"


def enforce_exposure_caps(
    order: dict[str, Any],
    *,
    account_size: float,
    holdings: dict[str, float] | None = None,
    prices: dict[str, float] | None = None,
    max_total_pct: float | None,
    max_single_pct: float | None,
    positions: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Re-check one pending order against capital caps. Pure, deterministic.

    ``order`` is a plain dict with at least ``symbol``, ``side`` and
    ``qty``/``notional`` (the normalized shape the clients already use).
    ``holdings`` maps upper-cased base asset -> currently held quantity and
    ``prices`` the same asset -> current price; either may be empty, in
    which case the pre-trade wallet figures in ``positions`` (list of
    ``{asset, qty, price, market_value}``) are used instead.

    The order is amended in place where it is safe to do so and a verdict
    is returned::

        {"action": "allow" | "downsize" | "block",
         "reason": str,
         "qty": float,            # resulting (possibly reduced) qty
         "notional": float,
         "order": order}

    A ``sell`` (close) is only ever rejected for ordering the venue to sell
    more than the wallet holds — exposure caps never block risk *reduction*.
    The accountable single-position cap is therefore applied to the
    post-trade exposure, which is what a cap on "one position" means.

    ``qty`` in the verdict is the size actually to be placed (0 on a
    block). ``notional`` is the *post-trade* exposure this verdict
    accounts for: for a buy that includes what is already held, and for a
    sell the value being removed. It is a diagnostic for the operator,
    not a restatement of ``qty * price``.
    """
    side = str(order.get("side") or "").lower()
    symbol = str(order.get("symbol") or "")
    base = symbol.split("-")[0].split("/")[0].strip().upper()
    qty = order.get("qty")
    try:
        qty = float(qty or 0.0)
    except (TypeError, ValueError):
        qty = 0.0
    notional_in = order.get("notional")
    try:
        notional_in = float(notional_in or 0.0)
    except (TypeError, ValueError):
        notional_in = 0.0

    holdings = dict(holdings or {})
    prices = dict(prices or {})
    positions = list(positions or [])
    held_qty = 0.0
    price = 0.0
    if base:
        held_qty = holdings.get(base, 0.0)
        price = prices.get(base, 0.0)
    if not price and positions:
        for p in positions:
            asset = str(p.get("asset") or "").split("-")[0].split("/")[0].strip().upper()
            if asset and asset == base:
                held_qty = float(p.get("qty") or 0.0)
                price = float(p.get("price") or 0.0)
                break

    # Prefer the caller's price; a notional-only order (no qty) is sized by
    # it. With neither we cannot size anything safely.
    if price <= 0 and qty > 0:
        notional = qty * (prices.get(base, 0.0) if base in prices else 0.0)
    else:
        notional = notional_in if notional_in > 0 else qty * price
    if notional <= 0:
        if qty <= 0:
            return {
                "action": "block", "reason": "order has no size to place",
                "qty": 0.0, "notional": 0.0, "order": order,
            }
        notional = qty * price if price > 0 else 0.0
        if notional <= 0:
            # An unpriced asset cannot be safety-checked, and the guard's job
            # is to keep bookings *provably* within limits — an asset whose
            # capacity to hold the account cannot be measured would
            # be allowed through by skipping the cap check.
            return {
                "action": "block",
                "reason": (f"no price for {base or symbol} — cannot check "
                           "exposure against the caps"),
                "qty": 0.0, "notional": 0.0, "order": order,
            }

    total_cap = exposure_limit(account_size, max_total_pct)
    single_cap = exposure_limit(account_size, max_single_pct)
    other_holdings = sum(
        float(p.get("market_value") or 0.0)
        for p in positions
        if str(p.get("asset") or "").split("-")[0].split("/")[0].strip().upper() != base
    ) if positions else 0.0

    if allow_close(side):
        # Risk-reducing: never cap. Only the venue's own solvency matters.
        if qty > held_qty > 0:
            return {
                "action": "block",
                "reason": (f"sell qty {qty:g} exceeds holding {held_qty:g} "
                           f"on {base or symbol}"),
                "qty": 0.0, "notional": 0.0, "order": order,
            }
        return {
            "action": "allow",
            "reason": "risk-reducing order (sell) — caps do not apply",
            "qty": qty, "notional": notional, "order": order,
        }

    # A buy is always *adding* risk; an existing position only makes it worse.
    # The caps below apply to the post-trade exposure, which is what a cap on
    # "one position" means.
    held_notional = held_qty * price
    notional += held_notional

    # -- exposure caps, computed post-trade ------------------------------
    post_total = other_holdings + notional
    post_single = notional
    if single_cap > 0 and post_single > single_cap + 1e-9:
        return {
            "action": "block",
            "reason": (f"position value {post_single:,.2f} exceeds the "
                       f"{max_single_pct:.1%} single-position cap "
                       f"({single_cap:,.2f})"),
            "qty": 0.0, "notional": notional, "order": order,
        }
    if total_cap > 0:
        room = total_cap - post_total
        if room < -1e-9:
            return {
                "action": "block",
                "reason": (f"total exposure {post_total:,.2f} exceeds the "
                           f"{max_total_pct:.1%} cap ({total_cap:,.2f})"),
                "qty": 0.0, "notional": notional, "order": order,
            }
        if notional > room + 1e-9:
            shrunk = room / price if price > 0 else 0.0
            if shrunk <= 0:
                return {
                    "action": "block",
                    "reason": "no exposure room left under the total cap",
                    "qty": 0.0, "notional": notional, "order": order,
                }
            return {
                "action": "downsize",
                "reason": (f"downsized to the remaining "
                           f"{max_total_pct:.1%} exposure room ({room:,.2f})"),
                "qty": round(shrunk, 10),
                "notional": room, "order": order,
            }

    return {
        "action": "allow",
        "reason": "within capital caps",
        "qty": qty, "notional": notional, "order": order,
    }


def _day_key(now_iso: str | None = None) -> str:
    """UTC calendar day of ``now_iso`` (or now), e.g. ``2026-01-01``.

    The breaker resets on the *UTC day*, which keeps it deterministic and
    timezone-free; a local-midnight reset would silently tie the
    bot's risk budget to wherever the host happens to run.
    """
    if now_iso:
        try:
            dt = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).date().isoformat()


class DailyLossBreaker:
    """Per-UTC-day realized-loss circuit breaker.

    Fail-closed: once today's realized loss reaches the limit,
    :meth:`guard` raises :class:`CircuitBreakerOpen` instead of letting an
    order through. De-risking (sells/closes) stays allowed so positions can
    still be unwound — an open circuit must not trap a losing position.

    Realized P&L is *absolute* quote currency, summed from ledger rows with
    a ``realized_pnl`` value; ``limit`` is an absolute amount derived from
    ``max_daily_loss_pct`` *percent* of the account at construction (5 = 5%
    of the account, so a 10,000 account breaks at a 500 loss).
    """

    def __init__(
        self,
        account_size: float,
        max_daily_loss_pct: float | None = None,
        *,
        ledger: Iterable[dict[str, Any]] | None = None,
        now_iso: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        pct_raw = (
            max_daily_loss_pct
            if max_daily_loss_pct is not None
            else _env_float("EXECUTION_MAX_DAILY_LOSS_PCT", DEFAULT_MAX_DAILY_LOSS_PCT)
        )
        self.account_size = _to_float(account_size)
        # ``max_daily_loss_pct`` is a *percentage* (5 = 5%), matching the env
        # knob and the main.py flags, while ``enforce_exposure_caps`` takes
        # fractions. Convert once here so the two never get mixed up.
        self.max_daily_loss_pct = _to_float(
            pct_raw if pct_raw is not None else -1.0, -1.0
        )
        if pct_raw is not None and pct_raw >= 0:
            self.limit = self.account_size * (pct_raw / 100.0)
        else:
            self.limit = 0.0
        self._today = _day_key(now_iso)
        self._realized_today = 0.0
        self._rows_today = 0

        persisted = dict(state or {})
        if persisted.get("day") == self._today:
            self._realized_today = _to_float(persisted.get("realized_today"), 0.0)
            self._rows_today = int(persisted.get("rows_today") or 0)
        for row in ledger or []:
            self.record(row, now_iso=now_iso)

    # -- state -------------------------------------------------------------

    def is_armed(self) -> bool:
        """False only when the limit was explicitly non-positive/disabled."""
        return self.max_daily_loss_pct is not None and self.max_daily_loss_pct >= 0

    def to_state(self) -> dict[str, Any]:
        return {
            "day": self._today,
            "realized_today": round(self._realized_today, 10),
            "rows_today": self._rows_today,
            "limit": round(self.limit, 10),
            "max_daily_loss_pct": self.max_daily_loss_pct,
        }

    def remaining(self) -> float:
        """Quote-currency loss still affordable today (can be negative)."""
        return self.limit + self._realized_today if self.is_armed() else float("inf")

    # -- internals ---------------------------------------------------------

    def _roll(self, now_iso: str | None) -> None:
        """Reset the running total when the UTC day turns over."""
        day = _day_key(now_iso)
        if day != self._today:
            self._today = day
            self._realized_today = 0.0
            self._rows_today = 0

    def record(self, row: dict[str, Any], *, now_iso: str | None = None) -> float:
        """Accumulate one ledger row's realized P&L into today's total.

        Accepts rows from either ledger; ````side == "close"``` etc. matter
        only in that they carry a ``realized_pnl``. A row from a previous
        day is ignored so a replay of the whole ledger never double counts.
        """
        if not isinstance(row, dict):
            return self._realized_today
        value = row.get("realized_pnl")
        if value is None:
            return self._realized_today
        self._roll(now_iso)
        row_day = _day_key(row.get("ts"))
        if row_day != self._today:
            return self._realized_today
        pnl = _to_float(value, 0.0)
        self._realized_today += pnl
        self._rows_today += 1
        return self._realized_today

    def guard(self, order: dict[str, Any] | None = None, *, now_iso: str | None = None) -> None:
        """Refuse new risk once the daily loss limit is breached.

        Raises :class:`CircuitBreakerOpen`. Risk-*reducing* orders (sells)
        always pass: the breaker limits new risk, it never traps a position.
        """
        if not self.is_armed():
            return
        self._roll(now_iso)
        if order is not None and allow_close(order.get("side")):
            return
        if self._realized_today <= -self.limit:
            raise CircuitBreakerOpen(
                "daily realized loss {loss:,.2f} reached the {pct}% limit "
                "({limit:,.2f}) — refusing new orders until the next UTC day "
                "or a manual reset".format(
                    loss=-self._realized_today,
                    pct=format(self.max_daily_loss_pct, "g"),
                    limit=self.limit,
                )
            )


def breakers_from_env() -> dict[str, Any]:
    """Read the Phase 6 knobs that exist in the code (never invent any)."""
    return {
        "max_daily_loss_pct": _env_float(
            "EXECUTION_MAX_DAILY_LOSS_PCT", DEFAULT_MAX_DAILY_LOSS_PCT
        ),
    }


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
