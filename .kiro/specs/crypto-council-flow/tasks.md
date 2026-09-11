# Implementation Plan: Crypto Council Flow — Test Suite

## Overview

All source code is already implemented. This plan creates a comprehensive test
suite that verifies correctness of the scout tools, JSONC loader, output parsing,
report generation, Pydantic model validation, and crew instantiation. Tests are
co-located with source files under `tests/` and use `pytest` with `unittest.mock`
to avoid live API calls.

## Tasks

- [ ] 1. Set up test infrastructure
  - Create `tests/` directory with `__init__.py`, `conftest.py`, and `pytest.ini`
    (or add `[tool.pytest.ini_options]` to `pyproject.toml`)
  - `conftest.py` should add `src/` to `sys.path` so imports resolve without
    installing the package in editable mode
  - Verify `pytest` and `pytest-mock` are available (add via `uv add --dev pytest
    pytest-mock` if missing)
  - _Requirements: test infrastructure prerequisite for all subsequent tasks_

- [ ] 2. Import sanity checks
  - [ ] 2.1 Write import smoke tests
    - Create `tests/test_imports.py`
    - Assert that all 16 tool classes can be imported from
      `crypto_council_flow.tools`
    - Assert `CoinOpportunity`, `CryptoCouncilState`, `CryptoCouncilFlow`,
      `_build_report` can be imported from `crypto_council_flow.main`
    - Assert `CouncilScoutCrew`, `CouncilAnalysisCrew`, `_load_jsonc` can be
      imported from `crypto_council_flow.crews.council.council_crew`
    - _Requirements: 5.1 — package imports must not raise_

- [ ] 3. Unit tests — `CoinOpportunity` Pydantic model
  - [ ] 3.1 Write validation tests for `CoinOpportunity`
    - Create `tests/test_models.py`
    - Test: a fully populated valid object is accepted by the model
    - Test: `risk_tier` defaults to `"MEDIUM"` when omitted
    - Test: `signals` defaults to `[]` when omitted
    - Test: missing required fields (`rank`, `coin_id`, `symbol`, `name`, `score`)
      each raise a `ValidationError`
    - Test: `score` accepts 0 and 100 as boundary values
    - _Requirements: 1.4 — valid and invalid CoinOpportunity inputs_

  - [ ]* 3.2 Write property test for `CoinOpportunity` round-trip serialisation
    - **Property 1: Scout output always contains valid CoinOpportunity objects**
    - Use `hypothesis` strategies to generate arbitrary dicts and verify that
      `model_validate(d)` either succeeds (producing a valid model) or raises
      `ValidationError` — never raises any other exception
    - **Validates: Requirements 1.4, 5.5**

- [ ] 4. Unit tests — `_load_jsonc()` JSONC parser
  - [ ] 4.1 Write unit tests for `_load_jsonc`
    - Create `tests/test_jsonc_loader.py`
    - Test: plain JSON (no comments) parses identically to `json.loads`
    - Test: `//` single-line comments are stripped
    - Test: `/* */` block comments are stripped
    - Test: parenthesised multi-line strings collapse into a single string
    - Test: trailing commas before `}` are removed
    - Test: trailing commas before `]` are removed
    - Test: parsing the real `agents.jsonc` file produces a dict with keys
      `market_scout`, `technical_analyst`, `sentiment_analyst`, `risk_manager`
    - _Requirements: 5.3 — JSONC loader_

  - [ ]* 4.2 Write property test for `_load_jsonc` idempotency
    - **Property 10: JSONC loader produces valid JSON for any well-formed JSONC input**
    - Use `hypothesis` to generate JSON-serialisable dicts, serialise them to
      plain JSON strings, and assert that `_load_jsonc` on a temp file returns a
      dict equal to the original (i.e., no data is lost for comment-free input)
    - **Validates: Requirements 5.3**

- [ ] 5. Unit tests — scout output parsing (`run_scout`)
  - [ ] 5.1 Write unit tests for JSON parsing logic in `run_scout`
    - Create `tests/test_run_scout_parsing.py`
    - Extract the parsing logic into a testable helper (or test it via a
      `CryptoCouncilFlow` instance with a mocked crew result)
    - Test: a clean JSON array string is parsed into a list of `CoinOpportunity`
    - Test: a JSON array wrapped in triple-backtick fences is unwrapped and parsed
    - Test: a JSON array embedded in prose is extracted via regex fallback
    - Test: malformed entries in an otherwise valid array are silently skipped
    - Test: a fully malformed string produces an empty list (no exception raised)
    - Test: an empty JSON array `[]` produces an empty list
    - _Requirements: 5.5 — defensive parsing_

- [ ] 6. Unit tests — `_build_report()` report header
  - [ ] 6.1 Write unit tests for `_build_report`
    - Create `tests/test_build_report.py`
    - Test: report contains `opportunity.symbol` in the header
    - Test: report contains `str(opportunity.score)` in the header
    - Test: report contains `opportunity.risk_tier` in the header
    - Test: report contains `opportunity.reason` in the header
    - Test: every entry in `opportunity.signals` appears somewhere in the report
    - Test: `analysis_raw` content appears after the `---` separator
    - Test: the report starts with `# Crypto Council Report:`
    - _Requirements: 5.2 — report header generation_

  - [ ]* 6.2 Write property test for `_build_report` completeness
    - **Property 9: Report header contains all CoinOpportunity fields**
    - Use `hypothesis` to generate arbitrary `CoinOpportunity`-compatible dicts
      and arbitrary `analysis_raw` strings, then assert all required fields
      appear in the generated report
    - **Validates: Requirements 5.2**

