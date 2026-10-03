"""Parallel per-coin analysis (plan Task 7: sub-15-min flow).

Workers run CouncilAnalysisCrew per coin in a thread pool; the main thread
does ALL flow-state mutation and file writes. These tests stub the crew so
no LLM or network is touched.
"""

import threading
import time
from types import SimpleNamespace

import crypto_council_flow.main as main_mod
from crypto_council_flow.main import CoinOpportunity, CryptoCouncilFlow


def _install_fake_crew(monkeypatch, tmp_path, delay: float = 0.0):
    """Replace CouncilAnalysisCrew with a fake; redirect output files to tmp."""
    monkeypatch.setenv("COUNCIL_ANALYSIS_WORKERS", "3")
    calls: list[tuple[str, int]] = []

    class _Handle:
        def kickoff(self, inputs=None):
            assert inputs is not None
            calls.append((inputs["coin_id"], threading.get_ident()))
            if delay:
                time.sleep(delay)
            if inputs["coin_id"] == "coin-bad":
                raise RuntimeError("boom")
            return SimpleNamespace(raw=f"# Analysis for {inputs['symbol']}")

    class _FakeAnalysisCrew:
        def crew(self):
            return _Handle()

    monkeypatch.setattr(main_mod, "CouncilAnalysisCrew", _FakeAnalysisCrew)
    monkeypatch.setattr(main_mod, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(main_mod, "CONSOLIDATED_RESULTS", tmp_path / "output" / "all.txt")
    return calls


def _flow_with(*coin_ids: str) -> CryptoCouncilFlow:
    flow = CryptoCouncilFlow()
    flow.state.coin_opportunities = [
        CoinOpportunity(coin_id=cid, symbol=cid.upper(), score=90 - i)
        for i, cid in enumerate(coin_ids)
    ]
    return flow


def test_parallel_merge_keeps_all_coins(tmp_path, monkeypatch):
    """Every coin's report lands in state + on disk, even with a failure."""
    _install_fake_crew(monkeypatch, tmp_path)
    flow = _flow_with("coin-0", "coin-1", "coin-2", "coin-bad")

    flow.analyse_coins()

    assert sorted(flow.state.analysed_coins) == ["coin-0", "coin-1", "coin-2"]
    assert "coin-bad" not in flow.state.analysed_coins
    assert set(flow.state.last_coin_durations_s) == {"coin-0", "coin-1", "coin-2", "coin-bad"}
    assert set(flow.state.analysis_reports) == {"coin-0", "coin-1", "coin-2"}
    for cid in ("coin-0", "coin-1", "coin-2"):
        assert (tmp_path / "output" / f"{cid}_report.md").exists()
    assert (tmp_path / "output" / "all.txt").exists()


def test_to_analyse_cap():
    from crypto_council_flow.main import CoinOpportunity, CryptoCouncilFlow

    f = CryptoCouncilFlow()
    f.state.coin_opportunities = [
        CoinOpportunity(coin_id=f"c{i}", symbol=f"C{i}", score=90 - i)
        for i in range(10)
    ]
    f.state.max_coins = 3
    assert len(f.state.coin_opportunities[: f.state.max_coins]) == 3


def test_analyse_coins_respects_max_coins(tmp_path, monkeypatch):
    """analyse_coins analyses at most max_coins, highest score first."""
    _install_fake_crew(monkeypatch, tmp_path)
    flow = _flow_with(*[f"coin-{i}" for i in range(5)])
    # invert scores so the slice must sort, not just take the head
    for i, op in enumerate(flow.state.coin_opportunities):
        op.score = i * 10
    flow.state.max_coins = 3

    flow.analyse_coins()

    assert sorted(flow.state.analysed_coins) == ["coin-2", "coin-3", "coin-4"]


def test_analyse_coins_fast_skips_portfolio_extras(tmp_path, monkeypatch):
    """Fast mode: portfolio holds outside the cap are not pulled in."""
    _install_fake_crew(monkeypatch, tmp_path)
    flow = _flow_with("coin-0", "coin-1", "coin-2")
    flow.state.max_coins = 3
    flow.state.fast = True
    flow.state.portfolio_plan = {
        "actions": [
            {"coin_id": "coin-extra", "symbol": "EXTRA", "target": 500.0},
        ]
    }

    flow.analyse_coins()

    assert "coin-extra" not in flow.state.analysed_coins
    assert len(flow.state.analysed_coins) == 3


def test_analyse_coins_fills_portfolio_extras_within_cap(tmp_path, monkeypatch):
    """Normal mode: portfolio holds fill spare slots up to the cap."""
    _install_fake_crew(monkeypatch, tmp_path)
    flow = _flow_with("coin-0", "coin-1")
    flow.state.max_coins = 3
    flow.state.fast = False
    flow.state.portfolio_plan = {
        "actions": [
            {"coin_id": "coin-extra", "symbol": "EXTRA", "target": 500.0},
            {"coin_id": "coin-overflow", "symbol": "OVER", "target": 500.0},
        ]
    }

    flow.analyse_coins()

    assert "coin-extra" in flow.state.analysed_coins
    assert "coin-overflow" not in flow.state.analysed_coins
    assert len(flow.state.analysed_coins) == 3


def test_parallel_analysis_runs_concurrently(tmp_path, monkeypatch):
    """3 coins x 0.4s of work must finish well under serial time, on >1 thread."""
    calls = _install_fake_crew(monkeypatch, tmp_path, delay=0.4)
    flow = _flow_with("coin-0", "coin-1", "coin-2")

    started = time.perf_counter()
    flow.analyse_coins()
    elapsed = time.perf_counter() - started

    assert sorted(flow.state.analysed_coins) == ["coin-0", "coin-1", "coin-2"]
    thread_ids = {tid for _, tid in calls}
    assert len(thread_ids) >= 2, f"expected >1 worker thread, got {thread_ids}"
    assert elapsed < 0.9, (
        f"expected parallel (~0.4s), took {elapsed:.2f}s (serial would be ~1.2s)"
    )
