"""Panel live-mode support (Phase 5).

The panel used to read paper_ledger.json unconditionally, so in live mode
it showed stale or empty paper numbers against a plan whose fills happened
on a real exchange. These tests pin the mode selection and the labels the
page renders, because a mislabelled P&L number is worse than no number.

The panel script is deliberately stdlib-only and cannot import the app
(that would pull the whole CREWAI stack into a page generator), so the mode
contract it relies on is what is asserted here: pnl.mode == "live" and the
presence of a live reconciliation error under pnl.error.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import portfolio_panel as panel  # noqa: E402


PAPER_ROWS = [
    {
        "ts": "2026-01-02T10:00:00+00:00", "cycle": 7, "coin_id": "btc",
        "side": "buy", "qty": 0.001, "price": 5_000_000_000.0,
        "amount": 5_000_000.0, "realized_pnl": None, "fee": 0.0,
    },
]

LIVE_ROWS = [
    {
        "ts": "2026-01-02T09:00:00+00:00", "cycle": 7, "coin_id": "eth",
        "side": "buy", "qty": 0.05, "price": 90_000_000.0,
        "amount": 4_500_000.0, "realized_pnl": None, "fee": 0.0,
    },
    {
        "ts": "2026-01-02T10:30:00+00:00", "cycle": 7, "coin_id": "eth",
        "side": "close", "qty": 0.05, "price": 95_000_000.0,
        "amount": 4_750_000.0, "realized_pnl": 250_000.0, "fee": 0.0,
    },
]


def _snapshot(**overrides):
    snap = {
        "saved_utc": "2026-01-02T10:35:00+00:00",
        "portfolio_cycle": 7,
        "duration_s": 42.0,
        "account_size": 10_000_000.0,
        "base_currency": "rls",
        "max_total_exposure_pct": 30,
        "max_single_position_pct": 5,
        "plan": {
            "total_target": 4_500_000.0,
            "cash_remaining": 5_500_000.0,
            "rationale": "test plan",
            "actions": [
                {
                    "coin_id": "eth", "symbol": "ETH", "action": "open",
                    "target": 4_500_000.0, "current": 0.0,
                },
            ],
        },
        "opportunities": [
            {"coin_id": "eth", "symbol": "ETH", "score": 8, "risk_tier": "medium"},
        ],
        "pnl": {
            "mode": "paper",
            "total_pnl": 0.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0,
            "equity": 10_000_000.0, "invested": 0.0, "n_trades": 0,
            "open_positions": [],
        },
    }
    snap.update(overrides)
    return snap


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=False, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Point the panel at an isolated output dir with both ledgers present."""
    monkeypatch.setattr(panel, "OUT_DIR", tmp_path)
    monkeypatch.setattr(panel, "SNAPSHOT", tmp_path / "portfolio_plan.json")
    monkeypatch.setattr(panel, "HISTORY", tmp_path / "portfolio_history.jsonl")
    monkeypatch.setattr(panel, "LEDGER", tmp_path / "paper_ledger.json")
    monkeypatch.setattr(panel, "LEDGER_LIVE", tmp_path / "live_ledger.json")
    monkeypatch.setattr(panel, "PANEL", tmp_path / "portfolio_panel.html")
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    _write(panel.LEDGER, PAPER_ROWS)
    _write(panel.LEDGER_LIVE, LIVE_ROWS)
    return tmp_path


# ---------------------------------------------------------------------------
# mode resolution
# ---------------------------------------------------------------------------


def test_resolve_mode_override_beats_the_snapshot(output_dir):
    """--mode is absolute: you can inspect the paper ledger while live runs."""
    snap = _snapshot(pnl={"mode": "live"}, pnl_live_note="reconciled")
    assert panel._resolve_mode(snap, "paper") == "paper"
    assert panel._resolve_mode(snap, "live") == "live"