- [ ] 7. Unit tests — `TrendingCoinsTool`
  - [ ] 7.1 Write unit tests for `TrendingCoinsTool`
    - Create `tests/test_scout_tools.py`
    - Mock `requests.get` to return a canned CoinGecko trending response
    - Test: `_run(top_n=3)` returns JSON with `"count": 3` and `"coins"` list
    - Test: each coin entry has `coin_id`, `symbol`, `name`, `market_cap_rank`,
      `price_btc`, `score` keys
    - Test: `requests.RequestException` is caught and returns `{"error": "..."}`
    - Test: empty `"coins"` list in API response produces `"count": 0`
    - _Requirements: 1.1 — trending coins data source_

- [ ] 8. Unit tests — `MomentumScreenerTool`
  - [ ] 8.1 Write unit tests for `MomentumScreenerTool`
    - Add to `tests/test_scout_tools.py`
    - Mock `requests.get` to return a list of market coins with varying volumes
      and 24h changes
    - Test: coins below `min_volume_usd` are filtered out
    - Test: coins above `max_market_cap_rank` are filtered out
    - Test: remaining coins are sorted by `price_change_24h_pct` descending
    - Test: result is limited to `top_n` entries
    - Test: `requests.RequestException` returns `{"error": "..."}`
    - _Requirements: 1.1 — momentum screener data source_

- [ ] 9. Unit tests — `NewListingsTool`
  - [ ] 9.1 Write unit tests for `NewListingsTool`
    - Add to `tests/test_scout_tools.py`
    - Mock `requests.get` to return a list of coins including some with
      `current_price == 0` or `None`
    - Test: coins with zero or null price are filtered out
    - Test: coins with valid prices appear in the result
    - Test: result JSON has `"source": "coingecko_new_listings"` key
    - Test: `requests.RequestException` returns `{"error": "..."}`
    - _Requirements: 1.1 — new listings data source_

- [ ] 10. Unit tests — `UpcomingCatalystsTool`
  - [ ] 10.1 Write unit tests for `UpcomingCatalystsTool`
    - Add to `tests/test_scout_tools.py`
    - Mock both `requests.get` calls (important + rising filter) with canned
      CryptoPanic responses containing articles with known bullish/bearish keywords
    - Test: articles with only bearish keywords get `catalyst_direction == "bearish"`
    - Test: articles with only bullish keywords get `catalyst_direction == "bullish"`
    - Test: articles with both get `catalyst_direction == "mixed"`
    - Test: articles with no matching keywords are excluded from results
    - Test: coin symbols are correctly extracted from `currencies` metadata
    - Test: `coins_with_catalysts` is sorted by `net_vote_score` descending
    - Test: `requests.RequestException` returns `{"error": "..."}`
    - _Requirements: 1.1 — catalyst detection; 3.3 — keyword classification_

  - [ ]* 10.2 Write property test for catalyst direction detection
    - **Property 11: Catalyst direction detection is consistent with keyword presence**
    - Use `hypothesis` to generate titles that contain only bullish keywords,
      only bearish keywords, or both, and assert the `catalyst_direction` logic
      is deterministic and consistent with the keyword sets
    - **Validates: Requirements 3.3**

- [ ] 11. Checkpoint — run all unit tests
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Integration smoke tests — crew instantiation
  - [ ] 12.1 Write crew instantiation smoke tests
    - Create `tests/test_crew_instantiation.py`
    - Test: `CouncilScoutCrew()` instantiates without raising, and `.crew()`
      returns a `Crew` object
    - Test: `CouncilAnalysisCrew()` instantiates without raising, and `.crew()`
      returns a `Crew` object
    - Test: `CouncilScoutCrew().crew().agents` contains exactly one agent with
      `role == "Crypto Market Scout"`
    - Test: `CouncilAnalysisCrew().crew().agents` contains exactly three agents
    - Test: `CouncilAnalysisCrew().crew().tasks` contains exactly three tasks
    - Test: the scout agent's tools include instances of all four scout tool classes
    - These tests must NOT call `kickoff()` — instantiation only
    - _Requirements: 5.1, 5.4 — crew wiring_

- [ ] 13. Final checkpoint — full test suite
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Run the test suite with: `pytest tests/ -v`
- Run with coverage: `pytest tests/ --cov=src/crypto_council_flow --cov-report=term-missing`
- All unit tests mock HTTP calls — no live API access needed
- `hypothesis` is required only for PBT tasks (3.2, 4.2, 6.2, 10.2); install
  with `uv add --dev hypothesis` if running those tasks
- The crew instantiation tests (task 12) will trigger `_load_jsonc` and
  `load_skill` at import time — ensure the test runner's working directory is
  set to the project root so relative paths in `council_crew.py` resolve correctly
- Property tests validate universal correctness guarantees from the design doc

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1"] },
    { "id": 1, "tasks": ["3.1", "4.1", "5.1", "6.1", "7.1", "8.1", "9.1", "10.1"] },
    { "id": 2, "tasks": ["3.2", "4.2", "6.2", "10.2"] },
    { "id": 3, "tasks": ["12.1"] }
  ]
}
```
