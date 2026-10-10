"""Tests for NobitexExecutionClient.apply_plan — the live-apply path (Phase 4).

All tests use a fake ``requests.Session`` so nothing touches the network.
"""

from __future__ import annotations

import json

import pytest
import requests

from crypto_council_flow.tools.execution_base import OrderStatus
from crypto_council_flow.tools.execution_nobitex import NobitexExecutionClient


# ---------------------------------------------------------------------------
# Fake transport (same pattern as test_execution_nobitex.py)
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

    def request(self, method, url, json=None, params=None, timeout=None, **kwargs):  # pyright: ignore[reportIncompatibleMethodOverride] — fake transport, same pattern as test_execution_nobitex.py
        self.calls.append({"method": method, "url": url, "json": json, "params": params})
        if self._exc is not None:
            raise self._exc
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse({"status": "ok"})


_WALLETS = {
    "status": "ok",
    "wallets": [
        {"currency": "btc", "balance": "0.1", "blockedBalance": "0",
         "activeBalance": "0.1"},
        {"currency": "rls", "balance": "1000000000", "blockedBalance": "0",
         "activeBalance": "1000000000"},
    ],
}

_ORDER = {
    "id": 25, "type": "buy", "execution": "Market", "market": "BTC-RLS",
    "price": "5000000000", "amount": "0.1", "matchedAmount": "0.1",
    "unmatchedAmount": "0", "status": "Done", "fee": "500000",
    "totalOrderPrice": "500000000", "averagePrice": "5000000000",
    "created_at": "2026-01-01T12:00:00+00:00", "dstCurrency": "RLS",
}

_NO_TRADES = {"status": "ok", "trades": [], "hasNext": False}


def _client(session, **kw):
    c = NobitexExecutionClient(
        api_key="TOKEN123",
        base_url="https://apiv2.nobitex.ir",
        quote_currency="rls",
        session=session,
        **kw,
    )
    # Skip the live /v2/options lookup (no network in tests).
    c._min_notional_cache = {}
    return c


def _env(monkeypatch, tmp_path, dry_run="false"):
    monkeypatch.setenv("EXECUTION_DRY_RUN", dry_run)
    monkeypatch.setenv("EXECUTION_LOG_PATH", str(tmp_path / "live_executions.jsonl"))
    monkeypatch.delenv("EXECUTION_MIN_TRADE", raising=False)


def _snapshot(price=5_000_000_000.0):
    return {"bitcoin": {"current_price": price, "atr_pct": 0.02}}


def _posted_urls(sess):
    return [c["url"] for c in sess.calls]


def _no_caps() -> dict:
    """Opt out of Phase 6 caps so these tests exercise translation only.

    Phase 4 owns order *translation* (sizing, dust, fees, skew) and its
    fixtures post orders worth a large share of a small test account, which
    the Phase 6 single-position cap correctly refuses. Passing generous
    caps here keeps each test about one thing: caps are asserted separately
    in tests/test_execution_live_safety.py.
    """
    return {"max_total_pct": 1.0, "max_single_pct": 1.0}


# ---------------------------------------------------------------------------
# dry run — builds orders, posts nothing
# ---------------------------------------------------------------------------

def test_apply_plan_dry_run_posts_no_orders(tmp_path, monkeypatch):
    sess = FakeSession([FakeResponse(_WALLETS)])
    c = _client(sess)
    _env(monkeypatch, tmp_path, dry_run="true")
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 1_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=3,
    )
    assert out["mode"] == "live" and out["dry_run"] is True
    assert len(out["orders"]) == 1
    assert out["orders"][0]["dry_run"] is True
    assert out["orders"][0]["symbol"] == "BTC-RLS"  # symbol -> venue pair
    assert out["orders"][0]["side"] == "buy"
    assert out["orders"][0]["client_order_id"].startswith("live-c3-BTC")
    assert not any(u.endswith("/market/orders/add") for u in _posted_urls(sess))
    assert out["errors"] == []
    # summary keys match what the panel/history writers read
    for key in ("realized_pnl", "unrealized_pnl", "total_pnl", "equity",
                "invested", "open_positions", "n_trades"):
        assert key in out


