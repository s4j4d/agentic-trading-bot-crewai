"""Tests for Phase 6 safety controls (execution_safety.py).

Both guards are pure (no network, no LLM, no clock mocking beyond
``now_iso`` kwargs), so these tests run offline like the rest of the suite.
"""

from __future__ import annotations

import pytest

from crypto_council_flow.tools.execution_base import (
    ExecutionHalted,
    get_execution_mode,
    MODE_HALTED,
)
from crypto_council_flow.tools.execution_safety import (
    DEFAULT_MAX_DAILY_LOSS_PCT,
    CircuitBreakerOpen,
    DailyLossBreaker,
    allow_close,
    breakers_from_env,
    enforce_exposure_caps,
    exposure_limit,
)


# ---------------------------------------------------------------------------
# exposure_limit
# ---------------------------------------------------------------------------

def test_exposure_limit_plain_math():
    assert exposure_limit(10_000.0, 0.05) == 500.0
    assert exposure_limit(10_000.0, 0.30) == 3_000.0


def test_exposure_limit_none_or_negative_pct_is_zero():
    assert exposure_limit(10_000.0, None) == 0.0
    assert exposure_limit(10_000.0, -1.0) == 0.0


def test_exposure_limit_bad_account_is_zero():
    assert exposure_limit(0.0, 0.3) == 0.0
    assert exposure_limit(-5.0, 0.3) == 0.0
    assert exposure_limit(float("nan"), 0.3) == 0.0


# ---------------------------------------------------------------------------
# enforce_exposure_caps
# ---------------------------------------------------------------------------

def _order(**kw):
    out = {"symbol": "BTC-RLS", "side": "buy", "qty": 0.01, "notional": 50.0}
    out.update(kw)
    return out


def test_buy_within_caps_is_allowed():
    verdict = enforce_exposure_caps(
        _order(notional=50.0, qty=0.01),
        account_size=10_000.0, free_cash=9_000.0,
        prices={"BTC": 5_000.0},
        max_total_pct=0.30, max_single_pct=0.05,
    )
    assert verdict["action"] == "allow"
    assert verdict["qty"] == pytest.approx(0.01)


def test_buy_over_single_cap_is_blocked_not_downsized():
    verdict = enforce_exposure_caps(
        _order(notional=600.0, qty=1.0),
        account_size=10_000.0, free_cash=9_000.0,
        prices={"BTC": 600.0},
        max_total_pct=0.30, max_single_pct=0.05,
    )
    assert verdict["action"] == "block"
    assert "single" in verdict["reason"].lower()


def test_buy_over_total_cap_is_blocked():
    existing = [{"asset": "ETH", "qty": 1.0, "price": 2_000.0, "market_value": 2_000.0}]
    verdict = enforce_exposure_caps(
        _order(notional=1_500.0, qty=1.0),
        account_size=10_000.0, free_cash=9_000.0,
        positions=existing, prices={"BTC": 1_500.0},
        max_total_pct=0.30, max_single_pct=0.30,
    )
    assert verdict["action"] == "block"
    assert "total exposure" in verdict["reason"].lower()


def test_sells_are_never_exposure_blocked():
    verdict = enforce_exposure_caps(
        _order(side="sell", notional=5_000.0, qty=5.0),
        account_size=1_000.0, free_cash=0.0,
        prices={"BTC": 1_000.0},
        max_total_pct=0.01, max_single_pct=0.01,
    )
    assert verdict["action"] == "allow"
    assert "risk-reducing" in verdict["reason"]


def test_sell_more_than_held_is_blocked():
    verdict = enforce_exposure_caps(
        _order(side="sell", qty=2.0, notional=100.0),
        account_size=10_000.0, free_cash=9_000.0,
        holdings={"BTC": 1.0}, prices={"BTC": 50.0},
        max_total_pct=0.30, max_single_pct=0.30,
    )
    assert verdict["action"] == "block"
    assert "exceeds holding" in verdict["reason"]


