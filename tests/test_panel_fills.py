"""Tests for the panel's computed-fill reporting.

The paper ledger is the only place a plan's notional target becomes an
actual quantity: qty = target / price. These tests pin the aggregation the
panel does over one cycle's ledger rows.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

def _load_panel_mod():
    path = Path(__file__).resolve().parent.parent / "scripts" / "portfolio_panel.py"
    spec = importlib.util.spec_from_file_location("portfolio_panel", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


panel = _load_panel_mod()


def _row(coin="bitcoin", side="buy", qty=25.0, price=100.0, cycle=2, **kw):
    return {
        "ts": "2026-01-01T00:00:00+00:00",
        "cycle": cycle,
        "coin_id": coin,
        "side": side,
        "qty": qty,
        "price": price,
        "amount": qty * price,
        **kw,
    }


# --- cycle filtering --------------------------------------------------------


def test_only_the_requested_cycle_is_aggregated():
    rows = [_row(cycle=1), _row(cycle=2)]
    fills = panel._fills_for_cycle(rows, 2)
    assert set(fills) == {"bitcoin"}
    assert fills["bitcoin"]["qty"] == pytest.approx(25.0)


def test_other_coins_are_not_pulled_in():
    rows = [_row(coin="bitcoin"), _row(coin="ethereum", qty=24.0, price=50.0)]
    assert set(panel._fills_for_cycle(rows, 2)) == {"bitcoin", "ethereum"}


def test_rows_without_a_coin_id_are_skipped():
    assert panel._fills_for_cycle([_row(coin="")], 2) == {}


def test_junk_cycle_falls_back_to_every_row():
    """A snapshot with no usable cycle must not blank the fills table."""
    rows = [_row(cycle=1), _row(cycle=2, coin="ethereum")]
    assert len(panel._fills_for_cycle(rows, "not-a-number")) == 2


# --- netting (the bug that made gross figures wrong) -------------------------


def test_buy_then_sell_in_one_cycle_nets_down():
    """25 bought + 13.64 sold is a net HOLD of 11.36, not 38.64 coins.

    Summing gross flow reported 38.6 coins and 4,000 deployed for a position
    that never exceeded 25 coins — neither the position nor the trade.
    """
    rows = [
        _row(side="buy", qty=25.0, price=100.0),
        _row(side="sell", qty=13.636363636, price=110.0, realized_pnl=136.36),
    ]
    fill = panel._fills_for_cycle(rows, 2)["bitcoin"]
    assert fill["qty"] == pytest.approx(11.3636, abs=0.001)
    assert fill["amount"] == pytest.approx(1000.0)
    assert fill["n_fills"] == 2


def test_close_is_treated_as_an_outflow():
    """`close` must reduce the position like `sell`, not add to it."""
    fill = panel._fills_for_cycle([_row(side="close", qty=25.0)], 2)["bitcoin"]
    assert fill["qty"] == pytest.approx(-25.0)
    assert fill["amount"] == pytest.approx(-2500.0)


def test_sell_only_gives_a_negative_deployment():
    fill = panel._fills_for_cycle([_row(side="sell", qty=10.0)], 2)["bitcoin"]
    assert fill["qty"] == pytest.approx(-10.0)
    assert fill["amount"] == pytest.approx(-1000.0)


def test_realized_pnl_accumulates_across_fills():
    rows = [
        _row(side="sell", qty=10.0, realized_pnl=50.0),
        _row(side="sell", qty=5.0, realized_pnl=25.0),
    ]
    assert panel._fills_for_cycle(rows, 2)["bitcoin"]["realized_pnl"] == pytest.approx(75.0)


def test_realized_pnl_is_absent_until_a_fill_realizes():
    fill = panel._fills_for_cycle([_row(side="buy")], 2)["bitcoin"]
    assert fill["realized_pnl"] == pytest.approx(0.0)


def test_negative_stored_qty_is_read_as_a_magnitude():
    """Legacy rows stored a negative qty on sells; never show a negative size."""
    row = _row(side="sell", qty=-10.0)
    row["amount"] = -1000.0
    fill = panel._fills_for_cycle([row], 2)["bitcoin"]
    assert fill["qty"] == pytest.approx(-10.0), "sign comes from side, not the stored value"
    assert fill["amount"] == pytest.approx(-1000.0)


# --- unpriced fills ---------------------------------------------------------


def test_zero_size_fill_is_marked_unpriced():
    """A close with no price is recorded as a zero-size note, not a trade."""
    row = {"cycle": 2, "coin_id": "bitcoin", "side": "close", "qty": 0, "price": None,
           "amount": 0.0, "realized_pnl": None}
    fill = panel._fills_for_cycle([row], 2)["bitcoin"]
    assert fill["priced"] is False
    assert fill["amount"] == pytest.approx(0.0)


def test_priced_fill_is_marked_priced():
    assert panel._fills_for_cycle([_row()], 2)["bitcoin"]["priced"] is True


def test_junk_numbers_do_not_raise():
    rows = [{"cycle": 2, "coin_id": "x", "side": "buy", "qty": "n/a", "price": None,
             "amount": None}]
    fill = panel._fills_for_cycle(rows, 2)["x"]
    assert fill["qty"] == pytest.approx(0.0)
    assert fill["amount"] == pytest.approx(0.0)


# --- loader -----------------------------------------------------------------


def test_missing_ledger_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "LEDGER", tmp_path / "nope.json")
    assert panel._load_ledger() == []


def test_corrupt_ledger_is_empty_not_an_error(tmp_path, monkeypatch):
    bad = tmp_path / "paper_ledger.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(panel, "LEDGER", bad)
    assert panel._load_ledger() == []


def test_ledger_that_is_not_a_list_is_ignored(tmp_path, monkeypatch):
    obj = tmp_path / "paper_ledger.json"
    obj.write_text('{"a": 1}', encoding="utf-8")
    monkeypatch.setattr(panel, "LEDGER", obj)
    assert panel._load_ledger() == []


def test_non_dict_rows_are_dropped(tmp_path, monkeypatch):
    obj = tmp_path / "paper_ledger.json"
    obj.write_text('["x", 3, {"coin_id": "bitcoin"}]', encoding="utf-8")
    monkeypatch.setattr(panel, "LEDGER", obj)
    assert panel._load_ledger() == [{"coin_id": "bitcoin"}]


# --- rendered output --------------------------------------------------------


def test_positions_renders_net_fill_columns():
    html = panel._positions(
        [{"coin_id": "bitcoin", "symbol": "BTC", "action": "decrease",
          "current": 2500, "target": 1250}],
        {}, 10000.0, "usd",
        {"bitcoin": {"qty": 11.3636, "amount": 1000.0, "realized_pnl": 136.36,
                     "priced": True, "n_fills": 2}},
    )
    assert "Filled" in html
    assert "+11.3636" in html
    assert "+1,000" in html


def test_positions_renders_dash_when_no_fill_row():
    html = panel._positions(
        [{"coin_id": "solana", "symbol": "SOL", "action": "hold",
          "current": 300, "target": 300}],
        {}, 10000.0, "usd", {},
    )
    assert "—" in html
    assert "No actions in plan." not in html


def test_positions_still_works_without_the_fills_argument():
    """Existing callers pass no fills; the table must render unchanged."""
    html = panel._positions(
        [{"coin_id": "bitcoin", "symbol": "BTC", "action": "open", "current": 0, "target": 2500}],
        {"bitcoin": 78}, 10000.0, "usd",
    )
    assert "<span>Portfolio worth</span>" not in html
    assert "BTC" in html and "2,500" in html


def test_fills_section_explains_itself_when_empty():
    html = panel._fills_section({}, [], 3, "usd", {})
    assert "no fills yet" in html
    assert "cycle #3" not in html.split("</h2>")[0]


def test_fills_section_lists_each_ledger_row():
    html = panel._fills_section(
        {"bitcoin": {}},
        [_row(side="buy"), _row(side="sell", realized_pnl=136.36)],
        2, "usd", {"bitcoin": "BTC"},
    )
    assert "Computed fills" in html
    assert "2 fill(s) on cycle #2" in html
    assert "+136.36" in html


def test_fills_section_says_when_the_table_is_truncated():
    rows = [_row(cycle=2, qty=1.0 * i) for i in range(1, 31)]
    html = panel._fills_section({}, rows, 2, "usd", {})
    assert "25 fill(s) on cycle #2" in html
    assert "Showing the last 25 of 30." in html
    assert html.count("<tr>") <= 26  # header + 25 rows