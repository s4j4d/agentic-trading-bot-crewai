"""Tests for the portfolio panel chart (scripts/portfolio_panel.py).

Covers the cases the _axis_chart / _row_worth work has to get right:
degenerate inputs, a flat (unchanged) series, rows with no equity key,
and short x-label lists.
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
    spec = importlib.util.spec_from_file_location("portfolio_panel_under_test", PANEL_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def panel():
    return _load_panel()


# --------------------------------------------------------------------------
# _axis_chart
# --------------------------------------------------------------------------


@pytest.mark.parametrize("values", [[], [1.0]])
def test_axis_chart_needs_two_points(panel, values):
    assert panel._axis_chart(values) == ""


def test_axis_chart_has_labelled_axes(panel):
    svg = panel._axis_chart([10.0, 20.0, 15.0], ["a", "b", "c"], y_title="Worth (USD)", x_title="Cycle")
    assert svg.startswith("<svg")
    assert 'class="axis"' in svg
    assert 'class="tick"' in svg
    assert "Worth (USD)" in svg
    assert "Cycle" in svg


def test_axis_chart_short_labels_do_not_raise(panel):
    """Regression: labels shorter than values used to IndexError."""
    svg = panel._axis_chart([1.0, 2.0, 3.0], labels=["only-one"])
    assert svg.startswith("<svg")


def test_axis_chart_flat_series_has_one_tick(panel):
    """Regression: a flat series rendered five identical rounded y-ticks."""
    svg = panel._axis_chart([10000.0] * 4)
    labels = _texts(svg)
    assert any("unchanged at" in t for t in labels)
    # exactly one numeric y-tick, not five duplicates
    # the single gridline tick must carry the true value, not a padded one
    assert "unchanged at 10.0k" in labels


def test_axis_chart_y_ticks_distinct_for_small_range(panel):
    """Regression: with values near-constant, _compact rendered all five
    y-ticks as the same string ("10.0k"), leaving the axis unreadable."""
    svg = panel._axis_chart([10000.0, 10050.0, 9990.0, 10020.0])
    ticks = [t for t in _texts(svg) if t and t[0].isdigit()]
    assert len(ticks) == len(set(ticks)), f"duplicate y-tick labels: {ticks}"


def test_axis_chart_escapes_user_text(panel):
    svg = panel._axis_chart([1.0, 2.0], ["<script>x</script>", "b"], y_title='"><img onerror=x>')
    assert "<script>" not in svg
    assert '"><img' not in svg
    assert "&lt;img" in svg  # escaped, not raw


def _texts(svg: str) -> list[str]:
    import re

    return [m.strip() for m in re.findall(r">([^<>]+)<", svg)]


# --------------------------------------------------------------------------
# _row_worth / build(): chart and table must agree
# --------------------------------------------------------------------------


def _write_history(tmp_path, rows):
    path = tmp_path / "portfolio_history.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _write_snapshot(tmp_path, snap):
    (tmp_path / "portfolio_plan.json").write_text(json.dumps(snap), encoding="utf-8")


@pytest.fixture
def sandbox(tmp_path, monkeypatch, panel):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(panel, "OUT_DIR", out)
    monkeypatch.setattr(panel, "SNAPSHOT", out / "portfolio_plan.json")
    monkeypatch.setattr(panel, "HISTORY", out / "portfolio_history.jsonl")
    return out


def test_row_worth_falls_back_to_account_size(panel):
    assert panel._row_worth({"account_size": 10000}) == 10000.0
    assert panel._row_worth({"account_size": 10000, "equity": 9000}) == 9000.0
    # junk equity must not crash and must not become 0
    assert panel._row_worth({"account_size": 10000, "equity": "junk"}) == 10000.0


def test_kpi_shows_zero_equity_not_account(panel, sandbox):
    """Regression: `or account` made a genuine equity of 0.0 fall back to
    the full account size."""
    _write_snapshot(
        sandbox,
        {
            "saved_utc": "2026-10-05T00:00:00+00:00",
            "account_size": 10000,
            "base_currency": "usd",
            "plan": {"actions": []},
            "pnl": {"equity": 0.0, "total_pnl": -10000.0},
        },
    )
    html = panel.build()
    head = html.split("Profit / loss")[0]
    # the KPI tile reads the real equity (0), not the 10,000 account size
    import re as _re
    kpi = _re.search(r"<span>Account Equity</span><b>([^<]*)</b>", head)
    assert kpi, "Account Equity KPI tile missing"
    assert kpi.group(1).strip().startswith("0")


def test_chart_and_table_agree_without_equity_key(panel, sandbox):
    """Regression: pre-ledger rows plotted account_size in the chart but 0
    in the table."""
    rows = [
        {"ts": f"2026-10-0{i}T00:00:00+00:00", "cycle": i, "account_size": 10000,
         "total_target": 3500, "cash_remaining": 6500, "duration_s": 30}
        for i in (1, 2, 3)
    ]
    _write_history(sandbox, rows)
    _write_snapshot(
        sandbox,
        {"saved_utc": "2026-10-05T00:00:00+00:00", "account_size": 10000,
         "base_currency": "usd", "plan": {"actions": []}},
    )
    html = panel.build()
    table = html.split("<th>Equity</th>")[1]
    assert ">0<" not in table  # no zero cells where the equity should be
    assert "10,000" in table


def test_baseline_uses_first_logged_cycle(panel, sandbox):
    """Regression: with a 60-row window the baseline silently re-based to
    hist[0]; it must come from the earliest logged row."""
    rows = [{"ts": "2026-10-01T00:00:00+00:00", "cycle": 1, "account_size": 50000,
             "total_target": 0, "cash_remaining": 50000, "duration_s": 10}]
    rows += [{"ts": f"2026-10-02T00:00:{i:02d}+00:00", "cycle": i, "account_size": 10000,
              "equity": 10000.0 + i, "total_target": 0, "cash_remaining": 10000,
              "duration_s": 10} for i in range(2, 5)]
    _write_history(sandbox, rows)
    _write_snapshot(
        sandbox,
        {"saved_utc": "2026-10-05T00:00:00+00:00", "account_size": 10000,
         "base_currency": "usd", "plan": {"actions": []},
         "pnl": {"equity": 10004.0}},
    )
    html = panel.build()
    assert "start 50.0k" in html  # anchored to the real first cycle


def test_empty_state_still_renders(panel, sandbox):
    html = panel.build()
    assert "<html" in html
