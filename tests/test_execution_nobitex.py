"""Tests for NobitexExecutionClient — the live execution client (Phase 3).

All tests use a fake ``requests.Session`` so nothing touches the network.
"""

from __future__ import annotations

import json

import pytest
import requests

from crypto_council_flow.tools.execution_base import (
    ExecutionError,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from crypto_council_flow.tools.execution_nobitex import NobitexExecutionClient


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession(requests.Session):
    """Records calls and replays queued responses (or exceptions)."""

    def __init__(self, responses=None, exc=None):
        super().__init__()
        self.calls = []
        self._responses = list(responses or [])
        self._exc = exc

    def request(self, method, url, json=None, params=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "json": json, "params": params})
        if self._exc is not None:
            raise self._exc
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse({"status": "ok"})


def _client(session=None, **kw):
    c = NobitexExecutionClient(
        api_key="TOKEN123",
        base_url="https://apiv2.nobitex.ir",
        quote_currency="rls",
        session=session or FakeSession(),
        **kw,
    )
    # Skip the live /v2/options lookup in tests (no network); the min-notional
    # path has its own dedicated tests that monkeypatch _min_notional_for.
    c._min_notional_cache = {}
    return c


# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("BTC-RLS", ("btc", "rls")),
        ("btc/rls", ("btc", "rls")),
        ("ETH_USDT", ("eth", "usdt")),
        ("BTC", ("btc", "rls")),  # bare base pairs with configured quote
    ],
)
def test_resolve_pair(symbol, expected):
    assert _client()._resolve_pair(symbol) == expected


def test_empty_symbol_rejected():
    with pytest.raises(ExecutionError):
        _client()._resolve_pair("")


# ---------------------------------------------------------------------------
# Order payload translation
# ---------------------------------------------------------------------------

def test_limit_buy_payload_fields():
    c = _client()
    req = OrderRequest(
        symbol="BTC-RLS", side=OrderSide.BUY, type=OrderType.LIMIT,
        qty=0.6, limit_price=520000000.0, client_order_id="order1",
    )
    p = c.build_order_payload(req)
    assert p["type"] == "buy"
    assert p["execution"] == "limit"
    assert p["srcCurrency"] == "btc"
    assert p["dstCurrency"] == "rls"
    assert p["amount"] == "0.6"
    assert p["price"] == "520000000"
    assert p["clientOrderId"] == "order1"


def test_market_sell_uses_qty_and_no_price():
    c = _client()
    req = OrderRequest(
        symbol="DOGE-RLS", side=OrderSide.SELL, type=OrderType.MARKET, qty=64
    )
    p = c.build_order_payload(req)
    assert p["type"] == "sell"
    assert p["execution"] == "market"
    assert p["amount"] == "64"
    assert "price" not in p


def test_market_notional_sizing_fetches_last_price(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_last_price", lambda s, d: __import__("decimal").Decimal("100"))
    req = OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY, type=OrderType.MARKET, notional=1000)
    p = c.build_order_payload(req)
    assert p["amount"] == "10"  # 1000 / 100
    assert p["execution"] == "market"


def test_market_notional_without_price_raises(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_last_price", lambda s, d: None)
    req = OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY, type=OrderType.MARKET, notional=1000)
    with pytest.raises(ExecutionError):
        c.build_order_payload(req)


def test_below_min_notional_rejected(monkeypatch):
    """SmallOrder is enforced pre-flight against the venue minimum."""
    c = _client()
    monkeypatch.setattr(c, "_min_notional_for", lambda q: 500000.0)
    monkeypatch.setattr(c, "_last_price", lambda s, d: __import__("decimal").Decimal("1000"))
    req = OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY, type=OrderType.LIMIT,
                       qty=1, limit_price=1000.0)  # 1000 < 500000
    with pytest.raises(ExecutionError) as ei:
        c.build_order_payload(req)
    assert "minimum" in str(ei.value).lower()


def test_client_order_id_truncated_to_32():
    c = _client()
    req = OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY, type=OrderType.MARKET,
                       qty=1, client_order_id="x" * 40)
    p = c.build_order_payload(req)
    assert len(p["clientOrderId"]) == 32


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

_ORDER = {
    "id": 25, "type": "buy", "execution": "Limit", "market": "BTC-RLS",
    "price": "520000000", "amount": "0.6", "matchedAmount": "0.6",
    "unmatchedAmount": "0", "status": "Done", "fee": "1000",
    "totalOrderPrice": "312000000", "averagePrice": "520000000",
    "created_at": "2018-11-28T11:36:13.592827+00:00", "clientOrderId": "order1",
    "dstCurrency": "RLS",
}