def test_resolve_mode_uses_the_snapshot_not_the_env(output_dir):
    """The snapshot says how it was made; the current EXECUTION_MODE is only
    a fallback, or restarting the bot would rewrite the page's history."""
    snap = _snapshot(pnl={"mode": "live"})
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("EXECUTION_MODE", "paper")
        assert panel._resolve_mode(snap) == "live"

    snap_paper = _snapshot(pnl={"mode": "paper"})
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("EXECUTION_MODE", "live")
        assert panel._resolve_mode(snap_paper) == "paper"


def test_resolve_mode_falls_back_to_env_for_a_pre_phase5_snapshot(output_dir):
    """A snapshot written before Phase 5 has neither marker, so the bot's
    configured mode decides rather than silently showing paper numbers."""
    snap = _snapshot(pnl={"total_pnl": 1.0})
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("EXECUTION_MODE", raising=False)
        assert panel._resolve_mode(snap) == "paper"
        mp.setenv("EXECUTION_MODE", "live")
        assert panel._resolve_mode(snap) == "live"


def test_resolve_mode_halted_counting_as_paper_is_deliberate(output_dir):
    """halted has no ledger of its own; the paper files are the only thing
    that exists, and the page must not invent a 'live' view for them."""
    snap = _snapshot(pnl={"total_pnl": 1.0})
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("EXECUTION_MODE", "halted")
        assert panel._resolve_mode(snap) == "paper"


def test_ledger_path_and_label_per_mode(output_dir):
    assert panel._ledger_path("live") == panel.LEDGER_LIVE
    assert panel._ledger_path("paper") == panel.LEDGER
    assert "venue" in panel._ledger_label("live")
    assert "simulated" in panel._ledger_label("paper")


# ---------------------------------------------------------------------------
# rendered page
# ---------------------------------------------------------------------------


def _build(output_dir, snap):
    _write(panel.SNAPSHOT, snap)
    return panel.build()


def test_paper_snapshot_renders_paper_ledger(output_dir):
    """Default behaviour is unchanged: paper numbers, paper wording."""
    out = _build(output_dir, _snapshot())
    # paper rows are read: cycle 1, btc
    assert ">1</td>" in out or "0.001" in out
    assert "paper ledger (simulated, no real orders)" in out
    assert "live ledger" not in out
    # the live-only fill (cycle 7 eth close, realized 250000) is NOT shown
    assert "250,000" not in out
    assert "ledger <b>paper</b>" in out


def test_live_snapshot_renders_live_ledger(output_dir):
    """The whole point of Phase 5: a live snapshot shows the venue's fills."""
    snap = _snapshot(
        pnl={
            "mode": "live", "exchange": "nobitex",
            "total_pnl": 250_000.0, "realized_pnl": 250_000.0,
            "unrealized_pnl": 0.0, "equity": 10_000_000.0,
            "invested": 0.0, "n_trades": 2,
            "open_positions": [],
        },
        pnl_live_note="live reconciliation: fills pulled from the venue",
    )
    out = _build(output_dir, snap)
    assert "live ledger (real venue fills, reconciled)" in out
    assert "ledger <b>LIVE</b>" in out
    # realized P&L from the venue fill is rendered
    assert "+250,000" in out
    assert "paper ledger" not in out
    # the paper-only row (btc) is not shown
    assert "5,000,000" not in out


def test_mode_override_switches_the_ledger(output_dir):
    """--mode forces the ledger regardless of what the snapshot claims."""
    snap = _snapshot(pnl={"mode": "live"}, pnl_live_note="reconciled")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(panel, "_MODE_OVERRIDE", "paper")
        out = _build(output_dir, snap)
        assert "ledger <b>paper</b>" in out
        assert "250,000" not in out

        mp.setattr(panel, "_MODE_OVERRIDE", "live")
        out = _build(output_dir, snap)
        assert "ledger <b>LIVE</b>" in out
        assert "+250,000" in out


