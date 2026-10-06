"""RiskLevelsTool — deterministic stop-loss / take-profit math (no network, no LLM).

RED tracer 1: long levels are 1x ATR stop + 2:1 take-profit.
"""

from __future__ import annotations


class TestRiskLevelsTool:
    def test_long_stop_is_1x_atr_and_take_is_2_to_1(self):
        import json

        from crypto_council_flow.tools.portfolio_tools import RiskLevelsTool

        out = RiskLevelsTool()._run(
            coin_id="solana",
            current_price=100.0,
            atr_pct=2.5,
        )
        data = json.loads(out)
        assert data["stop_loss"] == 98.75
        assert data["take_profit"] == 102.5


class TestRiskLevelsFor:
    """Deterministic backfill helper in main.py (pure math, no network)."""

    def test_standard_levels(self):
        from crypto_council_flow.main import _risk_levels_for

        assert _risk_levels_for(100.0, 2.5) == (98.75, 102.5)

    def test_tiny_atr_floored_at_1pct(self):
        from crypto_council_flow.main import _risk_levels_for

        assert _risk_levels_for(200.0, 0.2) == (199.0, 202.0)

    def test_invalid_returns_none(self):
        from crypto_council_flow.main import _risk_levels_for

        assert _risk_levels_for(0.0, 2.5) == (None, None)
        assert _risk_levels_for(100.0, 0.0) == (None, None)


class TestAtrSnapshotFor:
    """Retry-then-None: cached OHLC -> (price, atr_pct), failure -> (None, None)."""

    def test_snapshot_from_candles(self, monkeypatch):
        import crypto_council_flow.main as main_mod

        # 20 flat candles: close=100, high=101, low=99 -> TR=2, ATR%=2.0
        candles = [[i * 86400000, 100.0, 101.0, 99.0, 100.0, 0.0] for i in range(20)]
        monkeypatch.setattr(main_mod, "_fetch_ohlcv", lambda *a, **k: candles)

        price, atr = main_mod._atr_snapshot_for("solana")
        assert price == 100.0
        assert atr == 2.0

    def test_snapshot_none_when_no_candles(self, monkeypatch):
        import crypto_council_flow.main as main_mod

        monkeypatch.setattr(main_mod, "_fetch_ohlcv", lambda *a, **k: [])
        assert main_mod._atr_snapshot_for("solana") == (None, None)

    def test_snapshot_none_on_fetch_error(self, monkeypatch):
        import crypto_council_flow.main as main_mod

        def _boom(*a, **k):
            raise RuntimeError("429")
        monkeypatch.setattr(main_mod, "_fetch_ohlcv", _boom)
        assert main_mod._atr_snapshot_for("solana") == (None, None)


class TestBackfillRiskLevels:
    """Deterministic backfill with crew-level validation:
    sane + within 20% of computed -> kept; otherwise computed."""

    def test_fills_missing_levels_from_snapshot(self):
        from crypto_council_flow.main import _backfill_risk_levels

        plan = {"actions": [
            {"coin_id": "solana", "symbol": "SOL", "action": "open",
             "current": 0.0, "target": 1500.0, "reason": "Top.",
             "stop_loss": None, "take_profit": None},
            {"coin_id": "bitcoin", "symbol": "BTC", "action": "close",
             "current": 500.0, "target": 0.0, "reason": "Stale.",
             "stop_loss": None, "take_profit": None},
        ]}
        snap = {"solana": {"current_price": 100.0, "atr_pct": 2.5},
                "bitcoin": {"current_price": 200.0, "atr_pct": 0.2}}
        out = _backfill_risk_levels(plan, snap)
        by_coin = {a["coin_id"]: a for a in out["actions"]}
        assert (by_coin["solana"]["stop_loss"], by_coin["solana"]["take_profit"]) == (98.75, 102.5)
        # close gets identical informational levels, target stays 0
        assert (by_coin["bitcoin"]["stop_loss"], by_coin["bitcoin"]["take_profit"]) == (199.0, 202.0)
        assert by_coin["bitcoin"]["target"] == 0.0

    def test_keeps_crew_levels_within_20pct(self, monkeypatch):
        from crypto_council_flow.main import _backfill_risk_levels

        # Pin the tolerance this test is about: the code reads
        # COUNCIL_RISK_LEVEL_TOLERANCE_PCT from the operator's live .env, so an
        # unpatched run silently retargets itself at whatever tolerance the
        # local config happens to carry.
        monkeypatch.setenv("COUNCIL_RISK_LEVEL_TOLERANCE_PCT", "20")
        # atr 2.5% -> computed stop 98.75, take 102.5; crew values inside 20%
        plan = {"actions": [
            {"coin_id": "solana", "symbol": "SOL", "action": "open",
             "current": 0.0, "target": 1500.0, "reason": "Top.",
             "stop_loss": 98.0, "take_profit": 104.0},
        ]}
        snap = {"solana": {"current_price": 100.0, "atr_pct": 2.5}}
        out = _backfill_risk_levels(plan, snap)
        a = out["actions"][0]
        assert (a["stop_loss"], a["take_profit"]) == (98.0, 104.0)

    def test_rejects_insane_and_replaces_with_computed(self):
        from crypto_council_flow.main import _backfill_risk_levels

        # stop above entry, take below entry -> insane -> computed wins
        plan = {"actions": [
            {"coin_id": "solana", "symbol": "SOL", "action": "open",
             "current": 0.0, "target": 1500.0, "reason": "Top.",
             "stop_loss": 110.0, "take_profit": 90.0},
        ]}
        snap = {"solana": {"current_price": 100.0, "atr_pct": 2.5}}
        out = _backfill_risk_levels(plan, snap)
        a = out["actions"][0]
        assert (a["stop_loss"], a["take_profit"]) == (98.75, 102.5)

    def test_rejects_far_from_computed_and_replaces(self, monkeypatch):
        from crypto_council_flow.main import _backfill_risk_levels

        # Pin the tolerance: at 30% these crew values are inside the window and
        # would be kept, so this test only means what it says at 20%.
        monkeypatch.setenv("COUNCIL_RISK_LEVEL_TOLERANCE_PCT", "20")

        # sane side but far beyond 20% tolerance -> computed wins
        plan = {"actions": [
            {"coin_id": "solana", "symbol": "SOL", "action": "open",
             "current": 0.0, "target": 1500.0, "reason": "Top.",
             "stop_loss": 80.0, "take_profit": 130.0},
        ]}
        snap = {"solana": {"current_price": 100.0, "atr_pct": 2.5}}
        out = _backfill_risk_levels(plan, snap)
        a = out["actions"][0]
        assert (a["stop_loss"], a["take_profit"]) == (98.75, 102.5)

    def test_none_without_snapshot(self):
        from crypto_council_flow.main import _backfill_risk_levels

        plan = {"actions": [
            {"coin_id": "ghost", "symbol": "GHO", "action": "open",
             "current": 0.0, "target": 500.0, "reason": "New.",
             "stop_loss": None, "take_profit": None},
        ]}
        out = _backfill_risk_levels(plan, {})
        assert out["actions"][0]["stop_loss"] is None
        assert out["actions"][0]["take_profit"] is None
