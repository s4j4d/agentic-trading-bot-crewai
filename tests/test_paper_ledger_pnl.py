"""Regression tests for the paper-ledger P&L chain.

Two independent defects made the dashboard report a flat 0.0 P&L while
real capital was deployed:

1. `_atr_snapshot_for` asked CoinGecko for `days=3`, which the free tier now
   rejects with HTTP 400. The exception was swallowed into `(None, None)`,
   so the risk snapshot had no prices and the ledger skipped every fill.
2. A position whose coin had no price in a cycle was *dropped* from the book
   (`positions.pop`) even though the close could not be priced. That
   destroyed its cost basis and its unrealized P&L permanently.

These tests drive the real `_update_paper_ledger` so the accounting is
pinned, not the ledger's shape.
"""

from __future__ import annotations

import json

import pytest

from crypto_council_flow import main as fm
from crypto_council_flow.tools import technical_indicators as ti
from crypto_council_flow.tools.technical_indicators import _valid_coingecko_days


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """Isolate the paper ledger onto tmp paths."""
    monkeypatch.setattr(fm, "LEDGER_PATH", tmp_path / "paper_ledger.json")
    monkeypatch.setattr(fm, "POSITIONS_PATH", tmp_path / "paper_positions.json")
    return tmp_path


def _open(coin, amount, price, cycle=1):
    return fm._update_paper_ledger(
        {"actions": [{"coin_id": coin, "action": "open", "current": 0, "target": amount}]},
        {coin: {"current_price": price, "atr_pct": 5.0}},
        10_000.0, "usd", f"2026-10-06T12:0{cycle}:00+00:00", cycle,
    )


def _plan(actions, prices, cycle):
    return fm._update_paper_ledger(
        {"actions": actions},
        {c: {"current_price": p, "atr_pct": 5.0} for c, p in prices.items()},
        10_000.0, "usd", f"2026-10-06T12:{cycle:02d}:00+00:00", cycle,
    )


def _positions(ledger):
    return json.loads((ledger / "paper_positions.json").read_text(encoding="utf-8"))


# --- root cause #1: CoinGecko no longer accepts days=3 ----------------------


@pytest.mark.parametrize("requested,expected", [
    (1, 1), (2, 7), (3, 7), (5, 7), (7, 7),
    (10, 14), (14, 14), (20, 30), (30, 30),
    (60, 90), (90, 90), (150, 180), (180, 180),
    (300, 365), (365, 365), (730, 365),
])
def test_requested_days_is_snapped_to_an_accepted_value(requested, expected):
    """days=3 raises HTTP 400; the fetch must never ask for it."""
    assert _valid_coingecko_days(requested) == expected


def test_only_accepted_days_values_are_ever_requested():
    accepted = {1, 7, 14, 30, 90, 180, 365}
    for d in range(1, 731):
        assert _valid_coingecko_days(d) in accepted


def test_the_atr_snapshot_default_is_a_valid_coingecko_window():
    """The whole P&L chain depends on this value being fetchable."""
    assert _valid_coingecko_days(fm._ATR_SNAPSHOT_DAYS) in {1, 7, 14, 30, 90, 180, 365}


def test_the_escalation_window_holds_no_invalid_days():
    """730 was in the old escalation list and always 400'd."""
    assert _valid_coingecko_days(730) == 365


def test_the_escalation_ladder_accepts_a_narrow_window_in_two_requests():
    """CoinGecko bars follow the window: days=7 gives 42 4h bars, days=14 gives
    84. A threshold of 50 rejected 7/90/180 (42/23/45 bars) and paid 4 throttled
    requests for data days=14 returns in 2. Wilder ATR needs period+1 bars."""
    assert ti._MIN_CANDLES <= 42, "days=7 (42 bars) must satisfy the threshold"
    assert ti._MIN_CANDLES <= 92, "days=365 (92 bars) must satisfy the threshold"
    assert ti._MIN_CANDLES <= 48, "days=1 (48 30m bars) must satisfy the threshold"


# --- root cause #2: an unpriced close destroyed the position ---------------


def test_unpriced_exit_does_not_destroy_the_position(ledger):
    _open("ethereum", 3000.0, 3000.0)
    fm._update_paper_ledger({"actions": []}, {}, 10_000.0, "usd",
                            "2026-10-06T12:10:00+00:00", 2)
    assert "ethereum" in _positions(ledger), "cost basis must survive an unpriced cycle"


def test_pnl_is_booked_once_a_price_returns(ledger):
    _open("ethereum", 3000.0, 3000.0)
    fm._update_paper_ledger({"actions": []}, {}, 10_000.0, "usd",
                            "2026-10-06T12:10:00+00:00", 2)
    r = _plan([], {"ethereum": 3300.0}, 3)
    assert r["realized_pnl"] == pytest.approx(300.0)
    assert r["equity"] == pytest.approx(10_300.0)
    assert _positions(ledger) == {}


