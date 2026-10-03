"""Scout structured-output wiring: pydantic path + raw fallback extractor."""

from crypto_council_flow.crews.council.council_crew import (
    CouncilScoutCrew,
    ScoutShortlist,
)
from crypto_council_flow.main import CoinOpportunity, _extract_scout_items


def test_shortlist_validates_logged_shape():
    """The zcash-shaped item from the failed run must validate (int score)."""
    s = ScoutShortlist.model_validate(
        {
            "opportunities": [
                {
                    "rank": 1,
                    "coin_id": "zcash",
                    "symbol": "ZEC",
                    "name": "Zcash",
                    "score": 19,
                    "signals": ["trending", "momentum"],
                    "risk_tier": "MEDIUM",
                    "reason": "x",
                }
            ]
        }
    )
    op = CoinOpportunity(**s.opportunities[0].model_dump())
    assert op.coin_id == "zcash"


def test_extractor_handles_envelope():
    raw = 'blah {"opportunities": [{"rank": 1, "coin_id": "a", "symbol": "A", "name": "A", "score": 5}]} tail'
    assert _extract_scout_items(raw) == [
        {"rank": 1, "coin_id": "a", "symbol": "A", "name": "A", "score": 5}
    ]


def test_extractor_handles_fenced_bare_array():
    raw = "see ```json\n[{\"rank\": 1, \"coin_id\": \"a\", \"symbol\": \"A\", \"name\": \"A\", \"score\": 5}]\n``` done"
    assert _extract_scout_items(raw) == [
        {"rank": 1, "coin_id": "a", "symbol": "A", "name": "A", "score": 5}
    ]


def test_extractor_returns_empty_on_prose():
    assert _extract_scout_items("The shortlist was verified, no JSON here.") == []


def test_scout_task_uses_structured_output():
    crew = CouncilScoutCrew().crew()
    (t,) = [t for t in crew.tasks if t.name == "market_scout_task"]
    assert t.output_pydantic is ScoutShortlist
