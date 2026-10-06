"""Regression tests for the paper-ledger equity formula and sell-side P&L.

Two bugs lived in `_update_paper_ledger()`:

1. `equity = account_size - invested + realized + unrealized`. Cash is
   `(account_size - invested)`, so adding the market value of open positions
   back cancels the `invested` term. Subtracting it a second time understated
   equity by exactly the amount sitting in open positions the moment any
   capital was deployed — which is what the dashboard charted.
   Correct: `equity = account_size + realized + unrealized`.

2. Sells stored a negative `delta_qty`, so
   `realized_pnl = delta_qty * (px - avg_cost)` produced the sign-flipped
   result: a profitable partial sell booked a loss. Ledger rows now carry a
   positive `qty`/`amount` with `side` carrying the direction.

Semantics these tests pin down: `target` is a *notional amount* in the base
currency, so each cycle re-derives quantity as `target / price`. A position is
therefore rebalanced toward the target every cycle, and `invested` (market
value at current prices) tracks the target amount rather than the original
purchase size.
"""

from __future__ import annotations

import json

import pytest

from crypto_council_flow import main as flow_main


@pytest.fixture()
def ledger_env(tmp_path, monkeypatch):
    """Point the ledger/positions files at tmp_path."""
    monkeypatch.setattr(flow_main, "LEDGER_PATH", tmp_path / "paper_ledger.json")
    monkeypatch.setattr(flow_main, "POSITIONS_PATH", tmp_path / "paper_positions.json")
    return tmp_path


def _run(plan_actions, prices, account_size=10_000.0):
    return flow_main._update_paper_ledger(
        plan={"actions": plan_actions},
        risk_snapshot={c: {"current_price": px} for c, px in prices.items()},
        account_size=account_size,
        base_currency="usd",
        now_iso="2026-01-01T00:00:00+00:00",
        cycle=1,
    )


def _positions(tmp_path) -> dict:
    return json.loads((tmp_path / "paper_positions.json").read_text(encoding="utf-8"))


def _ledger(tmp_path) -> list:
    return json.loads((tmp_path / "paper_ledger.json").read_text(encoding="utf-8"))


# --- equity formula ---------------------------------------------------------


def test_flat_book_equity_equals_account(ledger_env):
    """No positions, no fills -> equity is exactly the account size."""
    out = _run([], {}, account_size=10_000.0)
    assert out["equity"] == pytest.approx(10_000.0)
    assert out["invested"] == pytest.approx(0.0)
    assert out["total_pnl"] == pytest.approx(0.0)
    assert out["n_trades"] == 0


def test_deploying_capital_does_not_change_equity(ledger_env):
    """Opening a position moves cash into coins; equity must not budge.

    This is the regression for the double-counted `invested` term: cash
    7500 + positions 2500 == 10000, but the old formula reported 7500.
    """
    out = _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    assert out["invested"] == pytest.approx(2_500.0)
    assert out["total_pnl"] == pytest.approx(0.0)
    assert out["equity"] == pytest.approx(10_000.0)


def test_full_deployment_does_not_change_equity(ledger_env):
    """100% invested is still the full account — worst case for the old formula."""
    out = _run([{"coin_id": "bitcoin", "target": 10_000.0}], {"bitcoin": 100.0})
    assert out["invested"] == pytest.approx(10_000.0)
    assert out["equity"] == pytest.approx(10_000.0)


def test_equity_equals_cash_plus_positions_plus_pnl(ledger_env):
    """The identity the dashboard implies, checked on a live position."""
    out = _run([{"coin_id": "bitcoin", "target": 3_500.0}], {"bitcoin": 100.0})
    cash = 10_000.0 - out["invested"]
    assert out["equity"] == pytest.approx(cash + out["invested"] + out["total_pnl"])


# --- mark to market ---------------------------------------------------------


def test_price_rise_adds_unrealized_to_equity(ledger_env):
    """+20% on a held coin lifts equity by exactly the gain."""
    _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    out = _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 120.0})
    assert out["total_pnl"] == pytest.approx(500.0)
    assert out["equity"] == pytest.approx(10_500.0)


