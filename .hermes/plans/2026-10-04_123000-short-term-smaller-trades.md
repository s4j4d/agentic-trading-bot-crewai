# Short-Term, Smaller-Size Trades — Plan

## Goal
Adjust the crypto_council_flow trading configuration so the crew opens smaller positions and holds them for shorter durations (currently ETH trades run the full 30-day window; BTC stopped out in 6d), so limited capital is not locked up in slow-moving holds.

## Current context / assumptions
- Crew currently sizes positions up to `max_single_position_pct=20%` of a $10,000 account (i.e. up to $2,000 per coin) and up to `max_total_exposure_pct=60%` total exposure. Defaults in `src/crypto_council_flow/main.py:146-147,827-828`.
- Deterministic risk levels are 1×ATR stop-loss and 2:1 take-profit (`src/crypto_council_flow/main.py:629:_risk_levels_for`). These are wide on daily candles → trades take up to 30 days to resolve.
- `_ATR_SNAPSHOT_DAYS = 30` and `_ATR_SNAPSHOT_PERIOD = 14` in `src/crypto_council_flow/main.py` — daily timeframe, slow.
- No time-based exit exists today; a trade only closes on stop-loss or take-profit.
- `tests/test_backtest_real_flow.py` exists and can validate changes end-to-end (runs analysis + portfolio crews on frozen T-30 data, grades outcomes).
- Goal values (account size, how small, how short) are not yet pinned — see Open Questions before executing.
- Backtest result observed: BTC -3.72% in 6d, ETH +9.85% in 30d, SOL +17.78% in 21d. We want more trades like BTC's fast stop (small loss) and less than SOL's 21-day hold.

## Architecture / proposed approach
Make position size and hold-time explicit, tunable settings in `CryptoCouncilState` and the portfolio/risk prompt, and add a time-based exit guardrail so positions cannot run the full 30-day window silently. Three levers: (1) lower `max_single_position_pct` and the fixed-risk `max_risk_pct` in the position-sizing tool, (2) tighten the ATR multiplier on stop/take-profit and shorten the analysis window from daily to intraday candles so levels resolve faster, (3) add a max-hold-days exit in the deterministic layer. All changes are deterministic-config changes, not new agents.

## Step-by-step tasks

### Task 1 — Lower single-position size cap ✅
File: `src/crypto_council_flow/main.py`
- Change default at line ~147: `max_single_position_pct: float = 20.0` → `5.0`
- Change CLI default at line ~828: `"max_single_position_pct": 20.0` → `5.0`
- Lower `max_total_exposure_pct` at line ~146 from `60.0` to `30.0` (line ~827 same change).
Verify: `grep -n "max_single_position_pct\|max_total_exposure_pct" src/crypto_council_flow/main.py | head` shows `5.0` and `30.0` as defaults.

### Task 2 — Reduce per-trade risk in the position-sizing tool ⏭️ skipped (max_risk_pct stays 2%)
File: `src/crypto_council_flow/tools/risk_tools.py`
- In the Kelly/fixed-risk tool config (~line 61-65), change the `max_risk_pct` description default from `2.0` percent to `0.5` percent. Find the Field default: `max_risk_pct: float = Field(...)` — set `default=0.5`.
- In `_run` (~line 85), change `max_risk_pct: float = 2.0` → `0.5`.
Verify: `grep -n "max_risk_pct" src/crypto_council_flow/tools/risk_tools.py` shows `0.5` in both the Field default and the `_run` signature.

### Task 3 — Tighten stop/take-profit ATR multiplier ✅
File: `src/crypto_council_flow/main.py`
- In `_risk_levels_for` (~line 629), change `eff_pct = max(atr, 1.0)` → keep, but change the multipliers:
  - `stop = round(price * (1 - eff_pct / 100), 6)` → `stop = round(price * (1 - (eff_pct * 0.5) / 100), 6)` (0.5×ATR stop)
  - `take = round(price * (1 + 2 * eff_pct / 100), 6)` → `take = round(price * (1 + (eff_pct) / 100), 6)` (1:1 R/R instead of 2:1)
Verify: `grep -n "eff_pct\|stop =\|take =" src/crypto_council_flow/main.py | head` shows the new multipliers.

