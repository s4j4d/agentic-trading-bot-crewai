#!/usr/bin/env python3
"""Rollout divergence differ: paper vs. live, cycle by cycle.

Phase 7 of the execution plan runs ``EXECUTION_MODE=live`` in parallel with
paper for N cycles before the main loop is cut over. The question that
decides the cutover is not "did it work?" but "**how far apart are the two,
and is every difference explained?**" — a live run that quietly fills half
the orders paper filled is a problem even when nothing errored.

This script answers that from the two artefacts the flow already writes:

  paper  ->  ``output/portfolio_history.jsonl``   (one row per real cycle)
  live   ->  ``output/live_executions.jsonl``     (one row per real cycle)

Both rows carry ``cycle``, so they are joined on it. Nothing here needs the
network, credentials, or the app: this is a pure files-in / report-out tool
that a human (or a nightly job) can run without touching a live key.

Honesty rules (the whole point of the tool):

* A difference is only reported when it is *evidenced*. Missing rows are
  themselves a finding ("live never recorded cycle 7"), never treated as
  agreement.
* Divergence is reported with the numbers that caused it, so a reader can
  re-derive the verdict instead of trusting it.
* The tool never reads secrets and never places orders.
* A field that is present but not a number ("n_trades": null, "n/a", a list)
  is *corrupt*, not zero and not absent: it makes that one check unverifiable
  rather than being coerced into a comparison.

Usage::

    python scripts/rollout_diff.py [--output-dir output] [--json]

Exit codes: 0 when the tolerated tolerances hold, 1 otherwise, 2 on usage or
I/O problems. With ``--json`` the report is emitted as a single JSON object
so it can be diffed between runs.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

PAPER_HISTORY_NAME = "portfolio_history.jsonl"
LIVE_EXECUTIONS_NAME = "live_executions.jsonl"

# A coin may be in one book and not the other without it being a bug (paper
# holds a coin the venue never bought, or a sell closed it first). What
# matters is whether the *sides and sizes agree* where both have an opinion,
# so the differ compares per-coin quantities and flags real disagreements.

# Tolerances. Defaults are deliberately loose for a first parallel run: the
# goal is to surface the big, obvious failures first, then tighten.
DEFAULT_MAX_QTY_REL_TOL = 0.02   # 2% quantity agreement per coin
DEFAULT_MAX_CASH_REL_TOL = 0.05  # 5% cash/equity agreement
DEFAULT_MAX_ORDER_COUNT = 0     # exact order-count agreement by default

VERDICT_KEYS = ("pass", "warn", "fail")


def _num(value: Any, default: float = 0.0) -> float:
    """float() that never raises — artefact fields are machine-written but not
    guaranteed present or numeric."""
    try:
        if isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _finite(value: Any, default: Any = None) -> Any:
    """A readable float, or ``default`` when the value is not a finite number.

    Artefact fields are machine-written, but a null, a string, or a list where
    a number belongs is still possible (a crash mid-write, an older writer, a
    schema drift). Those are *corrupt*, which is a third answer distinct from
    both "absent" and "zero" — the differ must say it cannot use the field
    rather than silently coercing it into one of the two real answers.

    NaN/Infinity are excluded too: they would survive a comparison as numbers
    and then detonate on the first ``int()``/format.
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a JSONL file, reporting unreadable lines instead of hiding them.

    Returns ``(rows, problems)``. A missing file must be visible rather than
    silently read as empty, since an artefact that cannot be read must never
    be mistaken for one that agreed.
    """
    if not path.exists():
        return [], [f"{path.name}: file not found"]
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], [f"{path.name}: unreadable ({exc})"]
    for i, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}:{i}: not valid JSON ({exc.msg})")
            continue
        if not isinstance(obj, dict):
            problems.append(f"{path.name}:{i}: expected an object, got {type(obj).__name__}")
            continue
        rows.append(obj)
    return rows, problems


def _rows_from_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a JSONL file into *real-cycle* rows, reporting problems.

    Two things must never be true:

    * a missing file must be visible (it is the normal state before live has
      ever run, and silently reading it as empty would pass on no evidence),
    * a ``risk_tick`` heartbeat must never be counted as a cycle.
    """
    all_rows, problems = _read_jsonl(path)
    return [r for r in all_rows if _is_real_cycle(r)], problems