def test_place_order_maps_response():
    sess = FakeSession([FakeResponse({"status": "ok", "order": _ORDER})])
    c = _client(sess)
    res = c.place_order(
        OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY, type=OrderType.LIMIT,
                     qty=0.6, limit_price=520000000.0)
    )
    assert res.order_id == "25"
    assert res.status == OrderStatus.FILLED
    assert res.filled_qty == pytest.approx(0.6)
    assert res.avg_fill_price == pytest.approx(520000000.0)
    assert res.fee == pytest.approx(1000.0)
    # URL is the order-add endpoint
    assert sess.calls[0]["url"].endswith("/market/orders/add")


def test_place_order_failed_body_raises():
    sess = FakeSession([
        FakeResponse({"status": "failed", "code": "InvalidOrderPrice",
                      "message": "Price Validation Failed"})
    ])
    c = _client(sess)
    with pytest.raises(ExecutionError) as ei:
        c.place_order(OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY,
                                   type=OrderType.MARKET, qty=1))
    assert "InvalidOrderPrice" in str(ei.value)


def test_place_order_does_not_retry_on_network_timeout():
    """A timeout during placement is ambiguous -> exactly one attempt."""
    sess = FakeSession(exc=requests.Timeout("boom"))
    c = _client(sess)
    with pytest.raises(ExecutionError):
        c.place_order(OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY,
                                   type=OrderType.MARKET, qty=1))
    assert len(sess.calls) == 1


def test_place_order_retries_on_429():
    """429 (rejected, not executed) IS safe to retry."""
    sess = FakeSession([
        FakeResponse({"status": "failed"}, status_code=429),
        FakeResponse({"status": "ok", "order": _ORDER}),
    ])
    c = _client(sess)
    res = c.place_order(OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY,
                                     type=OrderType.MARKET, qty=1))
    assert res.order_id == "25"
    assert len(sess.calls) == 2


def test_auth_header_present():
    c = _client()
    assert c._session.headers.get("Authorization") == "Token TOKEN123"


# ---------------------------------------------------------------------------
# balances
# ---------------------------------------------------------------------------

def test_get_balances_maps_wallets():
    payload = {
        "status": "ok",
        "wallets": [
            {"currency": "btc", "balance": "0.6", "blockedBalance": "0.1",
             "activeBalance": "0.5"},
            {"currency": "rls", "balance": "1000000", "blockedBalance": "0",
             "activeBalance": "1000000"},
        ],
    }
    sess = FakeSession([FakeResponse(payload)])
    c = _client(sess)
    bals = {b.asset: b for b in c.get_balances()}
    assert bals["BTC"].total == pytest.approx(0.6)
    assert bals["BTC"].locked == pytest.approx(0.1)
    assert bals["BTC"].free == pytest.approx(0.5)
    assert bals["RLS"].free == pytest.approx(1_000_000.0)
    assert sess.calls[0]["url"].endswith("/users/wallets/list")


def test_get_balances_filters_assets():
    payload = {"status": "ok", "wallets": [
        {"currency": "btc", "balance": "0.6", "blockedBalance": "0", "activeBalance": "0.6"},
        {"currency": "eth", "balance": "2", "blockedBalance": "0", "activeBalance": "2"},
    ]}
    c = _client(FakeSession([FakeResponse(payload)]))
    bals = c.get_balances(assets=["BTC"])
    assert [b.asset for b in bals] == ["BTC"]


# ---------------------------------------------------------------------------
# get_order / open orders / cancel
# ---------------------------------------------------------------------------

def test_get_order_by_numeric_id():
    sess = FakeSession([FakeResponse({"status": "ok", "order": _ORDER})])
    c = _client(sess)
    res = c.get_order("BTC-RLS", "5684")
    assert res is not None
    assert res.order_id == "25"
    assert sess.calls[0]["json"] == {"id": 5684}


def test_get_order_by_client_id_when_not_numeric():
    sess = FakeSession([FakeResponse({"status": "ok", "order": _ORDER})])
    c = _client(sess)
    c.get_order("BTC-RLS", "order1")
    assert sess.calls[0]["json"] == {"clientOrderId": "order1"}


