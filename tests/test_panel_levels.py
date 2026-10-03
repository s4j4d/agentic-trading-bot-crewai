"""Panel SL/TP columns — every action row renders stop_loss/take_profit.

RED tracer: positions table must show per-action stop + take levels
("—" when null). Prices are vs-currency levels, not base amounts.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_panel_mod():
    path = Path(__file__).resolve().parent.parent / "scripts" / "portfolio_panel.py"
    spec = importlib.util.spec_from_file_location("portfolio_panel", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_snapshot(tmp_path, actions):
    snap = {
        "saved_utc": "2026-10-03T00:00:00+00:00",
        "portfolio_cycle": 1,
        "duration_s": 12.5,
        "account_size": 10000.0,
        "base_currency": "usd",
        "max_total_exposure_pct": 60.0,
        "max_single_position_pct": 20.0,
        "plan": {
            "actions": actions,
            "total_target": 1500.0,
            "total_exposure_pct": 15.0,
            "cash_remaining": 8500.0,
            "rationale": "Tracer.",
        },
        "opportunities": [],
        "analysed_coins": [],
    }
    snap_path = tmp_path / "portfolio_plan.json"
    snap_path.write_text(json.dumps(snap), encoding="utf-8")
    return snap_path


class TestPanelRiskColumns:
    def test_action_row_renders_stop_and_take(self, tmp_path, monkeypatch):
        mod = _load_panel_mod()
        snap_path = _write_snapshot(tmp_path, [
            {"coin_id": "solana", "symbol": "SOL", "action": "open",
             "current": 0.0, "target": 1500.0, "reason": "Top.",
             "stop_loss": 97.5, "take_profit": 105.0},
        ])
        monkeypatch.setattr(mod, "SNAPSHOT", snap_path)
        monkeypatch.setattr(mod, "HISTORY", tmp_path / "portfolio_history.jsonl")

        html = mod.build()

        assert "<th>Stop</th>" in html
        assert "<th>Take</th>" in html
        assert "97.5" in html
        assert "105.0" in html

    def test_null_levels_render_as_dash(self, tmp_path, monkeypatch):
        mod = _load_panel_mod()
        snap_path = _write_snapshot(tmp_path, [
            {"coin_id": "ghost", "symbol": "GHO", "action": "hold",
             "current": 100.0, "target": 100.0, "reason": "Wait.",
             "stop_loss": None, "take_profit": None},
        ])
        monkeypatch.setattr(mod, "SNAPSHOT", snap_path)
        monkeypatch.setattr(mod, "HISTORY", tmp_path / "portfolio_history.jsonl")

        html = mod.build()

        assert "<th>Stop</th>" in html
        assert "—" in html