def _duplicate_cycle_numbers(rows: list[dict[str, Any]]) -> dict[int, int]:
    """Cycle numbers that appear on more than one row, and how often.

    ``portfolio_cycle`` counts from 1 again after every bot restart, so a
    history that spans restarts legitimately reuses numbers. That is not a
    defect in the artefact, but it is a defect in a *join on cycle number*:
    one of those rows would have to be dropped, and dropping a row is the one
    thing this tool must never do silently.

    The count alone is not enough to audit the pick — a reader cannot tell
    from "68×" whether the row the join kept was the portfolio cycle live
    actually ran against or a stray trailing row (a zero-duration risk
    snapshot, an empty-book de-risking write). Use
    :func:`_cycle_row_picks` for that.

    Note both artefacts reuse numbers, and the number of collisions is a fact
    about the artefact, not about the run: a report claiming "49" is stale
    the moment another row is appended, so the live count must always be
    recomputed.
    """
    seen: dict[int, int] = {}
    for row in rows:
        key = _cycle_key(row)
        if key is not None:
            seen[key] = seen.get(key, 0) + 1
    return {cycle: n for cycle, n in seen.items() if n > 1}


def _cycle_row_picks(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Per reused cycle number: which row the join kept, and what it dropped.

    File order is the arrival order, so the last row in file order for a cycle
    number is the one that gets compared. Reporting *which* rows were dropped
    (their timestamps and book size) is what makes an audit of the join
    possible: a count tells the reader how many rows were lost, but only the
    actual rows can show that the kept one was the portfolio cycle rather than
    a trailing snapshot of the same number.
    """
    by_cycle: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if not _is_real_cycle(row):
            continue
        key = _cycle_key(row)
        if key is not None:
            by_cycle.setdefault(key, []).append(row)
    picks: dict[int, dict[str, Any]] = {}
    for cycle, cycle_rows in by_cycle.items():
        if len(cycle_rows) < 2:
            continue
        kept = cycle_rows[-1]
        picks[cycle] = {
            "kept": {
                "ts": kept.get("ts"),
                "duration_s": kept.get("duration_s"),
                "n_positions": _aggregate(kept, "n_positions"),
                "equity": _aggregate(kept, "equity"),
            },
            "dropped": [
                {
                    "ts": r.get("ts"),
                    "duration_s": r.get("duration_s"),
                    "n_positions": _aggregate(r, "n_positions"),
                    "equity": _aggregate(r, "equity"),
                }
                for r in cycle_rows[:-1]
            ],
        }
    return picks


def _index_rows(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Collapse rows to one entry per cycle number, keeping the latest write.

    ``risk_tick`` heartbeats are dropped first — they are not cycles, and
    comparing one against nothing would report a divergence that does not
    exist.

    The join needs a single row per cycle, and the artefact is append-only, so
    the last row in file order for a cycle number is the most recent state of
    that cycle. Rows are **not** re-sorted: file order is the arrival order,
    and re-sorting could silently demote a row that actually ran later.

    Collapsing is only safe because
    :func:`_duplicate_cycle_numbers` reports what was dropped — collapsing
    without saying so would hide evidence, which is the one thing this tool
    must never do.
    """
    by_cycle: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not _is_real_cycle(row):
            continue
        key = _cycle_key(row)
        if key is not None:
            by_cycle[key] = row
    return by_cycle


def _cycle_key(row: dict[str, Any]) -> int | None:
    for key in ("portfolio_cycle", "cycle"):
        if key in row:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                continue
    return None


def _position_sources(row: dict[str, Any]) -> list[Any]:
    """Where a row might keep its ``open_positions`` list.

    Two shapes exist in practice and both are supported:

    * the paper history row keeps per-coin detail under ``pnl`` (older runs),
    * the live execution row keeps it at the top level.

    Reading both is what makes one comparison work across the two artefacts.
    """
    pnl = row.get("pnl")
    return [pnl if isinstance(pnl, dict) else {}, row]


def _positions_from(row: dict[str, Any]) -> dict[str, float]:
    """Per-coin quantity, normalised from either artefact's shape."""
    out: dict[str, float] = {}
    for source in _position_sources(row):
        pos = source.get("open_positions")
        if not isinstance(pos, list):
            continue
        for entry in pos:
            if not isinstance(entry, dict):
                continue
            coin = str(entry.get("coin_id") or "").strip()
            if not coin:
                continue
            out[coin] = _num(entry.get("qty"), 0.0)
    return out


def _coins_reported(row: dict[str, Any]) -> set[str]:
    """Coins the artefact *mentions*, whatever the quantity.

    A zero quantity is a real position state (flat), not missing data, so
    "reported" must come from the coin's presence in the artefact rather than
    from its size.
    """
    coins: set[str] = set()
    for source in _position_sources(row):
        pos = source.get("open_positions")
        if not isinstance(pos, list):
            continue
        for entry in pos:
            if not isinstance(entry, dict):
                continue
            coin = str(entry.get("coin_id") or "").strip()
            if coin:
                coins.add(coin)
    return coins


def _quantity_for(row: dict[str, Any], coin: str) -> float | None:
    """Quantity of ``coin`` in the artefact, or None when it says nothing about
    the coin at all — a distinct answer from zero, which is a real holding."""
    if coin in _coins_reported(row):
        return _positions_from(row).get(coin, 0.0)
    return None


def _has_position_detail(row: dict[str, Any]) -> bool:
    """True when the row answers per-coin questions at all.

    An *empty* ``open_positions`` list is a real claim ("this book holds
    nothing"), which must be comparable — otherwise a live run that silently
    bought nothing would read as "no data" instead of as the gap it is. An
    *absent* list is what means "this artefact does not record the book"
    (aggregate paper rows), and only that is unverifiable.
    """
    for source in _position_sources(row):
        if isinstance(source.get("open_positions"), list):
            return True
    return False


def _aggregate(row: dict[str, Any], key: str) -> float | None:
    """A top-level field from either artefact, or None when it is not usable.

    None means "this artefact does not answer that question" — either the key
    is absent (an older writer) or the value is not a finite number (corrupt).
    Callers must treat both as unverifiable, never as 0.0, because a missing
    or corrupt metric is not a claim of zero.
    """
    for source in _position_sources(row):
        if key not in source:
            continue
        return _finite(source.get(key))
    return None


def _is_real_cycle(row: dict[str, Any]) -> bool:
    """True for a portfolio cycle, false for a ``risk_tick`` heartbeat.

    ``portfolio_history.jsonl`` mixes one row per *real* cycle with periodic
    ``risk_tick`` rows (``duration_s`` 0 and ``source: "risk_tick"``). Counting
    both would compare heartbeats as if they were cycles, so they are filtered
    out before the join.
    """
    return str(row.get("source") or "main") != "risk_tick"


def _orders_from(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Order attempts from a live row. Paper has no separate order list — its
    fills *are* the record — so an empty list is a truthful "no data", not a
    disagreement."""
    out: list[dict[str, Any]] = []
    for source in _position_sources(row):
        orders = source.get("orders")
        if isinstance(orders, list):
            out.extend(o for o in orders if isinstance(o, dict))
    return out


def _is_cumulative(row: dict[str, Any]) -> bool:
    """True when a row's ``n_trades`` counts fills accumulated across *all*
    cycles rather than only this one.

    The paper client writes ``n_trades = len(ledger)`` — one entry per fill
    ever booked, so the number only ever grows. The live client writes
    ``orders`` — just this cycle's attempts. These are different units, and
    comparing a cumulative counter against a per-cycle count manufactures a
    divergence out of the counter instead of the execution, so the caller must
    ask which one it is holding before arithmetic.

    Detected by shape rather than a magic threshold: a row that carries an
    ``orders`` list is per-cycle by construction, and everything else that
    still reports ``n_trades`` is the cumulative paper ledger.

    A writer that wants to be unambiguous can emit ``n_trades_scope``
    explicitly (``"cumulative"`` or ``"per_cycle"``), which wins over the
    shape guess — see ``main.py``. A later change that adds an ``orders`` list
    to the paper row must not silently flip the meaning of ``n_trades``.
    """
    for source in _position_sources(row):
        scope = source.get("n_trades_scope")
        if scope == "per_cycle":
            return False
        if scope == "cumulative":
            return True
    for source in _position_sources(row):
        if isinstance(source.get("orders"), list):
            return False
    for source in _position_sources(row):
        if source.get("n_trades") is not None:
            return True
    return False


def compare_cycles(
    paper_rows: list[dict[str, Any]],
    live_rows: list[dict[str, Any]],
    *,
    max_qty_rel_tol: float = DEFAULT_MAX_QTY_REL_TOL,
    max_cash_rel_tol: float = DEFAULT_MAX_CASH_REL_TOL,
    max_order_count_delta: int = DEFAULT_MAX_ORDER_COUNT,
    problems: list[str] | None = None,
) -> dict[str, Any]:
    """Compare paper and live rows cycle by cycle.

    Returns a report dict (see ``scripts/rollout_diff.py`` docstring for the
    honesty rules). ``findings`` entries are dicts with ``severity``
    (``pass``/``warn``/``fail``), ``code``, ``cycle``, ``message``, and any
    ``detail`` needed to re-derive it.
    """
    findings: list[dict[str, Any]] = []

    # A ``risk_tick`` heartbeat is not a portfolio cycle; comparing one
    # against nothing would report a divergence that does not exist.
    paper_by_cycle = _index_rows(paper_rows)
    live_by_cycle = _index_rows(live_rows)

    # A cycle number reused within one artefact (the counter restarts after a
    # bot restart) means the join had to pick one row and drop the others.
    # The pick is deterministic (latest write wins), but the drop is invisible
    # evidence unless it is reported, so it is reported — per artefact, and with
    # the row that was kept alongside the ones that were not, so a reader can
    # check that the kept row was the portfolio cycle and not a trailing
    # snapshot of the same number.
    for artefact, dupes, rows in (("paper", _duplicate_cycle_numbers(paper_rows), paper_rows),
                                  ("live", _duplicate_cycle_numbers(live_rows), live_rows)):
        picks = _cycle_row_picks(rows)
        for cycle, count in sorted(dupes.items()):
            pick = picks.get(cycle, {})
            findings.append({
                "severity": "warn", "code": "cycle_number_reused", "cycle": cycle,
                "message": (f"{artefact} row claims cycle {cycle} {count}× — "
                            f"cycle numbers restart with the bot, so only the "
                            f"latest {artefact} row for it was compared; the "
                            f"other {count - 1} are not visible in this "
                            f"comparison"),
                "detail": {"artefact": artefact, "occurrences": count,
                           "kept": pick.get("kept"),
                           "dropped": pick.get("dropped", [])},
            })

    all_cycles = sorted(set(paper_by_cycle) | set(live_by_cycle))
    if not all_cycles:
        # The early return must carry the problems too, or an unreadable
        # artefact pair would look like clean evidence of nothing rather than
        # a failure to read either side.
        findings.append({
            "severity": "fail",
            "code": "no_cycles_at_all",
            "cycle": None,
            "message": ("no comparable cycles: neither artefact recorded a "
                        "cycle with a usable number"),
        })
        return _report(findings, paper_by_cycle, live_by_cycle, problems)

    for cycle in all_cycles:
        paper = paper_by_cycle.get(cycle)
        live = live_by_cycle.get(cycle)

        if paper is None:
            findings.append({
                "severity": "warn", "code": "live_only_cycle", "cycle": cycle,
                "message": (f"cycle {cycle} recorded live but not paper — "
                            "paper may have been stopped before the live run"),
            })
            continue
        if live is None:
            findings.append({
                "severity": "fail", "code": "paper_only_cycle", "cycle": cycle,
                "message": (f"cycle {cycle} recorded paper but not live — the "
                            "live run never logged this cycle"),
            })
            continue

        # -- order volume -------------------------------------------------
        # "Absent" and "zero" are different answers and must not be conflated:
        # an artefact that does not track a metric cannot be compared on it,
        # and treating a missing metric as 0 would manufacture a divergence
        # out of a missing field.
        #
        # The paper side records ``n_trades = len(ledger)`` — a CUMULATIVE
        # count that grows for the life of the bot — while the live side
        # records ``orders``: only this cycle's attempts. Those are different
        # units, so comparing them directly would manufacture a divergence
        # that is an artefact of the counter, not the execution.
        p_trades = _aggregate(paper, "n_trades")
        l_orders = len(_orders_from(live))
        if p_trades is None:
            # Either the paper row records no fill count (older writer) or the
            # value present is not a usable number. Both mean the question
            # cannot be answered, so it is unverifiable — never 0-vs-N, which
            # would manufacture a failure out of a missing field.
            findings.append({
                "severity": "warn", "code": "order_count_unverifiable",
                "cycle": cycle,
                "message": ("cycle {}: paper row records no usable fill count, "
                            "only aggregates — order-volume divergence is "
                            "unverifiable from these artefacts".format(cycle)),
            })
        elif _is_cumulative(paper):
            findings.append({
                "severity": "warn", "code": "order_count_unverifiable",
                "cycle": cycle,
                "message": ("cycle {cycle}: paper records {n} fills in total across "
                            "every cycle, live records {o} order attempt(s) for "
                            "this cycle — different units, so order-volume "
                            "divergence is unverifiable from these artefacts"
                            .format(n=int(p_trades), o=l_orders, cycle=cycle)),
            })
        else:
            order_delta = abs(l_orders - int(p_trades))
            if order_delta > max_order_count_delta:
                findings.append({
                    "severity": "fail", "code": "order_count_mismatch", "cycle": cycle,
                    "message": (f"cycle {cycle}: live placed {l_orders} order(s) "
                                f"against {p_trades:g} paper fill(s)"),
                    "detail": {"paper_trades": p_trades, "live_orders": l_orders},
                })
            elif p_trades or l_orders:
                findings.append({
                    "severity": "pass", "code": "order_count_match", "cycle": cycle,
                    "message": (f"cycle {cycle}: {p_trades:g} paper fill(s) == "
                                f"{l_orders} live order(s)"),
                })

        # -- per-coin quantity --------------------------------------------------
        # The universe is every coin either artefact *mentions*, because a
        # "missing" coin is not the same as a "flat" coin. When a coin appears
        # on one side only, this loop does NOT decide it is a display artefact
        # (it might be the other mode forgetting to buy); it compares what is
        # verifiable and reports the gap so the reader can see it.
        p_detail = _has_position_detail(paper)
        l_detail = _has_position_detail(live)
        if p_detail and l_detail:
            for coin in sorted(_coins_reported(paper) | _coins_reported(live)):
                p_qty = _quantity_for(paper, coin)
                l_qty = _quantity_for(live, coin)
                worst = max(abs(p_qty or 0.0), abs(l_qty or 0.0))
                rel = abs((p_qty or 0.0) - (l_qty or 0.0)) / worst if worst > 0 else 0.0
                if rel > max_qty_rel_tol:
                    findings.append({
                        "severity": "fail", "code": "qty_mismatch", "cycle": cycle,
                        "message": (f"cycle {cycle}: {coin} qty "
                                    f"{l_qty if l_qty is not None else 0:g} live vs "
                                    f"{p_qty if p_qty is not None else 0:g} paper "
                                    f"({rel:.1%} apart)"),
                        "detail": {"coin": coin, "paper_qty": p_qty,
                                   "live_qty": l_qty, "rel": rel},
                    })
                else:
                    findings.append({
                        "severity": "pass", "code": "qty_match", "cycle": cycle,
                        "message": (f"cycle {cycle}: {coin} within tolerance "
                                    f"({rel:.1%} apart)"),
                    })
        elif p_detail != l_detail:
            # Exactly one artefact records the book. Whichever it is, the
            # other side's per-coin quantities are *unknown* — not zero — so
            # the honest answer is unverifiable. Comparing an empty book
            # against a book with coins (or vice versa) would either
            # manufacture a divergence out of a missing field or, worse,
            # read as agreement about quantities that were never compared.
            missing = "live" if p_detail else "paper"
            findings.append({
                "severity": "warn", "code": "per_coin_unverifiable", "cycle": cycle,
                "message": ("cycle {}: {} row has no per-coin positions, only "
                            "aggregates — per-coin divergence is unverifiable "
                            "from these artefacts".format(cycle, missing)),
                "detail": {"missing_from": missing},
            })
        # else: neither side records per-coin positions — nothing to compare,
        # nothing to claim. Reporting a pass here would be a lie.

        # -- aggregate book size ------------------------------------------------
        # Available from both artefacts even when the per-coin list is not, so
        # it is the one positional check that always runs. When the live writer
        # reports no count, the per-coin list is the honest fallback: the number
        # of entries the live artefact actually recorded. Reading it as a count
        # is better than declaring the check unverifiable forever, because it
        # is still a real claim about the book rather than an absence.
        n_paper = _aggregate(paper, "n_positions")
        n_live = _aggregate(live, "n_positions")
        if n_live is None and l_detail:
            n_live = float(len(_positions_from(live)))
        if n_paper is None and p_detail:
            n_paper = float(len(_positions_from(paper)))
        if n_paper is None or n_live is None:
            findings.append({
                "severity": "warn", "code": "position_count_unavailable", "cycle": cycle,
                "message": ("cycle {}: position count missing from "
                            "{} — cannot compare book size".format(
                                cycle, "paper" if n_paper is None else "live")),
            })
        elif n_paper != n_live:
            findings.append({
                "severity": "fail", "code": "position_count_mismatch", "cycle": cycle,
                "message": (f"cycle {cycle}: {n_live:g} live position(s) vs "
                            f"{n_paper:g} paper"),
                "detail": {"paper_n": n_paper, "live_n": n_live},
            })

        # -- cash / equity --------------------------------------------------
        p_equity = _aggregate(paper, "equity")
        l_equity = _aggregate(live, "equity")
        if p_equity is None or l_equity is None:
            findings.append({
                "severity": "warn", "code": "equity_unavailable", "cycle": cycle,
                "message": ("cycle {}: equity missing from "
                            "{} — cannot compare cash".format(
                                cycle, "paper" if p_equity is None else "live")),
            })
        else:
            worst = max(abs(p_equity), abs(l_equity))
            if worst > 0:
                rel = abs(p_equity - l_equity) / worst
                if rel > max_cash_rel_tol:
                    findings.append({
                        "severity": "fail", "code": "equity_mismatch", "cycle": cycle,
                        "message": (f"cycle {cycle}: equity {l_equity:g} live vs "
                                    f"{p_equity:g} paper ({rel:.1%} apart)"),
                        "detail": {"paper_equity": p_equity, "live_equity": l_equity,
                                   "rel": rel},
                    })
                else:
                    findings.append({
                        "severity": "pass", "code": "equity_match", "cycle": cycle,
                        "message": f"cycle {cycle}: equity within tolerance ({rel:.1%} apart)",
                    })

        # -- refusals -------------------------------------------------------
        errors = live.get("errors")
        if isinstance(errors, list) and errors:
            guard = sum(1 for e in errors if isinstance(e, dict)
                        and ("caps" in str(e.get("error", "")).lower()
                             or "circuit breaker" in str(e.get("error", "")).lower()))
            findings.append({
                "severity": "warn", "code": "live_errors_present", "cycle": cycle,
                "message": (f"cycle {cycle}: {len(errors)} live error(s), "
                            f"{guard} of them Phase 6 guard refusals — expected "
                            "while the caps are being tuned"),
                "detail": {"errors": [e.get("error") for e in errors
                                      if isinstance(e, dict)]},
            })

    return _report(findings, paper_by_cycle, live_by_cycle, problems)


def _report(
    findings: list[dict[str, Any]],
    paper_by_cycle: dict[int, dict[str, Any]],
    live_by_cycle: dict[int, dict[str, Any]],
    problems: list[str] | None = None,
) -> dict[str, Any]:
    """Score the findings, then let I/O problems demote the verdict.

    The problems live in the returned report so the JSON and text reports
    cannot disagree: a verdict computed without them (as ``compare_cycles``
    does) would be demoted afterwards by the caller, and the two outputs would
    then agree only by luck of ordering.
    """
    counts = {k: 0 for k in VERDICT_KEYS}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    verdict = "pass" if counts["fail"] == 0 else "fail"
    if verdict == "pass" and counts["warn"]:
        verdict = "warn"
    problems = list(problems or [])
    if problems and verdict == "pass":
        verdict = "fail"
    return {
        "verdict": verdict,
        "counts": counts,
        "problems": problems,
        "cycles_compared": sorted(set(paper_by_cycle) & set(live_by_cycle)),
        "cycles_paper_only": sorted(set(paper_by_cycle) - set(live_by_cycle)),
        "cycles_live_only": sorted(set(live_by_cycle) - set(paper_by_cycle)),
        "findings": findings,
    }


def format_text(report: dict[str, Any]) -> str:
    """Human-readable report. Never claims a pass the verdict does not."""
    lines: list[str] = []
    counts = report.get("counts", {})
    lines.append("rollout divergence: paper vs live")
    lines.append("  verdict      : " + str(report.get("verdict", "unknown")).upper())
    lines.append("  cycles       : {} compared, {} paper-only, {} live-only".format(
        len(report.get("cycles_compared", [])),
        len(report.get("cycles_paper_only", [])),
        len(report.get("cycles_live_only", []))))
    lines.append("  findings     : {} fail / {} warn / {} pass".format(
        counts.get("fail", 0), counts.get("warn", 0), counts.get("pass", 0)))
    lines.append("")
    interesting = [f for f in report.get("findings", [])
                   if f.get("severity") in ("fail", "warn")]
    if not interesting:
        lines.append("  no divergences found")
    else:
        current = None
        for f in interesting:
            if f.get("cycle") != current:
                current = f.get("cycle")
                lines.append(f"  cycle {current}")
            lines.append("    [{sev}] {code}: {msg}".format(
                sev=str(f.get("severity", "?")).upper(),
                code=f.get("code", "?"),
                msg=f.get("message", "")))
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rollout_diff",
        description="Compare paper and live execution artefacts for divergence.")
    parser.add_argument("--output-dir", default="output", type=Path,
                        help="directory holding the JSONL artefacts (default: output)")
    parser.add_argument("--history", default=PAPER_HISTORY_NAME, type=str,
                        help=f"paper artefact name (default: {PAPER_HISTORY_NAME})")
    parser.add_argument("--live", default=LIVE_EXECUTIONS_NAME, type=str,
                        help=f"live artefact name (default: {LIVE_EXECUTIONS_NAME})")
    parser.add_argument("--json", action="store_true",
                        help="emit the report as JSON instead of text")
    parser.add_argument("--max-qty-rel-tol", type=float, default=DEFAULT_MAX_QTY_REL_TOL,
                        help="relative quantity tolerance per coin (default: 0.02)")
    parser.add_argument("--max-cash-rel-tol", type=float, default=DEFAULT_MAX_CASH_REL_TOL,
                        help="relative equity tolerance (default: 0.05)")
    parser.add_argument("--max-order-count-delta", type=int, default=DEFAULT_MAX_ORDER_COUNT,
                        help="allowed |live orders − paper fills| (default: 0)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    out_dir = Path(args.output_dir)
    if not out_dir.is_dir():
        print(f"rollout_diff: output directory not found: {out_dir}",
              file=sys.stderr)
        return 2

    paper_rows, paper_problems = _rows_from_jsonl(out_dir / args.history)
    live_rows, live_problems = _rows_from_jsonl(out_dir / args.live)
    # I/O problems are findings, not silence: an artefact that could not be
    # read must never be mistaken for an artefact that agreed. They are passed
    # into the comparison rather than applied afterwards so that the verdict,
    # the text report and the JSON report are all computed from the same state.
    problems = paper_problems + live_problems

    report = compare_cycles(
        paper_rows, live_rows,
        max_qty_rel_tol=args.max_qty_rel_tol,
        max_cash_rel_tol=args.max_cash_rel_tol,
        max_order_count_delta=args.max_order_count_delta,
        problems=problems,
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
        for p in problems:
            print(f"  [problem] {p}", file=sys.stderr)

    return 0 if report.get("verdict") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