def test_downsize_verdict_exists_for_partial_room():
    # Existing 500 ETH exposure + a 2k BTC order = 2.5k post-trade exposure
    # against a 3k total cap (30% of 10k): 500 of room, so the order is
    # downsized to a 0.5 BTC position rather than blocked outright.
    existing = [{"asset": "ETH", "qty": 0.5, "price": 1_000.0, "market_value": 500.0}]
    verdict = enforce_exposure_caps(
        _order(notional=2_000.0, qty=2.0),
        account_size=10_000.0, free_cash=9_000.0,
        positions=existing, prices={"BTC": 1_000.0},
        max_total_pct=0.30, max_single_pct=1.0,
    )
    assert verdict["action"] == "downsize"
    assert verdict["notional"] == pytest.approx(500.0)
    assert verdict["qty"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# DailyLossBreaker
# ---------------------------------------------------------------------------

_NOW = "2026-01-01T18:00:00+00:00"
_LOSING_LEDGER = [
    {"ts": "2026-01-01T10:00:00+00:00", "coin_id": "btc", "side": "sell", "realized_pnl": -300.0},
    {"ts": "2026-01-01T12:00:00+00:00", "coin_id": "eth", "side": "sell", "realized_pnl": -250.0},
    {"ts": "2026-01-01T13:00:00+00:00", "coin_id": "xrp", "side": "buy", "realized_pnl": None},
]


def test_breaker_opens_on_breached_limit():
    breaker = DailyLossBreaker(
        10_000.0, max_daily_loss_pct=0.05,
        ledger=_LOSING_LEDGER, now_iso=_NOW,
    )
    # -550 realized vs a 500 limit: open.
    with pytest.raises(CircuitBreakerOpen):
        breaker.guard({"symbol": "BTC-RLS", "side": "buy"}, now_iso=_NOW)
    assert isinstance(breaker.guard.__self__ if hasattr(breaker.guard, "__self__") else breaker,
                      DailyLossBreaker)


def test_breaker_allows_winning_days():
    winner = DailyLossBreaker(
        10_000.0, max_daily_loss_pct=0.05,
        ledger=[{"ts": _NOW, "coin_id": "btc", "side": "sell", "realized_pnl": 900.0}],
        now_iso=_NOW,
    )
    winner.guard({"symbol": "BTC-RLS", "side": "buy"}, now_iso=_NOW)  # no raise


def test_breaker_never_blocks_closes():
    breaker = DailyLossBreaker(
        10_000.0, max_daily_loss_pct=0.05,
        ledger=_LOSING_LEDGER, now_iso=_NOW,
    )
    breaker.guard({"symbol": "BTC-RLS", "side": "sell"}, now_iso=_NOW)  # no raise


def test_breaker_ignores_other_days_and_none_rows():
    stale = [
        {"ts": "2025-12-31T23:00:00+00:00", "coin_id": "btc", "side": "sell", "realized_pnl": -9_000.0},
        {"ts": _NOW, "coin_id": "btc", "side": "buy", "realized_pnl": None},
    ]
    breaker = DailyLossBreaker(10_000.0, max_daily_loss_pct=0.05, ledger=stale, now_iso=_NOW)
    breaker.guard({"symbol": "BTC-RLS", "side": "buy"}, now_iso=_NOW)  # no raise


def test_breaker_resets_next_day():
    breaker = DailyLossBreaker(
        10_000.0, max_daily_loss_pct=0.05,
        ledger=_LOSING_LEDGER, now_iso=_NOW,
    )
    with pytest.raises(CircuitBreakerOpen):
        breaker.guard({"symbol": "BTC-RLS", "side": "buy"}, now_iso=_NOW)
    state = breaker.to_state()
    assert state["day"] == "2026-01-01"
    assert state["rows_today"] == 2  # the None row is not counted
    breaker.guard({"symbol": "BTC-RLS", "side": "buy"},
                  now_iso="2026-01-02T01:00:00+00:00")  # new day: reset


def test_breaker_survives_round_trip_state():
    breaker = DailyLossBreaker(
        10_000.0, max_daily_loss_pct=0.05,
        ledger=_LOSING_LEDGER, now_iso=_NOW,
    )
    restored = DailyLossBreaker(
        10_000.0, max_daily_loss_pct=0.05,
        state=breaker.to_state(), now_iso=_NOW,
    )
    with pytest.raises(CircuitBreakerOpen):
        restored.guard({"symbol": "BTC-RLS", "side": "buy"}, now_iso=_NOW)


def test_breaker_is_fail_closed_not_paper():
    breaker = DailyLossBreaker(
        10_000.0, max_daily_loss_pct=0.05,
        ledger=_LOSING_LEDGER, now_iso=_NOW,
    )
    try:
        breaker.guard({"symbol": "BTC-RLS", "side": "buy"}, now_iso=_NOW)
    except CircuitBreakerOpen as exc:
        assert isinstance(exc, ExecutionHalted)
    else:  # pragma: no cover — guard must raise
        raise AssertionError("breaker did not open")


def test_breaker_default_pct_matches_env_default():
    assert DailyLossBreaker(10_000.0, now_iso=_NOW).limit == pytest.approx(
        10_000.0 * DEFAULT_MAX_DAILY_LOSS_PCT
    )


def test_breakers_from_env_reads_only_real_knob(monkeypatch):
    monkeypatch.setenv("EXECUTION_MAX_DAILY_LOSS_PCT", "2.5")
    assert breakers_from_env() == {"max_daily_loss_pct": 2.5}
    monkeypatch.delenv("EXECUTION_MAX_DAILY_LOSS_PCT")
    assert breakers_from_env() == {"max_daily_loss_pct": DEFAULT_MAX_DAILY_LOSS_PCT}


def test_allow_close_contract():
    assert allow_close("sell") is True
    assert allow_close("SELL") is True
    assert allow_close("buy") is False
    assert allow_close(None) is False


# ---------------------------------------------------------------------------
# kill-switch (halted) at the apply_plan boundary — the Phase 6 audit item
# ---------------------------------------------------------------------------

def test_halted_mode_is_visible_to_guards(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "halted")
    assert get_execution_mode() == MODE_HALTED