def test_get_order_not_found_returns_none():
    sess = FakeSession([
        FakeResponse({"status": "failed", "code": "OrderNotFound", "message": "no"})
    ])
    c = _client(sess)
    assert c.get_order("BTC-RLS", "order1") is None


def test_get_open_orders_maps_status_and_filters_pair():
    payload = {"status": "ok", "orders": [dict(_ORDER, status="Active", matchedAmount="0")]}
    sess = FakeSession([FakeResponse(payload)])
    c = _client(sess)
    orders = c.get_open_orders("BTC-RLS")
    assert orders[0].status == OrderStatus.OPEN
    assert sess.calls[0]["params"]["srcCurrency"] == "btc"
    assert sess.calls[0]["params"]["dstCurrency"] == "rls"
    assert sess.calls[0]["params"]["status"] == "open"


def test_cancel_order_numeric_id():
    sess = FakeSession([FakeResponse({
        "status": "ok", "updatedStatus": "Canceled", "order": dict(_ORDER, status="Canceled"),
    })])
    c = _client(sess)
    res = c.cancel_order("BTC-RLS", "5684")
    assert res.status == OrderStatus.CANCELED
    assert sess.calls[0]["json"]["order"] == 5684
    assert sess.calls[0]["json"]["status"] == "canceled"


# ---------------------------------------------------------------------------
# fills
# ---------------------------------------------------------------------------

_TRADE = {
    "id": 123412, "orderId": 1231222, "market": "USDT-RLS", "type": "sell",
    "price": "316800", "amount": "57.3605", "total": "18171806.4", "fee": "27257.7096",
    "timestamp": "2022-07-05T09:57:38.560820+00:00", "dstCurrency": "RLS",
}


def test_get_fills_maps_trades():
    sess = FakeSession([FakeResponse({"status": "ok", "trades": [_TRADE], "hasNext": False})])
    c = _client(sess)
    fills = c.get_fills()
    assert len(fills) == 1
    assert fills[0].trade_id == "123412"
    assert fills[0].side == OrderSide.SELL
    assert fills[0].qty == pytest.approx(57.3605)
    assert fills[0].fee == pytest.approx(27257.7096)
    assert fills[0].ts is not None


def test_get_fills_since_watermark_filters():
    old = dict(_TRADE, timestamp="2020-01-01T00:00:00+00:00")
    sess = FakeSession([FakeResponse({"status": "ok", "trades": [old], "hasNext": False})])
    c = _client(sess)
    assert c.get_fills(since=10**15) == []


def test_get_fills_paginates_until_hasnext_false():
    sess = FakeSession([
        FakeResponse({"status": "ok", "trades": [_TRADE], "hasNext": True}),
        FakeResponse({"status": "ok", "trades": [_TRADE], "hasNext": False}),
    ])
    c = _client(sess)
    fills = c.get_fills()
    assert len(fills) == 2
    assert len(sess.calls) == 2


# ---------------------------------------------------------------------------
# factory registration
# ---------------------------------------------------------------------------

def test_factory_live_mode_returns_nobitex(monkeypatch):
    from crypto_council_flow.tools.execution_base import get_execution_client

    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("EXECUTION_EXCHANGE", "nobitex")
    monkeypatch.setenv("EXECUTION_API_KEY", "TOKEN123")
    client = get_execution_client()
    assert isinstance(client, NobitexExecutionClient)
    assert client.name == "nobitex"
    assert client.is_simulated is False


def test_factory_halted_raises(monkeypatch):
    from crypto_council_flow.tools.execution_base import (
        ExecutionHalted,
        get_execution_client,
    )

    monkeypatch.setenv("EXECUTION_MODE", "halted")
    with pytest.raises(ExecutionHalted):
        get_execution_client()


# ---------------------------------------------------------------------------
# precision
# ---------------------------------------------------------------------------

def test_qty_rounds_down_price_rounds_half_up():
    from decimal import Decimal

    c = _client(qty_decimals=2, price_decimals=2)
    assert c._round_qty(Decimal("1.239")) == Decimal("1.23")   # down
    assert c._round_price(Decimal("1.235")) == Decimal("1.24")  # half-up


def test_order_amount_rounds_down_to_zero_rejected():
    c = _client(qty_decimals=2)
    req = OrderRequest(symbol="BTC-RLS", side=OrderSide.BUY, type=OrderType.MARKET, qty=0.001)
    with pytest.raises(ExecutionError):
        c.build_order_payload(req)  # rounds to 0.00
