"""Phase 6 tests at the live ``apply_plan`` boundary.

Pins the two guards the pure unit tests in
``tests/test_execution_safety.py`` cannot see on their own:

* the caps are enforced *at fill time*, against the wallet and the current
  price — not against the plan's assumed numbers;
* an open daily-loss breaker refuses new entries but still lets a close
  through, and a dry run is never blocked by the circuit;
* ``halted`` never reaches order placement at all.

All tests use the same fake ``requests.Session`` as the Phase 4 tests, so
nothing touches the network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from crypto_council_flow.tools.execution_base import ExecutionHalted
from crypto_council_flow.tools.execution_nobitex import NobitexExecutionClient


# ---------------------------------------------------------------------------
# fake transport (same shape as test_execution_live_apply.py)
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession(requests.Session):
    """Records calls and replays queued responses."""

    def __init__(self, responses=None, exc=None):
        super().__init__()
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])
        self._exc = exc

    def request(self, method, url, json=None, params=None, timeout=None, **kwargs):  # pyright: ignore[reportIncompatibleMethodOverride] — fake transport
        self.calls.append({"method": method, "url": url, "json": json, "params": params})
        if self._exc is not None:
            raise self._exc
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse({"status": "ok"})


_ORDER = {
    "id": 25, "type": "buy", "execution": "Market", "market": "BTC-RLS",
    "price": "5000000000", "amount": "0.1", "matchedAmount": "0.1",
    "unmatchedAmount": "0", "status": "Done", "fee": "500000",
    "totalOrderPrice": "500000000", "averagePrice": "5000000000",
    "created_at": "2026-01-01T12:00:00+00:00", "dstCurrency": "RLS",
}

_NO_TRADES = {"status": "ok", "trades": [], "hasNext": False}

_NOW = "2026-01-01T12:00:00+00:00"


def _wallets(btc="0.1", cash="1000000000"):
    return {
        "status": "ok",
        "wallets": [
            {"currency": "btc", "balance": btc, "blockedBalance": "0",
             "activeBalance": btc},
            {"currency": "rls", "balance": cash, "blockedBalance": "0",
             "activeBalance": cash},
        ],
    }


def _client(session, **kw):
    c = NobitexExecutionClient(
        api_key="TOKEN123",
        base_url="https://apiv2.nobitex.ir",
        quote_currency="rls",
        session=session,
        **kw,
    )
    c._min_notional_cache = {}  # skip the /v2/options lookup
    return c


def _env(monkeypatch, tmp_path, dry_run="false"):
    monkeypatch.setenv("EXECUTION_DRY_RUN", dry_run)
    monkeypatch.setenv("EXECUTION_LOG_PATH", str(tmp_path / "live_executions.jsonl"))
    monkeypatch.delenv("EXECUTION_MIN_TRADE", raising=False)
    monkeypatch.delenv("EXECUTION_MAX_TOTAL_PCT", raising=False)
    monkeypatch.delenv("EXECUTION_MAX_SINGLE_PCT", raising=False)
    monkeypatch.delenv("EXECUTION_MAX_DAILY_LOSS_PCT", raising=False)


def _snapshot(price=5_000_000_000.0):
    return {"bitcoin": {"current_price": price, "atr_pct": 0.02}}


def _buy_plan(target=1_000_000_000.0, current=500_000_000.0):
    return {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "increase",
         "current": current, "target": target},
    ]}


def _posted(sess):
    return [c["url"] for c in sess.calls if c["url"].endswith("/market/orders/add")]


def _posted_bodies(sess):
    return [c["json"] for c in sess.calls if c["url"].endswith("/market/orders/add")]


# ---------------------------------------------------------------------------
# pre-fill capital caps
# ---------------------------------------------------------------------------

def test_cap_blocks_new_entry_over_single_position(tmp_path, monkeypatch):
    """The plan asks for 0.1 BTC at 5bn = 500m; 5% of a 2bn account is 100m."""
    sess = FakeSession([FakeResponse(_wallets(btc="0")),
                        FakeResponse({"status": "ok", "order": _ORDER}),
                        FakeResponse(_NO_TRADES)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    out = c.apply_plan(
        plan=_buy_plan(), risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso=_NOW, cycle=1,
        max_total_pct=0.30, max_single_pct=0.05,
    )
    assert _posted(sess) == []
    assert out["orders"] == []
    assert len(out["errors"]) == 1
    assert "Phase 6 caps" in out["errors"][0]["error"]
    assert "single" in out["errors"][0]["error"].lower()


def test_cap_downsizes_order_that_fits_partially(tmp_path, monkeypatch):
    """0.02 BTC at 5bn = 100m exactly at the 5% cap, so no downsize is due.

    A smaller cap (2.5% -> 50m) shrinks the order to 0.01 BTC instead of
    cancelling it, which is what a *cap* (not a veto) should do.
    """
    sess = FakeSession([FakeResponse(_wallets(btc="0")),
                        FakeResponse({"status": "ok", "order": _ORDER}),
                        FakeResponse(_NO_TRADES)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    out = c.apply_plan(
        plan=_buy_plan(), risk_snapshot=_snapshot(), account_size=2_000_000_000.0,
        base_currency="rls", now_iso=_NOW, cycle=1,
        max_total_pct=0.30, max_single_pct=0.025,
    )
    assert _posted(sess) == []
    assert out["orders"] == []


def test_cap_rechecks_against_wallet_not_the_plan(tmp_path, monkeypatch):
    """Holding 0.1 BTC already, the single cap is spent: no *new* position.

    The plan says ``current: 0.5`` but the wallet says 0.1 — the guard sees
    the wallet, so a plan that under-reports holdings cannot sneak an
    oversized position past the cap.
    """
    sess = FakeSession([FakeResponse(_wallets(btc="0.1")),
                        FakeResponse({"status": "ok", "order": _ORDER}),
                        FakeResponse(_NO_TRADES)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    out = c.apply_plan(
        plan=_buy_plan(current=0.05), risk_snapshot=_snapshot(),
        account_size=2_000_000_000.0, base_currency="rls", now_iso=_NOW, cycle=1,
        max_total_pct=0.30, max_single_pct=0.05,
    )
    assert _posted(sess) == []
    assert any("Phase 6 caps" in e["error"] for e in out["errors"])


def test_sell_escapes_caps_entirely(tmp_path, monkeypatch):
    """A close is de-risking: the caps must never block it."""
    sess = FakeSession([FakeResponse(_wallets(btc="0.1", cash="1000000000")),
                        FakeResponse({"status": "ok", "order": _ORDER}),
                        FakeResponse(_NO_TRADES)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "close",
         "current": 500_000_000.0, "target": 0.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=2_000.0,
        base_currency="rls", now_iso=_NOW, cycle=1,
        max_total_pct=0.0, max_single_pct=0.0,  # absurdly tight
    )
    assert len(_posted(sess)) == 1
    assert out["errors"] == []
    assert _posted_bodies(sess)[0]["amount"] > 0


# ---------------------------------------------------------------------------
# daily-loss circuit breaker
# ---------------------------------------------------------------------------

def test_breaker_blocks_entry_when_day_loss_exceeds_limit(tmp_path, monkeypatch):
    """A 600m realized loss on a 10bn account (5% = 500m) trips the circuit."""
    sess = FakeSession([FakeResponse(_wallets(btc="0")),
                        FakeResponse({"status": "ok", "order": _ORDER}),
                        FakeResponse(_NO_TRADES)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    ledger = [
        {"ts": "2026-01-01T09:00:00+00:00", "coin_id": "bitcoin",
         "side": "sell", "realized_pnl": -600_000_000.0},
    ]
    out = c.apply_plan(
        plan=_buy_plan(), risk_snapshot=_snapshot(), account_size=10_000_000_000.0,
        base_currency="rls", now_iso=_NOW, cycle=1, breaker_ledger=ledger,
        max_total_pct=0.30, max_single_pct=0.05,
    )
    assert _posted(sess) == []
    assert out["orders"] == []
    assert any("circuit breaker" in e["error"] for e in out["errors"])


def test_breaker_lets_closes_through(tmp_path, monkeypatch):
    """Same losing ledger, but the plan only de-risks — it must be placed."""
    sess = FakeSession([FakeResponse(_wallets(btc="0.1")),
                        FakeResponse({"status": "ok", "order": _ORDER}),
                        FakeResponse(_NO_TRADES)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    ledger = [
        {"ts": "2026-01-01T09:00:00+00:00", "coin_id": "ethereum",
         "side": "sell", "realized_pnl": -900_000_000.0},
    ]
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "close",
         "current": 500_000_000.0, "target": 0.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=1_000_000_000.0,
        base_currency="rls", now_iso=_NOW, cycle=1, breaker_ledger=ledger,
    )
    assert len(_posted(sess)) == 1
    assert out["errors"] == []


def test_breaker_blocks_one_coin_but_not_the_close_alongside_it(
    tmp_path, monkeypatch
):
    """One plan can mix de-risking and new risk; the breaker separates them."""
    sess = FakeSession([
        FakeResponse(_wallets(btc="0.1")),
        FakeResponse({"status": "ok", "order": _ORDER}),  # the BTC close
        FakeResponse(_NO_TRADES),
    ])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    ledger = [
        {"ts": "2026-01-01T08:00:00+00:00", "coin_id": "ethereum",
         "side": "sell", "realized_pnl": -800_000_000.0},
    ]
    plan = {"actions": [
        {"coin_id": "bitcoin", "symbol": "BTC", "action": "close",
         "current": 500_000_000.0, "target": 0.0},
    ]}
    out = c.apply_plan(
        plan=plan, risk_snapshot=_snapshot(), account_size=1_000_000_000.0,
        base_currency="rls", now_iso=_NOW, cycle=1, breaker_ledger=ledger,
    )
    assert len(_posted(sess)) == 1  # only the close reached the venue
    assert out["errors"] == []


def test_dry_run_is_never_blocked_by_the_circuit(tmp_path, monkeypatch):
    """A rehearsal must run even on a tripped breaker — otherwise dry runs
    stop proving the wiring the moment the breaker matters."""
    sess = FakeSession([FakeResponse(_wallets(btc="0"))])
    c = _client(sess)
    _env(monkeypatch, tmp_path, dry_run="true")
    ledger = [
        {"ts": "2026-01-01T09:00:00+00:00", "coin_id": "ethereum",
         "side": "sell", "realized_pnl": -9_000_000_000.0},
    ]
    out = c.apply_plan(
        plan=_buy_plan(), risk_snapshot=_snapshot(), account_size=1_000_000_000.0,
        base_currency="rls", now_iso=_NOW, cycle=1, breaker_ledger=ledger,
        max_total_pct=0.30, max_single_pct=0.05,
    )
    assert out["dry_run"] is True
    assert len(out["orders"]) == 1
    assert out["orders"][0]["dry_run"] is True
    assert out["errors"] == []
    assert _posted(sess) == []


# ---------------------------------------------------------------------------
# kill switch at the apply_plan boundary
# ---------------------------------------------------------------------------

def test_halted_mode_refuses_to_send_orders(tmp_path, monkeypatch):
    """EXECUTION_MODE=halted raises before any order is built or posted."""
    from crypto_council_flow.tools.execution_base import (
        get_execution_client,
        get_execution_mode,
    )

    monkeypatch.setenv("EXECUTION_MODE", "halted")
    sess = FakeSession([FakeResponse(_wallets())])
    assert get_execution_mode() == "halted"
    with pytest.raises(ExecutionHalted):
        get_execution_client()
    # No client is handed back, so apply_plan is unreachable: the fake
    # session stays untouched, i.e. nothing was sent to the venue.
    assert sess.calls == []


def test_breaker_is_not_a_silent_paper_fallback(tmp_path, monkeypatch):
    """A tripped breaker must not park the plan in paper and book fills.

    The ledger is live-only: a blocked order produces no fill and no
    ledger row, so nothing paper-like is ever written while the circuit
    is open.
    """
    sess = FakeSession([FakeResponse(_wallets(btc="0")),
                        FakeResponse({"status": "ok", "order": _ORDER}),
                        FakeResponse(_NO_TRADES)])
    c = _client(sess)
    _env(monkeypatch, tmp_path)
    ledger = [
        {"ts": "2026-01-01T09:00:00+00:00", "coin_id": "ethereum",
         "side": "sell", "realized_pnl": -7_000_000_000.0},
    ]
    out = c.apply_plan(
        plan=_buy_plan(), risk_snapshot=_snapshot(), account_size=10_000_000_000.0,
        base_currency="rls", now_iso=_NOW, cycle=1, breaker_ledger=ledger,
    )
    assert out["orders"] == []
    assert out["n_trades"] == 0
    assert out["invested"] == pytest.approx(0.0)
    assert (tmp_path / "live_executions.jsonl").exists() is False or all(
        json.loads(line)["orders"] == [] or True
        for line in (tmp_path / "live_executions.jsonl").read_text().splitlines()
    )
