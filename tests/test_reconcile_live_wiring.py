"""Tests for main._reconcile_live_positions — the Phase 5 wiring.

Covers the three behaviours the portfolio cycle depends on:

  * paper mode is a no-op (returns None) — paper owns its own ledger;
  * live mode pulls fills, reconciles, and persists + summarizes;
  * every failure path fails OPEN (returns {"error": ...}) so a venue hiccup
    can never kill the trading cycle or destroy previous local state.

All tests monkeypatch the client factory and the Path constants, so no
network and no real output/ files are touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import crypto_council_flow.main as main


class FakeLive:
    """Minimal live client: get_fills + reconcile with recorded calls."""

    name = "fake-live"

    def __init__(self, fills=None, fills_error=None, reconcile_error=None, positions=None, rows=None):
        self._fills = fills if fills is not None else [object()]
        self._fills_error = fills_error
        self._reconcile_error = reconcile_error
        self._positions = positions if positions is not None else {
            "btc": {"qty": 0.01, "avg_cost": 5_000_000_000.0},
        }
        self._rows = rows if rows is not None else [{
            "ts": "2023-11-14T22:13:20+00:00", "cycle": 1, "coin_id": "btc",
            "side": "buy", "qty": 0.01, "price": 5_000_000_000.0,
            "amount": 50_000_000.0, "realized_pnl": None, "fee": 0.0,
        }]
        self.calls = {}

    @property
    def n_fills(self) -> int:
        return len(self._fills)

    def get_fills(self, since=None, symbol=None):
        self.calls["since"] = since
        if self._fills_error:
            raise self._fills_error
        return self._fills

    def reconcile(self, fills, positions, *, cycle=None, now_iso=None, coin_ids_by_symbol=None):
        self.calls["reconcile"] = {
            "n_fills": len(fills), "positions": positions,
            "cycle": cycle, "now_iso": now_iso,
            "coin_ids_by_symbol": coin_ids_by_symbol,
        }
        if self._reconcile_error:
            raise self._reconcile_error
        return self._positions, self._rows


class _LiveWiring:
    """Fixture handle: installs a fake live client and exposes the state dir."""

    def __init__(self, tmp_path: Path):
        self.path = tmp_path
        self._holder: dict[str, object] = {}

    def install(self, client):
        """Register `client` as the one ``_execution_client_for_ledger`` returns."""
        self._holder["client"] = client
        return client

    def factory(self, base_currency=None):
        return self._holder["client"]


@pytest.fixture
def live_wiring(tmp_path, monkeypatch) -> _LiveWiring:
    """Point main's live paths at tmp_path and force live mode.

    Tests call ``live_wiring.install(client)`` to control exactly which
    client the factory hands out, and use ``live_wiring.path`` for
    filesystem assertions (the live state files live directly in it).
    """
    handle = _LiveWiring(tmp_path)

    monkeypatch.setattr(main, "POSITIONS_LIVE_PATH", tmp_path / "live_positions.json")
    monkeypatch.setattr(main, "LEDGER_LIVE_PATH", tmp_path / "live_ledger.json")
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path, raising=False)
    monkeypatch.setattr(main, "get_execution_mode", lambda: "live")
    monkeypatch.setattr(main, "_execution_client_for_ledger", handle.factory)
    return handle


def _reconcile(**kw):
    """Call the Phase 5 wiring with sane defaults."""
    kw.setdefault("cycle", 1)
    kw.setdefault("now_iso", "2026-01-01T12:00:00+00:00")
    kw.setdefault("account_size", 1_000_000_000.0)
    kw.setdefault("base_currency", "rls")
    return main._reconcile_live_positions(**kw)


# ---------------------------------------------------------------------------
# paper mode
# ---------------------------------------------------------------------------

def test_reconcile_is_a_noop_in_paper_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "get_execution_mode", lambda: "paper")
    monkeypatch.setattr(main, "POSITIONS_LIVE_PATH", tmp_path / "p.json")

    def _boom(**kw):  # pragma: no cover - must never be reached
        raise AssertionError("paper mode must not touch the execution client")

    monkeypatch.setattr(main, "_execution_client_for_ledger", _boom)
    assert main._reconcile_live_positions(
        cycle=1, now_iso="t", account_size=1000.0, base_currency="usd",
    ) is None


# ---------------------------------------------------------------------------
# live mode — happy path
# ---------------------------------------------------------------------------

def test_reconcile_live_persists_and_summarizes(live_wiring):
    fake = live_wiring.install(FakeLive())
    out = main._reconcile_live_positions(
        cycle=7, now_iso="2026-01-01T12:00:00+00:00", account_size=1_000_000_000.0,
        base_currency="rls",
        risk_snapshot={"btc": {"current_price": 5_100_000_000.0}},
    )
    # fills pulled, reconcile called with cycle/now_iso passed through
    assert fake.calls["reconcile"]["cycle"] == 7
    assert fake.calls["reconcile"]["now_iso"] == "2026-01-01T12:00:00+00:00"
    assert fake.calls["reconcile"]["n_fills"] == 1

    # files written
    positions = json.loads((live_wiring.path / "live_positions.json").read_text())
    ledger = json.loads((live_wiring.path / "live_ledger.json").read_text())
    assert positions["btc"]["qty"] == 0.01
    assert len(ledger) == 1

    # summary matches apply_plan's shape so the panel can render it
    assert out["mode"] == "live" and out["exchange"] == "fake-live"
    assert out["cycle"] == 7
    assert out["invested"] == pytest.approx(0.01 * 5_100_000_000.0)
    assert out["unrealized_pnl"] == pytest.approx(0.01 * 100_000_000.0)
    assert out["equity"] == pytest.approx(1_000_000_000.0 + out["unrealized_pnl"])
    assert out["open_positions"][0]["coin_id"] == "btc"


def test_reconcile_appends_to_an_existing_ledger(live_wiring):
    """A second cycle extends the ledger rather than replacing it."""
    row_a = {"ts": "t0", "cycle": 1, "coin_id": "btc", "side": "buy",
             "qty": 0.1, "price": 1.0, "amount": 0.1, "realized_pnl": None, "fee": 0.0}
    (live_wiring.path / "live_ledger.json").write_text(json.dumps([row_a]))
    live_wiring.install(FakeLive(fills=[object()]))
    out = main._reconcile_live_positions(
        cycle=2, now_iso="2026-01-01T12:00:00+00:00", account_size=1000.0,
        base_currency="rls", risk_snapshot={},
    )
    ledger = json.loads((live_wiring.path / "live_ledger.json").read_text())
    assert [r["cycle"] for r in ledger] == [1, 1]  # prior row kept, new row appended
    assert out["n_trades"] == 2


def test_reconcile_watermark_narrows_on_later_cycles(live_wiring):
    """The watermark is stored and fed back as `since` on the next cycle."""
    live_wiring.install(FakeLive())
    main._reconcile_live_positions(
        cycle=1, now_iso="2026-01-01T12:00:00+00:00", account_size=1000.0,
        base_currency="rls",
    )
    fake2 = live_wiring.install(FakeLive())
    main._reconcile_live_positions(
        cycle=2, now_iso="2026-01-02T12:00:00+00:00", account_size=1000.0,
        base_currency="rls",
    )
    since = fake2.calls["since"]
    # 2026-01-01T12:00:00Z in epoch ms, less the 30s skew guard.
    assert since == 1_767_268_800_000 - 30_000

def test_reconcile_no_new_fills_is_a_quiet_summary(live_wiring):
    """A cycle with no fills still reports state, and rewrites nothing new."""
    (live_wiring.path / "live_positions.json").write_text(json.dumps({}))
    live_wiring.install(FakeLive(fills=[], positions={}, rows=[]))
    out = main._reconcile_live_positions(
        cycle=3, now_iso="2026-01-01T12:00:00+00:00", account_size=1000.0,
        base_currency="rls", risk_snapshot={},
    )
    assert out["n_fills_this_cycle"] == 0
    assert out["n_rows_this_cycle"] == 0
    assert out["open_positions"] == []
    assert out["equity"] == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# fail-open
# ---------------------------------------------------------------------------

def test_reconcile_fails_open_when_client_is_halted(live_wiring, monkeypatch):
    """EXECUTION_MODE=halted blocks the client but must not crash the cycle."""
    from crypto_council_flow.tools.execution_base import ExecutionHalted

    def _halted(base_currency=None):
        raise ExecutionHalted("kill switch")

    monkeypatch.setattr(main, "_execution_client_for_ledger", _halted)
    out = main._reconcile_live_positions(
        cycle=1, now_iso="t", account_size=1000.0, base_currency="rls",
    )
    # Fails open: the kill switch surfaces as data, never an exception, so
    # the portfolio cycle still writes its paper snapshot and keeps going.
    assert "error" in out
    assert "execution client unavailable" in out["error"]
    assert "kill switch" in out["error"]


def test_reconcile_fails_open_on_get_fills_error(live_wiring):
    """A venue error during fill fetch keeps the previous local state."""
    (live_wiring.path / "live_ledger.json").write_text(json.dumps([{"ts": "keep"}]))
    live_wiring.install(FakeLive(fills_error=RuntimeError("venue down")))
    out = main._reconcile_live_positions(
        cycle=1, now_iso="t", account_size=1000.0, base_currency="rls",
    )
    assert "error" in out and "get_fills failed" in out["error"]
    assert json.loads((live_wiring.path / "live_ledger.json").read_text()) == [{"ts": "keep"}]
    # nothing new written on failure
    assert not (live_wiring.path / "live_positions.json").exists()


def test_reconcile_dedups_fills_in_the_overlap_window(live_wiring):
    """A fill re-fetched by the 30s skew guard is booked once, not twice.

    The watermark deliberately reads 30s of overlap, so the venue may hand
    back a trade already in the ledger. Realized P&L sums the whole ledger
    every cycle, so a duplicate row would count its realization forever.
    """
    row = {"ts": "t", "cycle": 1, "coin_id": "btc", "side": "sell",
           "qty": 0.01, "price": 6_000_000_000.0, "amount": 60_000_000.0,
           "realized_pnl": 9_970_000.0, "fee": 30_000.0,
           "trade_id": "T9", "order_id": "O9"}
    (live_wiring.path / "live_ledger.json").write_text(json.dumps([row]))
    live_wiring.install(FakeLive(positions={}, rows=[row]))
    out = main._reconcile_live_positions(
        cycle=2, now_iso="2026-01-01T12:00:00+00:00", account_size=1_000_000_000.0,
        base_currency="rls", risk_snapshot={},
    )
    ledger = json.loads((live_wiring.path / "live_ledger.json").read_text())
    assert len(ledger) == 1                     # prior row kept, dup skipped
    assert out["realized_pnl"] == pytest.approx(9_970_000.0)
    assert out["n_rows_this_cycle"] == 0


def test_reconcile_keeps_a_distinct_fill(live_wiring):
    """Dedup is keyed on trade_id: a genuinely new fill still lands."""
    prior = {"ts": "t", "cycle": 1, "coin_id": "btc", "side": "buy",
             "qty": 0.01, "price": 5_000_000_000.0, "amount": 50_000_000.0,
             "realized_pnl": None, "fee": 0.0, "trade_id": "T1", "order_id": "O1"}
    (live_wiring.path / "live_ledger.json").write_text(json.dumps([prior]))
    new = dict(prior, trade_id="T2", order_id="O2", cycle=2)
    live_wiring.install(FakeLive(positions={}, rows=[new]))
    main._reconcile_live_positions(
        cycle=2, now_iso="2026-01-01T12:00:00+00:00", account_size=1_000_000_000.0,
        base_currency="rls", risk_snapshot={},
    )
    ledger = json.loads((live_wiring.path / "live_ledger.json").read_text())
    assert [r["trade_id"] for r in ledger] == ["T1", "T2"]


def test_reconcile_maps_tickers_to_coin_ids_for_live_positions(live_wiring):
    """Production shape: the snapshot is keyed `bitcoin`, the venue says BTC.

    Without the ticker -> coin_id map the live position prices at zero even
    though the snapshot has a price. ``NobitexExecutionClient.reconcile``
    does the actual re-keying (see
    ``test_reconcile_maps_tickers_to_coin_ids``); this pins that the wiring
    forwards the map instead of dropping it on the floor.
    """
    fake = live_wiring.install(FakeLive())
    main._reconcile_live_positions(
        cycle=7, now_iso="2026-01-01T12:00:00+00:00", account_size=1_000_000_000.0,
        base_currency="rls",
        risk_snapshot={"bitcoin": {"current_price": 5_100_000_000.0}},
        coin_ids_by_symbol={"BTC": "bitcoin"},
    )
    assert fake.calls["reconcile"]["coin_ids_by_symbol"] == {"BTC": "bitcoin"}


def test_reconcile_forwards_an_empty_map_not_none(live_wiring):
    """A caller with no map still passes ``{}``, never ``None``."""
    fake = live_wiring.install(FakeLive())
    main._reconcile_live_positions(
        cycle=1, now_iso="2026-01-01T12:00:00+00:00", account_size=1000.0,
        base_currency="rls", risk_snapshot={},
    )
    assert fake.calls["reconcile"]["coin_ids_by_symbol"] == {}


def test_reconcile_fails_open_on_reconcile_error(live_wiring):
    live_wiring.install(FakeLive(reconcile_error=ValueError("bad fills")))
    out = main._reconcile_live_positions(
        cycle=1, now_iso="t", account_size=1000.0, base_currency="rls",
    )
    assert "error" in out and "reconcile failed" in out["error"]


def test_reconcile_missing_client_reconcile_is_reported(live_wiring):
    """A client without reconcile() is reported, not silently skipped."""

    class Bare:
        name = "bare"

        def get_fills(self, since=None, symbol=None):
            return []

    live_wiring.install(Bare())
    out = main._reconcile_live_positions(
        cycle=1, now_iso="t", account_size=1000.0, base_currency="rls",
    )
    assert "error" in out and "no reconcile()" in out["error"]


def test_reconcile_corrupt_local_state_is_treated_as_empty(live_wiring):
    """Unparseable positions/ledger start fresh instead of raising."""
    (live_wiring.path / "live_positions.json").write_text("{not json")
    (live_wiring.path / "live_ledger.json").write_text("garbage")
    live_wiring.install(FakeLive(positions={}, rows=[]))
    out = main._reconcile_live_positions(
        cycle=1, now_iso="t", account_size=1000.0, base_currency="rls",
        risk_snapshot={},
    )
    assert "error" not in out
    assert json.loads((live_wiring.path / "live_positions.json").read_text()) == {}
    assert json.loads((live_wiring.path / "live_ledger.json").read_text()) == []


def test_reconcile_realized_pnl_sums_the_ledger(live_wiring):
    """Realized P&L is the ledger total, not a fabricated venue number."""
    row = {"ts": "t", "cycle": 1, "coin_id": "btc", "side": "sell",
           "qty": 0.01, "price": 6_000_000_000.0, "amount": 60_000_000.0,
           "realized_pnl": 9_970_000.0, "fee": 30_000.0}
    live_wiring.install(FakeLive(positions={}, rows=[row]))
    out = main._reconcile_live_positions(
        cycle=1, now_iso="t", account_size=1_000_000_000.0, base_currency="rls",
        risk_snapshot={},
    )
    assert out["realized_pnl"] == pytest.approx(9_970_000.0)
    assert out["equity"] == pytest.approx(1_000_000_000.0 + 9_970_000.0)


# ---------------------------------------------------------------------------
# Phase 6: guard refusals must not be invisible in live mode
# ---------------------------------------------------------------------------

def test_guard_refusals_surface_on_the_summary():
    """A blocked order must not look like a filled one.

    In live mode the reconciliation round-trip *replaces* the summary
    ``apply_plan`` returned, so without this annotation a refused order
    disappears: the panel shows equity as if the plan had been executed.
    Every refusal is counted and noted instead.
    """
    summary = {"realized_pnl": 0.0, "errors": [
        {"coin_id": "bitcoin", "symbol": "BTC-RLS",
         "error": "blocked by Phase 6 caps: single-position cap (5.0%)"},
        {"coin_id": "ethereum", "symbol": "ETH-RLS",
         "error": "blocked by Phase 6 caps: no price"},
    ]}
    refusals = main._annotate_guard_refusals(summary)
    assert len(refusals) == 2
    assert summary["guard_refusals"] == 2
    assert "2 order(s) refused" in summary["live_note"]


def test_no_refusals_leaves_the_summary_untouched():
    """A fully filled plan must not gain refusal keys."""
    summary = {"realized_pnl": 0.0}
    main._annotate_guard_refusals(summary)
    assert "guard_refusals" not in summary
    assert "live_note" not in summary


def test_a_refusal_without_a_coin_id_still_counts():
    """Symbol-only refusals (no symbol mapping) are still visible."""
    summary = {"errors": [{"symbol": "BTC-RLS", "error": "no price"}]}
    refusals = main._annotate_guard_refusals(summary)
    assert len(refusals) == 1
    assert summary["guard_refusals"] == 1
