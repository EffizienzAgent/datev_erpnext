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

Create one **DATEV Client** per ERPNext Company and set its `company` field to the exact Company name. Only enabled clients count. Invoices of a Company without any DATEV Client are ignored. If the Company's client is disabled, the invoice gets the comment `DATEV: DATEV Client <name> is disabled, not transferred.` Two enabled clients for one Company fail the sync with an error on the invoice.

## Settings

Open **DATEV ERPNext Settings**. Defaults are stored on install and migrate if nothing is saved yet.

| Field | Meaning |
| --- | --- |
| Enabled | Sync on submit, catch-up and backfill. Off: nothing is queued, and jobs that are already queued do nothing. |
| Sync from date | Invoices with a posting date on or after this date are synced on submit and by the catch-up. Empty: no automatic sync. The backfill ignores it. |
| Customer Number Field | Fieldname on Customer used as preferred debtor number. Empty: the DATEV app assigns numbers. |
| Document Image Source | `Print format` renders the invoice; `Latest attached PDF` uses the newest PDF attached to the invoice and falls back to the print format. |
| Print Format | Used when rendering, limited to print formats of Sales Invoice. Empty: default print format of Sales Invoice. |
| Posting Text Template | Placeholders `{customer_name}`, `{name}`, `{po_no}`. The DATEV app cuts the result to 60 characters. |

### Customer numbers

If your customers carry a legacy customer number (Kundennummer), add a custom field on Customer that holds it (for example `customer_number`) and enter its fieldname in the settings. The value becomes the preferred debtor number. Customers without a numeric value get a number according to the client's numbering rule. Do not change the field after the first sync.

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

## Going live

1. Install, set `host_name`, create and test the DATEV Client with a test mandate, fill the settings. Add one row per tax code (Sales Taxes and Charges Template or Item Tax Template name) to the client's tax keys; leave the BU key empty for automatic accounts.
2. Set **Sync from date** to the go-live date. Leave it empty while you import invoices or run ERPNext in parallel with your old system: every invoice submitted on or after this date is sent to DATEV, imported or not, which can book revenue twice. The daily catch-up also picks up every submitted invoice with a posting date on or after this date that is not in DATEV yet.
3. Run `register_customers` for the Company. It creates the party accounts of all customers with submitted invoices (and all customers with a number in the customer number field). Disabled customers are skipped.

   ```
   bench --site <site> execute datev_erpnext.parties.register_customers --kwargs '{"company": "Example GmbH"}'
   ```
4. Run the core's initial matching on the client (**Match DATEV accounts** or **Match from file**), so existing DATEV accounts are linked instead of duplicated. Decide the parties that end up "To Clarify".
5. If needed, run the backfill for invoices before the sync from date. Run `register_customers` first, so the backfill jobs do not create party accounts concurrently.
6. Check stuck items: the DATEV status report and the report **Sales Invoices without DATEV**.

### Backfill

`datev_erpnext.sync.backfill(company, from_date, to_date=None)` is a POST method for DATEV Managers. It queues every submitted invoice of the Company in the period that has no DATEV Document yet, on the long queue, and returns the count. It requires **Enabled**, ignores **Sync from date** and skips opening invoices. It is safe to run again. Run `register_customers` before it.

```
bench --site <site> execute datev_erpnext.sync.backfill --kwargs '{"company": "Example GmbH", "from_date": "2026-01-01"}'
```

## Daily operation

Submitting a Sales Invoice on or after the sync from date queues one job per invoice (deduplicated by job id). Opening invoices (`is_opening`) and invoices without non-zero lines are skipped. The job builds the lines, ensures the debtor account, creates the document with the PDF and adds the bookings. The core takes it from there (batches, approval, export).

**Status indicator.** A submitted invoice shows `DATEV: <status>` in its dashboard: grey `not transferred`, orange while pending or batched, green when transferred or exported, red when stuck or failed. It shows a red `DATEV: error` when the latest `DATEV:` comment on the invoice is newer than its DATEV Document, or there is a comment and no document.

**Failures.** Every sync failure, whether the invoice cannot be mapped (see [rejected cases](mapping.md#rejected-cases)) or something else breaks (for example PDF rendering), rolls back completely, logs an error and adds a comment `DATEV: <reason>` on the invoice. The same comment is not added twice. Fix the cause; the daily catch-up retries the invoice, or run `backfill` for that date to retry it now. Nothing is partially created, so the invoice is picked up again.

**Backdated invoices.** An invoice with a posting date before **Sync from date** is not sent. If it is submitted after the go-live, it gets the comment `DATEV: posting date before sync from date, not transferred.`, so it does not go missing silently. Decide with your tax advisor whether it belongs in DATEV; if so, send it with the backfill.

### Catch-up and report

A daily job queues every submitted, non-opening invoice of a Company with an enabled DATEV Client that has a posting date on or after **Sync from date** and no DATEV Document, at most 500 per run, oldest first. It catches invoices whose job was lost or failed for a temporary reason. An invoice that keeps failing is retried every day; the same failure comment is not repeated.

The script report **Sales Invoices without DATEV** (DATEV User, DATEV Manager, Accounts Manager) lists the same invoices for all Companies with a DATEV Client, with the latest `DATEV:` comment as the reason. Filter by Company. Check it regularly; an empty report means every invoice since the go-live is in DATEV.

## Cancelling

An invoice whose DATEV Document is still open (Pending or Stuck) can be cancelled; the document and its open bookings are discarded. Once it is uploaded to DATEV, cancelling is blocked: create a Stornorechnung (return) instead. Invoice and Stornorechnung are both sent, the Stornorechnung with the opposite side.

Tell your team before go-live: an invoice that went to DATEV is corrected with a Stornorechnung and a new invoice, never by cancelling and amending. Usually the upload happens within minutes after submit, so in practice cancelling works only right after submitting.
