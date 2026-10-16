"""Tests for NobitexExecutionClient — the live execution client (Phase 3).

All tests use a fake ``requests.Session`` so nothing touches the network.
"""

from __future__ import annotations

import json
import os

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
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "params": params,
                "data": kwargs.get("data"),
                "headers": kwargs.get("headers"),
            }
        )
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


# ---------------------------------------------------------------------------
# Ed25519 API-key auth
# ---------------------------------------------------------------------------
# Fixed seed/timestamp/endpoint give a reproducible vector, so a refactor that
# changes the signed string (dropping the query, altering key order, adding a
# separator) fails here instead of silently 401ing against the venue.
_ED_SEED = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="  # bytes 0..31 — a THROWAWAY test vector, NOT a real key
_ED_PUBKEY = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg="


def _signed_client(session=None, **kw):
    """A client configured for Ed25519 API-key auth (never touches the net)."""
    return NobitexExecutionClient(
        api_key=_ED_PUBKEY,
        api_secret=_ED_SEED,
        base_url="https://apiv2.nobitex.ir",
        quote_currency="rls",
        session=session or FakeSession(),
        **kw,
    )


def test_signature_matches_documented_vector(monkeypatch):
    """Signature over timestamp+METHOD+full_path+raw_body, URL-safe Base64."""
    from crypto_council_flow.tools.execution_nobitex import (
        _build_signed_body,
        _load_ed25519_private_key,
        _sign_request,
    )

    monkeypatch.setattr(
        "crypto_council_flow.tools.execution_nobitex.time.time",
        lambda: 1700000000,
    )
    key = _load_ed25519_private_key(_ED_SEED)
    body = _build_signed_body({"type": "spot"})
    assert body == '{"type":"spot"}'  # compact, exactly as sent

    sig = _sign_request(key, "POST", "/users/wallets/list", body, "1700000000")
    assert sig == (
        "DkMoGA5hHmOqaGhsoz8c1xhc_NNoRgSfaBz8-ltzUbKTxKOpmIfGhmVG2QfDpCax"
        "-FI8OEzBcXuBrm0wt3AmAA=="
    )


def test_public_key_derives_from_private_seed():
    """api_key (Nobitex-Key) must be the public half of api_secret."""
    import base64

    from crypto_council_flow.tools.execution_nobitex import (
        _load_ed25519_private_key,
    )

    derived = _load_ed25519_private_key(_ED_SEED).public_key().public_bytes_raw()
    assert derived == base64.urlsafe_b64decode(_ED_PUBKEY + "=")


def test_ed25519_request_headers_and_exact_body(monkeypatch):
    """Signed requests carry the three Nobitex headers and byte-exact body."""
    monkeypatch.setattr(
        "crypto_council_flow.tools.execution_nobitex.time.time",
        lambda: 1700000000,
    )
    s = FakeSession([FakeResponse({"status": "ok", "wallets": []})])
    c = _signed_client(session=s)
    # Avoid the live /v2/options lookup (no network in tests).
    monkeypatch.setattr(c, "_min_notional_for", lambda *_: None)

    c._request("POST", "/users/wallets/list", json_body={"type": "spot"})

    call = s.calls[0]
    assert call["json"] is None  # legacy json= path must not be used
    assert call["data"] == '{"type":"spot"}'  # exact bytes that were signed
    h = call["headers"]
    assert h["Nobitex-Key"] == _ED_PUBKEY
    assert h["Nobitex-Timestamp"] == "1700000000"
    assert h["Nobitex-Signature"] == (
        "DkMoGA5hHmOqaGhsoz8c1xhc_NNoRgSfaBz8-ltzUbKTxKOpmIfGhmVG2QfDpCax"
        "-FI8OEzBcXuBrm0wt3AmAA=="
    )
    # Legacy token auth must not be sent alongside API-key auth.
    assert "Authorization" not in call["headers"]


def test_query_string_is_part_of_the_signed_path(monkeypatch):
    """GET with a query string signs the full path, not just the endpoint."""
    monkeypatch.setattr(
        "crypto_council_flow.tools.execution_nobitex.time.time",
        lambda: 1700000001,
    )
    s = FakeSession([FakeResponse({"status": "ok", "trades": []})])
    c = _signed_client(session=s)

    c._request("GET", "/market/trades/list", params={"fromId": 123})

    call = s.calls[0]
    assert call["data"] == ""  # no body to sign
    assert "Nobitex-Signature" in call["headers"]
    assert call["headers"]["Nobitex-Timestamp"] == "1700000001"