def test_apply_plan_dry_run_skips_close_with_no_balance(tmp_path, monkeypatch):
    """A close with nothing live held is a no-op, not an error."""
    sess = FakeSession([FakeResponse(_WALLETS)])  # no ETH balance
    c = _client(sess)
    _env(monkeypatch, tmp_path, dry_run="true")
    plan = {"actions": [
        {"coin_id": "ethereum", "symbol": "ETH", "action": "close",
         "current": 100.0, "target": 0.0},
    ]}
    out = c.apply_plan(
        plan=plan,
        risk_snapshot={"ethereum": {"current_price": 400_000_000.0, "atr_pct": 0.03}},
        account_size=1_000_000_000.0, base_currency="rls",
        now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    )
    assert out["orders"] == [] and out["errors"] == []


# ---------------------------------------------------------------------------
# real placement
# ---------------------------------------------------------------------------

def test_apply_plan_places_market_buy_by_delta_qty(tmp_path, monkeypatch):
    sess = FakeSession([
        FakeResponse(_WALLETS),
        FakeResponse({"status": "ok", "order": _ORDER}),
        FakeResponse(_NO_TRADES),
    ])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 1_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    **_no_caps(),
    )
    assert any(u.endswith("/market/orders/add") for u in _posted_urls(sess))
    assert len(out["orders"]) == 1
    assert out["orders"][0]["status"] == OrderStatus.FILLED
    assert out["errors"] == []
    assert out["n_trades"] == 0  # empty trade history this cycle
    assert out["realized_pnl"] == pytest.approx(0.0)
    # JSONL audit row appended
    rows = (tmp_path / "live_executions.jsonl").read_text().strip().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["mode"] == "live"


def test_apply_plan_close_sells_full_free_balance(tmp_path, monkeypatch):
    sess = FakeSession([
        FakeResponse(_WALLETS),
        FakeResponse({"status": "ok", "order": dict(_ORDER, type="sell")}),
        FakeResponse(_NO_TRADES),
    ])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "close",
         "current": 500_000_000.0, "target": 0.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=2,
    )
    assert len(out["orders"]) == 1
    assert out["orders"][0]["side"] == "sell"
    assert out["orders"][0]["qty"] == pytest.approx(0.1)  # full free balance


def test_apply_plan_dust_delta_skipped(tmp_path, monkeypatch):
    sess = FakeSession([FakeResponse(_WALLETS)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MIN_TRADE", "1000000")  # 1M rials
    # delta = 100 rials of BTC — dust
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 500_000_100.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    )
    assert out["orders"] == [] and out["errors"] == []
    assert not any(u.endswith("/market/orders/add") for u in _posted_urls(sess))


def test_apply_plan_missing_symbol_or_price_recorded_not_raised(
    tmp_path, monkeypatch
):
    sess = FakeSession([FakeResponse(_WALLETS)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "action": "open", "target": 1_000_000.0},  # no symbol
        {"coin_id": "dogecoin", "symbol": "DOGE", "action": "open",
         "target": 1_000_000.0},  # no snapshot price
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    )
    assert out["orders"] == []
    assert len(out["errors"]) == 2
    assert not any(u.endswith("/market/orders/add") for u in _posted_urls(sess))


def test_apply_plan_failure_reconciles_and_continues(tmp_path, monkeypatch):
    """A rejected placement is never retried; a reconcile note is recorded."""
    sess = FakeSession([
        FakeResponse(_WALLETS),
        FakeResponse({"status": "failed", "code": "SmallOrder",
                      "message": "too small"}),
        FakeResponse({"status": "ok", "orders": []}),  # get_open_orders
        FakeResponse(_NO_TRADES),
    ])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 1_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    **_no_caps(),
    )
    assert out["orders"] == []
    assert len(out["errors"]) == 1
    assert "reconcile" in out["errors"][0]
    # exactly one placement attempt (no blind retry)
    adds = [u for u in _posted_urls(sess) if u.endswith("/market/orders/add")]
    assert len(adds) == 1


