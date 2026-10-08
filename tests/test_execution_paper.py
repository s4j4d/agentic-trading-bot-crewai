"""Tests for PaperExecutionClient — the paper-ledger client behind the interface."""

from __future__ import annotations

import json

import pytest

from crypto_council_flow.tools.execution_paper import PaperExecutionClient


def _client(tmp_path, fee_bps=0.0):
    return PaperExecutionClient(
        ledger_path=tmp_path / "paper_ledger.json",
        positions_path=tmp_path / "paper_positions.json",
        quote_currency="usd",
        fee_bps=fee_bps,
    )


def _snapshot(price=100.0):
    return {"bitcoin": {"current_price": price, "atr_pct": 0.02}}


def test_apply_plan_roundtrip_fee_free_matches_legacy(tmp_path):
    c = _client(tmp_path)
    plan = {"actions": [{"coin_id": "bitcoin", "target": 1000.0}]}
    summary = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(100.0),
        account_size=1000.0, base_currency="usd", now_iso="t", cycle=1,
    )
    assert summary["equity"] == pytest.approx(1000.0)
    assert summary["invested"] == pytest.approx(1000.0)
    ledger = json.loads((tmp_path / "paper_ledger.json").read_text())
    assert len(ledger) == 1
    assert ledger[0]["side"] == "buy" and ledger[0]["qty"] == pytest.approx(10.0)
    assert "fee" not in ledger[0]


def test_apply_plan_with_fee_booked_in_ledger(tmp_path):
    c = _client(tmp_path, fee_bps=10.0)  # 0.001
    plan = {"actions": [{"coin_id": "bitcoin", "target": 1000.0}]}
    c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(100.0),
        account_size=1000.0, base_currency="usd", now_iso="t", cycle=1,
    )
    ledger = json.loads((tmp_path / "paper_ledger.json").read_text())
    assert ledger[0]["fee"] == pytest.approx(1.0)  # 1000 * 0.001


def test_get_balances_reflects_cash_and_positions(tmp_path):
    c = _client(tmp_path)
    c.apply_plan(
        plan={"actions": [{"coin_id": "bitcoin", "target": 500.0}]},
        risk_snapshot=_snapshot(100.0),
        account_size=1000.0, base_currency="usd", now_iso="t", cycle=1,
    )
    balances = {b.asset: b for b in c.get_balances()}
    assert balances["BITCOIN"].free == pytest.approx(5.0)
    # cash = account_size - invested(500) = 500
    assert balances["USD"].free == pytest.approx(500.0)


def test_place_order_fills_immediately(tmp_path):
    from crypto_council_flow.tools.execution_base import OrderRequest, OrderSide

    c = _client(tmp_path)
    c.apply_plan(
        plan={"actions": [{"coin_id": "bitcoin", "target": 500.0}]},
        risk_snapshot=_snapshot(100.0),
        account_size=1000.0, base_currency="usd", now_iso="t", cycle=1,
    )
    req = OrderRequest(symbol="BITCOIN", side=OrderSide.BUY, qty=2.0)
    res = c.place_order(req)
    assert res.status == "filled"
    assert res.avg_fill_price == pytest.approx(100.0)
    assert res.cost == pytest.approx(200.0)


def test_place_order_unique_order_ids(tmp_path):
    """Two identical orders must produce distinct order_ids for reconciliation."""
    from crypto_council_flow.tools.execution_base import OrderRequest, OrderSide

    c = _client(tmp_path)
    c.apply_plan(
        plan={"actions": [{"coin_id": "bitcoin", "target": 500.0}]},
        risk_snapshot=_snapshot(100.0),
        account_size=1000.0, base_currency="usd", now_iso="t", cycle=1,
    )
    req1 = OrderRequest(symbol="BITCOIN", side=OrderSide.BUY, qty=1.0)
    req2 = OrderRequest(symbol="BITCOIN", side=OrderSide.BUY, qty=1.0)
    r1 = c.place_order(req1)
    r2 = c.place_order(req2)
    assert r1.order_id != r2.order_id
    assert r1.order_id.startswith("paper-")
    assert r2.order_id.startswith("paper-")


def test_get_fills_filters_by_since_watermark(tmp_path):
    """Watermark filters rows by ISO timestamp (epoch ms)."""
    from datetime import datetime, timezone

    c = _client(tmp_path)
    # Two cycles: cycle 1 at T1, cycle 2 at T2
    t1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    c.apply_plan(
        plan={"actions": [{"coin_id": "bitcoin", "target": 500.0}]},
        risk_snapshot=_snapshot(100.0),
        account_size=1000.0, base_currency="usd", now_iso=t1, cycle=1,
    )
    c.apply_plan(
        plan={"actions": [{"coin_id": "bitcoin", "target": 1000.0}]},
        risk_snapshot=_snapshot(100.0),
        account_size=1000.0, base_currency="usd", now_iso=t2, cycle=2,
    )
    t1_ms = int(datetime.fromisoformat(t1).timestamp() * 1000)
    t2_ms = int(datetime.fromisoformat(t2).timestamp() * 1000)
    # Watermark between T1 and T2 should keep only cycle 2
    mid = (t1_ms + t2_ms) // 2
    fills = c.get_fills(since=mid)
    assert len(fills) == 1
    assert fills[0].ts == t2_ms


def test_get_fills_normalises_close_to_sell(tmp_path):
    """Ledger 'close' rows are reported as OrderSide.SELL in Fill."""
    from crypto_council_flow.tools.execution_base import OrderSide

    c = _client(tmp_path)
    c.apply_plan(
        plan={"actions": [{"coin_id": "bitcoin", "target": 500.0}]},
        risk_snapshot=_snapshot(100.0),
        account_size=1000.0, base_currency="usd", now_iso="2026-01-01T12:00:00+00:00", cycle=1,
    )
    # Close the position
    c.apply_plan(
        plan={"actions": [{"coin_id": "bitcoin", "target": 0.0}]},
        risk_snapshot=_snapshot(110.0),
        account_size=1000.0, base_currency="usd", now_iso="2026-01-01T13:00:00+00:00", cycle=2,
    )
    fills = c.get_fills()
    # Should have buy (cycle 1) and close->sell (cycle 2)
    sides = [f.side for f in fills]
    assert OrderSide.BUY in sides
    assert OrderSide.SELL in sides
    assert "close" not in sides
