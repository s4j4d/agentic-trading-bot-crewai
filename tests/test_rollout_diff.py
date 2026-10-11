"""Tests for the Phase 7 rollout divergence differ (scripts/rollout_diff.py).

Offline only: fake JSONL rows, no network, no app import, no live key. The
point of these tests is that the differ *reports what the rows actually say* —
a synthetic divergence must fail, and an honest agreement must pass, so nobody
can later read a "pass" as more than "these two files agreed within these
tolerances".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rollout_diff as rd  # noqa: E402


def _paper_row(cycle: int, *, btc_qty: float = 0.01, equity: float = 1_000_000.0,
               trades: object = 1, ts: str = "2026-01-01T12:00:00+00:00",
               aggregate: bool = False) -> dict:
    """Shape mirrors output/portfolio_history.jsonl (what main.py writes).

    ``btc_qty == 0.0`` means flat, which the ledger expresses by keeping the
    row out of ``open_positions`` entirely rather than writing a zero.

    ``aggregate=True`` mirrors the current ledger rows, which record book size
    as counts (``n_positions``) and not as a per-coin list.

    ``trades=None`` is the corrupt case: the field is written but is not a
    number, which the differ must treat as unverifiable rather than as zero.
    """
    if aggregate:
        return {
            "ts": ts, "cycle": cycle, "source": "main", "duration_s": 7.6,
            "base_currency": "usd", "account_size": 10_000.0,
            "equity": equity, "cash_remaining": equity - 3_500.0,
            "total_target": 3_500.0, "total_exposure_pct": 35.0,
            "n_positions": 1 if btc_qty else 0, "total_pnl": 0.0,
            "realized_pnl": 0.0, "unrealized_pnl": 0.0,
            "n_trades": trades, "n_trades_scope": "cumulative",
        }
    positions = ([{"coin_id": "bitcoin", "qty": btc_qty}] if btc_qty else [])
    # ``trades`` is per-cycle here (unlike the real writer, whose ``n_trades``
    # is a cumulative ledger length), so this row carries a matching per-cycle
    # order list — see ``test_cumulative_paper_fills...`` for the case where a
    # paper row reports only a cumulative count.
    orders = ([{"coin_id": "bitcoin", "side": "buy"}
               for _ in range(trades if isinstance(trades, int) and not isinstance(trades, bool) else 0)])
    return {
        "ts": ts,
        "cycle": cycle,
        "kind": "portfolio",
        "source": "main",
        "n_positions": len(positions),
        "equity": equity,
        "n_trades": trades,
        "orders": orders,
        "pnl": {
            "equity": equity,
            "n_trades": trades,
            "open_positions": positions,
        },
    }


def _live_row(cycle: int, *, btc_qty: float = 0.01, equity: float = 1_000_000.0,
              orders: int = 1, errors: list | None = None,
              ts: str = "2026-01-01T12:00:05+00:00") -> dict:
    """Shape mirrors output/live_executions.jsonl (what apply_plan writes)."""
    positions = ([{"coin_id": "bitcoin", "qty": btc_qty}] if btc_qty else [])
    return {
        "ts": ts,
        "cycle": cycle,
        "mode": "live",
        "equity": equity,
        "n_trades": orders,
        "n_trades_scope": "per_cycle",
        "n_positions": len(positions),
        "orders": [{"coin_id": "bitcoin", "side": "buy"} for _ in range(orders)],
        "open_positions": positions,
        "errors": errors or [],
    }


def _codes(report: dict) -> list[str]:
    return [f["code"] for f in report["findings"]]


def test_aggregate_paper_rows_are_comparable():
    """The real paper row carries counts, not a per-coin list.

    The differ must still compare what it *can* (equity, book size) and say
    plainly which questions it could not answer — a "pass" here would claim
    evidence the artefacts do not contain.
    """
    report = rd.compare_cycles([_paper_row(1, aggregate=True)],
                               [_live_row(1)])
    assert report["verdict"] == "warn", "unverifiable must never read as pass"
    codes = _codes_for(report, 1)
    assert "per_coin_unverifiable" in codes
    assert "order_count_unverifiable" in codes
    assert "equity_match" in codes
    assert "equity_mismatch" not in codes
    assert "position_count_mismatch" not in codes


def test_aggregate_omitted_metric_is_not_read_as_zero(tmp_path, capsys):
    """An aggregate paper row has no fill count, so order-volume divergence
    must be declared unverifiable rather than counted as 0-vs-1 (a fake fail)."""
    history = tmp_path / "portfolio_history.jsonl"
    live = tmp_path / "live_executions.jsonl"
    history.write_text(json.dumps(_paper_row(1, aggregate=True)) + "\n",
                       encoding="utf-8")
    live.write_text(json.dumps(_live_row(1, orders=1)) + "\n", encoding="utf-8")

    assert rd.main(["--output-dir", str(tmp_path)]) == 1, \
        "an unverifiable report is not a pass"
    text = capsys.readouterr().out
    assert "FAIL" not in text, "a missing metric must not manufacture a failure"
    assert "WARN" in text
    assert "order_count_unverifiable" in text


def test_per_coin_unverifiable_is_stated_not_passed():
    report = rd.compare_cycles([_paper_row(1, aggregate=True)],
                               [_live_row(1)])
    finding = next(f for f in report["findings"]
                   if f["code"] == "per_coin_unverifiable")
    assert finding["severity"] == "warn"
    assert "unverifiable" in finding["message"]


def test_position_count_mismatch_fails():
    """Live submitted 2 orders while paper recorded 1 fill — the always
    available check must catch it."""
    report = rd.compare_cycles([_paper_row(1, btc_qty=0.01, trades=1)],
                               [_live_row(1, orders=2)])
    assert report["verdict"] == "fail"
    assert "order_count_mismatch" in _codes_for(report, 1)


def test_risk_tick_rows_are_not_cycles():
    """portfolio_history.jsonl mixes heartbeats with cycles; counting both
    would compare a heartbeat as if it were a portfolio cycle."""
    heartbeat = {"ts": "2026-01-02T00:05:00+00:00", "cycle": 99,
                 "source": "risk_tick", "duration_s": 0.0, "equity": 1e6}
    report = rd.compare_cycles([_paper_row(1), heartbeat], [_live_row(1)])
    assert report["cycles_compared"] == [1], "heartbeats are not cycles"
    assert report["cycles_paper_only"] == [], "a heartbeat is not a missing cycle"


def test_reused_cycle_number_is_reported_not_silently_dropped():
    """The cycle counter restarts on every bot run, so ``portfolio_history``
    legitimately reuses numbers.

    The join must pick one row deterministically, but the rows it did *not*
    compare are invisible evidence — dropping them silently is how a false pass
    gets manufactured, so the reuse itself must surface as a finding.
    """
    older = _paper_row(1, btc_qty=0.09, trades=9, ts="2026-10-07T14:40:03+00:00")
    newer = _paper_row(1, btc_qty=0.01, trades=1, ts="2026-10-08T12:54:33+00:00")
    report = rd.compare_cycles([older, newer], [_live_row(1, ts="2026-10-08T12:54:33+00:00")])

    codes = _codes_for(report, 1)
    assert "cycle_number_reused" in codes, "a silent drop must never happen"
    finding = next(f for f in report["findings"] if f["code"] == "cycle_number_reused")
    assert finding["severity"] == "warn"
    assert finding["detail"]["occurrences"] == 2
    assert "2" in finding["message"]

    # The comparison itself must use the newest write, not the stale one:
    # 0.09 vs 0.01 would be a qty_mismatch, and that difference is real, but it
    # is not the cycle live just ran.
    assert "qty_mismatch" not in codes
    assert report["verdict"] in ("pass", "warn")


def test_cycle_number_reuse_is_reported_for_live_too():
    """Neither artefact is exempt: a reused number hides evidence either way."""
    report = rd.compare_cycles(
        [_paper_row(1)],
        [_live_row(1, ts="2026-10-07T14:40:03+00:00"),
         _live_row(1, ts="2026-10-08T12:54:33+00:00")],
    )
    finding = next(f for f in report["findings"] if f["code"] == "cycle_number_reused")
    assert finding["detail"]["artefact"] == "live"
    assert finding["detail"]["occurrences"] == 2


def test_reused_cycle_number_does_not_mask_a_real_divergence():
    """Reuse is a warn, never an excuse: if the *latest* rows genuinely
    disagree, the report must still fail."""
    older = _paper_row(1, ts="2026-10-07T14:40:03+00:00")
    report = rd.compare_cycles([older, _paper_row(1, ts="2026-10-08T12:54:33+00:00")],
                               [_live_row(1, btc_qty=0.5, ts="2026-10-08T12:54:33+00:00")])
    assert report["verdict"] == "fail", "a real qty gap must outrank the reuse note"
    assert "qty_mismatch" in _codes_for(report, 1)
    assert "cycle_number_reused" in _codes_for(report, 1)


def test_cumulative_paper_fills_are_not_compared_to_per_cycle_orders():
    """Paper ``n_trades`` is ``len(ledger)`` — every fill since the bot
    started — while live ``orders`` is only this cycle's attempts.

    Comparing the two directly would flag cycle 400 as wildly divergent purely
    because paper has been running all day. Different units, so the honest
    answer is "unverifiable", never a pass and never a manufactured fail.
    """
    paper = _paper_row(20, trades=137, btc_qty=0.01)
    # The real writer records the cumulative ledger length and no order list;
    # strip the fixture's per-cycle list so the row has the real shape.
    del paper["orders"]

    report = rd.compare_cycles([paper], [_live_row(20, btc_qty=0.01, orders=1)])
    codes = _codes_for(report, 20)
    assert "order_count_unverifiable" in codes
    assert "order_count_mismatch" not in codes
    assert report["verdict"] == "warn", "unverifiable must not read as pass"
    msg = next(f["message"] for f in report["findings"]
               if f["code"] == "order_count_unverifiable")
    assert "different units" in msg


def test_per_cycle_counts_are_still_compared_when_both_are_per_cycle():
    """The guard above must not blind the differ: when a paper row *does*
    carry a per-cycle order list, the counts are comparable and a real
    divergence must still fail."""
    paper = _paper_row(1, trades=1)
    paper["orders"] = [{"coin_id": "bitcoin", "side": "buy"}]

    ok = rd.compare_cycles([paper], [_live_row(1, orders=1)])
    assert "order_count_match" in _codes_for(ok, 1)
    bad = rd.compare_cycles([paper], [_live_row(1, orders=2)])
    assert "order_count_mismatch" in _codes_for(bad, 1)
    assert bad["verdict"] == "fail"


def test_explicit_scope_marker_overrides_the_shape_guess():
    """``n_trades_scope`` wins over inferring units from the row's shape.

    The shape guess is a heuristic and heuristics break silently. If a row says
    what its number means, that is the answer — otherwise a future schema change
    that adds an ``orders`` list to a cumulative row would flip a safe
    "unverifiable" into a fabricated ``order_count_mismatch``.
    """
    paper = _paper_row(7, trades=137)
    # Both markers present and contradictory: an order list (which otherwise
    # reads as per-cycle) *and* an explicit cumulative scope statement.
    paper["orders"] = [{"coin_id": "bitcoin", "side": "buy"}]
    paper["n_trades_scope"] = "cumulative"

    report = rd.compare_cycles([paper], [_live_row(7, orders=1)])
    codes = _codes_for(report, 7)
    assert "order_count_unverifiable" in codes
    assert "order_count_mismatch" not in codes, \
        "an explicit scope marker must not be overridden by the shape guess"

    live = _live_row(8, orders=5)
    live["n_trades_scope"] = "per_cycle"
    per_cycle = rd.compare_cycles([_paper_row(8, trades=5)], [live])
    assert "order_count_mismatch" not in _codes_for(per_cycle, 8), \
        "an explicit per_cycle marker makes two same-unit counts comparable"


def test_cycle_row_picks_name_the_row_the_join_kept():
    """A collision count is not auditable on its own.

    "68 rows reuse cycle 1" tells a reader rows were dropped, but not *which*
    row survived — and the kept row might be a stray risk snapshot rather than
    the portfolio cycle the join actually compared. The finding has to name it.
    """
    rows = [_paper_row(1, ts="2026-01-01T12:00:00+00:00", btc_qty=0.01),
            _paper_row(1, ts="2026-01-01T12:05:00+00:00", btc_qty=0.0),
            _paper_row(1, ts="2026-01-01T12:09:00+00:00", btc_qty=0.02)]

    report = rd.compare_cycles(rows, [])
    findings = [f for f in report["findings"]
                if f["code"] == "cycle_number_reused"]
    assert findings, "reused cycle numbers must be reported, not deduped silently"
    pick = findings[0].get("detail", {})
    assert pick, "the kept row must be identified so the comparison is auditable"

    # File order, latest arrival wins — never re-sorted, never a coin flip.
    assert pick["kept"]["ts"] == "2026-01-01T12:09:00+00:00"
    assert pick["kept"]["n_positions"] == 1
    # The dropped rows are named too, so a reader can check the keep was right
    # rather than being told a count and asked to trust it.
    assert [d["ts"] for d in pick["dropped"]] == [
        "2026-01-01T12:00:00+00:00",
        "2026-01-01T12:05:00+00:00",
    ]


def test_book_size_falls_back_to_the_position_list_before_giving_up():
    """A count is always better available than it looks.

    ``n_positions`` is a summary field a writer may omit, but a per-coin list
    of the same length is a real claim about the book. Reading the length keeps
    the check producing signal against live rows that only ever carry a list —
    the alternative is declaring the book size unverifiable forever.
    """
    paper = _paper_row(3, btc_qty=0.0)
    del paper["n_positions"]
    live = _live_row(3, btc_qty=0.01)

    report = rd.compare_cycles([paper], [live])
    codes = _codes_for(report, 3)
    assert "position_count_mismatch" in codes, \
        "an omitted count must not silently disable the book-size check"
    assert "position_count_unavailable" not in codes


def test_book_size_unverifiable_only_when_neither_side_says():
    """Giving up must require that there is genuinely nothing to compare."""
    paper = _paper_row(4, btc_qty=0.0)
    del paper["n_positions"]
    live = _live_row(4, btc_qty=0.0)
    del live["n_positions"]
    del live["open_positions"]

    report = rd.compare_cycles([paper], [live])
    assert "position_count_unavailable" in _codes_for(report, 4)


def _codes_for(report: dict, cycle: int) -> list[str]:
    return [f["code"] for f in report["findings"] if f.get("cycle") == cycle]


# --------------------------------------------------------------------------
# agreement
# --------------------------------------------------------------------------

def test_matching_cycles_pass():
    report = rd.compare_cycles([_paper_row(1), _paper_row(2)],
                               [_live_row(1), _live_row(2)])
    assert report["verdict"] == "pass"
    assert report["cycles_compared"] == [1, 2]
    assert "qty_match" in _codes_for(report, 1)
    assert "equity_match" in _codes_for(report, 2)
    assert "order_count_match" in _codes_for(report, 1)


def test_qty_within_tolerance_passes():
    """0.1% apart on a 0.01 BTC holding is well inside the 2% default."""
    report = rd.compare_cycles([_paper_row(1, btc_qty=0.01)],
                               [_live_row(1, btc_qty=0.01001)])
    assert report["verdict"] == "pass"
    assert "qty_match" in _codes_for(report, 1)


def test_no_trades_in_either_cycle_is_not_a_divergence():
    """A cycle where both modes did nothing must not be flagged."""
    report = rd.compare_cycles([_paper_row(1, trades=0, btc_qty=0.0)],
                               [_live_row(1, orders=0, btc_qty=0.0)])
    assert report["verdict"] == "pass"
    assert "order_count_mismatch" not in _codes_for(report, 1)


# --------------------------------------------------------------------------
# divergence
# --------------------------------------------------------------------------

def test_qty_mismatch_fails_with_numbers():
    report = rd.compare_cycles([_paper_row(1, btc_qty=0.01)],
                               [_live_row(1, btc_qty=0.005)])
    assert report["verdict"] == "fail"
    finding = next(f for f in report["findings"] if f["code"] == "qty_mismatch")
    assert finding["cycle"] == 1
    # The detail must be enough to re-derive the verdict, not just believe it.
    assert finding["detail"]["paper_qty"] == pytest.approx(0.01)
    assert finding["detail"]["live_qty"] == pytest.approx(0.005)


def test_order_count_mismatch_fails():
    """Live attempting 3 orders when paper filled 1 is the classic silent
    partial-run failure this tool exists to catch."""
    report = rd.compare_cycles([_paper_row(1, trades=1)],
                               [_live_row(1, orders=3)])
    assert report["verdict"] == "fail"
    assert "order_count_mismatch" in _codes_for(report, 1)


def test_equity_mismatch_fails():
    report = rd.compare_cycles([_paper_row(1, equity=1_000_000.0)],
                               [_live_row(1, equity=500_000.0)])
    assert report["verdict"] == "fail"
    assert "equity_mismatch" in _codes_for(report, 1)


# --------------------------------------------------------------------------
# missing evidence is a finding, never agreement
# --------------------------------------------------------------------------

def test_paper_only_cycle_fails():
    """The live run never logged a cycle paper did — never treat as agreement."""
    report = rd.compare_cycles([_paper_row(1), _paper_row(2)],
                               [_live_row(1)])
    assert report["verdict"] == "fail"
    assert report["cycles_paper_only"] == [2]
    assert "paper_only_cycle" in _codes_for(report, 2)


def test_live_only_cycle_warns_but_does_not_fail():
    report = rd.compare_cycles([_paper_row(1)], [_live_row(1), _live_row(2)])
    assert report["verdict"] == "warn"
    assert report["cycles_live_only"] == [2]
    assert "live_only_cycle" in _codes_for(report, 2)


def test_no_usable_cycles_fails():
    report = rd.compare_cycles([], [])
    assert report["verdict"] == "fail"
    assert "no_cycles_at_all" in _codes(report)


def test_corrupt_history_line_is_reported_not_skipped(tmp_path):
    path = tmp_path / "portfolio_history.jsonl"
    path.write_text(
        json.dumps(_paper_row(1)) + "\n{not json\n", encoding="utf-8")
    rows, problems = rd._read_jsonl(path)
    assert len(rows) == 1, "the good row still parses"
    assert any("not valid JSON" in p for p in problems), \
        "the bad line must be visible, never silently dropped"


def test_missing_file_is_a_bare_not_found_note(tmp_path):
    rows, problems = rd._read_jsonl(tmp_path / "nope.jsonl")
    assert rows == []
    assert any("file not found" in p for p in problems)


# --------------------------------------------------------------------------
# honest ambiguities
# --------------------------------------------------------------------------

def test_one_sided_holding_warns():
    """Paper says it holds bitcoin, and the live row never mentions the coin:
    the differ must not pass that off as verified agreement."""
    paper = {"cycle": 1, "source": "main", "equity": 1e6, "n_positions": 1,
             "open_positions": [{"coin_id": "bitcoin", "qty": 0.01}]}
    live = {"cycle": 1, "equity": 1e6, "n_positions": 0, "open_positions": []}
    report = rd.compare_cycles([paper], [live])
    assert report["verdict"] == "fail"
    assert "qty_mismatch" in _codes_for(report, 1)


def test_both_flat_is_agreement_not_a_divergence():
    """A zero is a real position state, not missing evidence — 0 paper and 0
    live for the same coin must read as agreement, not as one-sided."""
    rows_paper = [{"cycle": 1, "source": "main", "equity": 1e6, "n_positions": 0,
                   "n_trades": 0, "orders": [],
                   "open_positions": [{"coin_id": "bitcoin", "qty": 0.0}]}]
    rows_live = [{"cycle": 1, "equity": 1e6, "n_positions": 0, "n_trades": 0,
                  "orders": [],
                  "open_positions": [{"coin_id": "bitcoin", "qty": 0.0}]}]
    report = rd.compare_cycles(rows_paper, rows_live)
    assert report["verdict"] == "pass", "both flat must never look like a divergence"
    assert "qty_mismatch" not in _codes_for(report, 1)


def test_flat_against_nonzero_fails():
    """Live never bought what paper holds — the classic silent gap."""
    rows_paper = [{"cycle": 1, "source": "main", "equity": 1e6, "n_positions": 1,
                   "open_positions": [{"coin_id": "bitcoin", "qty": 0.01}]}]
    rows_live = [{"cycle": 1, "equity": 1e6, "n_positions": 0,
                  "open_positions": []}]
    report = rd.compare_cycles(rows_paper, rows_live)
    assert report["verdict"] == "fail"
    assert "qty_mismatch" in _codes_for(report, 1)
    assert "position_count_mismatch" in _codes_for(report, 1)


def test_coin_present_on_one_side_only_is_a_failure():
    """When the other artefact never mentions the coin, the missing side must
    read as 0 — a position neither mode bought is still a real disagreement."""
    rows_paper = [{"cycle": 1, "source": "main", "equity": 1e6, "n_positions": 1,
                   "open_positions": [{"coin_id": "bitcoin", "qty": 0.01}]}]
    rows_live = [{"cycle": 1, "equity": 1e6, "n_positions": 0,
                  "open_positions": []}]
    report = rd.compare_cycles(rows_paper, rows_live)
    finding = next(f for f in report["findings"] if f["code"] == "qty_mismatch")
    assert finding["detail"]["paper_qty"] == pytest.approx(0.01)
    assert finding["detail"]["live_qty"] is None, "live said nothing about it"


def test_missing_equity_warns_rather_than_passes_on_zero():
    report = rd.compare_cycles([_paper_row(1, equity=10**9)],
                               [_live_row(1, equity=0.0)])
    assert "equity_mismatch" in _codes_for(report, 1)


def test_guard_refusals_are_surfaced_as_warnings():
    errors = [{"error": "5.0% single-position cap exceeded"}]
    report = rd.compare_cycles([_paper_row(1)],
                               [_live_row(1, errors=errors)])
    codes = _codes_for(report, 1)
    assert "live_errors_present" in codes
    finding = next(f for f in report["findings"]
                   if f["code"] == "live_errors_present")
    assert finding["severity"] == "warn"
    assert "1" in finding["message"], "the count must be in the message"


# --------------------------------------------------------------------------
# CLI contract
# --------------------------------------------------------------------------

def test_cli_exit_code_reflects_verdict(tmp_path, capsys):
    history = tmp_path / "portfolio_history.jsonl"
    live = tmp_path / "live_executions.jsonl"
    history.write_text(json.dumps(_paper_row(1)) + "\n", encoding="utf-8")
    live.write_text(json.dumps(_live_row(1, btc_qty=0.02)) + "\n", encoding="utf-8")

    rc = rd.main(["--output-dir", str(tmp_path)])
    assert rc == 1, "a real divergence must exit non-zero"
    assert "verdict" in capsys.readouterr().out


def test_cli_json_mode_is_machine_readable(tmp_path, capsys):
    history = tmp_path / "portfolio_history.jsonl"
    live = tmp_path / "live_executions.jsonl"
    history.write_text(json.dumps(_paper_row(1)) + "\n", encoding="utf-8")
    live.write_text(json.dumps(_live_row(1)) + "\n", encoding="utf-8")

    assert rd.main(["--output-dir", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "pass"
    assert isinstance(payload["findings"], list)


def test_cli_missing_output_dir_is_usage_error(tmp_path, capsys):
    rc = rd.main(["--output-dir", str(tmp_path / "does_not_exist")])
    assert rc == 2
    assert "output directory not found" in capsys.readouterr().err


def test_cli_fails_when_live_artefact_is_absent(tmp_path, capsys):
    """Live has never run: that is a FAIL with a stated reason, not a pass."""
    history = tmp_path / "portfolio_history.jsonl"
    history.write_text(json.dumps(_paper_row(1)) + "\n", encoding="utf-8")

    rc = rd.main(["--output-dir", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "live_executions.jsonl" in err
    assert "file not found" in err


def test_text_report_states_the_verdict_plainly():
    report = rd.compare_cycles([_paper_row(1, btc_qty=0.01)],
                               [_live_row(1, btc_qty=0.001)])
    text = rd.format_text(report)
    assert "FAIL" in text
    assert "qty_mismatch" in text


def test_text_report_never_prints_pass_for_a_failing_run():
    report = rd.compare_cycles([_paper_row(1)], [_live_row(1, orders=3)])
    text = rd.format_text(report)
    assert "FAIL" in text
    # No passing line to skim past: `format_text` only lists fails and warns.
    assert text.count("[PASS]") == 0


def test_problems_demote_the_verdict_in_both_report_formats(tmp_path, capsys):
    """An unreadable artefact must fail in the text report *and* the JSON one.

    If one output were demoted after the fact and the other computed its verdict
    independently, the two reports would agree only by luck of ordering — and a
    CI gate reading the exit code would trust whichever one happened to be
    right. Both must come from the same state.
    """
    history = tmp_path / "portfolio_history.jsonl"
    history.write_text("{not valid json\n", encoding="utf-8")

    rc = rd.main(["--output-dir", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    assert "Traceback" not in out
    payload = json.loads(out[out.index("{"):])  # skip the stderr problem lines
    assert payload["verdict"] == "fail"
    assert payload["problems"], "the unreadable artefact must be reported"
    # The read failure also leaves zero cycles, which is a real finding on its
    # own — the point is that the problems are reported alongside it, not that
    # the count of findings is zero.
    assert payload["counts"]["fail"] >= 1

    capsys.readouterr()
    rc = rd.main(["--output-dir", str(tmp_path)])
    text = capsys.readouterr().out
    assert "FAIL" in text, "text and JSON must not disagree on the verdict"
    assert rc == 1


def test_cli_survives_a_corrupt_fill_count(tmp_path, capsys):
    """A malformed field must not be what stops the tool.

    ``n_trades`` is machine-written, but a truncated write, a schema change, or
    a hand-edited artefact can leave a string, a null or a list where a number
    belongs. That field alone becomes unverifiable; the rest of the comparison
    still runs, through the CLI path, with a summary printed either way.
    """
    history = tmp_path / "portfolio_history.jsonl"
    history.write_text(
        json.dumps(_paper_row(1, trades="n/a")) + "\n", encoding="utf-8")
    (tmp_path / "live_executions.jsonl").write_text(
        json.dumps(_live_row(1)) + "\n", encoding="utf-8")

    rc = rd.main(["--output-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Traceback" not in out
    # The unverifiable field is reported, and the report says so — the tool did
    # not die and did not quietly skip the row.
    assert "order_count_unverifiable" in out
    assert "WARN" in out
    # The rest of the comparison still ran: equity is comparable, so it was
    # compared. A passing line is deliberately not printed, so the report
    # cannot be skimmed into a false reading of agreement.
    assert "equity_mismatch" not in out
    # Exit code is 1, not 0: an unverifiable comparison must not read as
    # "paper and live agree" to anything gating on the exit status.
    assert rc == 1


def test_cli_corrupt_fill_count_is_zero_not_silence_when_live_has_orders(
        tmp_path, capsys):
    """Corrupt must not be coerced into 0-vs-N, which would manufacture a fail.

    A paper row whose fill count is unreadable and a live row that placed
    orders is *unverifiable*, not a divergence: the paper side never claimed a
    count, so there is nothing to disagree with.
    """
    history = tmp_path / "portfolio_history.jsonl"
    history.write_text(
        json.dumps(_paper_row(1, trades=None)) + "\n", encoding="utf-8")
    (tmp_path / "live_executions.jsonl").write_text(
        json.dumps(_live_row(1, orders=2)) + "\n", encoding="utf-8")

    rc = rd.main(["--output-dir", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    assert "Traceback" not in out
    payload = json.loads(out)
    assert payload["verdict"] == "warn"
    codes = {f["code"] for f in payload["findings"]}
    assert "order_count_unverifiable" in codes
    assert "order_count_mismatch" not in codes
    assert rc == 1
