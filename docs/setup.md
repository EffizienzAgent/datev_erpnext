# Setup

How to connect ERPNext to DATEV with `datev_erpnext`. The DATEV side (clients, connectors, approval, export) is described in the [`datev` integration guide](https://github.com/EffizienzAgent/datev/blob/main/docs/integration.md).

> Work in progress: not verified against a real DATEV system. Do the [go-live sequence](#going-live) with a test client first.

## Requirements

- ERPNext v16 and the `datev` app, installed on the site.
- `host_name` set in `site_config.json` (for example `"host_name": "https://erp.example.com"`). Invoices are synced in background workers, which render the PDF without a web request; without `host_name` the print rendering cannot resolve its URLs.
- `eu_einvoice` is optional. When it is installed, its XML is embedded into the generated PDF. If embedding fails, the plain PDF is used and the error is logged.
- A user with the DATEV Manager role for settings, `register_customers` and `backfill`.
- The scheduler enabled on the site (`bench --site <site> enable-scheduler`) for the daily catch-up.

## DATEV Client per Company

Create one **DATEV Client** per ERPNext Company and set its `company` field to the exact Company name. Only enabled clients count. Invoices of a Company without any DATEV Client are ignored. If the Company's client is disabled, the invoice gets the comment `DATEV note: DATEV Client <name> is disabled, not transferred.` and an orange `DATEV: client disabled` indicator. Two enabled clients for one Company fail the sync with an error on the invoice.

## Settings

Open **DATEV ERPNext Settings**. Defaults are stored on install and migrate if nothing is saved yet.

| Field | Meaning |
| --- | --- |
| Enabled | Sync on submit, catch-up and backfill. Off: nothing is queued, and jobs that are already queued do nothing. |
| Sync from date | Invoices with a posting date on or after this date are synced on submit and by the catch-up. Empty: no automatic sync. The backfill ignores it. |
| Customer Number Field | Fieldname on Customer used as preferred debtor number. Empty: the DATEV app assigns numbers. |
| Supplier Number Field | Fieldname on Supplier used as preferred creditor number. Empty: the DATEV app assigns numbers. |
| Document Image Source | `Print format` renders the invoice; `Latest attached PDF` uses the newest PDF attached to the invoice and falls back to the print format. |
| Print Format | Used when rendering, limited to print formats of Sales Invoice. Empty: default print format of Sales Invoice. |
| Posting Text Template | Placeholders `{customer_name}`, `{name}`, `{po_no}`. The DATEV app cuts the result to 60 characters. |
| Sync Payments | Off by default. Sends Payment Entries and Journal Entries with a Bank or Cash account, see [payments](#payments). |
| Sync Purchase Invoices | Off by default. Sends Purchase Invoices with the supplier's document, see [purchase invoices](#purchase-invoices). Also releases supplier payment lines. |
| Purchase Posting Text Template | Placeholders `{supplier_name}`, `{bill_no}`, `{bill_date}`, `{name}`. Empty: `{supplier_name}`. |

### Customer numbers

If your customers carry a legacy customer number (Kundennummer), add a custom field on Customer that holds it (for example `customer_number`) and enter its fieldname in the settings. The value becomes the preferred debtor number. Customers without a numeric value get a number according to the client's numbering rule. Do not change the field after the first sync.

Supplier numbers (Kreditorennummer) work the same way with **Supplier Number Field** on Supplier.

## Document image

The first source that returns a file wins: hook `datev_erpnext_document_image` (a function `fn(invoice) -> (file_name, bytes) | None`), then the configured source. Use the hook for a custom renderer.

The document image is rendered before the invoice is locked, so a payment booked while the sync runs does not wait for the PDF. If the invoice changes in the meantime, it is rendered once more.

### E-invoices

With `eu_einvoice` installed and the source `Print format`, the XML is embedded into the PDF only if the invoice has a ZUGFeRD profile (for example `EN 16931`). Invoices with the profile `XRECHNUNG` or without a profile get the plain PDF: an XRechnung cannot be embedded.

DATEV should receive the file the customer received. If you send something else (a pure XRechnung XML, a PDF from another system), supply exactly that file through the hook, for example by returning the XML or PDF attached to the invoice when it was sent:

```python
# hooks.py of your app
datev_erpnext_document_image = ["my_app.datev.sent_file"]

# my_app/datev.py
import frappe


def sent_file(invoice):
	file = frappe.db.get_value(
		"File",
		{
			"attached_to_doctype": "Sales Invoice",
			"attached_to_name": invoice.name,
			"file_name": ("like", "%.xml"),
		},
		"name",
	)
	if not file:
		return None
	doc = frappe.get_doc("File", file)
	return doc.file_name, doc.get_content()
```

Return `None` to fall back to the configured source.

With `Latest attached PDF`, the sync takes whatever PDF is attached when the job runs. A PDF attached before submit (for example an imported original) wins over the rendered print format; attach the final invoice PDF before submitting.

## Purchase invoices

With **Sync Purchase Invoices** on, a submitted Purchase Invoice (not opening) with a posting date on or after **Sync from date** is sent with the supplier's original file. The file is taken from, in this order:

1. the hook `datev_erpnext_document_image` (it receives Purchase Invoices too; check `invoice.doctype`),
2. with `eu_einvoice` installed: the file of the **E Invoice Import** linked to the invoice (XML or ZUGFeRD PDF),
3. the newest PDF or XML file attached to the invoice, ignoring files whose name starts with the invoice name (generated by ERPNext, for example a printed PDF),
4. for a debit note (return): the invoice's default print format as PDF.

Without a file, the invoice is not sent and gets the comment `DATEV note: no supplier document attached, not transferred.` Attach the file and the daily catch-up sends it. A print format is never rendered for a supplier invoice.

**Turning it on later:** the switch is not limited to new invoices. The daily catch-up sends every submitted Purchase Invoice with a posting date on or after **Sync from date** that is not in DATEV yet. If you booked supplier invoices in DATEV by hand until now, set **Sync from date** past them first or they are booked twice.

Belegfeld 1 is the supplier invoice number (`bill_no`), which is required. Add one row per purchase tax code to the client's tax keys; set its direction to `Incoming` if the same template is used on sales invoices with a different BU key. A reverse charge template needs a BU key (see [mapping](mapping.md#reverse-charge)). For invoices created from an E Invoice Import see [e-invoice imports](mapping.md#e-invoice-imports). Cancelling works like for sales invoices: blocked once the document is uploaded, correct with a debit note instead.

## Payments

With **Sync Payments** on, a submitted Payment Entry and a submitted Journal Entry that touches a Bank or Cash account (not opening) with a posting date on or after **Sync from date** become bookings without a document file (status Not Required in the core). Journal Entries without a bank or cash account are ignored. How each voucher becomes lines is described in [mapping](mapping.md#payments).

- A line that clears an invoice carries the invoice number in Belegfeld 1 and waits in the core until that invoice is in DATEV. Invoices before **Sync from date** are assumed to be in DATEV already; their payments go out right away.
- If the referenced invoice has no DATEV Document yet (its job has not run or it failed), the payment is not sent and gets the note `DATEV note: Waiting for ...`; the orange indicator shows `DATEV: waiting`. The daily catch-up retries.
- Lines for Purchase Invoices on or after **Sync from date** wait until **Sync Purchase Invoices** is on (note `waiting for purchase invoices to be synced`). A payment for a purchase invoice that is rejected (see [mapping](mapping.md#rejected-purchase-invoices)) waits until the invoice is fixed and in DATEV.
- When an invoice reaches the core, payments that reference it are queued again right away, so waiting payments do not wait for the daily catch-up.
- A waiting payment is recorded in **DATEV Payment Check** with the invoice it waits for and the reason. The catch-up re-checks it only when that invoice changes or reaches DATEV, and otherwise once a week, so many waiting payments cannot crowd out real changes.
- **Cancel:** before transfer, the payment's document and open bookings are discarded. After transfer, the cancel adds reversal lines (general reversal, posting text prefixed "Storno"). While a booking of the payment is in an approved or sending batch, cancelling is blocked; try again after the batch is done.
- **Re-allocation:** Payment Reconciliation, Unreconcile Payment and advance allocation change a submitted payment's allocation. The adapter recomputes the lines after these changes and in the daily catch-up: lines already in DATEV that changed get a reversal and a new line, unchanged lines stay, open lines are updated in place.

## Going live

1. Install, set `host_name`, create and test the DATEV Client with a test mandate, fill the settings. Add one row per tax code (Sales Taxes and Charges Template or Item Tax Template name) to the client's tax keys; leave the BU key empty for automatic accounts. Use direction `Any` unless a template needs different BU keys on sales (`Outgoing`) and purchases (`Incoming`).
2. Set **Sync from date** to the go-live date. Leave it empty while you import invoices or run ERPNext in parallel with your old system: every invoice submitted on or after this date is sent to DATEV, imported or not, which can book revenue twice. The daily catch-up also picks up every submitted invoice with a posting date on or after this date that is not in DATEV yet.
3. Run `register_customers` for the Company. It creates the party accounts of all customers with submitted invoices (and all customers with a number in the customer number field). Disabled customers are skipped.

   ```
   bench --site <site> execute datev_erpnext.parties.register_customers --kwargs '{"company": "Example GmbH"}'
   ```
4. Run the core's initial matching on the client (**Match DATEV accounts** or **Match from file**), so existing DATEV accounts are linked instead of duplicated. Decide the parties that end up "To Clarify".
5. If needed, run the backfill for invoices before the sync from date. Run `register_customers` first, so the backfill jobs do not create party accounts concurrently.
6. Check stuck items: the DATEV status report and the report **Vouchers without DATEV**.
7. Turn on the other sources one after the other, in this order: sales invoices first, then **Sync Payments** once the sales invoices reach DATEV, then **Sync Purchase Invoices** (which also releases the held supplier payments). Payments clear open items in DATEV only if the invoices they clear are there with the same Belegfeld 1.

### Backfill

`datev_erpnext.sync.backfill(company, from_date, to_date=None)` is a POST method for DATEV Managers. It queues every submitted invoice of the Company in the period that has no DATEV Document yet, on the long queue, and returns the count. It requires **Enabled**, ignores **Sync from date** and skips opening invoices. It is safe to run again. Run `register_customers` before it.

```
bench --site <site> execute datev_erpnext.sync.backfill --kwargs '{"company": "Example GmbH", "from_date": "2026-01-01"}'
```

## Daily operation

Submitting a Sales Invoice on or after the sync from date queues one job per invoice (deduplicated by job id). Opening invoices (`is_opening`) and invoices without non-zero lines are skipped. The job builds the lines, ensures the debtor account, creates the document with the PDF and adds the bookings. The core takes it from there (batches, approval, export).

**Status indicator.** A submitted invoice shows `DATEV: <status>` in its dashboard: grey `not transferred`, orange while pending or batched, green when transferred or exported, red when stuck or failed. It shows a red `DATEV: error` when the latest error comment written by the sync is newer than the invoice's DATEV Document, or there is such a comment and no document. Notes (`DATEV note: ...`) never turn it red. Comments you write by hand do not count, even if they start with `DATEV:`.

**Failures.** Every sync failure, whether the invoice cannot be mapped (see [rejected cases](mapping.md#rejected-cases)) or something else breaks (for example PDF rendering), rolls back completely, logs an error and adds a comment `DATEV: <reason>` on the invoice. A comment is not added again while it is still the latest DATEV comment of the invoice. Fix the cause; the daily catch-up retries the invoice, or run `backfill` for that date to retry it now. Nothing is partially created, so the invoice is picked up again.

**Backdated invoices.** An invoice with a posting date before **Sync from date** is not sent. If it is submitted after the go-live, it gets the comment `DATEV note: posting date before sync from date, not transferred.`, so it does not go missing silently. The indicator stays grey (`not transferred`). Decide with your tax advisor whether it belongs in DATEV; if so, send it with the backfill.

### Catch-up and report

A daily job queues every submitted, non-opening voucher (sales invoices, and purchase invoices and payments if switched on) of a Company with an enabled DATEV Client that has a posting date on or after **Sync from date** and no DATEV Document, at most 500 per source and run. It also re-checks payments whose ledger entries changed since the adapter last compared them (kept in **DATEV Payment Check**), newest changes first, at most 500 per voucher type and run. Invoices without a DATEV comment since their last change come first, then by posting date, so invoices that keep failing do not block the others. It catches invoices whose job was lost or failed for a temporary reason. An invoice that keeps failing is retried every day; its failure comment is not repeated.

The script report **Vouchers without DATEV** (DATEV User, DATEV Manager, Accounts Manager) lists the same vouchers for all Companies with a DATEV Client, with the latest error or note written by the sync as the reason. Filter by Company and voucher type. Check it regularly; an empty report means every voucher since the go-live is in DATEV. It replaces the report **Sales Invoices without DATEV** of earlier versions, which is removed on migrate.

## Cancelling

An invoice whose DATEV Document is still open (Pending or Stuck) can be cancelled; the document and its open bookings are discarded. Once it is uploaded to DATEV, cancelling is blocked: create a Stornorechnung (return) instead. Invoice and Stornorechnung are both sent, the Stornorechnung with the opposite side.

Tell your team before go-live: an invoice that went to DATEV is corrected with a Stornorechnung and a new invoice, never by cancelling and amending. Usually the upload happens within minutes after submit, so in practice cancelling works only right after submitting.
