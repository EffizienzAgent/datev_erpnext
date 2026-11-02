# Setup

How to connect ERPNext to DATEV with `datev_erpnext`. The DATEV side (clients, connectors, approval, export) is described in the [`datev` integration guide](https://github.com/EffizienzAgent/datev/blob/main/docs/integration.md).

> Work in progress: not verified against a real DATEV system. Do the [go-live sequence](#going-live) with a test client first.

## Requirements

- ERPNext v16 and the `datev` app, installed on the site.
- `host_name` set in `site_config.json` (for example `"host_name": "https://erp.example.com"`). Invoices are synced in background workers, which render the PDF without a web request; without `host_name` the print rendering cannot resolve its URLs.
- `eu_einvoice` is optional. When it is installed, its XML is embedded into the generated PDF. If embedding fails, the plain PDF is used and the error is logged.
- A user with the DATEV Manager role for settings, `register_customers` and `backfill`.

## DATEV Client per Company

Create one **DATEV Client** per ERPNext Company and set its `company` field to the exact Company name. Only enabled clients count. Invoices of a Company without an enabled client are ignored; two enabled clients for one Company fail the sync with an error on the invoice.

## Settings

Open **DATEV ERPNext Settings**. Defaults are stored on install and migrate if nothing is saved yet.

| Field | Meaning |
| --- | --- |
| Enabled | Sync on submit. Off: nothing is queued. |
| Customer Number Field | Fieldname on Customer used as preferred debtor number. Empty: the DATEV app assigns numbers. |
| Document Image Source | `Print format` renders the invoice; `Latest attached PDF` uses the newest PDF attached to the invoice and falls back to the print format. |
| Print Format | Used when rendering. Empty: default print format of Sales Invoice. |
| Posting Text Template | Placeholders `{customer_name}`, `{name}`, `{po_no}`. The DATEV app cuts the result to 60 characters. |

### Customer numbers

If your customers carry a legacy customer number (Kundennummer), add a custom field on Customer that holds it (for example `customer_number`) and enter its fieldname in the settings. The value becomes the preferred debtor number. Customers without a numeric value get a number according to the client's numbering rule. Do not change the field after the first sync.

## Document image

The first source that returns a file wins: hook `datev_erpnext_document_image` (a function `fn(invoice) -> (file_name, bytes) | None`), then the configured source. Use the hook for a custom renderer.

## Going live

1. Install, set `host_name`, create and test the DATEV Client with a test mandate, fill the settings.
2. Run `register_customers` for the Company. It creates the party accounts of all customers with submitted invoices (and all customers with a number in the customer number field). Disabled customers are skipped.

   ```
   bench --site <site> execute datev_erpnext.parties.register_customers --kwargs '{"company": "Example GmbH"}'
   ```
3. Run the core's initial matching on the client (**Match DATEV accounts** or **Match from file**), so existing DATEV accounts are linked instead of duplicated. Decide the parties that end up "To Clarify".
4. Run the backfill from the go-live date.
5. Check stuck items: the DATEV status report, plus comments on invoices that failed.

### Backfill

`datev_erpnext.sync.backfill(company, from_date, to_date=None)` is a POST method for DATEV Managers. It queues every submitted invoice of the Company in the period that has no DATEV Document yet, on the long queue, and returns the count. It is safe to run again.

```
bench --site <site> execute datev_erpnext.sync.backfill --kwargs '{"company": "Example GmbH", "from_date": "2026-01-01"}'
```

## Daily operation

Submitting a Sales Invoice queues one job per invoice (deduplicated by job id). The job builds the lines, ensures the debtor account, creates the document with the PDF and adds the bookings. The core takes it from there (batches, approval, export).

**Status indicator.** A submitted invoice shows `DATEV: <status>` in its dashboard: grey `not transferred`, orange while pending or batched, green when transferred or exported, red when stuck or failed.

**Failures.** Other failures, such as PDF rendering errors, show up only in the Error Log and as failed background jobs. If an invoice cannot be mapped (see [rejected cases](mapping.md#rejected-cases)), the sync rolls back completely, logs an error and adds a comment `DATEV: <reason>` on the invoice. Fix the cause and run `backfill` for that date; nothing is partially created, so the invoice is picked up again.

## Cancelling

An invoice whose DATEV Document is still open (Pending or Stuck) can be cancelled; the document and its open bookings are discarded. Once it is in DATEV, cancelling is blocked: create a Stornorechnung (return) instead. Invoice and Stornorechnung are both sent, the Stornorechnung with the opposite side.
