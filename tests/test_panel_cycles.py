"""Cycle accounting in the portfolio panel.

`manage_portfolio()` is the only step that counts as a cycle — it is the
"analysis + portfolio manager" pair the operator reasons about. `risk_tick()`
runs every 60s with no LLM call, force-closing positions whose stop/take or
max-hold triggered *between* cycles, and writes a slim history row per tick
tagged `source: "risk_tick"`.

Those tick rows carry the *current, unchanged* `portfolio_cycle`, so counting
every line in `portfolio_history.jsonl` made one cycle look like six: six
points on the chart, six table rows, all labelled `#1`.

The panel therefore counts main cycles only. A row explicitly tagged with a
non-main source is a progress note *inside* a cycle, not a cycle of its own.
Legacy rows written before the marker existed have no `source` key and are
main cycles.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "scripts" / "portfolio_panel.py"


def _load_panel():
    spec = importlib.util.spec_from_file_location(
        "portfolio_panel_cycles_under_test", PANEL_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def panel():
    return _load_panel()


# ---------------------------------------------------------------------------
# row builders
# ---------------------------------------------------------------------------


def _main_row(cycle: int, ts: str, **kw):
    return {
        "ts": ts,
        "cycle": cycle,
        "source": "main",
        "account_size": 10_000.0,
        "equity": 10_000.0,
        "total_target": 0.0,
        "total_exposure_pct": 0.0,
        "cash_remaining": 10_000.0,
        "n_positions": 0,
        "duration_s": 30.0,
        **kw,
    }


def _risk_row(cycle: int, ts: str, **kw):
    return {**_main_row(cycle, ts), "source": "risk_tick", "duration_s": 0.0, **kw}


def _write_history(tmp_path, rows):
    path = tmp_path / "portfolio_history.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _write_snapshot(tmp_path, **kw):
    snap = {
        "saved_utc": "2026-10-05T00:00:00+00:00",
        "portfolio_cycle": 1,
        "account_size": 10_000.0,
        "base_currency": "usd",
        "plan": {"actions": []},
        "pnl": {"equity": 10_000.0},
    }
    snap.update(kw)
    (tmp_path / "portfolio_plan.json").write_text(json.dumps(snap), encoding="utf-8")


@pytest.fixture
def sandbox(tmp_path, monkeypatch, panel):
    monkeypatch.setattr(panel, "OUT_DIR", tmp_path)
    monkeypatch.setattr(panel, "SNAPSHOT", tmp_path / "portfolio_plan.json")
    monkeypatch.setattr(panel, "HISTORY", tmp_path / "portfolio_history.jsonl")
    monkeypatch.setattr(panel, "LEDGER", tmp_path / "paper_ledger.json")
    return tmp_path


# ---------------------------------------------------------------------------
# the filter itself
# ---------------------------------------------------------------------------


def test_risk_tick_rows_are_not_cycles(panel):
    """A tick row shares its cycle's number, so it must not become a cycle."""
    rows = [
        _main_row(1, "2026-10-01T00:00:00+00:00"),
        _risk_row(1, "2026-10-01T00:01:00+00:00"),
        _risk_row(1, "2026-10-01T00:02:00+00:00"),
        _main_row(2, "2026-10-01T00:03:00+00:00"),
    ]
    assert [r["cycle"] for r in panel._main_cycles(rows)] == [1, 2]


def test_legacy_rows_without_a_source_are_main_cycles(panel):
    """Rows written before the marker existed carry no `source` key."""
    rows = [{"ts": "2026-10-01T00:00:00+00:00", "cycle": 4, "account_size": 10_000.0}]
    assert panel._main_cycles(rows) == rows


def test_unknown_sources_are_not_silently_dropped(panel):
    """Only known non-main sources are excluded; a new marker is a main cycle
    until someone says otherwise."""
    rows = [_main_row(1, "2026-10-01T00:00:00+00:00", source="something_new")]
    assert len(panel._main_cycles(rows)) == 1