def test_apply_plan_fee_from_fills_reduces_equity(tmp_path, monkeypatch):
    # SELL fill with the fee in the quote asset: counted directly.
    trade = {
        "id": 9, "orderId": 25, "market": "BTC-RLS", "type": "sell",
        "price": "5000000000", "amount": "0.1", "total": "500000000",
        "fee": "500000", "timestamp": "2026-01-01T13:00:00+00:00",
        "srcCurrency": "BTC", "dstCurrency": "RLS",
    }
    sess = FakeSession([
        FakeResponse(_WALLETS),
        FakeResponse({"status": "ok", "order": _ORDER}),
        FakeResponse({"status": "ok", "trades": [trade], "hasNext": False}),
    ])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 1_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    **_no_caps(),
    )
    assert out["n_trades"] == 1
    assert out["fee_total"] == pytest.approx(500000.0)
    assert out["realized_pnl"] == pytest.approx(-500000.0)
    assert out["unrealized_pnl"] == pytest.approx(0.0)
    assert out["equity"] == pytest.approx(2_000_000_000.0 - 500000.0)


# ---------------------------------------------------------------------------
# main.py wiring — live mode delegates to the client's apply_plan
# ---------------------------------------------------------------------------

def test_update_paper_ledger_live_delegates_to_apply_plan(monkeypatch):
    import crypto_council_flow.main as main

    calls = {}

    class FakeLive:
        name = "fake-live"

        def apply_plan(self, **kw):
            calls.update(kw)
            return {"mode": "live"}

    monkeypatch.setattr(
        main, "_execution_client_for_ledger", lambda base_currency=None: FakeLive()
    )
    out = main._update_paper_ledger(
        {"actions": []}, {}, 1000.0, "usd", "t", 1,
    )
    assert out == {"mode": "live"}
    assert calls["cycle"] == 1 and calls["account_size"] == 1000.0


# ---------------------------------------------------------------------------
# Review fixes — cash exclusion, fee units, dry-run log, skew guard
# ---------------------------------------------------------------------------

def test_apply_plan_open_positions_exclude_cash_and_match_schema(
    tmp_path, monkeypatch
):
    """Quote cash (RLS) is not a position; entries carry price/market_value."""
    sess = FakeSession([FakeResponse(_WALLETS)])
    c = _client(sess)
    _env(monkeypatch, tmp_path, dry_run="true")
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 500_000_100.0},  # dust: no order
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    )
    assets = [p["asset"] for p in out["open_positions"]]
    assert "RLS" not in assets  # cash excluded (paper parity)
    assert assets == ["BTC"]
    pos = out["open_positions"][0]
    assert pos["coin_id"] == "bitcoin"
    assert pos["qty"] == pytest.approx(0.1)
    assert pos["price"] == pytest.approx(5_000_000_000.0)
    assert pos["market_value"] == pytest.approx(500_000_000.0)
    assert "avg_cost" not in pos  # venue keeps no cost basis
    assert out["invested"] == pytest.approx(500_000_000.0)


def test_apply_plan_base_currency_fee_converted_via_fill_price(
    tmp_path, monkeypatch
):
    """BUY fill fee in base asset (BTC) converts to quote via fill price."""
    trade = {
        "id": 10, "orderId": 26, "market": "BTC-RLS", "type": "buy",
        "price": "5000000000", "amount": "0.1", "total": "500000000",
        "fee": "0.0001", "timestamp": "2026-01-01T13:00:00+00:00",
        "srcCurrency": "BTC", "dstCurrency": "RLS",
    }
    sess = FakeSession([
        FakeResponse(_WALLETS),
        FakeResponse({"status": "ok", "order": _ORDER}),
        FakeResponse({"status": "ok", "trades": [trade], "hasNext": False}),
    ])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 1_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    **_no_caps(),
    )
    # 0.0001 BTC * 5e9 RLS/BTC = 500,000 RLS
    assert out["fee_total"] == pytest.approx(500000.0)
    assert out["fee_currency"] == "RLS"
    assert out["fee_unconverted"] == []
    assert out["equity"] == pytest.approx(2_000_000_000.0 - 500000.0)


