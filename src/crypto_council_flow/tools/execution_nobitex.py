"""Nobitex live execution client (Phase 3 of the paper->live plan).

Implements the abstract :class:`ExecutionClient` against Nobitex's
authenticated **apiv2** REST API, so the flow can place real spot orders by
switching ``EXECUTION_MODE=live``. Everything venue-specific lives here:
request signing, symbol pair translation, precision rounding, min-notional
enforcement, pagination, rate-limit handling and error translation.

Endpoint map (verified against the published OpenAPI specs at
``https://apidocs.nobitex.ir/openapi/{spot_trade,user_data}.yaml``):

===============================  ==============================  =========
interface method                 Nobitex endpoint                 verb
===============================  ==============================  =========
``get_balances``                 ``/users/wallets/list``          POST
``place_order``                  ``/market/orders/add``           POST
``cancel_order``                 ``/market/orders/update-status`` POST
``get_order``                    ``/market/orders/status``        POST
``get_open_orders``              ``/market/orders/list``          GET
``get_fills``                    ``/market/trades/list``          GET
min-notional source              ``/v2/options`` (``nobitex.minOrders``) GET
===============================  ==============================  =========

Auth: token auth (``Authorization: Token <key>``), matching
``exchange_base.py``. Nobitex also offers Ed25519 API-key auth
(``Nobitex-Key`` / ``Nobitex-Signature`` / ``Nobitex-Timestamp``); that is a
deliberate extension point, not implemented here (it needs a local private key
and cannot be exercised in tests).

Two correctness rules from the plan are enforced:
  * ``client_order_id`` is NOT treated as an idempotency guarantee — a
    placement that times out is never blind-retried; the caller must
    ``get_order``/``get_open_orders`` first.
  * A minimum real order + immediate close is the only live smoke test —
    Nobitex publishes no reachable public sandbox.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

import requests

from crypto_council_flow.tools.execution_base import (
    Balance,
    ExecutionClient,
    ExecutionError,
    ExecutionHalted,
    Fill,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    register_execution_client,
)
from crypto_council_flow.tools.execution_safety import (
    DailyLossBreaker,
    allow_close,
    enforce_exposure_caps,
    exposure_limit,
)

_DEFAULT_BASE_URL = "https://apiv2.nobitex.ir"
_DEFAULT_TIMEOUT = 20
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF = 0.5  # seconds; doubled per retry

# Phase 6 fallback caps, used only when the caller passes none and no env
# override exists. They mirror the flow's own defaults so a client used
# standalone is not silently uncapped.
_DEFAULT_MAX_TOTAL_PCT = 0.30
_DEFAULT_MAX_SINGLE_PCT = 0.05

# Nobitex order-status vocabulary -> normalized OrderStatus.
_STATUS_MAP = {
    "new": OrderStatus.NEW,
    "active": OrderStatus.OPEN,
    # A resting stop order that has not reached its trigger yet is still live.
    "inactive": OrderStatus.OPEN,
    "done": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "cancelled": OrderStatus.CANCELED,
}

# Nobitex execution vocabulary -> normalized OrderType.
_EXECUTION_MAP = {
    "limit": OrderType.LIMIT,
    "market": OrderType.MARKET,
    "stop_limit": OrderType.LIMIT,
    "stop_market": OrderType.MARKET,
}


def _decimals_from_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _pct_from_env(name: str, default: float) -> float:
    """Read an optional percentage env var; None/blank/invalid -> default."""
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "market"):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _quantise(value: Decimal, decimals: int, rounding: str) -> Decimal:
    quantum = Decimal(1).scaleb(-decimals)  # 10 ** -decimals
    return value.quantize(quantum, rounding=rounding)


def _format_decimal(value: Decimal) -> str:
    """Render a Decimal without scientific notation or trailing zeros."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _iso_to_epoch_ms(value: Any) -> int | None:
    """Parse an ISO-8601 timestamp to epoch milliseconds (best effort)."""
    if not value:
        return None
    from datetime import datetime, timezone
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _epoch_ms_to_iso(value: Any) -> str | None:
    """Render epoch milliseconds as an ISO-8601 UTC string (best effort).

    Inverse of :func:`_iso_to_epoch_ms`, so a live fill's ``ts`` (epoch ms)
    can be written into a ledger row in the same format paper uses.
    """
    if not value:
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class NobitexExecutionClient(ExecutionClient):
    """Live spot execution on Nobitex (apiv2), token-authenticated.

    Configuration (all env vars — nothing hardcoded, per project convention):

    * ``EXECUTION_API_KEY`` / ``EXCHANGE_API_KEY`` — token (``TRADE`` scope for
      placing/cancelling, ``READ`` for balances/orders/trades).
    * ``EXECUTION_API_BASE`` / ``EXCHANGE_API_BASE`` — base URL.
    * ``EXECUTION_QUOTE`` / ``EXCHANGE_QUOTE`` — quote currency (``rls``/``usdt``).
    * ``EXECUTION_QTY_DECIMALS`` / ``EXECUTION_PRICE_DECIMALS`` — rounding
      precision when the venue does not publish per-pair tick/lot.
    * ``EXECUTION_MIN_NOTIONAL`` — hard override for the minimum order value in
      the quote currency; otherwise read live from ``/v2/options``.
    * ``EXECUTION_MIN_NOTIONAL_<QUOTE>`` e.g. ``EXECUTION_MIN_NOTIONAL_RLS=500000``.
    * ``EXECUTION_MAX_RETRIES`` / ``EXECUTION_TIMEOUT`` — transport tuning.

    ``symbol`` is accepted as ``BTC-RLS``, ``BTC/RLS`` or a bare base such as
    ``BTC`` (which then pairs with the configured quote currency).
    """

    name = "nobitex"
    is_simulated = False

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        quote_currency: str | None = None,
        timeout: int | None = None,
        session: requests.Session | None = None,
        qty_decimals: int | None = None,
        price_decimals: int | None = None,
    ):
        quote = (
            quote_currency
            or os.getenv("EXECUTION_QUOTE")
            or os.getenv("EXCHANGE_QUOTE")
            or "rls"
        )
        super().__init__(quote_currency=quote)
        self.base_url = (
            base_url
            or os.getenv("EXECUTION_API_BASE")
            or os.getenv("EXCHANGE_API_BASE")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = api_key or os.getenv("EXECUTION_API_KEY") or os.getenv(
            "EXCHANGE_API_KEY"
        )
        try:
            self.timeout = int(
                timeout
                if timeout is not None
                else os.getenv("EXECUTION_TIMEOUT", str(_DEFAULT_TIMEOUT))
            )
        except (TypeError, ValueError):
            self.timeout = _DEFAULT_TIMEOUT
        try:
            self.max_retries = max(
                0, int(os.getenv("EXECUTION_MAX_RETRIES", str(_DEFAULT_MAX_RETRIES)))
            )
        except (TypeError, ValueError):
            self.max_retries = _DEFAULT_MAX_RETRIES
        self.qty_decimals = (
            qty_decimals
            if qty_decimals is not None
            else _decimals_from_env("EXECUTION_QTY_DECIMALS", 8) if os.getenv("EXECUTION_QTY_DECIMALS") is not None
            else _decimals_from_env("EXCHANGE_QTY_DECIMALS", 8)
        )
        self.price_decimals = (
            price_decimals
            if price_decimals is not None
            else _decimals_from_env("EXECUTION_PRICE_DECIMALS", 8) if os.getenv("EXECUTION_PRICE_DECIMALS") is not None
            else _decimals_from_env("EXCHANGE_PRICE_DECIMALS", 8)
        )

        self._session = session or requests.Session()
        if self.api_key:
            self._session.headers.update({"Authorization": f"Token {self.api_key}"})
        # Nobitex asks bots to identify themselves via User-Agent.
        self._session.headers.update(
            {
                "accept": "application/json",
                "content-type": "application/json",
                "user-agent": os.getenv("EXECUTION_USER_AGENT", "TraderBot/crypto-council-1.0"),
            }
        )
        self._min_notional_cache: dict[str, float] | None = None

    # -- transport ----------------------------------------------------------

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        retry_on_network_error: bool = True,
    ) -> Any:
        """Call the venue, normalise every failure into ``ExecutionError``.

        ``retry_on_network_error`` must be False for non-idempotent calls
        (order placement): a timeout there is ambiguous — the order may have
        been accepted — so the caller reconciles instead of blind-retrying.
        HTTP 429 is always safe to retry (the request was rejected, not run).
        """
        url = f"{self.base_url}{endpoint}"
        attempts = self.max_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = self._session.request(
                    method, url, json=json_body, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                if not retry_on_network_error or attempt >= attempts - 1:
                    raise ExecutionError(
                        f"{self.name}: network error calling {endpoint}: {exc}",
                        exchange=self.name,
                    ) from exc
                time.sleep(_DEFAULT_BACKOFF * (2**attempt))
                continue

            if resp.status_code == 429:
                last_error = ExecutionError(
                    f"{self.name}: rate limited on {endpoint}",
                    exchange=self.name,
                )
                if attempt >= attempts - 1:
                    raise last_error
                time.sleep(_DEFAULT_BACKOFF * (2**attempt))
                continue

            if resp.status_code >= 500:
                last_error = ExecutionError(
                    f"{self.name}: server error {resp.status_code} on {endpoint}",
                    exchange=self.name,
                )
                if not retry_on_network_error or attempt >= attempts - 1:
                    raise last_error
                time.sleep(_DEFAULT_BACKOFF * (2**attempt))
                continue

            try:
                data = resp.json()
            except ValueError as exc:
                raise ExecutionError(
                    f"{self.name}: non-JSON response ({resp.status_code}) on {endpoint}",
                    exchange=self.name,
                    payload=resp.text[:500],
                ) from exc

            if resp.status_code == 401 or resp.status_code == 403:
                raise ExecutionError(
                    f"{self.name}: auth rejected ({resp.status_code}) on {endpoint}; "
                    "check EXECUTION_API_KEY scope",
                    exchange=self.name,
                    payload=data,
                )
            if resp.status_code >= 400:
                raise ExecutionError(
                    f"{self.name}: HTTP {resp.status_code} on {endpoint}",
                    exchange=self.name,
                    payload=data,
                )

            # Nobitex signals failures inside a 200 body via status=failed.
            if isinstance(data, dict) and str(data.get("status")).lower() == "failed":
                raise ExecutionError(
                    f"{self.name}: {data.get('code', 'Unknown')} — "
                    f"{data.get('message', 'request failed')}",
                    exchange=self.name,
                    payload=data,
                )
            return data

        raise last_error or ExecutionError(
            f"{self.name}: exhausted retries on {endpoint}", exchange=self.name
        )

    def _post(self, endpoint: str, body: dict | None, *, safe_to_retry: bool = True) -> Any:
        return self._request(
            "POST",
            endpoint,
            json_body=body if body is not None else {},
            retry_on_network_error=safe_to_retry,
        )

    def _get(self, endpoint: str, params: dict | None = None) -> Any:
        return self._request("GET", endpoint, params=params)

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    # -- symbol / precision helpers ----------------------------------------

    def _resolve_pair(self, symbol: str) -> tuple[str, str]:
        """Translate ``symbol`` into Nobitex's (srcCurrency, dstCurrency) pair.

        Accepts ``BTC-RLS``, ``BTC/RLS`` or a bare base (``BTC``), which pairs
        with the configured quote currency. Symbols are lowercased; market
        names are upper-case on the wire but the order API takes lowercase.
        """
        raw = (symbol or "").strip()
        if not raw:
            raise ExecutionError("empty symbol", exchange=self.name)
        for sep in ("-", "/", "_"):
            if sep in raw:
                src, _, dst = raw.partition(sep)
                if src and dst:
                    return src.strip().lower(), dst.strip().lower()
        return raw.lower(), self.quote_currency.lower()

    def _round_qty(self, qty: Decimal) -> Decimal:
        # Round DOWN so rounding never tries to spend more than intended.
        return _quantise(qty, self.qty_decimals, ROUND_DOWN)

    def _round_price(self, price: Decimal) -> Decimal:
        return _quantise(price, self.price_decimals, ROUND_HALF_UP)

    def get_market_constraints(self, symbol: str) -> dict[str, Any]:
        """Local precision/min-notional for a symbol.

        Nobitex does not publish per-pair tick/lot via its public market list,
        so this returns the configured rounding precision plus the live
        min-notional (from ``/v2/options``), which is the only hard venue
        constraint actually exposed.
        """
        src, dst = self._resolve_pair(symbol)
        min_notional = self._min_notional_for(dst)
        out: dict[str, Any] = {
            "source": "configured (venue publishes no per-pair tick/lot)",
            "src_currency": src,
            "dst_currency": dst,
            "price_decimals": self.price_decimals,
            "qty_decimals": self.qty_decimals,
        }
        if min_notional is not None:
            out["min_notional"] = min_notional
        return out

    def _min_notional_for(self, quote: str) -> float | None:
        """Minimum order value for a quote currency (env override wins).

        Order of resolution:
          1. ``EXECUTION_MIN_NOTIONAL`` (explicit override, any quote).
          2. ``EXECUTION_MIN_NOTIONAL_<QUOTE>`` (e.g. ``..._RLS``).
          3. live ``GET /v2/options`` -> ``nobitex.minOrders[<quote>]`` (cached).
        Returns ``None`` when unknown, so callers can fail open with a warning.
        """
        override = os.getenv("EXECUTION_MIN_NOTIONAL")
        if override:
            try:
                return float(override)
            except (TypeError, ValueError):
                pass
        per_quote = os.getenv(f"EXECUTION_MIN_NOTIONAL_{quote.upper()}")
        if per_quote:
            try:
                return float(per_quote)
            except (TypeError, ValueError):
                pass
        if self._min_notional_cache is None:
            try:
                data = self._get("/v2/options")
                self._min_notional_cache = {
                    str(k).lower(): float(v)
                    for k, v in (data.get("nobitex", {}) or {}).get("minOrders", {}).items()
                    if str(k).isalpha()
                }
            except Exception:
                self._min_notional_cache = {}
        return self._min_notional_cache.get(quote.lower())

    def _last_price(self, src: str, dst: str) -> Decimal | None:
        """Best-effort last trade price for a pair (used to size market buys)."""
        try:
            data = self._get(
                "/market/stats", params={"srcCurrency": src, "dstCurrency": dst}
            )
        except ExecutionError:
            return None
        stats = (data or {}).get("stats", {}) or {}
        for key in ("latest", "lastTradePrice", "close"):
            px = _to_decimal(stats.get(key))
            if px and px > 0:
                return px
        return None

    # -- order translation --------------------------------------------------

    def build_order_payload(self, request: OrderRequest) -> dict[str, Any]:
        """Translate a normalized ``OrderRequest`` into a Nobitex payload.

        Deterministic and network-free except when a market order must be sized
        from notional (then the last price is fetched). ``amount`` is always in
        ``srcCurrency``; ``price`` is in the quote currency (Rial for Rial
        markets) and only sent for limit orders.
        """
        src, dst = self._resolve_pair(request.symbol)
        side = request.side

        price: Decimal | None = (
            Decimal(str(request.limit_price))
            if request.limit_price is not None
            else None
        )
        if request.type == OrderType.LIMIT:
            if price is None:
                raise ExecutionError(
                    f"limit order for {request.symbol!r} requires limit_price",
                    exchange=self.name,
                )
            amount: Decimal | None = (
                Decimal(str(request.qty)) if request.qty is not None else None
            )
            if amount is None:
                amount = Decimal(str(request.notional)) / price
        else:  # market
            amount = Decimal(str(request.qty)) if request.qty is not None else None
            if amount is None:
                px = price if price is not None else self._last_price(src, dst)
                if px is None:
                    raise ExecutionError(
                        f"cannot size market order for {request.symbol!r}: no qty and "
                        "no obtainable price for notional",
                        exchange=self.name,
                    )
                amount = Decimal(str(request.notional)) / px

        amount = self._round_qty(amount)
        if amount <= 0:
            raise ExecutionError(
                f"{self.name}: order amount rounds to zero for {request.symbol!r}",
                exchange=self.name,
            )

        min_notional = self._min_notional_for(dst)
        if min_notional:
            ref_price = price or self._last_price(src, dst)
            if ref_price:
                notional = amount * ref_price
                if notional < Decimal(str(min_notional)):
                    raise ExecutionError(
                        f"{self.name}: order value {_format_decimal(notional)} {dst.upper()} "
                        f"is below the venue minimum {min_notional} {dst.upper()} (SmallOrder)",
                        exchange=self.name,
                    )

        payload: dict[str, Any] = {
            "type": side,
            "execution": "limit" if request.type == OrderType.LIMIT else "market",
            "srcCurrency": src,
            "dstCurrency": dst,
            "amount": _format_decimal(amount),
        }
        if request.type == OrderType.LIMIT and price is not None:
            payload["price"] = _format_decimal(self._round_price(price))
        if request.client_order_id:
            payload["clientOrderId"] = str(request.client_order_id)[:32]
        return payload

    @staticmethod
    def _order_to_result(order: dict[str, Any], fallback_symbol: str = "") -> OrderResult:
        """Map a Nobitex order object to a normalized ``OrderResult``."""
        if not isinstance(order, dict):
            raise ExecutionError("Nobitex returned a malformed order object", exchange="nobitex")
        raw_status = str(order.get("status", "")).lower()
        raw_execution = str(order.get("execution", "")).lower()
        avg = _to_decimal(order.get("averagePrice"))
        limit = _to_decimal(order.get("price"))
        raw_side = str(order.get("type") or "").lower()
        if raw_side not in (OrderSide.BUY, OrderSide.SELL):
            raise ExecutionError(
                f"Nobitex order object missing a usable 'type': {order.get('type')!r}",
                exchange="nobitex",
                payload=order,
            )
        return OrderResult(
            order_id=str(order.get("id", "")),
            client_order_id=order.get("clientOrderId"),
            symbol=str(order.get("market") or fallback_symbol).upper(),
            side=raw_side,
            type=_EXECUTION_MAP.get(raw_execution, OrderType.LIMIT),
            status=_STATUS_MAP.get(raw_status, OrderStatus.NEW),
            qty=float(_to_decimal(order.get("amount")) or 0),
            filled_qty=float(_to_decimal(order.get("matchedAmount")) or 0),
            remaining_qty=float(_to_decimal(order.get("unmatchedAmount")) or 0),
            limit_price=float(limit) if limit and limit > 0 else None,
            avg_fill_price=float(avg) if avg and avg > 0 else None,
            cost=float(_to_decimal(order.get("totalOrderPrice")) or 0) or None,
            fee=float(_to_decimal(order.get("fee")) or 0),
            fee_currency=str(order.get("dstCurrency") or ""),
            ts=_iso_to_epoch_ms(order.get("created_at")),
        )

    # -- ExecutionClient interface -----------------------------------------

    def get_balances(self, assets: list[str] | None = None) -> list[Balance]:
        """Spot wallet balances via ``POST /users/wallets/list``.

        ``free`` is the active (unblocked) balance; ``locked`` is the blocked
        balance (in open orders / withdrawals). ``assets`` filters by currency
        symbol (e.g. ``["BTC", "RLS"]``).
        """
        data = self._post("/users/wallets/list", {"type": "spot"})
        wallets = data.get("wallets") or []
        wanted = {a.upper() for a in assets} if assets else None
        out: list[Balance] = []
        for w in wallets:
            currency = str(w.get("currency") or "").upper()
            if not currency or (wanted and currency not in wanted):
                continue
            total = float(_to_decimal(w.get("balance")) or 0)
            blocked = float(_to_decimal(w.get("blockedBalance")) or 0)
            active = _to_decimal(w.get("activeBalance"))
            free = float(active) if active is not None else max(0.0, total - blocked)
            out.append(Balance(asset=currency, free=free, locked=blocked, total=total))
        return out

    def place_order(self, request: OrderRequest) -> OrderResult:
        """Submit a spot order via ``POST /market/orders/add``.

        Never blind-retries on a network timeout (``safe_to_retry=False``): the
        order may already be live. On an ambiguous failure the caller must call
        ``get_order``/``get_open_orders`` with the same ``client_order_id``
        before doing anything else.
        """
        payload = self.build_order_payload(request)
        data = self._post("/market/orders/add", payload, safe_to_retry=False)
        order = data.get("order")
        if not isinstance(order, dict):
            raise ExecutionError(
                "Nobitex accepted no order object in the response",
                exchange=self.name,
                payload=data,
            )
        return self._order_to_result(order, fallback_symbol=request.symbol)

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        """Cancel an open order via ``POST /market/orders/update-status``."""
        body: dict[str, Any] = {"status": "canceled"}
        if str(order_id).isdigit():
            body["order"] = int(order_id)
        else:
            body["clientOrderId"] = str(order_id)[:32]
        data = self._post("/market/orders/update-status", body)
        # The venue may return status=failed with an order+updatedStatus; on a
        # failed cancel our _request raises before we get here, so the only
        # success shapes are: an order object (the new state), or a minimal
        # ack. Best-effort result either way.
        order = data.get("order") if isinstance(data, dict) else None
        if isinstance(order, dict):
            result = self._order_to_result(order, fallback_symbol=symbol)
            updated = data.get("updatedStatus")
            if updated:
                result.status = _STATUS_MAP.get(str(updated).lower(), result.status)
            return result
        return OrderResult(order_id=str(order_id), symbol=symbol, status=OrderStatus.CANCELED)

    def get_order(self, symbol: str, order_id: str) -> OrderResult | None:
        """Look up one order via ``POST /market/orders/status``.

        Numeric ``order_id`` is sent as ``id``; otherwise it is treated as a
        ``clientOrderId`` (only resolvable while the order is still open).
        Returns ``None`` when the venue has no such order — which is also the
        correct post-timeout reconciliation signal.
        """
        if not order_id:
            return None
        body: dict[str, Any]
        if str(order_id).isdigit():
            body = {"id": int(order_id)}
        else:
            body = {"clientOrderId": str(order_id)[:32]}
        try:
            data = self._post("/market/orders/status", body)
        except ExecutionError as exc:
            code = (exc.payload or {}).get("code") if isinstance(exc.payload, dict) else None
            if code in ("NullIdAndClientOrderId", "OrderNotFound"):
                return None
            raise
        order = data.get("order") if isinstance(data, dict) else None
        if not isinstance(order, dict):
            return None
        return self._order_to_result(order, fallback_symbol=symbol)

    def get_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        """List live orders via ``GET /market/orders/list`` (``status=open``).

        ``details=2`` makes the venue include ``status``, ``fee``,
        ``created_at`` and ``averagePrice`` per order.
        """
        params: dict[str, Any] = {"status": "open", "details": 2}
        if symbol:
            src, dst = self._resolve_pair(symbol)
            params["srcCurrency"] = src
            params["dstCurrency"] = dst
        data = self._get("/market/orders/list", params=params)
        return [
            self._order_to_result(o, fallback_symbol=symbol or "")
            for o in (data.get("orders") or [])
            if isinstance(o, dict)
        ]

    def get_fills(
        self, since: int | None = None, symbol: str | None = None
    ) -> list[Fill]:
        """Executed trades via ``GET /market/trades/list`` (spot, 180 days).

        ``since`` (epoch ms) is applied as a client-side timestamp filter so the
        caller always gets a deterministic watermark (no ``fromId`` derivation).
        Paginates until exhausted (``hasNext``); caps at 100 pages and fails
        loudly if hit, so a runaway never silently truncates the fill log.
        """
        base_params: dict[str, Any] = {"tradeOrder": "asc", "pageSize": 500}
        if symbol:
            src, dst = self._resolve_pair(symbol)
            base_params["srcCurrency"] = src
            base_params["dstCurrency"] = dst

        fills: list[Fill] = []
        page = 1
        while page <= 100:
            params = dict(base_params, page=page)
            data = self._get("/market/trades/list", params=params)
            trades = data.get("trades") or []
            for t in trades:
                if not isinstance(t, dict):
                    continue
                ts = _iso_to_epoch_ms(t.get("timestamp"))
                if since is not None and ts is not None and ts < since:
                    continue
                src = str(t.get("srcCurrency") or "").upper()
                dst = str(t.get("dstCurrency") or "").upper()
                market = str(t.get("market") or f"{src}-{dst}").upper()
                # Fee currency: Nobitex typically charges in the RECEIVED currency
                # (dst for SELL, src for BUY). This is a heuristic, not a
                # documented guarantee — Phase 7 live verification must confirm.
                fee_currency = dst if t.get("type", "").lower() == OrderSide.SELL else src
                fills.append(
                    Fill(
                        trade_id=str(t.get("id", "")),
                        order_id=str(t.get("orderId") or ""),
                        symbol=market,
                        side=str(t.get("type") or "").lower(),
                        qty=float(_to_decimal(t.get("amount")) or 0),
                        price=float(_to_decimal(t.get("price")) or 0),
                        cost=float(_to_decimal(t.get("total")) or 0),
                        fee=float(_to_decimal(t.get("fee")) or 0),
                        fee_currency=fee_currency,
                        ts=ts,
                    )
                )
            if not data.get("hasNext"):
                break
            page += 1
        if page > 100:
            raise ExecutionError(
                f"{self.name}: get_fills hit 100-page cap — possible runaway or "
                "misconfigured since/symbol",
                exchange=self.name,
            )
        return fills

    # -- live plan application (Phase 4) ------------------------------------

    def apply_plan(
        self,
        plan: dict[str, Any],
        risk_snapshot: dict[str, dict[str, float]],
        account_size: float,
        base_currency: str,
        now_iso: str,
        cycle: int,
        max_total_pct: float | None = None,
        max_single_pct: float | None = None,
        breaker_ledger: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Translate plan deltas into real spot orders and summarize.

        Same signature as ``PaperExecutionClient.apply_plan`` so ``main.py``
        can call either client interchangeably. Each action's ``symbol``
        (upper-case ticker, e.g. ``BTC``) is paired with this client's quote
        currency (``BTC-RLS``); ``coin_id`` is only used to look up the
        snapshot price for sizing.

        Rules:
          * market orders sized by quantity (no price guessing);
          * ``EXECUTION_DRY_RUN=true`` builds the orders without POSTing and
            logs nothing (rehearsals must not pollute the live audit log);
          * dust deltas below ``EXECUTION_MIN_TRADE`` (default 10, same unit
            as the allocator's ``min_trade``) are skipped, mirroring hold —
            but 10 is a dust floor only: set it >= the venue's
            minimum-notional (Nobitex ``/v2/options`` minimums are orders of
            magnitude larger) or every live order is ``SmallOrder``-rejected;
          * a placement failure is NEVER blind-retried — one attempt, then a
            best-effort ``get_open_orders`` reconciliation note, then the
            next coin (``client_order_id`` is advisory, not idempotent);
          * P&L is honest about what live knows: ``realized_pnl`` is
            ``-fee_total`` where each fill's fee is converted to quote (via
            its own fill price, else the snapshot price). Fees with no
            known currency/rate land in ``fee_unconverted`` and are
            reported, never silently mixed into the total. A venue keeps no
            cost basis, so no realized gains are fabricated;
            ``unrealized_pnl`` is 0.0;
          * ``open_positions``/``invested`` are pre-trade (a single balances
            fetch); the quote asset (cash) is excluded from positions, and
            each entry carries ``coin_id``/``asset``/``qty``/``price``/
            ``market_value``. Post-trade truth comes from the fills plus
            Phase 5 reconciliation. ``n_trades`` counts this cycle's fills
            (paper's ``n_trades`` is cumulative ledger length). One JSONL
            row per real (non-dry-run) cycle is appended to
            ``EXECUTION_LOG_PATH`` (default
            ``output/live_executions.jsonl``), best-effort;
          * **Phase 6** re-checks every order pre-trade against the caps
            (``max_total_pct``/``max_single_pct`` — fractions of the
            account, defaulting to the flow's own 30%/5% or the
            ``EXECUTION_MAX_TOTAL_PCT``/``EXECUTION_MAX_SINGLE_PCT`` env
            overrides) and refuses to place new entries once today's
            realized loss reaches the daily-loss limit
            (``EXECUTION_MAX_DAILY_LOSS_PCT``, from ``breaker_ledger``).
            Sells/closes are never capped — the circuit must never trap a
            losing position. A dry run builds the same orders but is not
            blocked, so rehearsals can run at any cap/loss state.
        """
        snapshot = risk_snapshot or {}
        since_ms = _iso_to_epoch_ms(now_iso)
        try:
            min_trade = max(0.0, float(os.getenv("EXECUTION_MIN_TRADE", "10") or 0))
        except (TypeError, ValueError):
            min_trade = 10.0
        dry_run = str(os.getenv("EXECUTION_DRY_RUN", "")).strip().lower() in (
            "1", "true", "yes", "on",
        )

        pre_balances = {
            b.asset.upper(): float(b.total or 0.0) for b in self.get_balances()
        }
        quote = self.quote_currency.upper()
        coin_by_asset = {
            str(a.get("symbol") or "").strip().upper(): str(a.get("coin_id") or "")
            for a in (plan or {}).get("actions", [])
            if isinstance(a, dict) and str(a.get("symbol") or "").strip()
        }

        def _coin_for(asset: str, fallback: str) -> str:
            """Resolve a wallet asset to the snapshot's ``coin_id`` key.

            The plan's actions are the only place ticker and ``coin_id``
            appear together, so an asset the plan does not mention (a
            leftover from an earlier cycle, or a manual deposit) has no
            mapping. Falling back to ``""`` prices it at 0 *and* hides it
            from the panel, which filters blank ids; the lowercase asset is
            not a ``coin_id`` either, but it is at least non-empty and
            traceable.
            """
            return coin_by_asset.get(asset) or fallback

        # Pre-trade exposure in quote terms, from the same balance fetch —
        # the caps are checked against what the wallet really holds now, not
        # what the plan assumed when it was priced (Phase 6).
        pre_positions = [
            {"asset": a, "qty": q,
             "price": float((snapshot.get(_coin_for(a, a.lower())) or {}).get("current_price") or 0.0)}
            for a, q in pre_balances.items()
            if a != quote and q > 0
        ]
        for p in pre_positions:
            p["market_value"] = p["qty"] * p["price"]

        orders: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        # ------------------------------------------------------------------
        # Phase 6 safety guards.
        #
        # The allocator priced this plan on the *last* price it saw; these
        # re-check every order at fill time against what the wallet holds
        # *now*. Sells/closes are never capped (de-risking must always stay
        # possible) and the breaker fails closed — an open circuit raises,
        # it never demotes to paper and books simulated profits.
        # ------------------------------------------------------------------
        max_total_pct = (
            max_total_pct if max_total_pct is not None
            else _pct_from_env("EXECUTION_MAX_TOTAL_PCT", _DEFAULT_MAX_TOTAL_PCT)
        )
        max_single_pct = (
            max_single_pct if max_single_pct is not None
            else _pct_from_env("EXECUTION_MAX_SINGLE_PCT", _DEFAULT_MAX_SINGLE_PCT)
        )
        # Fail loudly on a percent written where a fraction belongs: a 30
        # "fraction" would arm a 3000% cap that blocks nothing while the
        # guard looks armed, which is the same as no guard at all.
        try:
            exposure_limit(float(account_size or 0.0), max_total_pct)
            exposure_limit(float(account_size or 0.0), max_single_pct)
        except ValueError as exc:
            raise ExecutionError(f"Phase 6 caps misconfigured: {exc}") from exc
        if not dry_run:
            breaker = DailyLossBreaker(
                account_size, ledger=breaker_ledger or [], now_iso=now_iso,
            )
        else:
            breaker = None  # a rehearsal must not be blocked by the circuit

        for a in (plan or {}).get("actions", []):
            if not isinstance(a, dict):
                continue
            coin_id = str(a.get("coin_id") or "")
            base = str(a.get("symbol") or "").strip().upper()
            venue_symbol = f"{base}-{self.quote_currency.upper()}" if base else ""
            if not base:
                errors.append({
                    "coin_id": coin_id, "symbol": venue_symbol,
                    "error": "skipped: no symbol — cannot translate coin_id to a venue pair",
                })
                continue
            try:
                target = float(a.get("target", 0) or 0)
            except (TypeError, ValueError):
                errors.append({
                    "coin_id": coin_id, "symbol": venue_symbol,
                    "error": f"skipped: non-numeric target {a.get('target')!r}",
                })
                continue
            try:
                px = float((snapshot.get(coin_id) or {}).get("current_price") or 0.0)
            except (TypeError, ValueError):
                px = 0.0
            if px <= 0:
                errors.append({
                    "coin_id": coin_id, "symbol": venue_symbol,
                    "error": "skipped: no snapshot price for sizing",
                })
                continue
            current_qty = max(0.0, pre_balances.get(base, 0.0))
            if target <= 0:
                if current_qty <= 0:
                    continue  # nothing live to close — not an error
                side, qty = OrderSide.SELL, current_qty
            else:
                delta_qty = target / px - current_qty
                if abs(delta_qty) * px < min_trade:
                    continue  # dust -> hold, mirrors the allocator
                side = OrderSide.BUY if delta_qty > 0 else OrderSide.SELL
                qty = abs(delta_qty)
            client_order_id = f"live-c{cycle}-{base}-{side}"[:32]
            try:
                request = OrderRequest(
                    symbol=venue_symbol, side=side, type=OrderType.MARKET,
                    qty=qty, client_order_id=client_order_id,
                )
            except Exception as exc:
                errors.append({
                    "coin_id": coin_id, "symbol": venue_symbol,
                    "error": f"skipped: invalid order ({exc})",
                })
                continue
            if dry_run:
                orders.append({"dry_run": True, "coin_id": coin_id,
                               **request.model_dump()})
                continue
            # Phase 6: pre-trade safety. Downsize if there is room, block if
            # not, and let the breaker refuse new risk on a losing day.
            order_dict = {
                "symbol": venue_symbol, "side": side,
                "qty": qty, "notional": qty * px,
            }
            verdict = enforce_exposure_caps(
                order_dict,
                account_size=float(account_size or 0.0),
                holdings=pre_balances,
                prices={b: (float((snapshot.get(_coin_for(b, b.lower())) or {}).get("current_price") or 0.0)) for b in pre_balances},
                max_total_pct=max_total_pct,
                max_single_pct=max_single_pct,
                positions=pre_positions,
            )
            if verdict["action"] == "block":
                errors.append({
                    "coin_id": coin_id, "symbol": venue_symbol,
                    "error": f"blocked by Phase 6 caps: {verdict['reason']}",
                })
                continue
            if verdict["action"] == "downsize":
                qty = verdict["qty"]
                errors.append({
                    "coin_id": coin_id, "symbol": venue_symbol,
                    "error": f"downsized by Phase 6 caps: {verdict['reason']}",
                })
                request = request.model_copy(
                    update={"qty": _format_decimal(Decimal(str(qty)))}
                )
            if breaker is not None:
                try:
                    breaker.guard(order_dict, now_iso=now_iso)
                except ExecutionHalted as exc:
                    errors.append({
                        "coin_id": coin_id, "symbol": venue_symbol,
                        "error": f"blocked by Phase 6 circuit breaker: {exc}",
                    })
                    continue
            try:
                result = self.place_order(request)
            except ExecutionError as exc:
                try:
                    still_open = self.get_open_orders(symbol=venue_symbol)
                    note = (f"{len(still_open)} open order(s) on {venue_symbol} "
                            "after failure — reconcile before retrying")
                except Exception:
                    note = "reconciliation query also failed"
                errors.append({
                    "coin_id": coin_id, "symbol": venue_symbol,
                    "error": str(exc), "reconcile": note,
                })
                continue
            orders.append({"coin_id": coin_id, **result.model_dump()})

        fills: list[dict[str, Any]] = []
        fills_note: str | None = None
        fills_since = (since_ms - 30_000) if since_ms is not None else None
        if fills_since is not None:
            fills_since = max(0, fills_since)
        if not dry_run:
            try:
                # Skew guard: the cycle timestamp is stamped just before this
                # call, so fills timestamped a few seconds earlier (clock
                # skew, same-second granularity) must not be excluded.
                fills = [
                    f.model_dump() for f in self.get_fills(since=fills_since)
                ]
            except ExecutionError as exc:
                fills_note = str(exc)
        quote = self.quote_currency.upper()
        fee_total = 0.0
        fee_unconverted: list[dict[str, Any]] = []
        for f in fills:
            try:
                raw_fee = float(f.get("fee") or 0.0)
            except (TypeError, ValueError):
                raw_fee = 0.0
            if raw_fee == 0:
                continue
            fee_ccy = str(f.get("fee_currency") or "").upper()
            if fee_ccy == quote:
                fee_total += raw_fee
                continue
            base_leg = str(f.get("symbol") or "").upper().split("-")[0]
            try:
                fill_px = float(f.get("price") or 0.0)
            except (TypeError, ValueError):
                fill_px = 0.0
            if fee_ccy and fee_ccy == base_leg and fill_px > 0:
                fee_total += raw_fee * fill_px
            else:
                fee_unconverted.append({
                    "trade_id": f.get("trade_id"), "order_id": f.get("order_id"),
                    "fee": raw_fee, "fee_currency": f.get("fee_currency"),
                    "note": "fee currency unknown or unpriced — excluded from fee_total",
                })
        invested = 0.0
        open_positions: list[dict[str, Any]] = []
        for asset, qty in pre_balances.items():
            if qty <= 0:
                continue
            if asset == quote:
                continue  # cash is not a position (paper parity)
            cid = _coin_for(asset, asset.lower())
            try:
                asset_px = float((snapshot.get(cid) or {}).get("current_price") or 0.0)
            except (TypeError, ValueError):
                asset_px = 0.0
            market_value = qty * asset_px if asset_px > 0 else 0.0
            invested += market_value
            # No avg_cost / unrealized_pnl: the venue keeps no cost basis,
            # so unlike paper we report price + market_value only.
            open_positions.append({
                "coin_id": cid, "asset": asset, "qty": qty,
                "price": asset_px, "market_value": market_value,
            })

        account = float(account_size or 0.0)
        summary: dict[str, Any] = {
            "mode": "live",
            "exchange": self.name,
            "dry_run": dry_run,
            "cycle": cycle,
            "ts": now_iso,
            "base_currency": base_currency,
            "orders": orders,
            "errors": errors,
            # This cycle's fills only — paper's `n_trades` is cumulative
            # ledger length, so the two are not comparable. The scope is stated
            # explicitly so a reader (or the rollout differ) never has to guess
            # the units of the number from the row's shape.
            "n_trades_scope": "per_cycle",
            "n_trades": len(fills),
            # Quote-currency fee total. Fees whose currency could not be
            # priced are reported, never silently mixed in.
            "fee_total": fee_total,
            "fee_currency": quote,
            "fee_unconverted": fee_unconverted,
            "realized_pnl": -fee_total,
            "unrealized_pnl": 0.0,
            "total_pnl": -fee_total,
            "equity": account - fee_total,
            "invested": invested,
            "open_positions": open_positions,
        }
        if fills_note:
            summary["fills_note"] = fills_note
        if not dry_run:
            try:
                log_path = Path(os.getenv("EXECUTION_LOG_PATH", "output/live_executions.jsonl"))
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
            except Exception:
                pass
        return summary

    # -- reconciliation (Phase 5) ----------------------------------------------
    #
    # A local ledger built by replaying fills drifts from reality: fills that
    # straddle a watermark, partial fills and duplicates all corrupt a running
    # cost basis. The venue's wallet balances are the single source of truth
    # for *how much we hold*; fills only supply the *history* (prices, fees
    # and side) needed to realize P&L and build a cost basis from scratch.
    #
    # Strategy: rebuild positions from a persistent local ledger that is
    # itself corrected against venue balances. Each reconcile call:
    #   1. fetches current balances (truth for qty),
    #   2. nets the new fills per coin into buy/sell flow,
    #   3. rewrites cost basis so qty and cost stay consistent,
    #   4. emits one ledger row per fill (paper-shaped) plus a correction row
    #      when the venue qty disagrees with the ledger-derived qty.

    def _fills_by_coin(self, fills: list[Fill]) -> dict[str, list[Fill]]:
        """Group fills by lowercase base asset (``BTC-RLS`` -> ``btc``)."""
        out: dict[str, list[Fill]] = {}
        for f in fills:
            symbol = str(f.symbol or "").upper()
            if not symbol:
                continue
            base = symbol.split("-", 1)[0].strip().lower()
            if not base or base == self.quote_currency.lower():
                continue
            out.setdefault(base, []).append(f)
        return out

    def reconcile(
        self,
        fills: list[Fill],
        positions: dict[str, dict[str, float]],
        *,
        cycle: int | None = None,
        now_iso: str | None = None,
        coin_ids_by_symbol: dict[str, str] | None = None,
    ) -> tuple[dict[str, dict[str, float]], list[dict[str, Any]]]:
        """Reconcile a batch of fills into local position state + ledger rows.

        Balance is the source of truth for *quantity*; fills supply *history*
        (price, side, fee) so realized P&L and cost basis can be derived
        rather than accumulated by drift-prone replay. For each coin the
        fills are written as paper-shaped ledger rows, then the local
        quantity is corrected to the wallet's, with a ``correction`` row
        recording any disagreement so nothing changes silently.

        ``cycle``/``now_iso`` label every row so the panel attributes them
        to a cycle exactly as it does paper rows.

        ``coin_ids_by_symbol`` maps venue tickers (``BTC``) to ``coin_id``
        (``bitcoin``). Positions are keyed by ``coin_id`` everywhere else in
        the flow (risk snapshot, paper ledger, panel), but fills and wallets
        only ever speak tickers; without this map every live position would
        be keyed ``btc`` and then price at zero when looked up as
        ``bitcoin``. Unmapped assets fall back to the lowercase ticker, which
        is at least stable across cycles.
        """
        symbol_to_coin = coin_ids_by_symbol or {}
        balances = {
            # `total`, not `free`: funds locked in an open order are still
            # ours, and reading them as 0 drops the position while the order
            # is still working (plus a spurious `close` row).
            b.asset.lower(): float(b.total or 0.0) for b in self.get_balances()
        }
        by_asset = self._fills_by_coin(fills)
        # Normalise both sources onto `coin_id` before touching positions,
        # since the rest of the flow (risk snapshot, paper ledger, panel)
        # speaks `coin_id` while the venue only knows tickers.
        by_coin = {
            symbol_to_coin.get(asset.upper(), asset): list(asset_fills)
            for asset, asset_fills in by_asset.items()
        }
        balances = {
            symbol_to_coin.get(asset.upper(), asset): qty
            for asset, qty in balances.items()
        }
        updated: dict[str, dict[str, float]] = {
            cid: dict(pos) for cid, pos in positions.items()
        }
        ledger: list[dict[str, Any]] = []
        ts = now_iso or ""
        quote = self.quote_currency.upper()

        def fee_to_quote(fee: float, fee_ccy: str, price: float) -> float:
            """Quote value of a fee charged in base/quote (0 when unknown)."""
            if fee <= 0 or not fee_ccy:
                return 0.0
            if fee_ccy == quote:
                return fee
            if price > 0:
                return fee * price
            return 0.0

        for coin_id in sorted(set(by_coin) | set(updated)):
            venue_qty = max(0.0, balances.get(coin_id, 0.0))
            pos = updated.setdefault(coin_id, {"qty": 0.0, "avg_cost": 0.0})
            cost = float(pos.get("avg_cost") or 0.0)
            prior_qty = float(pos.get("qty") or 0.0)
            coin_fills = by_coin.get(coin_id, [])

            # -- 1. one ledger row per fill (paper-shaped) --------------------
            # A zero cost basis means "bought before this ledger existed"
            # (a wallet import, or a position the fills do not explain).
            # Deriving realized P&L from a 0 cost books the entire proceeds
            # as one enormous *profit*, which flows straight into the
            # daily-loss breaker and can keep it permanently disarmed. So
            # refuse to invent a number: the row carries no realized_pnl
            # and a note saying why, so the operator can see it rather
            # than trust a figure computed from nothing.
            for f in sorted(coin_fills, key=lambda x: int(x.ts or 0)):
                qty = abs(float(f.qty or 0.0))
                price = float(f.price or 0.0)
                if qty <= 0 or price <= 0:
                    continue  # unpriced fill: cannot contribute to P&L
                fee = float(f.fee or 0.0)
                fee_ccy = str(f.fee_currency or "").upper()
                side = str(f.side or "").lower()
                row = {
                    "ts": _epoch_ms_to_iso(f.ts) or ts,
                    "cycle": cycle,
                    "coin_id": coin_id,
                    "side": side,
                    "qty": qty,
                    "price": price,
                    "amount": qty * price,
                    "realized_pnl": None,
                    "fee": fee or None,
                    "fee_currency": fee_ccy or None,
                    "trade_id": f.trade_id,
                    "order_id": f.order_id,
                    "source": "live_fill",
                }
                if side == OrderSide.SELL:
                    if cost > 0:
                        row["realized_pnl"] = (
                            qty * (price - cost)
                            - fee_to_quote(fee, fee_ccy, price)
                        )
                    else:
                        row["note"] = (
                            "no cost basis for this coin — realized P&L "
                            "withheld rather than computed against a 0 cost"
                        )
                        row["source"] = "live_fill_no_basis"
                ledger.append(row)

            # -- 2. cost basis from the batch --------------------------------
            bought_qty = sum(
                float(f.qty or 0.0) for f in coin_fills
                if str(f.side or "").lower() == OrderSide.BUY
            )
            sold_qty = sum(
                float(f.qty or 0.0) for f in coin_fills
                if str(f.side or "").lower() == OrderSide.SELL
            )
            if bought_qty > 0:
                bought_value = sum(
                    float(f.qty or 0.0) * float(f.price or 0.0) for f in coin_fills
                    if str(f.side or "").lower() == OrderSide.BUY
                )
                bought_fees = sum(
                    fee_to_quote(
                        float(f.fee or 0.0),
                        str(f.fee_currency or "").upper(),
                        float(f.price or 0.0),
                    )
                    for f in coin_fills
                    if str(f.side or "").lower() == OrderSide.BUY
                )
                batch_cost = (bought_value + bought_fees) / bought_qty
                if sold_qty <= 0 and (prior_qty + bought_qty) > 0:
                    # Blend the batch into whatever the ledger already held.
                    pos["avg_cost"] = (
                        prior_qty * cost + bought_qty * batch_cost
                    ) / (prior_qty + bought_qty)
                else:
                    # Buys and sells in one batch: the sell realized against
                    # the old cost, so the residual basis is the batch's.
                    pos["avg_cost"] = batch_cost

            # -- 3. wallet wins for quantity ----------------------------------
            # A correction row is only honest when the wallet DISAGREES with
            # what the fills explain. On a first buy the fills fully account
            # for the position, so no correction may be emitted — labeling
            # every open a "correction" would make the audit trail noise.
            expected_qty = prior_qty + bought_qty - sold_qty
            if venue_qty > 0:
                pos["qty"] = venue_qty
                if abs(venue_qty - expected_qty) > 1e-9:
                    ledger.append({
                        "ts": ts, "cycle": cycle, "coin_id": coin_id,
                        "side": "correction",
                        "qty": venue_qty - expected_qty,
                        "price": 0.0, "amount": 0.0, "realized_pnl": None,
                        "note": (f"venue qty {venue_qty:g} vs fills-derived "
                                 f"{expected_qty:g} — local qty corrected to "
                                 "the wallet"),
                        "source": "live_correction",
                    })
            else:
                if prior_qty > 0:
                    ledger.append({
                        "ts": ts, "cycle": cycle, "coin_id": coin_id,
                        "side": "close", "qty": prior_qty,
                        "price": 0.0, "amount": 0.0, "realized_pnl": None,
                        "note": "wallet holds none — position closed by reconciliation",
                        "source": "live_correction",
                    })
                updated.pop(coin_id, None)

        return updated, ledger


register_execution_client("nobitex", NobitexExecutionClient)

__all__ = ["NobitexExecutionClient"]
