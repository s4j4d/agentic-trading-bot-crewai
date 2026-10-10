"""Tests for NobitexExecutionClient.reconcile — live reconciliation (Phase 5).

The venue authors the fills, so a local ledger is a *reconstruction*, not a
record of our own decisions. These tests pin the contract:

  * the wallet is the source of truth for quantity;
  * fills supply price/side/fee, so realized P&L and cost basis are derived;
  * every disagreement (venue qty vs local qty, wallet emptied) is written
    as a ``correction``/``close`` row instead of silently overwriting state;
  * rows are paper-shaped so scripts/portfolio_panel.py can read them.

All tests use a fake ``requests.Session`` so nothing touches the network.
"""

from __future__ import annotations

import json

import pytest
import requests

from crypto_council_flow.tools.execution_base import Fill
from crypto_council_flow.tools.execution_nobitex import NobitexExecutionClient


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

    def request(self, method, url, json=None, params=None, timeout=None, **kwargs):  # pyright: ignore[reportIncompatibleMethodOverride] — fake transport
        self.calls.append({"method": method, "url": url, "json": json, "params": params})
        if self._exc is not None:
            raise self._exc
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse({"status": "ok"})


def _wallets(**assets: float):
    return FakeResponse({
        "status": "ok",
        "wallets": [
            {"currency": ccy, "balance": str(qty), "blockedBalance": "0",
             "activeBalance": str(qty)}
            for ccy, qty in assets.items()
        ],
    })


def _wallets_locked(**assets: "tuple[float, float]"):
    """Wallets with a locked portion: ``asset=(free, blocked)``.

    Production shape: ``balance`` is free+blocked, ``blockedBalance`` is the
    part parked in open orders. Reading only ``free`` (= ``activeBalance``)
    makes in-flight funds look like 0.
    """
    return FakeResponse({
        "status": "ok",
        "wallets": [
            {"currency": ccy, "balance": str(free + blocked),
             "blockedBalance": str(blocked), "activeBalance": str(free)}
            for ccy, (free, blocked) in assets.items()
        ],
    })


def _client(session, quote="rls"):
    c = NobitexExecutionClient(
        api_key="TOKEN123", base_url="https://apiv2.nobitex.ir",
        quote_currency=quote, session=session,
    )
    c._min_notional_cache = {}
    return c


def _fill(**kw):
    """Build a Fill; defaults to a 0.01 BTC buy at 5,000,000,000 rls."""
    base = {
        "trade_id": "T1", "order_id": "O1", "symbol": "BTC-RLS",
        "side": "buy", "qty": 0.01, "price": 5_000_000_000.0,
        "fee": 25_000.0, "fee_currency": "RLS", "ts": 1_700_000_000_000,
    }
    base.update(kw)
    return Fill(**base)


# ---------------------------------------------------------------------------
# basic fill -> position + ledger rows
# ---------------------------------------------------------------------------

