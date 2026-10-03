"""Tests for the portfolio manager: paper positions, exposure caps, rebalance.

Slices:
  1. PortfolioExposureTool — deterministic exposure math (no network, no LLM).
  2. RebalanceAllocatorTool — cap-aware target allocation.
  3. PortfolioPlan DTO — single canonical shape, alias normalisation.
  4. CouncilPortfolioCrew wiring (agents.jsonc, task yaml, crew instantiation).
  5. Flow manage_portfolio step (state transitions with stubbed crew).
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Slice 1: PortfolioExposureTool
# ---------------------------------------------------------------------------

class TestPortfolioExposureTool:
    def test_empty_portfolio_is_zero_exposure(self):
        from crypto_council_flow.tools.portfolio_tools import PortfolioExposureTool

        out = PortfolioExposureTool()._run(
            positions=[],
            account_size=10_000.0,
        )
        import json
        data = json.loads(out)
        assert data["total_position"] == 0.0
        assert data["total_exposure_pct"] == 0.0
        assert data["breaches"]["total_breached"] is False
        assert data["breaches"]["coins_over_single_cap"] == []

    def test_exposure_pct_and_per_coin_breakdown(self):
        from crypto_council_flow.tools.portfolio_tools import PortfolioExposureTool
        import json

        out = PortfolioExposureTool()._run(
            positions=[
                {"coin_id": "solana", "position": 1500.0},
                {"coin_id": "bitcoin", "position": 2500.0},
            ],
            account_size=10_000.0,
        )
        data = json.loads(out)
        assert data["total_position"] == 4000.0
        assert data["total_exposure_pct"] == 40.0
        by_coin = {p["coin_id"]: p for p in data["positions"]}
        assert by_coin["solana"]["exposure_pct"] == 15.0
        assert by_coin["bitcoin"]["exposure_pct"] == 25.0

    def test_total_cap_breach_flagged(self):
        from crypto_council_flow.tools.portfolio_tools import PortfolioExposureTool
        import json

        out = PortfolioExposureTool()._run(
            positions=[
                {"coin_id": "solana", "position": 4000.0},
                {"coin_id": "bitcoin", "position": 3000.0},
            ],
            account_size=10_000.0,
            max_total_exposure_pct=60.0,
        )
        data = json.loads(out)
        assert data["total_exposure_pct"] == 70.0
        assert data["breaches"]["total_breached"] is True

    def test_single_coin_cap_breach_flagged(self):
        from crypto_council_flow.tools.portfolio_tools import PortfolioExposureTool
        import json

        out = PortfolioExposureTool()._run(
            positions=[{"coin_id": "solana", "position": 2500.0}],
            account_size=10_000.0,
            max_single_position_pct=20.0,
        )
        data = json.loads(out)
        assert data["breaches"]["coins_over_single_cap"] == ["solana"]

    def test_alias_keys_accepted(self):
        """Caller may pass dicts with alias keys."""
        from crypto_council_flow.tools.portfolio_tools import PortfolioExposureTool
        import json

        out = PortfolioExposureTool()._run(
            positions=[
                {"coingecko_id": "solana", "current": 1000.0},
                {"coin_id": "bitcoin", "target": 2000.0},
            ],
            account_size=10_000.0,
        )
        data = json.loads(out)
        assert data["total_position"] == 3000.0

    def test_cash_remaining_reported(self):
        from crypto_council_flow.tools.portfolio_tools import PortfolioExposureTool
        import json

        out = PortfolioExposureTool()._run(
            positions=[{"coin_id": "solana", "position": 4000.0}],
            account_size=10_000.0,
        )
        data = json.loads(out)
        assert data["cash_remaining"] == 6000.0


# ---------------------------------------------------------------------------
# Slice 2: RebalanceAllocatorTool
# ---------------------------------------------------------------------------

class TestRebalanceAllocatorTool:
    def test_empty_opportunities_closes_book(self):
        from crypto_council_flow.tools.portfolio_tools import RebalanceAllocatorTool
        import json

        out = RebalanceAllocatorTool()._run(
            opportunities=[],
            positions=[{"coin_id": "solana", "position": 1000.0}],
            account_size=10_000.0,
        )
        data = json.loads(out)
        assert data["total_target"] == 0.0
        by_coin = {a["coin_id"]: a for a in data["allocations"]}
        assert by_coin["solana"]["action"] == "close"
        assert by_coin["solana"]["target"] == 0.0

    def test_single_coin_capped(self):
        from crypto_council_flow.tools.portfolio_tools import RebalanceAllocatorTool
        import json

        out = RebalanceAllocatorTool()._run(
            opportunities=[{"coin_id": "solana", "score": 90}],
            positions=[],
            account_size=10_000.0,
            max_total_exposure_pct=60.0,
            max_single_position_pct=20.0,
        )
        data = json.loads(out)
        assert len(data["allocations"]) == 1
        alloc = data["allocations"][0]
        assert alloc["target"] == 2000.0  # single cap, not full 6000 budget
        assert alloc["action"] == "open"
        assert data["unallocated"] == 4000.0

    def test_total_budget_never_exceeded(self):
        from crypto_council_flow.tools.portfolio_tools import RebalanceAllocatorTool
        import json

        opps = [{"coin_id": f"coin-{i}", "score": 80} for i in range(10)]
        out = RebalanceAllocatorTool()._run(
            opportunities=opps,
            positions=[],
            account_size=10_000.0,
            max_total_exposure_pct=60.0,
            max_single_position_pct=20.0,
        )
        data = json.loads(out)
        assert data["total_target"] <= 6000.0 + 0.01
        for alloc in data["allocations"]:
            assert alloc["target"] <= 2000.0 + 0.01

    def test_weighted_by_score(self):
        from crypto_council_flow.tools.portfolio_tools import RebalanceAllocatorTool
        import json

        out = RebalanceAllocatorTool()._run(
            opportunities=[
                {"coin_id": "solana", "score": 80},
                {"coin_id": "bitcoin", "score": 20},
            ],
            positions=[],
            account_size=10_000.0,
            max_total_exposure_pct=60.0,
            max_single_position_pct=50.0,  # high cap so weighting shows
        )
        data = json.loads(out)
        by_coin = {a["coin_id"]: a for a in data["allocations"]}
        # 80/100 * 6000 = 4800, 20/100 * 6000 = 1200
        assert by_coin["solana"]["target"] == 4800.0
        assert by_coin["bitcoin"]["target"] == 1200.0

    def test_dust_delta_is_hold(self):
        from crypto_council_flow.tools.portfolio_tools import RebalanceAllocatorTool
        import json

        out = RebalanceAllocatorTool()._run(
            opportunities=[{"coin_id": "solana", "score": 50}],
            positions=[{"coin_id": "solana", "position": 1995.0}],
            account_size=10_000.0,
            max_total_exposure_pct=60.0,
            max_single_position_pct=20.0,  # target 2000, delta 5 < min 10
        )
        data = json.loads(out)
        alloc = data["allocations"][0]
        assert alloc["action"] == "hold"
        assert alloc["target"] == 1995.0  # keeps current, no churn
        assert alloc["delta"] == 0.0

    def test_increase_and_decrease_verbs(self):
        from crypto_council_flow.tools.portfolio_tools import RebalanceAllocatorTool
        import json

        out = RebalanceAllocatorTool()._run(
            opportunities=[
                {"coin_id": "solana", "score": 90},
                {"coin_id": "bitcoin", "score": 10},
            ],
            positions=[
                {"coin_id": "solana", "position": 500.0},
                {"coin_id": "bitcoin", "position": 1900.0},
            ],
            account_size=10_000.0,
            max_total_exposure_pct=60.0,
            max_single_position_pct=50.0,
        )
        data = json.loads(out)
        by_coin = {a["coin_id"]: a for a in data["allocations"]}
        assert by_coin["solana"]["action"] == "increase"
        assert by_coin["bitcoin"]["action"] == "decrease"


# ---------------------------------------------------------------------------
# Slice 3: PortfolioPlan DTO — one canonical shape
# ---------------------------------------------------------------------------

class TestPortfolioPlanDTO:
    def test_canonical_validates(self):
        from crypto_council_flow.crews.council.council_crew import PortfolioPlan

        plan = PortfolioPlan.model_validate({
            "actions": [
                {"coin_id": "solana", "symbol": "SOL", "action": "open",
                 "current": 0.0, "target": 1500.0, "reason": "Top score."},
            ],
            "total_target": 1500.0,
            "total_exposure_pct": 15.0,
            "cash_remaining": 8500.0,
            "rationale": "Opened SOL.",
        })
        assert len(plan.actions) == 1
        assert plan.actions[0].coin_id == "solana"
        # exactly the canonical field count — proves one DTO, not two
        assert set(plan.model_dump()) == {
            "actions", "total_target", "total_exposure_pct",
            "cash_remaining", "rationale",
        }

    def test_action_aliases_converge(self):
        from crypto_council_flow.crews.council.council_crew import PortfolioPlan

        plan = PortfolioPlan.model_validate({
            "plan": [  # envelope alias
                {"coingecko_id": "solana", "ticker": "sol",
                 "side": "buy", "current": 0.0, "target": 1000.0,
                 "reason": "Strong setup."},
                {"coin_id": "bitcoin", "symbol": "btc",
                 "action": "sell", "current": 500.0,
                 "target": 500.0, "reason": "Stale."},
            ],
            "total_target": 1000.0,
            "total_exposure_pct": 10.0,
            "cash_remaining": 9000.0,
            "rationale": "Rotated.",
        })
        by_coin = {a.coin_id: a for a in plan.actions}
        assert by_coin["solana"].action == "open"  # buy → open
        assert by_coin["solana"].symbol == "SOL"  # uppercased
        assert by_coin["bitcoin"].action == "close"  # sell → close
        assert by_coin["bitcoin"].target == 0.0  # close forces 0

    def test_unknown_action_falls_back_to_hold(self):
        from crypto_council_flow.crews.council.council_crew import PortfolioAction

        action = PortfolioAction.model_validate({
            "coin_id": "solana", "symbol": "SOL", "action": "moon",
            "current": 100.0, "target": 200.0, "reason": "Hype.",
        })
        assert action.action == "hold"

    def test_action_carries_optional_risk_levels(self):
        from crypto_council_flow.crews.council.council_crew import PortfolioAction

        action = PortfolioAction.model_validate({
            "coin_id": "solana", "symbol": "SOL", "action": "open",
            "current": 0.0, "target": 1500.0, "reason": "Top score.",
            "stop_loss": 97.5, "take_profit": 105.0,
        })
        assert action.stop_loss == 97.5
        assert action.take_profit == 105.0

    def test_action_levels_default_to_none(self):
        from crypto_council_flow.crews.council.council_crew import PortfolioAction

        action = PortfolioAction.model_validate({
            "coin_id": "solana", "symbol": "SOL", "action": "hold",
            "current": 100.0, "target": 100.0, "reason": "Wait.",
        })
        assert action.stop_loss is None
        assert action.take_profit is None

    def test_action_junk_levels_become_none(self):
        from crypto_council_flow.crews.council.council_crew import PortfolioAction

        action = PortfolioAction.model_validate({
            "coin_id": "solana", "symbol": "SOL", "action": "open",
            "current": 0.0, "target": 1500.0, "reason": "Top score.",
            "stop_loss": "n/a", "take_profit": "",
        })
        assert action.stop_loss is None
        assert action.take_profit is None

    def test_close_action_keeps_levels(self):
        from crypto_council_flow.crews.council.council_crew import PortfolioAction

        action = PortfolioAction.model_validate({
            "coin_id": "solana", "symbol": "SOL", "action": "close",
            "current": 500.0, "target": 500.0, "reason": "Stale.",
            "stop_loss": 97.5, "take_profit": 105.0,
        })
        assert action.action == "close"
        assert action.target == 0.0  # close still forces 0
        assert action.stop_loss == 97.5
        assert action.take_profit == 105.0


# ---------------------------------------------------------------------------
# Slice 4: CouncilPortfolioCrew wiring
# ---------------------------------------------------------------------------

class TestCouncilPortfolioCrew:
    def test_portfolio_crew_instantiates(self):
        from crypto_council_flow.crews.council.council_crew import (
            CouncilPortfolioCrew,
            PortfolioPlan,
        )

        crew = CouncilPortfolioCrew().crew()
        assert [t.name for t in crew.tasks] == ["portfolio_management_task"]
        assert [a.role for a in crew.agents] == ["Crypto Portfolio Manager"]
        (task,) = [t for t in crew.tasks if t.name == "portfolio_management_task"]
        assert task.output_pydantic is PortfolioPlan

    def test_portfolio_agent_has_both_tools(self):
        from crypto_council_flow.crews.council.council_crew import CouncilPortfolioCrew

        crew = CouncilPortfolioCrew().crew()
        (agent,) = crew.agents
        tool_names = sorted(t.name for t in (agent.tools or []))
        assert tool_names == ["portfolio_exposure", "rebalance_allocator", "risk_levels"]

    def test_portfolio_task_config_renders(self):
        from crypto_council_flow.crews.council.council_crew import CouncilPortfolioCrew

        crew = CouncilPortfolioCrew().crew()
        (task,) = crew.tasks
        assert task.agent is not None
        assert "actions" in (task.expected_output or "")


# ---------------------------------------------------------------------------
# Slice 5: Flow manage_portfolio step (stubbed crew, no LLM)
# ---------------------------------------------------------------------------

class _StubResult:
    def __init__(self, pydantic=None, raw=""):
        self.pydantic = pydantic
        self.raw = raw


class TestManagePortfolioStep:
    def _flow_with_opportunities(self):
        from crypto_council_flow.main import CoinOpportunity, CryptoCouncilFlow

        flow = CryptoCouncilFlow()
        flow.state.account_size = 10_000.0
        flow.state.coin_opportunities = [
            CoinOpportunity(rank=1, coin_id="solana", symbol="SOL",
                            name="Solana", score=80),
            CoinOpportunity(rank=2, coin_id="bitcoin", symbol="BTC",
                            name="Bitcoin", score=60),
        ]
        flow.state.analysed_coins = ["solana", "bitcoin"]
        return flow

    def test_manage_portfolio_saves_plan(self, monkeypatch):
        import json

        from crypto_council_flow.crews.council.council_crew import (
            PortfolioAction,
            PortfolioPlan,
        )

        flow = self._flow_with_opportunities()
        plan = PortfolioPlan(
            actions=[
                PortfolioAction(coin_id="solana", symbol="SOL", action="open",
                                current=0.0, target=2000.0, reason="Top score."),
                PortfolioAction(coin_id="bitcoin", symbol="BTC", action="open",
                                current=0.0, target=1500.0, reason="Second pick."),
            ],
            total_target=3500.0,
            total_exposure_pct=35.0,
            cash_remaining=6500.0,
            rationale="Opened two positions.",
        )

        import crypto_council_flow.main as main_mod

        class StubCrew:
            def crew(self):
                return self

            def kickoff(self, inputs=None):
                assert inputs is not None
                assert json.loads(inputs["opportunities_json"])[0]["coin_id"] == "solana"
                assert json.loads(inputs["analysed_coins_json"]) == ["solana", "bitcoin"]
                return _StubResult(pydantic=plan, raw="{}")

        monkeypatch.setattr(main_mod, "CouncilPortfolioCrew", StubCrew)
        flow.manage_portfolio()

        assert flow.state.portfolio_cycle == 1
        assert flow.state.portfolio_plan["total_target"] == 3500.0
        assert len(flow.state.portfolio_plan["actions"]) == 2
        assert flow.state.last_portfolio_utc != ""
        assert flow.state.analysed_coins == []  # reset for next cycle

    def test_manage_portfolio_skips_when_no_opportunities(self, monkeypatch):
        from crypto_council_flow.main import CryptoCouncilFlow
        import crypto_council_flow.main as main_mod

        flow = CryptoCouncilFlow()
        flow.state.coin_opportunities = []

        called = {"n": 0}

        class StubCrew:
            def crew(self):
                return self

            def kickoff(self, inputs=None):
                called["n"] += 1
                return _StubResult(pydantic=None, raw="{}")

        monkeypatch.setattr(main_mod, "CouncilPortfolioCrew", StubCrew)
        flow.manage_portfolio()
        assert called["n"] == 0
        assert flow.state.portfolio_cycle == 0

    def test_manage_portfolio_keeps_previous_plan_on_failure(self, monkeypatch):
        flow = self._flow_with_opportunities()
        flow.state.portfolio_plan = {
            "actions": [], "total_target": 100.0,
            "total_exposure_pct": 1.0, "cash_remaining": 9900.0,
            "rationale": "old",
        }
        import crypto_council_flow.main as main_mod

        class StubCrew:
            def crew(self):
                return self

            def kickoff(self, inputs=None):
                raise RuntimeError("LLM down")

        monkeypatch.setattr(main_mod, "CouncilPortfolioCrew", StubCrew)
        flow.manage_portfolio()
        # previous plan preserved, cycle still counted
        assert flow.state.portfolio_plan["total_target"] == 100.0
        assert flow.state.portfolio_cycle == 1

    def test_extract_portfolio_plan_fallback(self):
        from crypto_council_flow.main import _extract_portfolio_plan

        raw = ('Here is the plan: {"actions": [{"coin_id": "solana", '
               '"symbol": "SOL", "action": "hold", "current": 0, '
               '"target": 0, "reason": "Wait."}]} done')
        parsed = _extract_portfolio_plan(raw)
        assert parsed["actions"][0]["coin_id"] == "solana"
        assert _extract_portfolio_plan("no json here") == {}

    def test_second_cycle_uses_previous_targets_as_positions(self, monkeypatch):
        import json

        from crypto_council_flow.crews.council.council_crew import PortfolioPlan

        flow = self._flow_with_opportunities()
        flow.state.portfolio_plan = {
            "actions": [
                {"coin_id": "solana", "symbol": "SOL", "action": "open",
                 "current": 0.0, "target": 2000.0, "reason": "old"},
            ],
            "total_target": 2000.0, "total_exposure_pct": 20.0,
            "cash_remaining": 8000.0, "rationale": "old",
        }
        import crypto_council_flow.main as main_mod

        seen = {}

        class StubCrew:
            def crew(self):
                return self

            def kickoff(self, inputs=None):
                assert inputs is not None
                seen["positions"] = json.loads(inputs["open_positions_json"])
                return _StubResult(
                    pydantic=PortfolioPlan(
                        actions=[], total_target=0.0,
                        total_exposure_pct=0.0, cash_remaining=10000.0,
                        rationale="flat",
                    ),
                    raw="{}",
                )

        monkeypatch.setattr(main_mod, "CouncilPortfolioCrew", StubCrew)
        flow.manage_portfolio()
        assert seen["positions"] == [
            {"coin_id": "solana", "position": 2000.0}
        ]