### Task 4 — Add a max-hold-days guardrail to portfolio actions ✅
File: `src/crypto_council_flow/main.py`
- In `CryptoCouncilState` (~line 151) add: `max_hold_days: int = 5`
- In `_backfill_risk_levels` area, add a helper `_apply_max_hold(actions, max_hold_days)` is NOT needed at state level; instead the deterministic ExitCandidate logic (existing `exit_candidates`) should emit `close` when a hold exceeds `max_hold_days`. Find where current positions are evaluated for close (search `close` in `manage_portfolio`, ~line 428) and add: if `position.age_days > max_hold_days`, force action `"close"`. Positions from `open_positions_json` need an `age_days` or `opened_utc` field; check the shape in `main.py` where open positions are built (search `open_positions`).
Verify: add `age_days` to open_positions entries; `grep -n "max_hold_days" src/crypto_council_flow/main.py` shows the new field and the close-forcing branch.

### Task 5 — Add `max_hold_days` to CLI inputs ✅
File: `src/crypto_council_flow/main.py`
- In `_parse_inputs` defaults (~line 827), add `"max_hold_days": 5`.
- In the arg loop (~line 851), add:
  ```python
  elif arg in ("--max-hold-days", "--max_hold_days") and i + 1 < len(argv):
      try:
          inputs["max_hold_days"] = int(argv[i + 1])
      except ValueError:
          pass
  ```
- In `kickoff` kwargs (~line 980), add `max_hold_days=inputs.get("max_hold_days", 5),`.
Verify: `python -c "from crypto_council_flow.main import _parse_inputs; print(_parse_inputs(['--max-hold-days','3'])['max_hold_days'])"` prints `3`.

### Task 6 — Unit tests for the new caps and hold-time guard ✅
File: `tests/test_short_term_caps.py` (new)
Create with:
```python
from crypto_council_flow.main import CryptoCouncilState, _risk_levels_for

def test_defaults_are_small_and_short():
    s = CryptoCouncilState()
    assert s.max_single_position_pct <= 5.0
    assert s.max_total_exposure_pct <= 30.0
    assert s.max_hold_days <= 5

def test_tighter_atr_levels():
    stop, take = _risk_levels_for(100.0, 4.0)
    # 0.5*4=2% stop, 1*4=4% take
    assert abs(stop - 98.0) < 1e-6
    assert abs(take - 104.0) < 1e-6
```
Run: `cd /c/Users/mmmsm/Programming/trading-bot-crewai/crypto_council_flow && uv run pytest tests/test_short_term_caps.py -v`
Expected: both tests pass. If `_risk_levels_for` import path differs, check with `grep -rn "_risk_levels_for" src/`.

### Task 7 — Re-run the real-flow backtest and compare hold times
Run (background, ~11 min, writes to log):
```
cd /c/Users/mmmsm/Programming/trading-bot-crewai/crypto_council_flow && COUNCIL_MEMORY=false nohup uv run pytest tests/test_backtest_real_flow.py -v -s > output/backtest_short_term_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```
When done, check the plan section:
```
grep -A 10 "PORTFOLIO PLAN" output/backtest_short_term_*.log
grep "closed in" output/backtest_short_term_*.log
```
Expected: `target=` values near $500 (5% of $10k) instead of $2000, and `closed in Nd` values smaller than 30 (tighter stops/takes resolve faster). If holds are still ~30d, the max-hold guard from Task 4 may not be feeding into the plan — check `manage_portfolio` emits close for stale positions.

## Tests / validation
- Task 6 unit tests must pass before running the expensive backtest.
- Task 7 backtest confirms the behavioral change (smaller targets, shorter holds) end-to-end through the real crew.
- No unit test currently covers `max_hold_days` forcing close — add one in Task 6 if the close-forcing logic is a pure function (search `def ` around `manage_portfolio` for a candidate function to call directly).

## Risks, tradeoffs, and open questions
- Tighter stops (0.5×ATR) and 1:1 R/R will increase whipsaw: BTC's 6d stop suggests the current 1×ATR stop already gets stopped often; 0.5× will stop more frequently. Backtest result will show whether this hurts P&L.
- Reducing `max_risk_pct` to 0.5% and single-position cap to 5% means each trade risks at most ~$50 on a $10k account — probably too small to matter after fees/slippage. May want $10–25 risk per trade instead.
- Forcing close on aged positions assumes we can age positions — `open_positions_json` entries currently may lack `opened_utc`/`age_days`; Task 4 requires adding/deriving that field first.
- These are paper trades on daily OHLC; intraday stops may not be hit even if the daily high/low crosses them. The `closed in Nd` metric is daily-candle resolution, not intraday.
- Open question: is the goal shorter hold times via tighter TP/SL, or via an explicit time exit (`max_hold_days`)? This plan does both; dropping one is fine.
- Open question: should `max_hold_days` also trigger a close in `manage_portfolio` for carried paper positions across flow restarts, or only within a single backtest cycle? Task 4 as written targets the within-cycle path.
