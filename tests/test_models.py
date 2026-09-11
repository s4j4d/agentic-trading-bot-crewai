"""
Tests for the Pydantic state models: CoinOpportunity and CryptoCouncilState.
"""

import pytest
from pydantic import ValidationError

from crypto_council_flow.main import CoinOpportunity, CryptoCouncilState


class TestCoinOpportunity:
    def test_minimal_valid_construction(self):
        op = CoinOpportunity(rank=1, coin_id="bitcoin", symbol="BTC", name="Bitcoin", score=90)
        assert op.rank == 1
        assert op.coin_id == "bitcoin"
        assert op.symbol == "BTC"
        assert op.name == "Bitcoin"
        assert op.score == 90

    def test_default_values(self):
        op = CoinOpportunity(rank=1, coin_id="solana", symbol="SOL", name="Solana", score=75)
        assert op.signals == []
        assert op.risk_tier == "MEDIUM"
        assert op.reason == ""

    def test_full_construction(self):
        op = CoinOpportunity(
            rank=2,
            coin_id="avalanche-2",
            symbol="AVAX",
            name="Avalanche",
            score=60,
            signals=["trending_rank_3", "momentum_+5%"],
            risk_tier="LOW",
            reason="Strong top-20 setup.",
        )
        assert op.signals == ["trending_rank_3", "momentum_+5%"]
        assert op.risk_tier == "LOW"
        assert op.reason == "Strong top-20 setup."

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            CoinOpportunity(rank=1, symbol="BTC", name="Bitcoin", score=90)  # missing coin_id

    def test_score_is_integer(self):
        op = CoinOpportunity(rank=1, coin_id="bitcoin", symbol="BTC", name="Bitcoin", score=50)
        assert isinstance(op.score, int)

    def test_signals_is_list(self):
        op = CoinOpportunity(rank=1, coin_id="bitcoin", symbol="BTC", name="Bitcoin", score=50,
                             signals=["a", "b", "c"])
        assert isinstance(op.signals, list)
        assert len(op.signals) == 3

    def test_dict_round_trip(self):
        data = {
            "rank": 3,
            "coin_id": "cardano",
            "symbol": "ADA",
            "name": "Cardano",
            "score": 45,
            "signals": ["catalyst:listing"],
            "risk_tier": "HIGH",
            "reason": "New exchange listing catalyst.",
        }
        op = CoinOpportunity(**data)
        assert op.model_dump()["coin_id"] == "cardano"


class TestCryptoCouncilState:
    def test_default_construction(self):
        state = CryptoCouncilState()
        assert state.account_size_usd == 10_000.0
        assert state.period == 14
        assert state.coin_opportunities == []
        assert state.last_scout_utc == ""
        assert state.analysis_reports == {}
        assert state.scout_cycle == 0
        assert state.analysis_cycle == 0

    def test_custom_account_size(self):
        state = CryptoCouncilState(account_size_usd=25_000.0)
        assert state.account_size_usd == 25_000.0

    def test_coin_opportunities_list(self):
        op = CoinOpportunity(rank=1, coin_id="solana", symbol="SOL", name="Solana", score=80)
        state = CryptoCouncilState(coin_opportunities=[op])
        assert len(state.coin_opportunities) == 1
        assert state.coin_opportunities[0].coin_id == "solana"

    def test_analysis_reports_dict(self):
        state = CryptoCouncilState()
        state.analysis_reports["solana"] = "output/solana_report.md"
        assert state.analysis_reports["solana"] == "output/solana_report.md"

    def test_scout_cycle_increment(self):
        state = CryptoCouncilState()
        state.scout_cycle += 1
        assert state.scout_cycle == 1

    def test_missing_required_fields_uses_defaults(self):
        # All fields have defaults, so construction without args should succeed
        state = CryptoCouncilState()
        assert state is not None
