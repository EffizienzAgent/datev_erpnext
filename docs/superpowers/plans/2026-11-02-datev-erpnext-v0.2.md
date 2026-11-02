# DATEV ERPNext Adapter v0.2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No submitted invoice may silently miss DATEV, no line may go out with an unchecked tax treatment, and the adapter must stay out of the way of normal ERPNext work.

**Architecture:** Changes in `sync.py`, `lines.py`, `parties.py`, `images.py`, a new script report and a daily scheduler job. Release 0.2.0 via release-please; pin the core to its 0.2.0 release once published (Task 4).

**Tech Stack:** ERPNext/Frappe v16, core app `datev` (local `/Users/tobi/Projekte/frappe-datev`), dev bench `dev/compose.yaml`.

**Spec:** `/Users/tobi/Projekte/frappe-datev/docs/superpowers/specs/2026-11-02-datev-app-design.md` (ERPNext adapter, anti-goals). Review source: `/Users/tobi/Projekte/NORO/docs/datev-offene-punkte.md`.

## Global Constraints

- All constraints of the v0.1 plan still apply (GPL-3, English, German translations in `datev_erpnext/locale/de.po`, "Stornorechnung" never "Gutschrift", no client names, writes to DATEV only via `datev.intake`, idempotent sync, tests with `IntegrationTestCase`).
- Conventional commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests that create custom fields do so in `setUpClass` (DDL auto-commits; see v0.1 CI fix).

## Review Focus

1. Backdated invoice submitted after go-live (posting date before `sync_from_date`): must not vanish silently (Task 2: `test_backdated_invoice_comments`).
2. A sync that failed transiently: must be retried automatically (Task 2: `test_catch_up_retries_missing`).
3. Invoice with tax rows typed by hand and no template: must be rejected (Task 1: `test_line_without_tax_code_rejected`).
4. Item template says 19 % but the invoice applies 0 %: must be rejected (Task 1: `test_rate_mismatch_rejected`).
5. Payment Entry submitted while the invoice syncs: must not hit a lock timeout (Task 3: `test_pdf_rendered_before_lock`).

---

### Task 1: Tax and amount strictness

**Files:** Modify `datev_erpnext/lines.py`, `datev_erpnext/sync.py`, tests `test_lines.py`, `test_sync.py`

**Decisions:**
- A line without tax code (neither item tax template nor invoice `taxes_and_charges`) → `frappe.ValidationError(_("Line on account {0} has no tax template. Set an item tax template or a sales taxes template."))`.
- Expected rate: from the tax code's template — Item Tax Template: sum of `tax_rate` over its rows whose `tax_type` account is of type Tax; Sales Taxes and Charges Template: sum of `rate` over rows with charge_type "On Net Total" on Tax accounts. If the line's applied rate differs from the expected rate by more than 0.001 → ValidationError naming code, expected and applied rate.
- Write-off: `base_write_off_amount != 0` → ValidationError ("Write-offs are not supported yet").
- Net-zero total with non-zero lines → ValidationError with a clear message (instead of the core's generic amount error).

- [ ] Tests: `test_line_without_tax_code_rejected`, `test_rate_mismatch_rejected`, `test_rate_match_ok`, `test_write_off_rejected`, `test_net_zero_total_rejected` → implement → commit `fix: reject lines with missing or contradicting tax treatment`.

### Task 2: Nothing goes missing

**Files:** Modify `datev_erpnext/sync.py`, `datev_erpnext/hooks.py`; create report `datev_erpnext/datev_erpnext/report/sales_invoices_without_datev/*`; modify `public/js/sales_invoice.js`; tests `test_sync.py`, new `test_catch_up.py`

**Decisions:**
- Skip with notice: when an invoice is skipped because `posting_date < sync_from_date` but it was submitted on/after `sync_from_date` was set (compare invoice `modified` ≥ settings `sync_from_date`), add a comment once: `DATEV: posting date before sync from date, not transferred.` The same once-only comment when the client is disabled. Comments are deduplicated (do not add an identical DATEV comment twice).
- Daily scheduler `datev_erpnext.sync.catch_up()`: if enabled and `sync_from_date` set, enqueue sync for submitted, non-opening invoices with `posting_date >= sync_from_date` that have no DATEV Document (same filter as backfill), at most 500 per run, deduplicated job ids.
- Script report "Sales Invoices without DATEV" (roles DATEV User/Manager, Accounts Manager): submitted invoices since `sync_from_date` without DATEV Document, with the latest DATEV comment as reason; filter company.
- Indicator: red "DATEV: error" when the latest DATEV comment is newer than the DATEV Document (or no document exists and a DATEV comment exists).
- Job checks `enabled` before syncing (already-queued jobs after disabling do nothing).

- [ ] Tests: `test_backdated_invoice_comments`, `test_disabled_client_comments_once`, `test_catch_up_retries_missing`, `test_catch_up_skips_existing`, `test_report_lists_missing`, `test_job_respects_enabled` → implement → commit `feat: catch up missing invoices and report what is not in DATEV`.

### Task 3: Concurrency and locks

**Files:** Modify `datev_erpnext/sync.py`, `datev_erpnext/parties.py`, tests `test_sync.py`, `test_parties.py`

**Decisions:**
- Render the document image before taking the invoice row lock; then `get_doc(..., for_update=True)`, re-check `docstatus == 1`, then intake. If the invoice changed (`modified` differs from the version rendered), re-render once.
- `party_account_for` locks the Customer row (`frappe.db.get_value("Customer", name, "name", for_update=True)`) before `ensure_party_account`, so two parallel syncs for a new customer cannot create two party accounts.
- `register_customers` POST-only.

- [ ] Tests: `test_pdf_rendered_before_lock` (spy on order of calls), `test_party_lock_taken`, `test_register_customers_post_only` → implement → commit `fix: shorten invoice locks and serialize party creation`.

### Task 4: Docs, translations, settings polish, pin core 0.2.0

**Files:** `README.md`, `docs/setup.md`, `docs/mapping.md`, locale files, settings JSON (`print_format` link filtered to Sales Invoice via `link_filters`), `.github/versions.env`

**Decisions:**
- Docs: XML embedding only for ZUGFeRD profiles (not XRechnung/empty profile) and how to supply the sent file via the `datev_erpnext_document_image` hook; new rejection cases; catch-up job and report; backdated invoices; cancel blocked once uploaded (tell the team to use a Stornorechnung).
- `versions.env`: `DATEV_COMMIT` = the core's `version-16` commit of release 0.2.0 (the controller supplies it; if the core is not released yet, leave as is and note it).
- Full suite + ruff green; commit `docs: update docs and translations for 0.2.0`.