def test_signature_covers_query_string_not_endpoint_only():
    """Proof the query string is inside the signed path, not beside it."""
    from crypto_council_flow.tools.execution_nobitex import (
        _sign_request,
        _load_ed25519_private_key,
    )

    key = _load_ed25519_private_key(_ED_SEED)
    with_qs = _sign_request(key, "GET", "/market/orders/list?fromId=123", "", "1700000001")
    without = _sign_request(key, "GET", "/market/orders/list", "", "1700000001")
    assert with_qs != without  # dropping the query would 401 at the venue


def test_each_retry_is_resigned_with_a_fresh_timestamp(monkeypatch):
    """A retry must not replay a stale timestamp (venue allows 30s skew)."""
    stamps = iter([1700000000, 1700000060])
    monkeypatch.setattr(
        "crypto_council_flow.tools.execution_nobitex.time.time",
        lambda: next(stamps),
    )
    s = FakeSession([FakeResponse({"status": "ok"}, status_code=500),
                     FakeResponse({"status": "ok"})])
    c = _signed_client(session=s)

    c._request("POST", "/users/wallets/list", json_body={"type": "spot"})

    ts = [call["headers"]["Nobitex-Timestamp"] for call in s.calls]
    assert ts == ["1700000000", "1700000060"]
    assert s.calls[0]["headers"]["Nobitex-Signature"] != (
        s.calls[1]["headers"]["Nobitex-Signature"]
    )


def test_token_auth_still_works_without_a_secret():
    """Legacy v2 token auth is unchanged: Authorization header, json= body."""
    s = FakeSession([FakeResponse({"status": "ok", "wallets": []})])
    c = NobitexExecutionClient(
        api_key="TOKEN123",
        base_url="https://apiv2.nobitex.ir",
        quote_currency="rls",
        session=s,
    )
    c._request("POST", "/users/wallets/list", json_body={"type": "spot"})

    call = s.calls[0]
    assert call["headers"] is None  # nothing per-request
    assert call["json"] == {"type": "spot"}
    assert c._session.headers["Authorization"] == "Token TOKEN123"
    assert c._ed25519_key is None


def test_bad_secret_fails_loudly():
    """A malformed private key must not silently fall back to token auth."""
    with pytest.raises(ExecutionError) as exc:
        NobitexExecutionClient(
            api_key=_ED_PUBKEY,
            api_secret="not-a-key",
            quote_currency="rls",
            session=FakeSession(),
        )
    assert "EXECUTION_API_SECRET" in str(exc.value)


def test_secret_read_from_env(monkeypatch):
    """EXECUTION_API_SECRET (and the EXCHANGE_ alias) select signed auth."""
    monkeypatch.setenv("EXECUTION_API_KEY", _ED_PUBKEY)
    monkeypatch.setenv("EXECUTION_API_SECRET", _ED_SEED)
    c = NobitexExecutionClient(session=FakeSession(), quote_currency="rls")
    assert c._ed25519_key is not None
    # API-key auth owns the Authorization header space entirely.
    assert "Authorization" not in c._session.headers


def test_exchange_alias_secret_read_from_env(monkeypatch):
    """EXCHANGE_API_SECRET is the accepted alias for EXECUTION_API_SECRET."""
    monkeypatch.setenv("EXCHANGE_API_KEY", _ED_PUBKEY)
    monkeypatch.setenv("EXCHANGE_API_SECRET", _ED_SEED)
    c = NobitexExecutionClient(session=FakeSession())
    assert c._ed25519_key is not None


def test_credentials_are_isolated_between_tests(monkeypatch):
    """A leaked EXECUTION_API_SECRET must not flip later clients to signed auth.

    Regression test for a real leak: a test that set the env var and then
    failed (so monkeypatch never undid it) left every later client in the
    session signing requests, which sends no Authorization header at all —
    so token-auth assertions would silently stop testing token auth.

    The autouse fixture in conftest.py deletes these vars; this test fails if
    that fixture is ever removed or narrowed.
    """
    # Prove the fixture is doing the work: without it, this would be set.
    monkeypatch.setenv("EXECUTION_API_SECRET", _ED_SEED)
    assert os.environ.get("EXECUTION_API_SECRET") is not None

    monkeypatch.delenv("EXECUTION_API_SECRET")
    c = NobitexExecutionClient(api_key="TOKEN123", session=FakeSession())
    assert c._ed25519_key is None
    assert c._session.headers["Authorization"] == "Token TOKEN123"


def test_no_ambient_credentials_during_tests():
    """The suite must not see credentials from the developer's own shell."""
    for var in (
        "EXECUTION_API_KEY",
        "EXECUTION_API_SECRET",
        "EXCHANGE_API_KEY",
        "EXCHANGE_API_SECRET",
    ):
        assert var not in os.environ, f"{var} leaked into the test session"
