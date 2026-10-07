# test_cycle_lock.py

> 16 nodes

## Key Concepts

- **test_cycle_lock.py** (8 connections) — `tests/test_cycle_lock.py`
- **test_cycle_lock_allows_sequential_execution()** (2 connections) — `tests/test_cycle_lock.py`
- **test_cycle_lock_prevents_concurrent_execution()** (2 connections) — `tests/test_cycle_lock.py`
- **test_lock_released_on_exception()** (2 connections) — `tests/test_cycle_lock.py`
- **test_scheduler_skips_analysis_when_scout_running_concurrently()** (2 connections) — `tests/test_cycle_lock.py`
- **test_skipped_cycle_does_not_update_timer()** (2 connections) — `tests/test_cycle_lock.py`
- **test_timer_updates_on_failure()** (2 connections) — `tests/test_cycle_lock.py`
- **test_timer_updates_on_success()** (2 connections) — `tests/test_cycle_lock.py`
- **Tests for the cycle locking mechanism in the scheduler. Verifies that: 1. No…** (1 connections) — `tests/test_cycle_lock.py`
- **Lock must be released even if the cycle raises an exception.** (1 connections) — `tests/test_cycle_lock.py`
- **Timer should update after a successful cycle.** (1 connections) — `tests/test_cycle_lock.py`
- **Timer should update even after a failed cycle (to avoid immediate retry).** (1 connections) — `tests/test_cycle_lock.py`
- **When cycle A is running, cycle B must be skipped.** (1 connections) — `tests/test_cycle_lock.py`
- **Simulate concurrent access: if scout is running (from another caller), analysis…** (1 connections) — `tests/test_cycle_lock.py`
- **Cycles that run sequentially should both complete.** (1 connections) — `tests/test_cycle_lock.py`
- **When a cycle is skipped because the lock is held, the timer must NOT advance.** (1 connections) — `tests/test_cycle_lock.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/test_cycle_lock.py`

## Audit Trail

- EXTRACTED: 15 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*