def test_apply_plan_unpriced_fee_reported_not_mixed(tmp_path, monkeypatch):
    """A fee with unknown currency is reported, never added to the total."""
    trade = {
        "id": 11, "orderId": 27, "market": "BTC-RLS", "type": "buy",
        "price": "5000000000", "amount": "0.1", "total": "500000000",
        "fee": "123", "timestamp": "2026-01-01T13:00:00+00:00",
    }
    sess = FakeSession([
        FakeResponse(_WALLETS),
        FakeResponse({"status": "ok", "order": _ORDER}),
        FakeResponse({"status": "ok", "trades": [trade], "hasNext": False}),
    ])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 1_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    **_no_caps(),
    )
    assert out["fee_total"] == pytest.approx(0.0)
    assert len(out["fee_unconverted"]) == 1
    assert out["fee_unconverted"][0]["fee"] == pytest.approx(123.0)
    assert out["equity"] == pytest.approx(2_000_000_000.0)


def test_apply_plan_dry_run_writes_no_log(tmp_path, monkeypatch):
    """Rehearsals must not pollute the live audit log."""
    from pathlib import Path

    sess = FakeSession([FakeResponse(_WALLETS)])
    c = _client(sess)
    _env(monkeypatch, tmp_path, dry_run="true")
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 1_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    )
    assert out["dry_run"] is True
    assert not Path(tmp_path / "live_executions.jsonl").exists()


def test_apply_plan_skew_guard_keeps_slightly_early_fill(tmp_path, monkeypatch):
    """Fills seconds before the cycle timestamp are included, not dropped."""
    def _t(ts):
        return {
            "id": 20, "orderId": 30, "market": "BTC-RLS", "type": "sell",
            "price": "5000000000", "amount": "0.01", "total": "50000000",
            "fee": "50000", "timestamp": ts,
            "srcCurrency": "BTC", "dstCurrency": "RLS",
        }

    sess = FakeSession([
        FakeResponse(_WALLETS),
        FakeResponse({"status": "ok", "order": _ORDER}),
        FakeResponse({"status": "ok", "trades": [
            _t("2026-01-01T11:59:00+00:00"),  # 60s early: excluded
            _t("2026-01-01T11:59:50+00:00"),  # 10s early: kept via skew guard
        ], "hasNext": False}),
    ])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 500_000_000.0, "target": 1_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    **_no_caps(),
    )
    assert out["n_trades"] == 1
    assert out["fee_total"] == pytest.approx(50000.0)


def test_default_caps_are_enforced_not_silent(tmp_path, monkeypatch):
    """Transaction path must pin that the default caps actually apply.

    The six tests above opt out via ``_no_caps()`` because their fixtures
    deliberately post orders worth ~25% of the test account — the guards
    were right to refuse those, so the caps live in
    ``tests/test_execution_live_safety.py``. This guards the one thing
    ``_no_caps()`` leaves unpinned: that a caller passing *no* caps
    (``main.py`` on a client that predates the guards, or a standalone
    client) still gets the 5% default applied, rather than an unlimited
    client.
    """
    sess = FakeSession([FakeResponse(_WALLETS),
                        FakeResponse({"status": "ok", "order": _ORDER}),
                        FakeResponse({"status": "ok", "trades": []})])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    # ~50% of a 10bn account, so the 5% default single cap blocks it.
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": 0.0, "target": 5_000_000_000.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=10_000_000_000.0,
        base_currency="rls", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    )
    # The wallets + trades lookups run before the guard; an order POST
    # does not, so nothing reached the venue's order endpoint.
    assert not any("/market/orders/add" in u for u in _posted_urls(sess))
    assert any("caps" in e["error"] for e in out["errors"])


def test_misconfigured_caps_raise_rather_than_arm_an_inert_cap(monkeypatch):
    """A percent written where a fraction belongs must fail loudly.

    ``30`` for 30% arms a 3000% "cap" that blocks nothing while looking
    perfectly armed — the worst outcome a safety system can have. Better
    to refuse the order than to pretend the guard exists.
    """
    sess = FakeSession([])
    c = _client(sess)
    with pytest.raises(Exception, match="misconfigured"):
        c.apply_plan(
            plan={"actions": []}, risk_snapshot=_snapshot(),
            account_size=10_000_000_000.0,
            base_currency="rls", now_iso="2026-01-01T12:00:00+00:00",
            cycle=1, max_total_pct=30.0, max_single_pct=5.0,
        )
