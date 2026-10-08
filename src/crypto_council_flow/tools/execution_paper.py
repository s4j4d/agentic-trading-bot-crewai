"""
Paper (simulated) execution client.

The paper ledger predates the execution abstraction: ``pipeline.py`` used to
own ``_update_paper_ledger()`` directly. Phase 2 of the paper->live plan moves
that logic here so paper trading becomes just another ``ExecutionClient``
implementation, selectable via ``EXECUTION_MODE=paper`` (the default).

Behaviour is intentionally identical to the original function:
  * ``target`` is a notional amount in the base currency, re-derived to
    quantity as ``target / price`` every cycle (positions rebalance toward
    the target rather than accumulating).
  * ``equity = account_size + realized + unrealized`` (cash is
    ``account_size - invested``, so ``invested`` cancels).
  * ledger rows carry a positive ``qty``/``amount``; ``side`` carries
    direction.
  * a close with no price keeps the position so the next priced cycle can
    book it — never silently drops cost basis.
  * an action with ``target <= 0`` never runs through ``setdefault`` (no
    phantom ``{qty: 0}`` rows).

The one addition is optional simulated fees via ``PAPER_FEE_BPS`` (default
``0`` = off, byte-identical to the old behaviour). With fees on, buys fold the
fee into the cost basis and sells subtract it from realized P&L, so a
paper-vs-live comparison during rollout is not apples-to-oranges against a
fee-free paper book.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crypto_council_flow.tools.execution_base import (
    Balance,
    ExecutionClient,
    ExecutionError,
    Fill,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    register_execution_client,
)

_DEFAULT_OUTPUT_DIR = Path("output")
_EPS = 1e-9


def _fee_bps_from_env() -> float:
    try:
        return max(0.0, float(os.getenv("PAPER_FEE_BPS", "0") or 0))
    except (TypeError, ValueError):
        return 0.0


def _iso_to_epoch_ms(value: Any) -> int | None:
    """Parse an ISO-8601 timestamp to epoch milliseconds (UTC).

    Returns ``None`` when the value is missing or unparseable so callers can
    fail open rather than drop a trade. Accepts both ``Z`` and ``+00:00``
    suffixes; naive timestamps are assumed UTC (the paper ledger writes UTC).
    """
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class PaperExecutionClient(ExecutionClient):
    """Simulated venue: fills plans locally at snapshot prices, no network.

    ``apply_plan`` is the primary entry point and reproduces the historical
    paper-ledger semantics exactly. The remaining ``ExecutionClient`` methods
    make the paper client a genuine drop-in for the interface so order
    translation (Phase 4) and reconciliation (Phase 5) can be exercised
    without touching a real exchange.
    """

    name = "paper"
    is_simulated = True

    def __init__(
        self,
        ledger_path: Path | str | None = None,
        positions_path: Path | str | None = None,
        quote_currency: str | None = None,
        fee_bps: float | None = None,
    ):
        quote = quote_currency or os.getenv("EXECUTION_QUOTE", "usd")
        super().__init__(quote_currency=quote)
        # Defaults mirror main.py's constants but are defined locally to avoid
        # a circular import; main.py passes its own (possibly monkeypatched)
        # paths in explicitly.
        self.ledger_path = Path(ledger_path or (_DEFAULT_OUTPUT_DIR / "paper_ledger.json"))
        self.positions_path = Path(
            positions_path or (_DEFAULT_OUTPUT_DIR / "paper_positions.json")
        )
        self.fee_bps = _fee_bps_from_env() if fee_bps is None else max(0.0, float(fee_bps))
        # Populated by apply_plan; lets the order-level methods price fills.
        self._prices: dict[str, float] = {}
        self._account_size: float = 0.0

    # -- persistence --------------------------------------------------------

    def _load_positions(self) -> dict[str, dict[str, float]]:
        if not self.positions_path.exists():
            return {}
        try:
            return json.loads(self.positions_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_ledger(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        try:
            return json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(
        self,
        positions: dict[str, dict[str, float]],
        ledger: list[dict[str, Any]],
    ) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.positions_path.write_text(
            json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- primary entry point ------------------------------------------------

    def apply_plan(
        self,
        plan: dict[str, Any],
        risk_snapshot: dict[str, dict[str, float]],
        account_size: float,
        base_currency: str,
        now_iso: str,
        cycle: int,
    ) -> dict[str, Any]:
        """Fill the plan against the paper ledger using current prices.

        Prices come from the risk snapshot (cached OHLC close) — zero new
        network calls. Positions are tracked as ``{coin_id: {qty, avg_cost}}``;
        trades append to the ledger. Returns a P&L summary dict.
        """
        positions = self._load_positions()
        ledger = self._load_ledger()
        self._account_size = float(account_size or 0.0)
        self._prices = {}
        for cid, snap in (risk_snapshot or {}).items():
            px_raw = (snap or {}).get("current_price")
            if px_raw:
                self._prices[cid] = float(px_raw)
        fee_rate = self.fee_bps / 10_000.0

        realized = 0.0
        trades: list[dict[str, Any]] = []
        # Close positions no longer in the plan at current price.
        plan_targets = {
            a.get("coin_id"): float(a.get("target", 0) or 0)
            for a in plan.get("actions", [])
        }
        for coin_id in list(positions.keys()):
            target = plan_targets.get(coin_id, 0.0)
            if target <= 0:
                pos = positions[coin_id]
                px = (risk_snapshot.get(coin_id) or {}).get("current_price")
                if not px:
                    # No price this cycle (429 / thin data). Realizing at cost
                    # is wrong, but DROPPING the position destroys cost basis
                    # and its P&L permanently -- the book silently shrinks and
                    # equity freezes. Keep the position so the next priced
                    # cycle closes it properly.
                    continue
                positions.pop(coin_id, None)
                if pos.get("qty"):
                    proceeds = pos["qty"] * px
                    cost = pos["qty"] * pos["avg_cost"]
                    fee = proceeds * fee_rate
                    realized += proceeds - cost - fee
                    row = {
                        "ts": now_iso, "cycle": cycle, "coin_id": coin_id,
                        "side": "close", "qty": pos["qty"], "price": px,
                        "amount": proceeds, "realized_pnl": proceeds - cost - fee,
                    }
                    if fee:
                        row["fee"] = fee
                    trades.append(row)
                else:
                    positions.pop(coin_id, None)
                    trades.append({
                        "ts": now_iso, "cycle": cycle, "coin_id": coin_id,
                        "side": "close", "qty": pos.get("qty", 0),
                        "price": px, "amount": 0.0, "realized_pnl": None,
                        "note": "zero-quantity position closed",
                    })

        # Apply plan deltas at current price.
        for a in plan.get("actions", []):
            coin_id = a.get("coin_id")
            if not coin_id:
                continue
            target = float(a.get("target", 0) or 0)
            px = (risk_snapshot.get(coin_id) or {}).get("current_price")
            if not px:
                continue
            qty_target = target / px
            # A plan action that targets 0 is a close, handled in the close
            # pass above; setdefault here would resurrect a phantom {qty: 0}
            # position that then leaks into every future cycle's book.
            if target <= 0:
                continue
            pos = positions.setdefault(coin_id, {"qty": 0.0, "avg_cost": 0.0})
            qty_now = pos["qty"]
            delta_qty = qty_target - qty_now
            if abs(delta_qty) * px < _EPS:
                continue
            side = "buy" if delta_qty > 0 else "sell"
            # Ledger rows record a positive traded size/amount on both sides
            # and `side` carries the direction. Selling used to store a
            # negative qty and amount, which made a profitable partial sell
            # realize the wrong sign of P&L downstream.
            traded_qty = abs(delta_qty)
            trade = {
                "ts": now_iso, "cycle": cycle, "coin_id": coin_id,
                "side": side, "qty": traded_qty, "price": px,
                "amount": traded_qty * px,
            }
            if side == "buy":
                new_qty = qty_now + delta_qty
                fee = traded_qty * px * fee_rate
                pos["avg_cost"] = (
                    (qty_now * pos["avg_cost"] + delta_qty * px + fee) / new_qty
                    if new_qty else 0.0
                )
                pos["qty"] = new_qty
                if fee:
                    trade["fee"] = fee
            else:
                fee = traded_qty * px * fee_rate
                trade["realized_pnl"] = traded_qty * (px - pos["avg_cost"]) - fee
                realized += trade["realized_pnl"]
                pos["qty"] = qty_now + delta_qty
                if fee:
                    trade["fee"] = fee
                if pos["qty"] <= _EPS:
                    positions.pop(coin_id, None)
            trades.append(trade)

        # Unrealized P&L on remaining positions.
        unrealized = 0.0
        open_positions: list[dict[str, Any]] = []
        for coin_id, pos in positions.items():
            px = (risk_snapshot.get(coin_id) or {}).get("current_price")
            if not px or not pos.get("qty"):
                continue
            market = pos["qty"] * px
            cost = pos["qty"] * pos["avg_cost"]
            upl = market - cost
            unrealized += upl
            open_positions.append({
                "coin_id": coin_id, "qty": pos["qty"], "avg_cost": pos["avg_cost"],
                "price": px, "market_value": market, "unrealized_pnl": upl,
            })

        ledger.extend(trades)
        self._save(positions, ledger)

        total_realized = sum(
            t.get("realized_pnl") or 0.0 for t in ledger if t.get("realized_pnl") is not None
        )
        invested = sum(p["market_value"] for p in open_positions)
        # Equity = cash + market value + P&L, and cash is (account_size -
        # invested), so `invested` cancels: equity = account_size + realized
        # + unrealized. Subtracting `invested` (the old formula) double-counted
        # the money sitting in open positions.
        equity = account_size + total_realized + unrealized
        return {
            "realized_pnl": total_realized,
            "unrealized_pnl": unrealized,
            "total_pnl": total_realized + unrealized,
            "equity": equity,
            "invested": invested,
            "open_positions": open_positions,
            "n_trades": len(ledger),
        }

    # -- ExecutionClient interface -----------------------------------------

    def _price_for_symbol(self, symbol: str) -> float | None:
        """Resolve a price from the last snapshot by an EXPLICIT coin_id match.

        The paper client has no symbol map of its own: coin_id -> venue symbol
        translation is Phase 4 (order translation). The snapshot is keyed by
        ``coin_id`` (e.g. ``bitcoin``), not by venue symbol (``BTCUSDT``), so
        only an exact case-insensitive coin_id match is honoured here. Prefix
        guessing used to be attempted and was unsound (``bitcoin`` never matches
        ``BTCUSDT`` in either direction); guessing silently priced the wrong
        asset, so it is deliberately removed. Callers that hold a venue symbol
        must pass ``limit_price`` explicitly, or go through the Phase 4 symbol
        map, rather than rely on this inferring one.
        """
        if not symbol:
            return None
        raw = symbol.strip()
        for coin_id, px in self._prices.items():
            if coin_id.casefold() == raw.casefold():
                return px
        return None

    def get_balances(self, assets: list[str] | None = None) -> list[Balance]:
        """Simulated balances: coin quantities plus quote-currency cash.

        Cash is ``account_size - invested``; ``account_size`` reflects the last
        ``apply_plan`` call (0 before any plan has been applied).
        """
        positions = self._load_positions()
        wanted = {a.upper() for a in assets} if assets else None
        out: list[Balance] = []
        invested = 0.0
        for coin_id, pos in positions.items():
            qty = float(pos.get("qty") or 0.0)
            if qty <= 0:
                continue
            px = self._prices.get(coin_id) or float(pos.get("avg_cost") or 0.0)
            invested += qty * px
            asset = coin_id.upper()
            if wanted and asset not in wanted:
                continue
            out.append(Balance(asset=asset, free=qty, locked=0.0, total=qty))
        cash = max(0.0, self._account_size - invested)
        quote_asset = self.quote_currency.upper()
        if not wanted or quote_asset in wanted:
            out.append(Balance(asset=quote_asset, free=cash, locked=0.0, total=cash))
        return out

    def place_order(self, request: OrderRequest) -> OrderResult:
        """Simulate an immediate full fill at the last snapshot price.

        Paper has no order book, so every order fills instantly. This exists so
        order translation can be exercised against the interface without a live
        venue; it does NOT mutate the ledger (``apply_plan`` owns that).
        """
        px = request.limit_price or self._price_for_symbol(request.symbol)
        if not px:
            raise ExecutionError(
                f"paper client has no price for {request.symbol!r}; pass an "
                "explicit limit_price or apply a plan whose risk snapshot is "
                "keyed by this coin_id first",
                exchange=self.name,
            )
        qty = request.qty
        if qty is None:
            qty = float(request.notional or 0.0) / px
        return OrderResult(
            # Unique per call: two fills for the same symbol at the same price
            # used to collide (order_id embedded only symbol+price). A random
            # suffix keeps get_order reconciliation keys distinct.
            order_id=f"paper-{request.client_order_id or request.symbol}-{uuid.uuid4().hex[:12]}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            type=request.type,
            status=OrderStatus.FILLED,
            qty=qty,
            filled_qty=qty,
            remaining_qty=0.0,
            limit_price=request.limit_price,
            avg_fill_price=px,
            cost=qty * px,
            fee=qty * px * self.fee_bps / 10_000.0,
            fee_currency=self.quote_currency.upper(),
        )

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        """Paper orders fill instantly, so nothing is ever live to cancel."""
        return OrderResult(
            order_id=order_id,
            symbol=symbol,
            status=OrderStatus.CANCELED,
        )

    def get_order(self, symbol: str, order_id: str) -> OrderResult | None:
        """Paper keeps no resting orders; nothing to look up."""
        return None

    def get_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        """Paper never has resting orders (all fills are immediate)."""
        return []

    def get_fills(
        self, since: int | None = None, symbol: str | None = None
    ) -> list[Fill]:
        """Map ledger rows to ``Fill`` objects (newest last).

        ``since`` is an epoch-ms watermark applied against each row's ``ts``
        (ISO-8601, UTC). Rows whose timestamp is missing/unparseable are kept
        (fail-open: never silently drop a trade from the reconciliation ledger)
        unless a watermark is set and a *parseable* row predates it.
        ``side`` is normalised to the ``OrderSide`` domain: this project's
        ledger records closes as ``"close"``, which is reported as ``"sell"``
        so consumers receive a valid buy/sell.
        """
        fills: list[Fill] = []
        for row in self._load_ledger():
            ts_ms = _iso_to_epoch_ms(row.get("ts"))
            if since is not None and ts_ms is not None and ts_ms < since:
                continue
            raw_side = str(row.get("side") or OrderSide.BUY).lower()
            side = OrderSide.SELL if raw_side == "close" else raw_side
            fee = float(row.get("fee") or 0.0)
            fills.append(
                Fill(
                    trade_id=f"{row.get('cycle')}-{row.get('coin_id')}-{row.get('ts')}",
                    order_id="",
                    symbol=str(row.get("coin_id") or "").upper(),
                    side=side,
                    qty=float(row.get("qty") or 0.0),
                    price=float(row.get("price") or 0.0),
                    cost=float(row.get("amount") or 0.0),
                    fee=fee,
                    fee_currency=self.quote_currency.upper() if fee else "",
                    ts=ts_ms,
                )
            )
        if symbol:
            want = symbol.upper()
            fills = [f for f in fills if f.symbol == want]
        return fills


# Self-registration for the factory (matched against EXECUTION_MODE=paper).
register_execution_client("paper", PaperExecutionClient)

__all__ = ["PaperExecutionClient"]