def test_price_drop_drags_equity_once_not_twice(ledger_env):
    """A 10% drawdown costs 500 once — not 500 plus the deployed amount."""
    _run([{"coin_id": "bitcoin", "target": 5_000.0}], {"bitcoin": 100.0})
    out = _run([{"coin_id": "bitcoin", "target": 5_000.0}], {"bitcoin": 90.0})
    assert out["total_pnl"] == pytest.approx(-500.0)
    assert out["equity"] == pytest.approx(9_500.0)


def test_missing_price_skips_position_instead_of_zeroing_it(ledger_env):
    """No price this cycle: the coin is skipped, not written down to zero."""
    _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    out = _run([{"coin_id": "bitcoin", "target": 2_500.0}], {})
    assert out["equity"] == pytest.approx(10_000.0)
    assert out["invested"] == pytest.approx(0.0)
    assert _positions(ledger_env)["bitcoin"]["qty"] == pytest.approx(25.0)


# --- realized P&L on the sell path -----------------------------------------


def test_profitable_close_books_a_gain(ledger_env):
    """Buy 25 @100, close @120 -> +500 realized, nothing unrealized."""
    _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    out = _run([{"coin_id": "bitcoin", "target": 0.0}], {"bitcoin": 120.0})
    assert out["realized_pnl"] == pytest.approx(500.0)
    assert out["unrealized_pnl"] == pytest.approx(0.0)
    assert out["open_positions"] == []
    assert out["equity"] == pytest.approx(10_500.0)


def test_losing_close_books_a_loss(ledger_env):
    """Buy 50 @100, close @80 -> -1000 realized."""
    _run([{"coin_id": "bitcoin", "target": 5_000.0}], {"bitcoin": 100.0})
    out = _run([{"coin_id": "bitcoin", "target": 0.0}], {"bitcoin": 80.0})
    assert out["realized_pnl"] == pytest.approx(-1_000.0)
    assert out["equity"] == pytest.approx(9_000.0)


def test_partial_sell_books_gain_with_correct_sign(ledger_env):
    """Regression for the sign-flipped sell P&L.

    Holding 25 coins bought at 100 and re-marked at 110, a plan that halves
    the notional sells ~13.6 coins for a ~+136 gain. The old negative-qty
    arithmetic booked -136.
    """
    _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    out = _run([{"coin_id": "bitcoin", "target": 1_250.0}], {"bitcoin": 110.0})
    assert out["realized_pnl"] == pytest.approx(136.36, abs=0.01)
    assert out["realized_pnl"] > 0, "profitable partial sell must not book a loss"
    assert out["total_pnl"] == pytest.approx(250.0)
    assert out["equity"] == pytest.approx(10_250.0)


def test_ledger_records_positive_size_on_both_sides(ledger_env):
    """`side` carries direction; qty/amount stay positive magnitudes."""
    _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    _run([{"coin_id": "bitcoin", "target": 1_250.0}], {"bitcoin": 110.0})
    rows = _ledger(ledger_env)
    assert [r["side"] for r in rows] == ["buy", "sell"]
    for r in rows:
        assert r["qty"] > 0, "ledger qty must be a positive magnitude"
        assert r["amount"] > 0, "ledger amount must be a positive magnitude"
    assert rows[1]["realized_pnl"] > 0


def test_avg_cost_is_blended_across_rebalances(ledger_env):
    """Adding to a position keeps the weighted average cost, not the last price."""
    _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    _run([{"coin_id": "bitcoin", "target": 7_500.0}], {"bitcoin": 200.0})
    pos = _positions(ledger_env)["bitcoin"]
    # target 7500 at a 200 mark = 37.5 coins; it buys the 12.5 coin delta
    # at 200 on top of the original 25 @100.
    # (25 * 100 + 12.5 * 200) / 37.5 = 133.33
    assert pos["qty"] == pytest.approx(37.5)
    assert pos["avg_cost"] == pytest.approx(133.333, abs=0.01)


def test_no_dust_fill_when_target_matches_position(ledger_env):
    """Re-running an unchanged plan at the same price logs nothing new."""
    _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    out = _run([{"coin_id": "bitcoin", "target": 2_500.0}], {"bitcoin": 100.0})
    assert out["n_trades"] == 1
    assert out["total_pnl"] == pytest.approx(0.0)