def test_missing_live_ledger_says_live_not_paper(output_dir):
    """A live snapshot before the first fill must not claim 'paper' P&L or
    read the paper ledger that happens to be sitting next to it."""
    panel.LEDGER_LIVE.unlink()
    snap = _snapshot(pnl={"mode": "live", "error": "get_fills failed: 429"})
    out = _build(output_dir, snap)
    assert "paper ledger (simulated" not in out
    assert "live ledger has no fills yet" in out


def test_reconcile_error_is_banner_ed(output_dir):
    """A failed live reconcile must be visible: the page shows *previous*
    local state, and a reader has to be able to tell that's what's on screen."""
    snap = _snapshot(
        pnl={"mode": "live", "error": "get_fills failed: HTTP 429"},
        pnl_live_note=None,
    )
    out = _build(output_dir, snap)
    assert "strip-err" in out
    assert "get_fills failed: HTTP 429" in out
    assert "showing previous local state" in out


def test_paper_snapshot_never_shows_a_live_error_banner(output_dir):
    """A stale pnl.error from an old live run must not appear on a paper page."""
    snap = _snapshot(pnl={"mode": "paper", "error": "venue down"})
    out = _build(output_dir, snap)
    assert "live reconciliation failed" not in out


def test_open_position_wording_per_mode(output_dir):
    pos = {
        "coin_id": "eth", "qty": 0.05, "avg_cost": 90_000_000.0,
        "price": 90_000_000.0, "market_value": 4_500_000.0,
        "unrealized_pnl": 0.0,
    }
    out = _build(output_dir, _snapshot(pnl={"mode": "live", "open_positions": [pos]}))
    assert "No open live positions." not in out
    out = _build(output_dir, _snapshot(pnl={"mode": "paper", "open_positions": []}))
    assert "No open paper positions." in out


def test_no_snapshot_still_renders(output_dir):
    panel.SNAPSHOT.unlink(missing_ok=True)
    out = panel.build()
    assert "Nothing to show yet" in out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_parse_args_accepts_both_forms(output_dir):
    assert panel._parse_args([]) is None
    assert panel._parse_args(["--mode", "live"]) == "live"
    assert panel._parse_args(["--mode=paper"]) == "paper"
    assert panel._parse_args(["--mode", "paper"]) == "paper"


def test_parse_args_rejects_unknown_modes_and_keeps_going(output_dir, capsys):
    """A typo must not crash the page build or silently pick a ledger."""
    assert panel._parse_args(["--mode", "bogus"]) is None
    assert "unknown --mode" in capsys.readouterr().out
    # a dangling --mode with no value is ignored, not an IndexError
    assert panel._parse_args(["--mode"]) is None


def test_main_sets_the_override(output_dir, monkeypatch):
    """main() is the only writer of the override, so argv actually reaches build()."""
    _write(panel.SNAPSHOT, _snapshot())
    monkeypatch.setattr(sys, "argv", ["portfolio_panel.py", "--mode", "live"])
    assert panel.main() == 0
    assert panel._MODE_OVERRIDE == "live"
    html = panel.PANEL.read_text(encoding="utf-8")
    assert "ledger <b>LIVE</b>" in html


def test_load_ledger_still_defaults_to_paper(output_dir):
    """Backwards compatibility: callers passing no path get the paper ledger."""
    rows = panel._load_ledger()
    assert len(rows) == 1 and rows[0]["coin_id"] == "btc"


def test_load_ledger_tolerates_corrupt_live_file(output_dir):
    """A half-written live_ledger.json must not break the page (main.py's
    reconcile repairs it on the next cycle)."""
    panel.LEDGER_LIVE.write_text("{not json", encoding="utf-8")
    assert panel._load_ledger(panel.LEDGER_LIVE) == []


def test_load_ledger_rejects_non_list_json(output_dir):
    _write(panel.LEDGER_LIVE, {"coin": "eth"})
    assert panel._load_ledger(panel.LEDGER_LIVE) == []
