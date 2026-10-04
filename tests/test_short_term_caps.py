from datetime import datetime, timezone, timedelta

from crypto_council_flow.main import (
    CryptoCouncilState,
    _apply_max_hold,
    _parse_inputs,
    _risk_levels_for,
)


def test_defaults_are_small_and_short():
    s = CryptoCouncilState()
    assert s.max_single_position_pct <= 5.0
    assert s.max_total_exposure_pct <= 30.0
    assert s.max_hold_days <= 4


def test_tighter_atr_levels():
    stop, take = _risk_levels_for(100.0, 4.0)
    assert stop is not None and take is not None
    # 0.5*4=2% stop, 1*4=4% take
    assert abs(stop - 98.0) < 1e-6
    assert abs(take - 104.0) < 1e-6


def test_max_hold_forces_close():
    now = datetime.now(timezone.utc)
    opened = (now - timedelta(days=7)).isoformat()
    opened_recent = (now - timedelta(days=2)).isoformat()
    plan = {
        "actions": [
            {"coin_id": "bitcoin", "action": "open", "target": 500.0,
             "current": 500.0, "stop_loss": 480.0, "take_profit": 520.0},
            {"coin_id": "ethereum", "action": "open", "target": 300.0,
             "current": 300.0, "stop_loss": 290.0, "take_profit": 320.0},
        ]
    }
    opened_utc = {"bitcoin": opened, "ethereum": opened_recent}
    out = _apply_max_hold(plan, opened_utc, 5, now)
    btc, eth = out["actions"]
    assert btc["action"] == "close"
    assert btc["target"] == 0
    assert "max_hold_days" in btc["notes"]
    assert eth["action"] == "open"
    assert eth["target"] == 300.0


def test_max_hold_days_cli_flag():
    parsed = _parse_inputs(["prog", "--max-hold-days", "3"])
    assert parsed["max_hold_days"] == 3
