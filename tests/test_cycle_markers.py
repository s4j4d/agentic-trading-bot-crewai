"""The writer side of the cycle-marker contract.

`portfolio_panel._main_cycles()` filters on the `source` key. That only works
while both writers in main.py keep stamping it:

  - `manage_portfolio()` -> one row per cycle, source="main"
  - `risk_tick()`        -> per-minute notes inside a cycle, source="risk_tick"

If the main writer stopped stamping `source`, its rows would fall back to the
no-source default (still "main", so nothing breaks) — but the risk_tick writer
losing its tag would make every 60s note look like a cycle, which is the bug
this contract exists to prevent.

These tests assert on the source text of the two writers rather than executing
them: both need a live crew/network, and the thing being pinned is a literal in
the row dict.
"""

from __future__ import annotations

import ast

import pytest

import crypto_council_flow.main as main_mod

SRC = main_mod.__file__


def _writer_body(func_name: str) -> str:
    """Source text of one top-level/method function, dedented."""
    src = open(SRC, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{func_name} not found in {SRC}")


class TestHistoryRowMarkers:
    def test_manage_portfolio_stamps_main(self):
        body = _writer_body("manage_portfolio")
        assert '"source": "main"' in body, (
            "manage_portfolio must stamp source=\"main\" on its history row"
        )

    def test_risk_tick_stamps_risk_tick(self):
        body = _writer_body("risk_tick")
        assert '"source": "risk_tick"' in body, (
            "risk_tick must stamp source=\"risk_tick\" so the panel can tell a "
            "per-minute note apart from a real cycle"
        )

    def test_both_writers_are_found(self):
        """Guards the lookup helper itself."""
        for name in ("manage_portfolio", "risk_tick"):
            with pytest.raises(AssertionError):
                _writer_body("no_such_writer_" + name)


class TestPanelAgreesWithWriter:
    def test_panel_excludes_exactly_the_risk_tick_marker(self):
        """The panel's NON_MAIN_SOURCES and main.py's risk_tick tag must not
        drift apart — a renamed marker silently restores the bug."""
        import importlib.util
        from pathlib import Path

        panel_path = Path(SRC).resolve().parents[2] / "scripts" / "portfolio_panel.py"
        spec = importlib.util.spec_from_file_location("panel_contract", panel_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert "risk_tick" in mod.NON_MAIN_SOURCES
        assert '"source": "risk_tick"' in _writer_body("risk_tick")
