"""
Tests for the scheduler cadence knobs exposed as env vars.

Covers:
  - _env_seconds parsing (bare seconds, duration suffixes, junk, clamping)
  - the module-level constants default to the documented values
  - an env override changes the resolved interval
  - _fmt_next_runs reports each step's real remaining time
"""

import importlib

import pytest

import crypto_council_flow.main as main_mod


# ---------------------------------------------------------------------------
# _env_seconds parsing
# ---------------------------------------------------------------------------

class TestEnvSeconds:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("7200", 7200),      # bare seconds
            ("30m", 1800),
            ("2h", 7200),
            ("90s", 90),
            ("1.5h", 5400),
            ("1d", 86400),
            ("  30M  ", 1800),   # whitespace + uppercase
            ("3600", 3600),
        ],
    )
    def test_parses_valid_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv("PROBE_KNOB", raw)
        assert main_mod._env_seconds("PROBE_KNOB", 999) == expected

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_falls_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("PROBE_KNOB", raw)
        assert main_mod._env_seconds("PROBE_KNOB", 999) == 999

    def test_unset_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("PROBE_KNOB", raising=False)
        assert main_mod._env_seconds("PROBE_KNOB", 999) == 999

    def test_junk_falls_back_to_default(self, monkeypatch):
        """A typo must not crash the scheduler or pick a wild cadence."""
        monkeypatch.setenv("PROBE_KNOB", "garbage")
        assert main_mod._env_seconds("PROBE_KNOB", 999) == 999

    @pytest.mark.parametrize("raw", ["inf", "-inf", "Inf", "Infinity", "1e309", "1e400", "nan", "NaN"])
    def test_non_finite_falls_back_to_default(self, monkeypatch, raw):
        """float() accepts these but int() raises OverflowError/ValueError.

        _env_seconds runs at module import, so an unguarded raise here
        takes the whole bot down over one bad .env value.
        """
        monkeypatch.setenv("PROBE_KNOB", raw)
        assert main_mod._env_seconds("PROBE_KNOB", 999) == 999

    def test_negative_and_zero_clamp_to_one_second(self, monkeypatch):
        monkeypatch.setenv("PROBE_KNOB", "0s")
        assert main_mod._env_seconds("PROBE_KNOB", 999) == 1
        monkeypatch.setenv("PROBE_KNOB", "-5")
        assert main_mod._env_seconds("PROBE_KNOB", 999) == 1


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestCadenceConstants:
    def test_documented_defaults(self, monkeypatch):
        """With the cadence env vars unset, fall back to 2h / 5m / 60s."""
        for var in (
            "COUNCIL_SCOUT_INTERVAL_S",
            "COUNCIL_ANALYSIS_INTERVAL_S",
            "COUNCIL_RISK_TICK_INTERVAL_S",
        ):
            monkeypatch.delenv(var, raising=False)
        reloaded = importlib.reload(main_mod)
        try:
            assert reloaded.SCOUT_INTERVAL_SECONDS == 2 * 60 * 60
            assert reloaded.ANALYSIS_INTERVAL_SECONDS == 5 * 60
            assert reloaded.RISK_TICK_INTERVAL_SECONDS == 60
        finally:
            monkeypatch.undo()
            importlib.reload(main_mod)

    @pytest.mark.parametrize(
        "var,attr,raw,expected",
        [
            ("COUNCIL_SCOUT_INTERVAL_S", "SCOUT_INTERVAL_SECONDS", "30m", 1800),
            ("COUNCIL_ANALYSIS_INTERVAL_S", "ANALYSIS_INTERVAL_SECONDS", "1m", 60),
            ("COUNCIL_RISK_TICK_INTERVAL_S", "RISK_TICK_INTERVAL_SECONDS", "2h", 7200),
        ],
    )
    def test_env_override_changes_interval(
        self, monkeypatch, var, attr, raw, expected
    ):
        monkeypatch.setenv(var, raw)
        reloaded = importlib.reload(main_mod)
        try:
            assert getattr(reloaded, attr) == expected
        finally:
            monkeypatch.undo()
            importlib.reload(main_mod)


# ---------------------------------------------------------------------------
# _fmt_next_runs
# ---------------------------------------------------------------------------

class TestFmtNextRuns:
    def test_reports_due_now(self, monkeypatch):
        monkeypatch.setenv("COUNCIL_SCOUT_INTERVAL_S", "2h")
        reloaded = importlib.reload(main_mod)
        try:
            now = 1_000_000.0
            # Each step last ran LONGER ago than its own interval, so all
            # three are due immediately on the next scheduler tick.
            out = reloaded._fmt_next_runs(
                now,
                last_scout=now - 7201,
                last_analysis=now - 301,
                last_risk_tick=now - 61,
            )
        finally:
            monkeypatch.undo()
            importlib.reload(main_mod)
        assert "scout due now" in out
        assert "analysis due now" in out
        assert "risk tick due now" in out

    def test_reports_remaining_time_per_step(self, monkeypatch):
        monkeypatch.setenv("COUNCIL_SCOUT_INTERVAL_S", "2h")
        reloaded = importlib.reload(main_mod)
        try:
            now = 1_000_000.0
            out = reloaded._fmt_next_runs(
                now,
                last_scout=now,                      # just ran -> full interval left
                last_analysis=now - 240,             # 300s interval -> 60s left
                last_risk_tick=now - 30,             # 60s interval -> 30s left
            )
        finally:
            monkeypatch.undo()
            importlib.reload(main_mod)
        # scout has ~2h left; analysis and risk tick are counted down
        assert "2h" in out
        assert "1m 00s" in out
        assert "30s" in out
        assert "due now" not in out


# ---------------------------------------------------------------------------
# CrewAI memory env name
# ---------------------------------------------------------------------------

class TestMemoryEnvName:
    """COUNCIL_MEMORY is the documented name (README, commit e635a87).

    USE_MEMORY is accepted as an alias so a deployment setting either name
    gets the same behaviour; renaming silently turned memory off.
    """

    @staticmethod
    def _flag(monkeypatch, **env):
        for k, v in env.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
        import importlib

        mod = importlib.reload(
            importlib.import_module("crypto_council_flow.crews.council.council_crew")
        )
        return mod._USE_MEMORY

    def test_default_is_off(self, monkeypatch):
        assert self._flag(monkeypatch, COUNCIL_MEMORY=None, USE_MEMORY=None) is False

    def test_council_memory_name_enables(self, monkeypatch):
        assert self._flag(monkeypatch, COUNCIL_MEMORY="true", USE_MEMORY=None) is True

    def test_use_memory_alias_still_works(self, monkeypatch):
        assert self._flag(monkeypatch, COUNCIL_MEMORY=None, USE_MEMORY="true") is True

    def test_council_memory_false_wins_over_alias(self, monkeypatch):
        assert self._flag(monkeypatch, COUNCIL_MEMORY="false", USE_MEMORY="true") is False
