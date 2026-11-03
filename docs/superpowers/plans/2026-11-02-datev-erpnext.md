# DATEV ERPNext Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first release of `datev_erpnext`: it feeds submitted ERPNext Sales Invoices (incl. cancellation invoices) into the `datev` core app as documents, bookings and party accounts — one booking line per revenue account and tax rate, gross, with the invoice PDF as document image.

**Architecture:** A small ERPNext app (GPL-3) that depends on `erpnext` and `datev`. A `doc_events` hook enqueues a sync job after a Sales Invoice is submitted; the job resolves the DATEV Client for the invoice's company, ensures the customer's party account, renders the document image, and calls `datev.intake.upsert_document` and `datev.intake.add_booking`. A `before_cancel` hook blocks hard cancels once the document is in DATEV. Settings live in one single doctype.

**Tech Stack:** Frappe v16, ERPNext v16, the `datev` core app (local repo `../frappe-datev`, GitHub `EffizienzAgent/datev`), optional `eu_einvoice`, Python 3.14, Docker dev bench.

**Spec:** `docs/superpowers/specs/2026-11-02-datev-app-design.md` (sections "Two apps", "ERPNext adapter", "Flow", "Duplicate protection and corrections"). Core integration guide: `docs/integration.md` and `datev/intake.py` in the core repo.

## Global Constraints