def test_an_unpriced_cycle_does_not_freeze_equity(ledger):
    """The old bug left equity pinned at the account size forever."""
    _open("ethereum", 3000.0, 3000.0)
    r = fm._update_paper_ledger({"actions": []}, {}, 10_000.0, "usd",
                                "2026-10-06T12:10:00+00:00", 2)
    assert r["equity"] == pytest.approx(10_000.0), "unpriced -> no mark, equity unchanged"
    r3 = _plan([], {"ethereum": 3300.0}, 3)
    assert r3["equity"] == pytest.approx(10_300.0)


def test_a_reinstated_target_revives_the_position(ledger):
    """Keeping the position must not block a later buy on the same coin."""
    _open("bitcoin", 1000.0, 100.0)
    fm._update_paper_ledger({"actions": []}, {}, 10_000.0, "usd",
                            "2026-10-06T12:10:00+00:00", 2)
    r = _plan([{"coin_id": "bitcoin", "action": "open", "current": 0, "target": 2000}],
              {"bitcoin": 100.0}, 3)
    assert _positions(ledger)["bitcoin"]["qty"] == pytest.approx(20.0)
    assert r["invested"] == pytest.approx(2000.0)


# --- phantom zero-quantity positions ----------------------------------------


def test_zero_target_close_leaves_no_phantom_position(ledger):
    """`setdefault` used to recreate {qty: 0} for a close, leaking forever."""
    _open("bitcoin", 1000.0, 100.0)
    _plan([{"coin_id": "bitcoin", "action": "close", "current": 1000, "target": 0}],
          {"bitcoin": 110.0}, 2)
    assert _positions(ledger) == {}


def test_zero_target_close_realizes_the_profit(ledger):
    _open("bitcoin", 1000.0, 100.0)
    r = _plan([{"coin_id": "bitcoin", "action": "close", "current": 1000, "target": 0}],
              {"bitcoin": 110.0}, 2)
    assert r["realized_pnl"] == pytest.approx(100.0)
    assert r["equity"] == pytest.approx(10_100.0)


def test_phantom_does_not_leak_into_later_cycles(ledger):
    _open("bitcoin", 1000.0, 100.0)
    _plan([{"coin_id": "bitcoin", "action": "close", "current": 1000, "target": 0}],
          {"bitcoin": 110.0}, 2)
    r = _plan([], {"bitcoin": 120.0}, 3)
    assert _positions(ledger) == {}
    assert r["invested"] == 0.0


# --- accounting invariants -------------------------------------------------


def test_equity_identity_holds_across_a_trim(ledger):
    r = _open("bitcoin", 2500.0, 100.0)
    r2 = _plan([{"coin_id": "bitcoin", "action": "decrease", "current": 2500, "target": 1250}],
               {"bitcoin": 110.0}, 2)
    assert r2["equity"] == pytest.approx(
        10_000.0 + r2["realized_pnl"] + r2["unrealized_pnl"]
    )


def test_partial_sell_realizes_the_correct_sign(ledger):
    _open("bitcoin", 2500.0, 100.0)
    r = _plan([{"coin_id": "bitcoin", "action": "decrease", "current": 2500, "target": 1250}],
              {"bitcoin": 110.0}, 2)
    expected = (25.0 - 12.5 / 1.1) * (110.0 - 100.0)
    assert r["realized_pnl"] == pytest.approx(expected)


def test_hold_at_target_books_nothing(ledger):
    _open("bitcoin", 2500.0, 100.0)
    r = _plan([{"coin_id": "bitcoin", "action": "hold", "current": 2500, "target": 2500}],
              {"bitcoin": 100.0}, 2)
    assert r["n_trades"] == 1
    assert r["realized_pnl"] == 0.0


def test_ledger_rows_are_all_positive_size(ledger):
    """Sells must not store negative qty/amount -- that flipped realized P&L."""
    _open("bitcoin", 2500.0, 100.0)
    _plan([{"coin_id": "bitcoin", "action": "decrease", "current": 2500, "target": 1250}],
          {"bitcoin": 110.0}, 2)
    rows = json.loads((ledger / "paper_ledger.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert all(r["qty"] > 0 and r["amount"] > 0 for r in rows)
    assert [r["side"] for r in rows] == ["buy", "sell"]


def test_a_cycle_with_no_price_books_no_trades(ledger):
    """No price -> no fill. That was the visible symptom of the days=3 400."""
    r = _plan([{"coin_id": "bitcoin", "action": "open", "current": 0, "target": 2500}],
              {}, 1)
    assert r["n_trades"] == 0
    assert r["invested"] == 0.0
    assert r["equity"] == pytest.approx(10_000.0)