# DATEV ERPNext Adapter v0.3.0 Implementation Plan (payments, incoming invoices)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed Payment Entries, bank-related Journal Entries and Purchase Invoices from ERPNext into the core, so sales invoices, payments and incoming invoices all reach DATEV.

**Architecture:** New modules beside the sales-invoice code: `payments.py` (GL-based translation), `purchases.py` (purchase invoice lines, sharing grouping/tax/rounding with `lines.py`), hooks and the existing sync/catch-up/report/indicator machinery extended to the new doctypes. Requires core `datev` 0.3.0 (pin in `.github/versions.env` once released; until then develop against the core's feature branch locally and note it).

**Spec:** core repo `docs/superpowers/specs/2026-11-03-payments-design.md` and `2026-11-03-incoming-invoices-design.md` (decisions there are binding), base spec `2026-11-02-datev-app-design.md`.

## Global Constraints

- All constraints of the earlier adapter plans apply (GPL-3, English, German translations, "Stornorechnung" wording, no client/customer names, no local paths, writes only via `datev.intake`, idempotent syncs, dedup job ids, full rollback on errors with comment, `IntegrationTestCase`, DDL in `setUpClass`).
- New behaviour is off by default: settings `sync_payments`, `sync_purchase_invoices`.
- Conventional commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Payment for two invoices plus a bank fee deduction → three lines with the right Belegfeld 1 and accounts (Task 1: `test_multi_invoice_with_deduction`).
2. Payment re-allocated after transfer → reversal of the changed line and a new line, nothing else (Task 2: `test_reallocation_after_transfer`).
3. Purchase invoice with reverse charge (Add/Deduct pair) → net line with the template's BU key; empty BU key → rejected (Task 4: `test_reverse_charge_net_with_bu_key`).
4. Purchase invoice without attached supplier file → not transferred, notice (Task 5: `test_no_supplier_file_notice`).
5. Supplier payment for a purchase invoice → waits until that invoice is in DATEV, uses its `bill_no` (Task 6: `test_supplier_payment_uses_bill_no_and_waits`).

---

### Task 1: Payments – GL-based translation

**Files:** create `datev_erpnext/payments.py`, tests `test_payments.py`, factories (bank account with account number, payment entries, journal entries)

**Decisions (from the payments design):**
- `voucher_lines(doctype, name) -> list[PaymentLine]` with `PaymentLine(line_key, party_type, party, account_number, contra_account_number, amount, debit_credit, document_field, depends_on, posting_text)`.
- Pivot = the voucher's Bank/Cash account (Payment Entry: paid_to for Receive, paid_from for Pay, the bank side for Internal Transfer; Journal Entry: first Bank/Cash line). Every other GL entry → one line: party account only if the GL account is Receivable/Payable and party type Customer/Supplier, else the GL account number; Gegenkonto = pivot account number; amount |debit−credit| (company currency); side from the line (debit S, credit H); Belegfeld 1 = the referenced invoice's DATEV Belegfeld 1 (Sales Invoice → its name; Purchase Invoice → its `bill_no`, or held — see Task 6) else the voucher name; `depends_on` = the referenced invoice's DATEV Document if the invoice is on/after `sync_from_date`.
- Line key = account + party + against voucher (stable).
- Journal Entries without any Bank/Cash account are ignored; GL accounts without numeric account number → ValidationError.
- [ ] Tests: `test_customer_payment_single_invoice`, `test_multi_invoice_with_deduction`, `test_unallocated_remainder`, `test_supplier_advance_on_advance_account_books_gl`, `test_bank_fee_journal_entry`, `test_internal_transfer`, `test_journal_without_bank_ignored` → commit `feat: translate payments and bank journal entries into DATEV lines`.

### Task 2: Payments – sync, cancel, re-allocation

**Files:** `datev_erpnext/sync.py` (or new `payment_sync.py`), `hooks.py`, settings JSON (`sync_payments`), tests

**Decisions:**
- on_submit (Payment Entry, Journal Entry) → enqueue `sync_payment(doctype, name)` if `sync_payments` and client exists and date ≥ `sync_from_date`; document via `upsert_document(..., source_key=f"{doctype}:{name}", direction="Other", content=None)` (Not Required), bookings kind `Payment` with `party_account` or `account`, `depends_on_document`.
- Cancel: before upload/transfer → `discard_open_bookings`; after transfer → add reversal lines (`general_reversal=True`, line key `storno:<key>`, posting text prefixed "Storno"); if bookings are Batched in an Approved/Sending batch → block cancel with the core's message.
- Re-allocation: hooks on `Unreconcile Payment` submit and Payment Reconciliation completion (whatever v16 fires; check the installed code) plus the daily catch-up recompute lines for transferred vouchers: changed lines → reversal + new line; unchanged lines untouched.
- Status indicator and "not in DATEV" report cover Payment Entry and Journal Entry (bank ones).
- [ ] Tests: `test_payment_synced_on_submit`, `test_payment_off_by_default`, `test_cancel_before_transfer_discards`, `test_cancel_after_transfer_reverses`, `test_cancel_blocked_while_batched`, `test_reallocation_after_transfer`, `test_catch_up_includes_payments` → commit `feat: sync payments and keep DATEV in step with re-allocations`.

### Task 3: Purchase invoices – lines

**Files:** create `datev_erpnext/purchases.py`, refactor shared parts of `lines.py` (grouping, tax rate check, rounding) into helpers used by both, tests `test_purchases.py`, factories (supplier with supplier number field, purchase tax templates incl. a reverse-charge Add/Deduct pair, expense accounts)

**Decisions (from the incoming-invoices design):**
- Lines per expense/asset account, tax code, rate; party side H for invoices, S for returns; opposite side for groups against the direction; zero lines dropped; rounding tolerance; unsupported charges rejected.
- Reverse charge: a template whose tax rows on Tax accounts are an Add/Deduct pair at the same rate → net amount; tax-key row must have a non-empty BU key, else ValidationError.
- Reject: lines on Stock Received But Not Billed / Asset Received But Not Billed / stock accounts ("goods receipts are not supported yet"); "Valuation"/"Valuation and Total" tax categories; other Deduct rows; `is_paid`; deferred expense; write-off.
- Belegfeld 1 = `bill_no` (returns: own `bill_no`, else `return_against`'s `bill_no`); missing → ValidationError.
- [ ] Tests: `test_purchase_single_line`, `test_purchase_mixed_tax`, `test_reverse_charge_net_with_bu_key`, `test_reverse_charge_without_bu_key_rejected`, `test_debit_note_sides`, `test_srbnb_rejected`, `test_is_paid_rejected`, `test_missing_bill_no_rejected`, `test_sales_lines_unchanged` (regression) → commit `feat: build DATEV lines from purchase invoices`.

### Task 4: Purchase invoices – document image, sync, cancel

**Files:** `datev_erpnext/images.py`, `sync.py`, `hooks.py`, settings JSON (`sync_purchase_invoices`, `supplier_number_field`, `purchase_posting_text_template`), `parties.py` (supplier party accounts, creditor, locked like customers), JS indicator for Purchase Invoice, report, tests

**Decisions:**
- Document image order: hook → e-invoice import file (`Purchase Invoice.e_invoice_import` if eu_einvoice installed; check the installed version for the link and the file field) → newest attached PDF/XML → for returns the print-format PDF → else notice "no supplier document attached", not transferred.
- Document type direction Incoming; sync, catch-up, cancel guard, report and indicator as for sales invoices.
- [ ] Tests: `test_purchase_synced`, `test_einvoice_import_file_used`, `test_attached_pdf_used`, `test_no_supplier_file_notice`, `test_debit_note_uses_print_pdf`, `test_purchase_cancel_blocked_after_upload`, `test_supplier_party_account_number` → commit `feat: sync purchase invoices with the supplier's document`.

### Task 5: Supplier payments released

**Files:** `payments.py`, tests

**Decisions:** payment lines referencing a Purchase Invoice: when `sync_purchase_invoices` is on, Belegfeld 1 = that invoice's DATEV Belegfeld 1 (`bill_no` rule) and `depends_on` its DATEV Document; when off, the line is held (Pending with message "waiting for purchase invoices to be synced").
- [ ] Tests: `test_supplier_payment_uses_bill_no_and_waits`, `test_supplier_payment_held_when_purchases_off` → commit `feat: release supplier payment lines with purchase invoices`.

### Task 6: Docs, translations, pin core 0.3.0

README, `docs/setup.md` (new settings, go-live order: invoices → payments → purchases), `docs/mapping.md` (payments and purchase mapping tables, rejected cases), translations, `.github/versions.env` → core 0.3.0 commit (controller supplies it); full suite + ruff → commit `docs: payments and purchase invoices`.
