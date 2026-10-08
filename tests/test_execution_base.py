"""Tests for the abstract execution interface (tools/execution_base.py).

Slices:
  1. DTO validation + alias normalisation (OrderRequest, OrderResult, Fill, Balance).
  2. ExecutionClient is genuinely abstract.
  3. Factory selection: mode/env resolution, kill switch, registry, venue swap.

No network and no LLM — pure interface contract.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Slice 1: DTOs
# ---------------------------------------------------------------------------

class TestOrderRequest:
    def test_requires_qty_or_notional(self):
        from crypto_council_flow.tools.execution_base import OrderRequest

        with pytest.raises(ValueError):
            OrderRequest(symbol="BTCUSDT", side="buy", type="market")

    def test_rejects_both_qty_and_notional(self):
        from crypto_council_flow.tools.execution_base import OrderRequest

        with pytest.raises(ValueError):
            OrderRequest(symbol="BTCUSDT", side="buy", type="market", qty=1.0, notional=100.0)

    def test_market_with_notional(self):
        from crypto_council_flow.tools.execution_base import OrderRequest

        req = OrderRequest(symbol="btcusdt", side="BUY", type="market", notional=100.0)
        assert req.symbol == "BTCUSDT"
        assert req.side == "buy"
        assert req.type == "market"
        assert req.notional == 100.0

    def test_limit_requires_price(self):
        from crypto_council_flow.tools.execution_base import OrderRequest

        with pytest.raises(ValueError):
            OrderRequest(symbol="BTCUSDT", side="buy", type="limit", qty=1.0)
        ok = OrderRequest(
            symbol="BTCUSDT", side="buy", type="limit", qty=1.0, limit_price=50000.0
        )
        assert ok.limit_price == 50000.0

    def test_post_only_only_for_limit(self):
        from crypto_council_flow.tools.execution_base import OrderRequest

        with pytest.raises(ValueError):
            OrderRequest(
                symbol="BTCUSDT", side="buy", type="market", notional=10.0, post_only=True
            )

    def test_rejects_bad_side_and_type(self):
        from crypto_council_flow.tools.execution_base import OrderRequest

        with pytest.raises(ValueError):
            OrderRequest(symbol="BTCUSDT", side="hodl", type="market", notional=1.0)
        with pytest.raises(ValueError):
            OrderRequest(symbol="BTCUSDT", side="buy", type="stop", notional=1.0)

    def test_alias_convergence(self):
        from crypto_council_flow.tools.execution_base import OrderRequest

        req = OrderRequest(
            symbol="ethusdt",
            direction="long",
            ordertype="limit_order",
            cost=250.0,
            clientOrderId="c-1",
            tif="ioc",
            limit_price=1000.0,
        )
        assert req.side == "buy"
        assert req.type == "limit"
        assert req.notional == 250.0
        assert req.client_order_id == "c-1"
        assert req.time_in_force == "IOC"


class TestOrderResult:
    def test_ccxt_aliases(self):
        from crypto_council_flow.tools.execution_base import OrderResult

        res = OrderResult(
            id="42",
            clientOrderId="c-9",
            symbol="btcusdt",
            filled=0.5,
            remaining=0.5,
            average=49000.0,
            price=49000.0,
            status="PARTIALLY_FILLED",
        )
        assert res.order_id == "42"
        assert res.client_order_id == "c-9"
        assert res.filled_qty == 0.5
        assert res.remaining_qty == 0.5
        assert res.avg_fill_price == 49000.0
        assert res.status == "partially_filled"
        assert res.is_final is False

    def test_is_final_states(self):
        from crypto_council_flow.tools.execution_base import OrderResult

        for status in ("filled", "canceled", "rejected", "expired"):
            assert OrderResult(status=status).is_final is True
        for status in ("new", "open", "partially_filled"):
            assert OrderResult(status=status).is_final is False


class TestBalanceAndFill:
    def test_balance_total_derived(self):
        from crypto_council_flow.tools.execution_base import Balance

        b = Balance(asset="btc", free=1.5, locked=0.5)
        assert b.asset == "BTC"
        assert b.total == 2.0

    def test_balance_explicit_total_respected(self):
        from crypto_council_flow.tools.execution_base import Balance

        b = Balance(asset="usdt", free=1.0, locked=1.0, total=10.0)
        assert b.total == 10.0

    def test_fill_cost_derived_and_aliases(self):
        from crypto_council_flow.tools.execution_base import Fill

        f = Fill(id="t-1", order="o-1", symbol="ethusdt", side="SELL", qty=2.0, price=100.0)
        assert f.trade_id == "t-1"
        assert f.order_id == "o-1"
        assert f.side == "sell"
        assert f.symbol == "ETHUSDT"
        assert f.cost == 200.0


# ---------------------------------------------------------------------------
# Slice 2: abstractness
# ---------------------------------------------------------------------------

class TestAbstractness:
    def test_cannot_instantiate_base(self):
        from crypto_council_flow.tools.execution_base import ExecutionClient

        with pytest.raises(TypeError):
            ExecutionClient()  # type: ignore[abstract]

    def test_subclass_must_implement_all_methods(self):
        from crypto_council_flow.tools.execution_base import ExecutionClient

        class Half(ExecutionClient):
            name = "half"

            def get_balances(self, assets=None):
                return []

            # deliberately missing the rest

        with pytest.raises(TypeError):
            Half()  # type: ignore[abstract]

    def test_default_market_constraints_empty(self):
        from crypto_council_flow.tools.execution_base import ExecutionClient

        class Full(ExecutionClient):
            name = "full"

            def get_balances(self, assets=None):
                return []

            def place_order(self, request):
                raise NotImplementedError

            def cancel_order(self, symbol, order_id):
                raise NotImplementedError

            def get_order(self, symbol, order_id):
                return None

            def get_open_orders(self, symbol=None):
                return []

            def get_fills(self, since=None, symbol=None):
                return []

        c = Full(quote_currency="RLS")
        assert c.quote_currency == "rls"
        assert c.get_market_constraints("BTCIRT") == {}
        assert "full" in c.describe()
        c.close()  # no-op, must not raise


# ---------------------------------------------------------------------------
# Slice 3: factory + registry
# ---------------------------------------------------------------------------

def _make_factory(tag):
    from crypto_council_flow.tools.execution_base import ExecutionClient

    class _C(ExecutionClient):
        name = tag

        def get_balances(self, assets=None):
            return []

        def place_order(self, request):
            raise NotImplementedError

        def cancel_order(self, symbol, order_id):
            raise NotImplementedError

        def get_order(self, symbol, order_id):
            return None

        def get_open_orders(self, symbol=None):
            return []

        def get_fills(self, since=None, symbol=None):
            return []

    return _C


class TestFactory:
    def test_default_mode_is_paper(self, monkeypatch):
        from crypto_council_flow.tools import execution_base as eb

        monkeypatch.delenv("EXECUTION_MODE", raising=False)
        assert eb.get_execution_mode() == "paper"

    def test_halted_kill_switch(self, monkeypatch):
        from crypto_council_flow.tools import execution_base as eb

        monkeypatch.setenv("EXECUTION_MODE", "halted")
        with pytest.raises(eb.ExecutionHalted):
            eb.get_execution_client()

    def test_unregistered_mode_raises(self, monkeypatch):
        from crypto_council_flow.tools import execution_base as eb

        monkeypatch.setenv("EXECUTION_MODE", "live")
        monkeypatch.setenv("EXECUTION_EXCHANGE", "nonexistent-venue")
        monkeypatch.setattr(eb, "_REGISTRY", {})
        with pytest.raises(eb.ExecutionError):
            eb.get_execution_client()

    def test_registered_paper_client_returned(self, monkeypatch):
        from crypto_council_flow.tools import execution_base as eb

        monkeypatch.setenv("EXECUTION_MODE", "paper")
        monkeypatch.setattr(eb, "_REGISTRY", {})
        monkeypatch.setattr(eb, "_load_builtin_clients", lambda: None)
        eb.register_execution_client("paper", _make_factory("paper-lite"))
        client = eb.get_execution_client()
        assert client.name == "paper-lite"
        assert "paper" in eb.registered_clients()

    def test_live_venue_selected_by_env(self, monkeypatch):
        from crypto_council_flow.tools import execution_base as eb

        monkeypatch.setattr(eb, "_REGISTRY", {})
        monkeypatch.setattr(eb, "_load_builtin_clients", lambda: None)
        eb.register_execution_client("venue-a", _make_factory("venue-a"))
        eb.register_execution_client("venue-b", _make_factory("venue-b"))

        monkeypatch.setenv("EXECUTION_MODE", "live")
        monkeypatch.setenv("EXECUTION_EXCHANGE", "venue-b")
        assert eb.get_execution_client().name == "venue-b"

        monkeypatch.setenv("EXECUTION_EXCHANGE", "venue-a")
        assert eb.get_execution_client().name == "venue-a"

    def test_live_falls_back_to_exchange_var(self, monkeypatch):
        from crypto_council_flow.tools import execution_base as eb

        monkeypatch.setattr(eb, "_REGISTRY", {})
        monkeypatch.setattr(eb, "_load_builtin_clients", lambda: None)
        eb.register_execution_client("legacy", _make_factory("legacy"))

        monkeypatch.setenv("EXECUTION_MODE", "live")
        monkeypatch.delenv("EXECUTION_EXCHANGE", raising=False)
        monkeypatch.setenv("EXCHANGE", "legacy")
        assert eb.get_execution_client().name == "legacy"

    def test_module_is_importable_without_concrete_clients(self, monkeypatch):
        """The interface must not depend on any concrete client existing."""
        from crypto_council_flow.tools import execution_base as eb

        assert isinstance(eb.registered_clients(), list)