def test_history_file_loads_only_main_cycles(panel, tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "HISTORY", _write_history(tmp_path, [
        _main_row(1, "2026-10-01T00:00:00+00:00"),
        _risk_row(1, "2026-10-01T00:01:00+00:00"),
        _main_row(2, "2026-10-01T00:02:00+00:00"),
    ]))
    assert [r["cycle"] for r in panel._load_history()] == [1, 2]


def test_window_counts_main_cycles_not_file_lines(panel, tmp_path, monkeypatch):
    """Regression: the 60-row window must not be spent on risk ticks, which
    would silently evict real cycles from the chart as the bot runs."""
    monkeypatch.setattr(panel, "HISTORY_WINDOW", 2)
    monkeypatch.setattr(panel, "HISTORY", _write_history(tmp_path, [
        _main_row(1, "2026-10-01T00:00:00+00:00"),
        *[_risk_row(1, f"2026-10-01T00:{m:02d}:00+00:00") for m in range(1, 30)],
        _main_row(2, "2026-10-01T01:00:00+00:00"),
        _main_row(3, "2026-10-01T02:00:00+00:00"),
    ]))
    assert [r["cycle"] for r in panel._load_history()] == [2, 3]


def test_baseline_anchors_to_first_main_cycle(panel, tmp_path, monkeypatch):
    """A tick row before the first main cycle must not become the baseline."""
    monkeypatch.setattr(panel, "HISTORY", _write_history(tmp_path, [
        _risk_row(0, "2026-10-01T00:00:00+00:00", account_size=999.0),
        _main_row(1, "2026-10-01T00:01:00+00:00", account_size=50_000.0),
    ]))
    assert panel._first_account_size() == 50_000.0


# ---------------------------------------------------------------------------
# rendered page
# ---------------------------------------------------------------------------


def test_page_counts_main_cycles_only(panel, sandbox):
    _write_history(sandbox, [
        _main_row(1, "2026-10-01T00:00:00+00:00"),
        *[_risk_row(1, f"2026-10-01T00:0{m}:00+00:00") for m in range(1, 6)],
    ])
    _write_snapshot(sandbox)
    html = panel.build()
    # one main cycle logged -> the chart (needs 2 points) must not render
    assert "1 cycle(s) logged so far." in html
    assert "Portfolio value over cycles" in html
    assert "<svg" not in html.split("Portfolio value over cycles")[1].split("Signal snapshot")[0]


def test_two_main_cycles_render_two_points_despite_ticks(panel, sandbox):
    _write_history(sandbox, [
        _main_row(1, "2026-10-01T00:00:00+00:00", equity=10_000.0),
        *[_risk_row(1, f"2026-10-01T00:0{m}:00+00:00") for m in range(1, 6)],
        _main_row(2, "2026-10-01T01:00:00+00:00", equity=10_500.0),
    ])
    _write_snapshot(sandbox, portfolio_cycle=2, pnl={"equity": 10_500.0})
    html = panel.build()
    chart = html.split("Portfolio value over cycles")[1].split("Signal snapshot")[0]
    assert "<svg" in chart
    # 2 points -> 2 line vertices + 1 endpoint callout dot
    assert chart.count("<circle") == 3
    # cycle-over-cycle gain is measured between the two MAIN cycles
    assert "+500" in chart


def test_page_discloses_the_filtered_out_ticks(panel, sandbox):
    """Five tick rows logged, two cycles counted — the page must say so."""
    _write_history(sandbox, [
        _main_row(1, "2026-10-01T00:00:00+00:00", equity=10_000.0),
        *[_risk_row(1, f"2026-10-01T00:0{m}:00+00:00") for m in range(1, 6)],
        _main_row(2, "2026-10-01T01:00:00+00:00", equity=10_500.0),
    ])
    _write_snapshot(sandbox, portfolio_cycle=2, pnl={"equity": 10_500.0})
    html = panel.build()
    assert "5 risk-tick note(s)" in html