def test_reconcile_opens_position_from_fills():
    """A buy fill creates a position whose qty comes from the wallet."""
    sess = FakeSession([_wallets(btc=0.01, rls=1_000_000_000.0)])
    c = _client(sess)
    positions, rows = c.reconcile(
        [_fill()], {}, cycle=1, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert positions["btc"]["qty"] == 0.01
    # The wallet agreed with the ledger, so no correction row is needed.
    assert rows == [
        {
            "ts": "2023-11-14T22:13:20+00:00", "cycle": 1, "coin_id": "btc",
            "side": "buy", "qty": 0.01, "price": 5_000_000_000.0,
            "amount": 50_000_000.0, "realized_pnl": None,
            "fee": 25_000.0, "fee_currency": "RLS",
            "trade_id": "T1", "order_id": "O1", "source": "live_fill",
        }
    ]


def test_reconcile_corrects_qty_to_wallet():
    """The wallet, not the fill arithmetic, decides how much we hold.

    A partial fill leaves the fills-derived qty (1.0) short of the wallet
    (1.5). The wallet wins, and a correction row records why.
    """
    sess = FakeSession([_wallets(btc=1.5, rls=1_000_000_000.0)])
    c = _client(sess)
    prior = {"btc": {"qty": 0.0, "avg_cost": 5_000_000_000.0}}
    positions, rows = c.reconcile(
        [_fill(qty=1.0)], prior, cycle=2, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert positions["btc"]["qty"] == 1.5
    corrections = [r for r in rows if r["side"] == "correction"]
    assert len(corrections) == 1
    assert corrections[0]["qty"] == 0.5
    assert "1.5" in corrections[0]["note"] and "1" in corrections[0]["note"]


def test_reconcile_blends_cost_basis_across_buys():
    """A second buy blends into the existing avg_cost (weighted mean)."""
    sess = FakeSession([_wallets(btc=0.02, rls=1_000_000_000.0)])
    c = _client(sess)
    prior = {"btc": {"qty": 0.01, "avg_cost": 5_000_000_000.0}}
    positions, rows = c.reconcile(
        [_fill(price=6_000_000_000.0, fee=0.0, fee_currency="")],
        prior, cycle=3, now_iso="2026-01-01T12:00:00+00:00",
    )
    # 0.01 @ 5e9 + 0.01 @ 6e9 -> 5.5e9
    assert positions["btc"]["qty"] == 0.02
    assert positions["btc"]["avg_cost"] == pytest.approx(5_500_000_000.0)


def test_reconcile_fees_raise_cost_basis():
    """A buy fee is part of what we paid, so it raises avg_cost."""
    sess = FakeSession([_wallets(btc=0.01, rls=1_000_000_000.0)])
    c = _client(sess)
    positions, _ = c.reconcile(
        [_fill(fee=50_000.0, fee_currency="RLS")], {}, cycle=1,
        now_iso="2026-01-01T12:00:00+00:00",
    )
    # (0.01 * 5e9 + 50_000) / 0.01
    assert positions["btc"]["avg_cost"] == pytest.approx(5_005_000_000.0)


def test_reconcile_base_currency_fee_converted_to_quote():
    """A fee charged in the base asset is valued at the fill price."""
    sess = FakeSession([_wallets(btc=0.01, rls=1_000_000_000.0)])
    c = _client(sess)
    positions, rows = c.reconcile(
        [_fill(fee=0.000001, fee_currency="BTC")], {}, cycle=1,
        now_iso="2026-01-01T12:00:00+00:00",
    )
    # 0.000001 BTC * 5e9 = 5,000 rls folded into cost basis
    assert positions["btc"]["avg_cost"] == pytest.approx(5_000_500_000.0)
    assert rows[0]["fee"] == 0.000001


# ---------------------------------------------------------------------------
# sells, closes and realized P&L
# ---------------------------------------------------------------------------

def test_reconcile_sell_realizes_against_prior_cost():
    """A sell's realized P&L is measured against the cost we carried in."""
    sess = FakeSession([_wallets(btc=0.0, rls=1_000_000_000.0)])
    c = _client(sess)
    prior = {"btc": {"qty": 0.01, "avg_cost": 5_000_000_000.0}}
    positions, rows = c.reconcile(
        [_fill(side="sell", qty=0.01, price=6_000_000_000.0, fee=30_000.0)],
        prior, cycle=4, now_iso="2026-01-01T12:00:00+00:00",
    )
    # (6e9 - 5e9) * 0.01 - 30,000
    assert rows[0]["realized_pnl"] == pytest.approx(9_970_000.0)
    assert positions == {}  # wallet emptied -> position closed


def test_reconcile_empty_wallet_emits_close_row():
    """A position the wallet no longer holds is closed, and says so."""
    sess = FakeSession([_wallets(rls=1_000_000_000.0)])  # no btc
    c = _client(sess)
    prior = {"btc": {"qty": 0.01, "avg_cost": 5_000_000_000.0}}
    positions, rows = c.reconcile(
        [], prior, cycle=5, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert positions == {}
    assert rows == [{
        "ts": "2026-01-01T12:00:00+00:00", "cycle": 5, "coin_id": "btc",
        "side": "close", "qty": 0.01, "price": 0.0, "amount": 0.0,
        "realized_pnl": None,
        "note": "wallet holds none — position closed by reconciliation",
        "source": "live_correction",
    }]


def test_reconcile_close_with_no_local_position_is_silent():
    """Nothing held locally and nothing in the wallet: no noise rows."""
    sess = FakeSession([_wallets(rls=1_000_000_000.0)])
    c = _client(sess)
    positions, rows = c.reconcile(
        [], {}, cycle=6, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert positions == {} and rows == []


def test_reconcile_buy_and_sell_in_one_batch():
    """Buys and sells net per coin; the residual basis is the buy's."""
    sess = FakeSession([_wallets(btc=0.01, rls=1_000_000_000.0)])
    c = _client(sess)
    prior = {"btc": {"qty": 0.01, "avg_cost": 5_000_000_000.0}}
    positions, rows = c.reconcile(
        [
            _fill(trade_id="SELL", side="sell", qty=0.01, price=6_000_000_000.0, fee=0.0, fee_currency=""),
            _fill(trade_id="BUY", price=7_000_000_000.0, fee=0.0, fee_currency=""),
        ],
        prior, cycle=7, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert positions["btc"]["qty"] == 0.01
    assert positions["btc"]["avg_cost"] == pytest.approx(7_000_000_000.0)
    sold = [r for r in rows if r["trade_id"] == "SELL"]
    assert sold[0]["realized_pnl"] == pytest.approx(10_000_000.0)


def test_reconcile_partial_sell_keeps_position_open():
    """Selling less than held keeps the position open at the wallet qty."""
    sess = FakeSession([_wallets(btc=0.007, rls=1_000_000_000.0)])
    c = _client(sess)
    prior = {"btc": {"qty": 0.01, "avg_cost": 5_000_000_000.0}}
    positions, rows = c.reconcile(
        [_fill(side="sell", qty=0.003, price=6_000_000_000.0, fee=0.0, fee_currency="")],
        prior, cycle=8, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert positions["btc"]["qty"] == 0.007
    assert rows[0]["realized_pnl"] == pytest.approx(3_000_000.0)
    assert not [r for r in rows if r["side"] == "close"]


# ---------------------------------------------------------------------------
# hygiene
# ---------------------------------------------------------------------------

def test_reconcile_ignores_quote_asset_and_unpriced_fills():
    """Quote-currency pairs and zero-price rows cannot become positions."""
    sess = FakeSession([_wallets(rls=1_000_000_000.0)])
    c = _client(sess)
    positions, rows = c.reconcile(
        [
            _fill(symbol="RLS-USDT"),
            _fill(price=0.0),
            _fill(qty=0.0),
        ],
        {}, cycle=9, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert positions == {}
    assert rows == []


def test_reconcile_is_deterministic_and_sorted():
    """Coins are processed in a stable order so rows never shuffle."""
    sess = FakeSession([_wallets(btc=0.01, eth=0.01, rls=1_000_000_000.0)])
    c = _client(sess)
    fills = [_fill(symbol="BTC-RLS"), _fill(symbol="ETH-RLS", trade_id="T2")]
    _, rows_a = c.reconcile(fills, {}, cycle=1, now_iso="2026-01-01T12:00:00+00:00")
    _, rows_b = c.reconcile(fills, {}, cycle=1, now_iso="2026-01-01T12:00:00+00:00")
    assert rows_a == rows_b
    # Alphabetical coin order, one row per fill (wallets agree with fills).
    assert [r["coin_id"] for r in rows_a] == ["btc", "eth"]
    assert all(r["side"] == "buy" for r in rows_a)


def test_reconcile_balance_failure_propagates():
    """A venue error must surface, not be swallowed into empty positions."""
    from crypto_council_flow.tools.execution_base import ExecutionError

    sess = FakeSession(exc=requests.ConnectionError("venue down"))
    c = _client(sess)
    with pytest.raises(ExecutionError):
        c.reconcile([], {}, cycle=1, now_iso="2026-01-01T12:00:00+00:00")


# ---------------------------------------------------------------------------
# ABC parity
# ---------------------------------------------------------------------------

def test_abc_reconcile_default_is_noop():
    """Clients that own their ledger (paper) keep the inherited no-op."""
    from crypto_council_flow.tools import PaperExecutionClient

    client = PaperExecutionClient()
    assert client.reconcile([], {}) == ({}, [])


# ---------------------------------------------------------------------------
# production-shaped keys: tickers vs coin_id
# ---------------------------------------------------------------------------
# The earlier tests above all use `btc` for both the venue asset and the
# position key, which is true in the fixture but never in production: the
# venue says `BTC`, while the risk snapshot / paper ledger / panel all key
# on CoinGecko `coin_id`s like `bitcoin`. That mismatch priced every live
# position at zero, so pin the real shapes here.

def test_reconcile_maps_tickers_to_coin_ids():
    """Fills/wallet speak tickers; positions must come out keyed by coin_id."""
    sess = FakeSession([_wallets(btc=0.01, rls=1_000_000_000.0)])
    c = _client(sess)
    positions, rows = c.reconcile(
        [_fill()], {}, cycle=1, now_iso="2026-01-01T12:00:00+00:00",
        coin_ids_by_symbol={"BTC": "bitcoin"},
    )
    assert "bitcoin" in positions and "btc" not in positions
    assert positions["bitcoin"]["qty"] == 0.01
    # The ledger row inherits the coin_id, so the panel's price lookup against
    # the risk snapshot (keyed `bitcoin`) finds a price instead of 0.
    assert [r["coin_id"] for r in rows] == ["bitcoin"]


def test_reconcile_without_a_map_falls_back_to_the_ticker():
    """No map supplied (paper-shaped callers) keeps the old lowercase key."""
    sess = FakeSession([_wallets(btc=0.01, rls=1_000_000_000.0)])
    c = _client(sess)
    positions, _ = c.reconcile(
        [_fill()], {}, cycle=1, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert "btc" in positions


def test_reconcile_locked_balance_still_counts_as_ours():
    """Funds parked in an open order are ours: use `total`, not `free`.

    A limit buy with a working order reports activeBalance 0 / balance 0.01.
    Reading `free` alone drops the position and emits a close row while the
    order is still live.
    """
    sess = FakeSession([_wallets_locked(btc=(0.0, 0.01), rls=(1_000_000_000.0, 0.0))])
    c = _client(sess)
    prior = {}
    positions, rows = c.reconcile(
        [_fill()], prior, cycle=1, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert positions["btc"]["qty"] == 0.01
    assert [r["side"] for r in rows] == ["buy"]


def test_reconcile_locked_balance_is_not_a_correction():
    """Wallet total agreeing with fills must not emit a correction row."""
    sess = FakeSession([_wallets_locked(btc=(0.0, 0.01), rls=(0.0, 1_000_000_000.0))])
    c = _client(sess)
    _, rows = c.reconcile(
        [_fill()], {}, cycle=1, now_iso="2026-01-01T12:00:00+00:00",
    )
    assert [r["side"] for r in rows] == ["buy"]
    assert all(r["source"] != "live_correction" for r in rows)
