"""
Abstract execution interface for order placement and account state.

Mirrors the design of ``exchange_base.py`` (read-only market data): concrete
exchanges implement ``ExecutionClient`` so the flow can trade on any venue by
swapping the implementation, and every caller speaks the same normalized DTOs
regardless of the venue's wire format.

Nothing in this module talks to a network and nothing here is exchange
specific — auth signing, symbol formats, precision rules and min-notional
handling live inside concrete subclasses and never leak upward.

DTOs are deliberately modelled on the CCXT unified private API (the de-facto
industry standard), with two deliberate deviations:

* ``get_fills`` is named for what callers want. CCXT has no direct fills
  endpoint — spot fills come from ``fetchMyTrades`` and/or the ``trades[]`` /
  ``fee`` fields of an order object. Subclasses decide which source to use.
* Every DTO ignores unknown keys (``extra="ignore"``) and normalises aliases,
  matching the single-canonical-shape convention used by ``PortfolioAction``
  and ``PortfolioPlan`` elsewhere in this project.
"""

from __future__ import annotations

import abc
import importlib
import os
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, model_validator

__all__ = [
    "ExecutionError",
    "ExecutionHalted",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "Balance",
    "OrderRequest",
    "OrderResult",
    "Fill",
    "ExecutionClient",
    "register_execution_client",
    "get_execution_mode",
    "get_execution_client",
    "registered_clients",
]

T = TypeVar("T", bound="ExecutionClient")

# Mode / auth env vars. Same convention as get_exchange_client(): everything
# that can vary between venues is an env var, never hardcoded.
MODE_PAPER = "paper"
MODE_LIVE = "live"
MODE_HALTED = "halted"

_DEFAULT_MODE = MODE_PAPER
_DEFAULT_LIVE_EXCHANGE = "nobitex"


# ---------------------------------------------------------------------------
# Canonical enums (plain strings — the project normalises rather than enums)
# ---------------------------------------------------------------------------

class OrderSide:
    BUY = "buy"
    SELL = "sell"
    ALL = (BUY, SELL)


class OrderType:
    MARKET = "market"
    LIMIT = "limit"
    ALL = (MARKET, LIMIT)


