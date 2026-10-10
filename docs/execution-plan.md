# Paper Trading → Real Trading — Phase Plan

Goal: run the council against a real exchange while keeping paper mode
as a first-class, always-available fallback. Everything exchange-specific
stays behind abstract interfaces so the venue can be swapped without
touching the crews, the portfolio DTOs, or `main.py`.

Status as of 2026-10-09: **Phases 1–5 implemented, Phase 6 not started,
Phase 7 partially applicable** (details per phase below).
See `.env.example` for the live knobs the code actually reads.

## Conventions (apply to every phase)

- **Exchange-agnostic core.** Venue details (auth signing, symbol formats,
  precision rules, min-notional, endpoint shapes) live inside concrete
  subclasses / factories. Council crews and portfolio DTOs never know
  venue payload shapes.
- **`EXECUTION_MODE=paper|live|halted`, default paper.** Paper stays
  possible after live support; paper file semantics are preserved.
- **Fail loudly, never silently fall back.** Live/halted misconfiguration
  raises (`ExecutionError` / `ExecutionHalted`), it does not quietly
  become paper.
- **Deterministic + offline tests.** No network, no LLM in tests
  (`tests/test_backtest_real_flow.py` excluded — it times out on this
  machine).
- **No invented env knobs.** Do not document a variable the code does
  not actually read.
- **Stdlib-only panel.** `scripts/portfolio_panel.py` cannot import the
  app (that drags in venv/CrewAI); it reads `output/*.json` only, and its
  labels must never lie (a paper-labelled number shown for live data is
  worse than no number).
- **Safety baseline (verbatim):** trade-only API keys, no withdrawal
  permission, IP allowlist, secrets only via env vars; kill switch via
  `EXECUTION_MODE=halted`; circuit breaker on daily realized loss.

## Review corrections (from the plan review, 2026-10-08)

These overclaims in the original draft must stay fixed:

1. **`client_order_id` is advisory, not idempotent.** Nobitex documents no
   dedup on retry — after a placement timeout, query (`get_order` /
   `get_open_orders`) before retrying. Never claim retry safety from the
   ID alone.
2. **Market metadata needs an explicit source.** `list_markets()` returns
   zeroed min/max/precision, so min-order enforcement and tick/lot
   rounding need a stated source (`EXECUTION_MAP` constraints map /
   empirical values), not the market list.
3. **Fills come from trade history / embedded order trades**, not a
   direct fills endpoint (CCXT-standard `fetchMyTrades` /
   order `trades[]`). Real fees are only knowable post-fill; paper has a
   `PAPER_FEE_BPS` knob so paper/live comparison stays meaningful.
4. **Capital caps are CLI flags** (`--max-total-exposure` /
   `--max-single-position`, defaults 30% / 5% on flow state) — there is
   no `COUNCIL_MAX_TOTAL_EXPOSURE` env var.
5. **Dust filter wording:** `min_trade` in base-currency notional,
   not `min_trade_usd`.
6. **No Nobitex testnet/sandbox exists** — Phase 7 integration means
   real minimum-size orders only.

## Phase 1 — Abstract Execution Interface ✅ DONE

`src/crypto_council_flow/tools/execution_base.py` — `ExecutionClient`
ABC with normalized DTOs (`Balance{asset,free,locked}`,
`OrderRequest{symbol,side,type,qty|notional,limit_price,client_order_id}`,
`OrderResult{order_id,client_order_id,status,filled_qty,avg_fill_price,fee,ts}`,
`Fill{order_id,symbol,side,qty,price,fee,ts}`); abstract
`get_balances/place_order/cancel_order/get_order/get_open_orders/get_fills(since)`
plus abstract `reconcile` (returns paper-shaped positions + ledger rows,
no-op default). `get_execution_client()` factory on
`EXECUTION_EXCHANGE/API_KEY/API_BASE/QUOTE`; `get_execution_mode()` on
`EXECUTION_MODE`; `ExecutionError` / `ExecutionHalted`.
Tests: `tests/test_execution_base.py` (21).

## Phase 2 — Paper Execution as a Plugin ✅ DONE

`tools/execution_paper.py` — `PaperExecutionClient` (fills at snapshot
price, zero network, `PAPER_FEE_BPS` fee knob). `_update_paper_ledger()`
in `main.py` delegates to it in paper mode; `paper_positions.json` /
`paper_ledger.json` semantics preserved (`tests/test_paper_ledger_equity.py`,
`tests/test_paper_ledger_pnl.py` untouched).
Deviation from draft: the plan said "rename to mode-agnostic
`positions.json`/`ledger.json`" — instead both pairs coexist
(`paper_*` + `live_*`), which keeps paper semantics byte-stable.

## Phase 3 — Live Execution Client ✅ DONE