- App name `datev_erpnext`, title "DATEV for ERPNext", license **GPL-3.0** (`license.txt` with the GPL-3 text, `app_license = "gpl-3.0"`), publisher "EffizienzAgent (Tobias Mende)", `oss@effizienzagent.de`. GitHub `EffizienzAgent/datev_erpnext`.
- `required_apps = ["erpnext", "datev"]`. `eu_einvoice` is optional: never imported at module level; detected via `"eu_einvoice" in frappe.get_installed_apps()`.
- No code copied from other DATEV or e-invoicing apps (only calling `eu_einvoice`'s public function when installed).
- English code, docs, commits; every user-facing string translatable, German translations in `datev_erpnext/locale/de.po`. Use "Stornorechnung" (never "Gutschrift") in German.
- No client or customer names anywhere in the repo. Test data uses DATEV sample numbers (consultant 29098, client 55003) and fictitious companies.
- Conventions as the core repo: flit/pyproject, ruff config identical to `../frappe-datev/pyproject.toml`, release-please, `version-16` branch, CI with bench + MariaDB, tests under `datev_erpnext/tests`, `frappe.tests.IntegrationTestCase`.
- The adapter writes to DATEV only through `datev.intake` (never creates DATEV Document/Booking records directly).
- Amounts: company currency (`base_*` fields). Gross per line = net + tax of that line; the sum of all lines of an invoice equals `abs(base_grand_total)` (rounding difference added to the largest line).
- Sync is idempotent: re-running for the same invoice never creates a second document or booking (source key `"Sales Invoice:<name>"`, line keys stable per revenue account + tax code + rate).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Invoices with mixed tax treatment** (taxable rent 19 % plus tax-exempt insurance in one invoice, item-level tax templates, invoice-level additional discount, rounding adjustment): lines must sum to the grand total and carry the right tax code per line. Pinned in Task 4 (`test_mixed_tax_lines`, `test_discount_and_rounding`).
2. **Cancellation invoices** (`is_return=1`, negative amounts, `return_against`): positive amounts with `debit_credit="H"`, their own document and Belegtyp rule match (`is_return`). Pinned in Task 4 and Task 6 (`test_return_invoice`).
3. **Re-submission / amend paths** (cancel before upload, amended invoice `-1`, sync job running twice): no duplicates; an amended invoice is a new document. Pinned in Task 6 (`test_sync_twice_is_idempotent`) and Task 7 (`test_cancel_before_upload_discards`).
4. **Company without DATEV Client or disabled adapter**: submitting an invoice must never fail because of the adapter; the sync is skipped and logged. Pinned in Task 6 (`test_no_client_is_skipped`).
5. **Customer without number when the client uses source-system numbering**: the booking must not go out with a wrong account; the party account exists without preferred number and the booking ends Stuck via core preflight. Pinned in Task 3 (`test_missing_number_field_value`).

---

## File Structure

```
pyproject.toml, license.txt (GPL-3), README.md, .gitignore, .editorconfig, .gitattributes, renovate.json,
release-please-config.json, .release-please-manifest.json
.github/versions.env (FRAPPE, ERPNEXT, DATEV, EU_EINVOICE branch+commit), .github/workflows/{test.yml,release.yml}
dev/compose.yaml, dev/bootstrap.sh, dev/test.sh
docs/{setup.md,mapping.md}
datev_erpnext/__init__.py, hooks.py, modules.txt ("DATEV ERPNext"), patches.txt, setup.py
datev_erpnext/datev_erpnext/doctype/datev_erpnext_settings/   single doctype
datev_erpnext/clients.py        company -> DATEV Client
datev_erpnext/parties.py        customer -> party account, bulk registration
datev_erpnext/lines.py          Sales Invoice -> booking lines
datev_erpnext/images.py         document image provider
datev_erpnext/sync.py           submit/cancel hooks, sync job, backfill
datev_erpnext/public/js/sales_invoice.js   DATEV status indicator
datev_erpnext/locale/{main.pot,de.po}
datev_erpnext/tests/{factories.py,test_*.py}
```

---

### Task 1: Scaffold, dev bench with ERPNext and the core app, CI

**Files:** all top-level files above; `datev_erpnext/{__init__.py,hooks.py,modules.txt,patches.txt,setup.py}`, `datev_erpnext/datev_erpnext/__init__.py`, `datev_erpnext/tests/{__init__.py,test_install.py}`

**Interfaces:**
- Copy-adapt from `../frappe-datev`: pyproject (name `datev_erpnext`, description "ERPNext adapter for the datev app: Sales Invoices to DATEV with Beleglink"), editorconfig, gitignore, gitattributes, renovate (managers for frappe, erpnext, datev, eu_einvoice, frappe-bench), release-please files (`{".": "0.1.0"}`), `release.yml` unchanged, `test.yml` extended: bench init at the pinned Frappe commit, then `bench get-app` ERPNext, datev and eu_einvoice at their pinned commits (same init-fetch-checkout pattern as the core's dev bootstrap), install erpnext, datev, eu_einvoice, datev_erpnext, run tests.
- `.github/versions.env`: `FRAPPE_BRANCH=version-16`, `FRAPPE_COMMIT` = the core repo's value; `ERPNEXT_BRANCH=version-16` + `ERPNEXT_COMMIT` = a current version-16 commit of `https://github.com/frappe/erpnext` that installs on that Frappe commit; `DATEV_BRANCH=main` + `DATEV_COMMIT` = current HEAD of `https://github.com/EffizienzAgent/datev` (if the push has not happened yet, use the local repo's HEAD sha and note it); `EU_EINVOICE_BRANCH=version-16` + commit from `https://github.com/alyf-de/eu_einvoice`.
- dev bench: copy-adapt `../frappe-datev/dev/*` — compose project `frappe-datev-erpnext`, host port `8011:8000`, mounts `..:/workspace/frappe-datev-erpnext` and `../frappe-datev:/workspace/frappe-datev`; bootstrap installs erpnext + eu_einvoice from pinned commits, `bench get-app --soft-link /workspace/frappe-datev` and `/workspace/frappe-datev-erpnext`, site `datev-erpnext.test`, installs erpnext, eu_einvoice, datev, datev_erpnext (erpnext setup wizard skipped: create a test company in tests).
- `hooks.py`: metadata, `required_apps = ["erpnext", "datev"]`, `after_install`/`after_migrate` → `datev_erpnext.setup.apply_defaults` (no-op placeholder that later tasks fill).

- [ ] Step 1: write scaffold + `test_install.py` (`test_app_installed`, `test_core_installed` asserting `"datev"` and `"erpnext"` in installed apps).
- [ ] Step 2: `docker compose -f dev/compose.yaml up -d` + bootstrap → site lists erpnext, eu_einvoice, datev, datev_erpnext.
- [ ] Step 3: `dev/test.sh` → PASS; ruff clean.
- [ ] Step 4: commit `chore: scaffold datev_erpnext with dev bench and CI` (include this plan).

---

### Task 2: Settings and company → client lookup

**Files:** Create `datev_erpnext/datev_erpnext/doctype/datev_erpnext_settings/*`, `datev_erpnext/clients.py`, `datev_erpnext/tests/factories.py`, `datev_erpnext/tests/test_clients.py`

**Interfaces:**
- Single doctype `DATEV ERPNext Settings` (DATEV Manager write, System Manager write): `enabled` Check default 1 · `customer_number_field` Data (fieldname on Customer used as preferred debtor number, e.g. a custom field; empty = none) · `document_image_source` Select `Print format\nLatest attached PDF` default `Print format` · `print_format` Link Print Format (empty = default print format of Sales Invoice) · `posting_text_template` Data default `{customer_name}` (placeholders `{customer_name}`, `{name}`, `{po_no}`; result cut to 60 by core). Validate: `customer_number_field` must be an existing field of Customer (via `frappe.get_meta("Customer").has_field`).
- `datev_erpnext.clients.client_for(company: str) -> Document | None` — the enabled `DATEV Client` whose `company` equals the company name; none → None; more than one → `frappe.ValidationError`.
- `datev_erpnext/tests/factories.py`: `make_company(abbr="DTV") -> str` creating (once) a German test company "Datev Test GmbH" (SKR04-like accounts created by the factory: revenue 4400 "Erlöse 19 % USt", 4100 "Steuerfreie Umsätze", tax account 3806 "Umsatzsteuer 19 %", receivable 1200; set `account_number` on each), a 19 % sales tax template `"DE 19 % - DTV"` and a 0 % template `"DE steuerfrei - DTV"`, item tax templates for both, items `MIETE` (income 4400, 19 %) and `VERS` (income 4100, steuerfrei), customer `Muster Automaten GmbH` with a `tax_id`. `make_client(company, **overrides)` building a `DATEV Client` via the core's own factory pattern (consultant 29098, client 55003, SKR04, length 4, storage "E-mail to DUo" with a dummy address, export "EXTF download", master data "EXTF", debtor numbering "Source system", `company=company`). `make_invoice(company, items: list[tuple[item_code, qty, rate]], **overrides) -> Document` (draft Sales Invoice, `set_missing_values`, item tax templates from the item). Keep factories idempotent.

- [ ] Steps: failing tests `test_client_for_company`, `test_no_client`, `test_two_clients_raise`, `test_settings_rejects_unknown_field` → implement → pass → commit `feat: add settings and company client lookup`.

---

### Task 3: Customer → party account

**Files:** Create `datev_erpnext/parties.py`, `datev_erpnext/tests/test_parties.py`

**Interfaces:**
- `datev_erpnext.parties.party_account_for(client, customer: str) -> str` — calls `datev.intake.ensure_party_account(client.name, "Debtor", party_type="Customer", party=customer, party_name=<customer_name>, vat_id=<tax_id>, iban=None, preferred_number=<int value of settings.customer_number_field or None>)`. Non-numeric or empty field value → `preferred_number=None` (core preflight later reports the booking as stuck).
- `datev_erpnext.parties.register_customers(company: str) -> int` whitelisted (DATEV Manager): for the company's client, `party_account_for` every enabled customer that has at least one submitted Sales Invoice of that company or a non-empty number field; returns count. Purpose: prepare the core's initial matching.

- [ ] Steps: failing tests `test_party_account_uses_number_field`, `test_missing_number_field_value` (empty → preferred_number empty), `test_idempotent`, `test_register_customers` → implement → pass → commit `feat: map customers to DATEV party accounts`.

---

### Task 4: Sales Invoice → booking lines

**Files:** Create `datev_erpnext/lines.py`, `datev_erpnext/tests/test_lines.py`

**Interfaces:**
- `@dataclass(frozen=True) Line(line_key: str, contra_account: int, amount: Decimal, tax_rate: float, tax_code: str | None)`.
- `datev_erpnext.lines.sales_invoice_lines(invoice) -> list[Line]`:
  - Group invoice items by (income account, tax code, tax rate). Tax code = the item's `item_tax_template` or, if empty, the invoice's `taxes_and_charges`; tax rate = the effective rate applied to that item (from ERPNext v16's item-wise tax details — read the ERPNext v16 code for the current structure, `item_wise_tax_details` child table or the JSON field, and use what the installed version has).
  - Net per item = `base_net_amount`; tax per item = sum of its item-wise tax amounts (base currency); gross = net + tax. Amounts as `Decimal`, quantized to 0.01, absolute values (returns are negative in ERPNext).
  - `contra_account` = the income account's `account_number` as int; missing/non-numeric → `frappe.ValidationError` naming the account.
  - Rounding: difference between `abs(base_rounded_total or base_grand_total)` and the sum of lines is added to the line with the largest amount.
  - `line_key` = `f"{account_number}:{tax_code or '-'}:{tax_rate:g}"`.
  - Lines sorted by `line_key` for stability.
- `datev_erpnext.lines.debit_credit(invoice) -> str` = `"H"` if `is_return` else `"S"`.

- [ ] Steps: failing tests with real ERPNext invoices from the factories: `test_single_line` (MIETE 1×100 → one line 4400, 119.00, 19), `test_mixed_tax_lines` (MIETE 100 + VERS 29.75 → 4400 119.00 @19 and 4100 29.75 @0, sum = grand total), `test_discount_and_rounding` (additional discount 10 % + rounding adjustment → sum equals rounded total), `test_return_invoice` (return of the mixed invoice → same lines, positive amounts, debit_credit "H"), `test_missing_account_number` → implement → pass → commit `feat: build DATEV booking lines from sales invoices`.

---

### Task 5: Document image

**Files:** Create `datev_erpnext/images.py`, `datev_erpnext/tests/test_images.py`

**Interfaces:**
- `datev_erpnext.images.document_image(invoice) -> tuple[str, bytes]` returns `(f"{invoice.name}.pdf", pdf_bytes)`:
  - `Print format`: `frappe.get_print("Sales Invoice", invoice.name, print_format=settings.print_format or None, as_pdf=True)`; if `eu_einvoice` is installed, pass the bytes through `eu_einvoice.european_e_invoice.einvoice.attach_xml_to_pdf(invoice.name, pdf, "Sales Invoice")` (imported inside the function; on any exception keep the plain PDF and `frappe.log_error`).
  - `Latest attached PDF`: newest `File` attached to the invoice with a `.pdf` name; none → fall back to `Print format`.
- Hook `datev_erpnext_document_image` (list of dotted paths) lets other apps override: first non-None result wins, before the built-in logic.

- [ ] Steps: failing tests `test_print_pdf` (bytes start with `%PDF`), `test_latest_attachment_preferred`, `test_override_hook`, `test_einvoice_embedding_called_when_installed` (patch `frappe.get_installed_apps` and the eu_einvoice function) → implement → pass → commit `feat: render the document image for sales invoices`.

---

### Task 6: Sync on submit, backfill

**Files:** Create `datev_erpnext/sync.py`, `datev_erpnext/tests/test_sync.py`; Modify `hooks.py`

**Interfaces:**
- `hooks.py`: `doc_events = {"Sales Invoice": {"on_submit": "datev_erpnext.sync.on_submit", "before_cancel": "datev_erpnext.sync.before_cancel"}}`.
- `datev_erpnext.sync.on_submit(doc, method=None)` — if settings enabled and `client_for(doc.company)` exists: `frappe.enqueue("datev_erpnext.sync.sync_sales_invoice", name=doc.name, queue="short", enqueue_after_commit=True)`. Never raises (errors → `frappe.log_error`).
- `datev_erpnext.sync.sync_sales_invoice(name: str) -> str | None` — skip unless docstatus 1 and client exists; `party = party_account_for(client, customer)`; `filename, content = document_image(invoice)`; `document = datev.intake.upsert_document(client.name, f"Sales Invoice:{name}", direction="Outgoing", file_name=filename, content=content, document_date=invoice.posting_date, document_number=invoice.name, amount=abs(base_grand_total), currency=company currency, source_doctype="Sales Invoice", source_name=name, is_return=bool(is_return))`; for each line `datev.intake.add_booking(document, line.line_key, kind="Outgoing Invoice", party_account=party, contra_account=line.contra_account, amount=line.amount, debit_credit=debit_credit(invoice), tax_rate=line.tax_rate, tax_code=line.tax_code, posting_text=<template>, due_date=invoice.due_date)`. Returns the DATEV Document name. A `frappe.ValidationError` from lines (missing account number) → `frappe.log_error` + add a Comment on the invoice ("DATEV: …"), return None.
- `datev_erpnext.sync.backfill(company: str, from_date: str, to_date: str | None = None) -> int` whitelisted (DATEV Manager): enqueue `sync_sales_invoice` for submitted invoices of the company in the date range that have no DATEV Document yet; returns count.
- `datev_erpnext.sync.datev_status(name: str) -> dict` whitelisted (read permission on the invoice): `{document: {status, external_id}, bookings: [{line_key, status, batch}]}` or `{}`.
- `public/js/sales_invoice.js` (via `doctype_js`): on submitted invoices show a dashboard indicator from `datev_status` (green Transferred/Exported, orange Pending/Batched, red Stuck/Failed, grey none).

- [ ] Steps: failing tests (call `sync_sales_invoice` directly; patch enqueue): `test_submit_enqueues`, `test_sync_creates_document_and_bookings` (mixed invoice → 1 document, 2 bookings with correct accounts/amounts/kind/party), `test_sync_twice_is_idempotent`, `test_return_invoice` (own document, is_return, H), `test_no_client_is_skipped` (submit works, nothing created), `test_missing_account_number_comments`, `test_backfill_only_missing`, `test_datev_status` → implement → pass → commit `feat: sync submitted sales invoices to the datev app`.

---

### Task 7: Cancel guard

**Files:** Modify `datev_erpnext/sync.py`; Create `datev_erpnext/tests/test_cancel.py`

**Interfaces:**
- `datev_erpnext.sync.before_cancel(doc, method=None)` — if a DATEV Document with source key `Sales Invoice:<name>` exists: call `datev.intake.discard_document(source_key)`; its ValidationError (document already in DATEV) propagates and blocks the cancel with the core's message ("…Create a cancellation invoice instead."). No DATEV Document → nothing. An enqueued-but-not-yet-run sync for a cancelled invoice is skipped by the docstatus check in `sync_sales_invoice`.

- [ ] Steps: failing tests `test_cancel_before_upload_discards`, `test_cancel_after_upload_blocked` (set the DATEV Document to Uploaded via `db_set`), `test_cancel_without_datev_document` → implement → pass → commit `feat: block cancelling invoices that are already in DATEV`.

---

### Task 8: Translations, README, docs

**Files:** Create `datev_erpnext/locale/{main.pot,de.po}`, `datev_erpnext/tests/test_translations.py` (same two tests as the core), `README.md`, `docs/setup.md`, `docs/mapping.md`

**Interfaces:**
- README in the core repo's shape incl. a "Work in progress" notice, "No compliance promise", requirements (ERPNext v16, `datev`), optional `eu_einvoice`, install (`bench get-app https://github.com/EffizienzAgent/datev --branch version-16`, then this app), license GPL-3.
- `docs/setup.md`: settings, customer number field, DATEV Client per company, `register_customers` + initial matching, `backfill`, cancel rules (Stornorechnung instead of cancel), status indicator.
- `docs/mapping.md`: how invoices become lines (grouping, gross, rounding, returns, tax code → BU key table in the core client), what is not supported yet (purchase invoices, payments).

- [ ] Steps: failing translation tests → generate pot/po, translate → full suite + ruff green → commit `docs: add German translations, README and docs`.