class OrderStatus:
    NEW = "new"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ALL = (NEW, OPEN, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ExecutionError(RuntimeError):
    """A normalized execution failure (network, rejected order, bad config).

    Carries the raw venue payload when one exists so callers can log it
    without the concrete client having to leak venue-specific types.
    """

    def __init__(self, message: str, *, exchange: str = "", payload: Any = None):
        super().__init__(message)
        self.exchange = exchange
        self.payload = payload


class ExecutionHalted(ExecutionError):
    """Raised by the factory when EXECUTION_MODE=halted (kill switch)."""


# ---------------------------------------------------------------------------
# Normalized DTOs
# ---------------------------------------------------------------------------

class Balance(BaseModel):
    """Account balance for a single asset."""

    asset: str = ""
    free: float = 0.0
    locked: float = 0.0
    total: float = 0.0

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if isinstance(d.get("asset"), str):
            d["asset"] = d["asset"].upper()
        if d.get("total") is None:
            free = float(d.get("free") or 0.0)
            locked = float(d.get("locked") or 0.0)
            d["total"] = free + locked
        return d


class OrderRequest(BaseModel):
    """A venue-agnostic order intent.

    Exactly one of ``qty`` (base amount) or ``notional`` (quote amount, used
    for market buys "by cost") must be supplied. ``client_order_id`` is
    advisory: not every venue honours it for deduplication, so callers must
    reconcile (query-before-retry) rather than trusting it.
    """

    symbol: str = ""
    side: str = OrderSide.BUY
    type: str = OrderType.MARKET
    qty: float | None = None
    notional: float | None = None
    limit_price: float | None = None
    client_order_id: str | None = None
    time_in_force: str | None = None  # GTC | IOC | FOK (limit orders)
    post_only: bool = False
    reduce_only: bool = False

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        # side aliases
        side = str(d.get("side") or d.get("direction") or OrderSide.BUY).lower().strip()
        d["side"] = {"long": OrderSide.BUY, "bid": OrderSide.BUY,
                     "short": OrderSide.SELL, "ask": OrderSide.SELL}.get(side, side)
        # type aliases
        otype = str(d.get("type") or d.get("ordertype") or OrderType.MARKET).lower().strip()
        d["type"] = {"m": OrderType.MARKET, "market_order": OrderType.MARKET,
                     "l": OrderType.LIMIT, "limit_order": OrderType.LIMIT}.get(otype, otype)
        # size aliases — notional is the quote-denominated amount (CCXT "cost")
        if d.get("notional") is None:
            for fb in ("cost", "quote_qty", "amount_quote"):
                if d.get(fb) is not None:
                    d["notional"] = d[fb]
                    break
        # client order id aliases
        if d.get("client_order_id") is None:
            for fb in ("clientOrderId", "cl_ord_id"):
                if d.get(fb) is not None:
                    d["client_order_id"] = d[fb]
                    break
        # time in force aliases
        if d.get("time_in_force") is None:
            for fb in ("timeInForce", "tif"):
                if d.get(fb) is not None:
                    d["time_in_force"] = str(d[fb]).upper()
                    break
        if isinstance(d.get("time_in_force"), str):
            d["time_in_force"] = d["time_in_force"].upper()
        if isinstance(d.get("symbol"), str):
            d["symbol"] = d["symbol"].upper()
        return d

    @model_validator(mode="after")
    def _check_shape(self) -> "OrderRequest":
        if self.side not in OrderSide.ALL:
            raise ValueError(f"invalid side: {self.side!r} (expected buy|sell)")
        if self.type not in OrderType.ALL:
            raise ValueError(f"invalid type: {self.type!r} (expected market|limit)")
        if (self.qty is None) == (self.notional is None):
            raise ValueError(
                "OrderRequest needs exactly one of qty (base amount) or "
                "notional (quote amount)"
            )
        if self.type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.post_only and self.type != OrderType.LIMIT:
            raise ValueError("post_only is only valid for limit orders")
        return self


class OrderResult(BaseModel):
    """Normalized outcome of a placed/cancelled/queried order."""

    order_id: str = ""
    client_order_id: str | None = None
    symbol: str = ""
    side: str = OrderSide.BUY
    type: str = OrderType.MARKET
    status: str = OrderStatus.NEW
    qty: float = 0.0
    filled_qty: float = 0.0
    remaining_qty: float = 0.0
    limit_price: float | None = None
    avg_fill_price: float | None = None
    cost: float | None = None
    fee: float = 0.0
    fee_currency: str = ""
    ts: int | None = None  # epoch milliseconds, UTC

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for key, fallbacks in (
            ("order_id", ("id", "orderId")),
            ("client_order_id", ("clientOrderId",)),
            ("filled_qty", ("filled",)),
            ("remaining_qty", ("remaining",)),
            ("avg_fill_price", ("average", "avgPrice")),
            ("cost", ("notional",)),
            ("limit_price", ("price",)),
        ):
            if d.get(key) is None:
                for fb in fallbacks:
                    if d.get(fb) is not None:
                        d[key] = d[fb]
                        break
        if isinstance(d.get("status"), str):
            d["status"] = d["status"].lower().strip()
        if isinstance(d.get("symbol"), str):
            d["symbol"] = d["symbol"].upper()
        if isinstance(d.get("fee_currency"), str):
            d["fee_currency"] = d["fee_currency"].upper()
        return d

    @property
    def is_final(self) -> bool:
        return self.status in (
            OrderStatus.FILLED, OrderStatus.CANCELED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED,
        )


class Fill(BaseModel):
    """One executed trade. Fees are only knowable post-fill."""

    trade_id: str = ""
    order_id: str = ""
    symbol: str = ""
    side: str = OrderSide.BUY
    qty: float = 0.0
    price: float = 0.0
    cost: float = 0.0
    fee: float = 0.0
    fee_currency: str = ""
    ts: int | None = None  # epoch milliseconds, UTC

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalise_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for key, fallbacks in (
            ("trade_id", ("id", "tradeId")),
            ("order_id", ("order", "orderId")),
        ):
            if d.get(key) is None:
                for fb in fallbacks:
                    if d.get(fb) is not None:
                        d[key] = d[fb]
                        break
        if d.get("cost") is None and d.get("qty") is not None and d.get("price") is not None:
            d["cost"] = float(d["qty"]) * float(d["price"])
        if isinstance(d.get("side"), str):
            d["side"] = d["side"].lower().strip()
        if isinstance(d.get("symbol"), str):
            d["symbol"] = d["symbol"].upper()
        if isinstance(d.get("fee_currency"), str):
            d["fee_currency"] = d["fee_currency"].upper()
        return d


# ---------------------------------------------------------------------------
# Abstract client
# ---------------------------------------------------------------------------

class ExecutionClient(abc.ABC):
    """Abstract base for order placement and account state on a venue.

    Concrete subclasses own every venue-specific concern: request signing,
    symbol mapping, precision rounding (tick/lot), min-notional enforcement,
    pagination, rate limiting, and error translation into ``ExecutionError``.

    All amounts are in the venue's quote currency unless stated otherwise.
    """

    #: Short venue identifier (e.g. "nobitex"), used for logging and errors.
    name: str = "abstract"
    #: True when this client simulates fills locally (paper mode).
    is_simulated: bool = False
    #: Quote currency this client settles in (e.g. "rls", "usdt").
    quote_currency: str = "usdt"

    def __init__(self, quote_currency: str | None = None):
        if quote_currency:
            self.quote_currency = quote_currency.lower()

    # -- account state ------------------------------------------------------

    @abc.abstractmethod
    def get_balances(self, assets: list[str] | None = None) -> list[Balance]:
        """Return balances, optionally limited to ``assets`` (upper-case)."""

    # -- trading ------------------------------------------------------------

    @abc.abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        """Submit an order. Raises ``ExecutionError`` on rejection/failure.

        Implementations must NOT assume ``client_order_id`` deduplicates —
        venues differ. Callers reconcile via ``get_order``/``get_open_orders``
        after a timeout rather than blind-retrying.
        """

    @abc.abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        """Cancel a live order and return its terminal state."""

    @abc.abstractmethod
    def get_order(self, symbol: str, order_id: str) -> OrderResult | None:
        """Return one order's current state, or None if the venue has no
        record of it (also the correct signal after a placement timeout)."""

    @abc.abstractmethod
    def get_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        """List live (unfilled/partially filled) orders."""

    @abc.abstractmethod
    def get_fills(
        self, since: int | None = None, symbol: str | None = None
    ) -> list[Fill]:
        """Return executed trades, newest-last, optionally since an epoch-ms
        watermark. Sourced from the venue's trade history and/or the order
        object's embedded trades — never a fictional direct endpoint."""

    # -- optional hooks -----------------------------------------------------

    def get_market_constraints(self, symbol: str) -> dict[str, Any]:
        """Return tick/lot/min-notional for a symbol when the venue exposes it.

        Default: empty — the venue does not publish constraints here and the
        caller must fall back to a configured per-pair map. Subclasses that do
        know tick/lot should override and return keys such as
        ``{"price_tick", "qty_step", "min_qty", "min_notional"}``.
        """
        return {}

    def describe(self) -> str:
        return f"{self.name} (quote={self.quote_currency}, simulated={self.is_simulated})"

    def close(self) -> None:
        """Release any held resources. Safe to call more than once."""

    def __enter__(self: T) -> T:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Factory — registry so a new venue plugs in without editing this module
# ---------------------------------------------------------------------------

_ClientFactory = Callable[[], ExecutionClient]
_REGISTRY: dict[str, _ClientFactory] = {}


def register_execution_client(name: str, factory: _ClientFactory) -> None:
    """Register a client factory under ``name`` (matched against
    EXECUTION_MODE for paper, or EXECUTION_EXCHANGE for live)."""
    _REGISTRY[name.lower()] = factory


def registered_clients() -> list[str]:
    return sorted(_REGISTRY)


def _load_builtin_clients() -> None:
    """Import concrete client modules so their self-registration runs.

    Concrete modules call ``register_execution_client`` at import time. Missing
    modules are ignored so the interface is usable before any client lands.
    """
    for module in ("execution_paper", "execution_nobitex"):
        try:
            importlib.import_module(f"crypto_council_flow.tools.{module}")
        except ImportError:
            continue


def get_execution_mode() -> str:
    """Resolve the configured mode: paper | live | halted (default paper)."""
    return os.getenv("EXECUTION_MODE", _DEFAULT_MODE).strip().lower()


def get_execution_client() -> ExecutionClient:
    """Factory: return the execution client selected by env config.

    ``EXECUTION_MODE`` chooses the mode (default ``paper``). In ``live`` mode
    ``EXECUTION_EXCHANGE`` (falling back to ``EXCHANGE``) picks the venue.
    ``halted`` is the kill switch — it refuses to hand back a client at all.
    """
    _load_builtin_clients()
    mode = get_execution_mode()

    if mode == MODE_HALTED:
        raise ExecutionHalted(
            "EXECUTION_MODE=halted — order placement is disabled (kill switch)."
        )

    key = mode
    if mode == MODE_LIVE:
        key = os.getenv(
            "EXECUTION_EXCHANGE", os.getenv("EXCHANGE", _DEFAULT_LIVE_EXCHANGE)
        ).strip().lower()

    factory = _REGISTRY.get(key)
    if factory is None:
        raise ExecutionError(
            f"No execution client registered for {mode!r}"
            + (f" (exchange={key!r})" if mode == MODE_LIVE else "")
            + f". Registered: {registered_clients() or 'none'}."
        )
    return factory()