`tools/execution_nobitex.py` (~1,130 lines) — `NobitexExecutionClient`:
request signing, precision rounding to per-pair decimals with explicit
source, min-order enforcement with a stated source, error mapping to
`{"error"}` JSON, rate-limit handling, `_epoch_ms_to_iso` /
`_iso_to_epoch_ms`, cap guard on `get_fills`.
Tests: `tests/test_execution_nobitex.py`, `tests/test_execution_live_apply.py` (9).

## Phase 4 — Order Translation Layer ✅ DONE

`NobitexExecutionClient.apply_plan`: market orders sized
`target/price − free balance`; close sells full free balance; dust below
`EXECUTION_MIN_TRADE` (default 10, base-currency notional) skipped, never
reaching the exchange; no-price/no-symbol actions recorded in `errors`,
not raised; exactly one placement attempt plus a `get_open_orders()`
reconcile note (no blind retry — see correction 1);
`EXECUTION_DRY_RUN` builds orders without POSTing; realized = −fees,
unrealized = 0, equity = account − fees; JSONL audit to
`EXECUTION_LOG_PATH`.

## Phase 5 — State & Reconciliation ✅ DONE

Venue balances are the single source of truth for *how much we hold*;
fills supply only *history* (price, side, fee) for P&L and cost basis.
`main.py`: `LEDGER_LIVE_PATH`/`POSITIONS_LIVE_PATH`
(`output/live_ledger.json`, `output/live_positions.json`),
`_reconcile_live_positions(*, cycle, now_iso, account_size,
base_currency, risk_snapshot)` — advisory/fails-open (paper → `None`;
halted/unreachable client → `{"error": ...}`, cycle continues on
previous local state; live summary replaces `pnl_summary` only when it
has no `"error"` key). Correction rows only on wallet-vs-fills
disagreement (`abs(venue_qty − expected_qty) > 1e-9`). Corrupt local
state repaired (`_load_json` → `(value, parseable)`, `repair` flag).
Panel: mode-aware live view (`_resolve_mode` from snapshot `pnl.mode`
→ `pnl_live_note` → `EXECUTION_MODE`; `--mode paper|live` override;
`strip-err` banner on reconcile failure).
Tests: `tests/test_execution_reconcile.py` (14),
`tests/test_reconcile_live_wiring.py` (11),
`tests/test_portfolio_panel_live.py` (19).

## Phase 6 — Safety & Go-Live Controls ❌ NOT STARTED

What exists today: the kill switch (`EXECUTION_MODE=halted` raises
`ExecutionHalted` from the factory; `_reconcile_live_positions` turns it
into an advisory error summary, trading stops because no client is
constructed), capital caps as flow-state/CLI inputs enforced in the plan
(`max_total_exposure_pct=30.0`, `max_single_position_pct=5.0`), key
hygiene documented in `.env.example` (trade-only keys, no withdrawal,
IP allowlist, env-only secrets).

What is still missing (the actual Phase 6 work):

1. **Pre-order cap enforcement** — caps currently shape the plan, not the
   order path. `apply_plan` (or a thin guard in front of it) must reject
   or downsize orders that would breach total/single-position exposure
   at fill time, since prices move between plan and placement.
2. **Circuit breaker on daily realized loss** — track realized P&L per
   UTC day from the ledger; past the limit, refuse new orders (fail
   closed: raise, do not fall back to paper) until the next day or a
   manual reset. No implementation exists yet.
3. **Halted-mode audit** — today halted fails loudly at client
   construction, which is correct, but there is no explicit test pinning
   "halted refuses to send orders" at the `apply_plan` boundary, only at
   reconcile. Add one.
4. **Key-permission check (if the venue allows)** — on startup in live
   mode, verify the key cannot withdraw where the API exposes it; where
   it doesn't, document the manual checklist. Venue-dependent.

## Phase 7 — Rollout ❌ NOT STARTED (no testnet simplification applies)

1. Unit-test the translation layer + both clients against a fake
   (done for existing surface; extend to Phase 6 guards).
2. ~~Testnet/sandbox integration~~ — N/A for Nobitex (correction 6):
   single minimum-size real order with immediate close, on a
   KYC'd key with a min-notional budget floor.
3. Run `EXECUTION_MODE=live` in parallel with paper for N cycles,
   diffing plans vs. real fills (paper/live ledger shapes already match,
   `PAPER_FEE_BPS` makes the comparison fair).
4. Cut the main loop over only then, keeping `paper` as instant rollback
   (already one env var away).

## Open questions (unchanged from the draft)

1. Target exchange for the first live client — Nobitex (matches current
   defaults), or a switch? (Code implements Nobitex.)
2. Spot market/limit only, or anything more exotic? (Code: spot only.)
