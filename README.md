ERPNext adapter for the datev app: Sales Invoices to DATEV with Beleglink

# DATEV for ERPNext

`datev_erpnext` sends submitted ERPNext Sales Invoices (including returns) to the [`datev`](https://github.com/EffizienzAgent/datev) app: the invoice PDF as a document with Beleglink, one booking per revenue account and tax code, and the customer as debtor account. The core app controls dispatch, approval and export; this app only feeds it, through the core's Python intake API.

> **Work in progress.** This app has not been verified against a real DATEV Rechnungswesen, DATEVconnect or Business OS yet, and neither has the core app it depends on. APIs and doctypes may change before 1.0. Use at your own risk and start with a test client. New here? Read the [setup guide](docs/setup.md).

## Status

Covered: Sales Invoices and their returns (Stornorechnungen), sent on submit from a configurable start date, plus a backfill for existing invoices. Opening invoices are not sent. Not covered yet: purchase invoices, payments, charges outside tax accounts. See [mapping](docs/mapping.md).

## No compliance promise

datev_erpnext is a technical tool. It does not make you compliant with DATEV requirements, GoBD or any other law or standard, and it gives no tax advice. Account numbers, tax keys and procedures are your responsibility. Check them with your tax advisor.

## Features

- Sync on submit as a background job, idempotent: re-running never creates a second document or booking.
- [One booking per revenue account and tax code](docs/mapping.md#lines), gross, with the rounding difference on the largest line.
- Stornorechnungen (ERPNext returns) with the opposite debit/credit side.
- Document image from a print format or the latest attached PDF; with `eu_einvoice` installed, the e-invoice XML is embedded (only with the "Print format" source). A hook (`datev_erpnext_document_image`) replaces the image source.
- [Customers as debtors](docs/setup.md#customer-numbers) with a preferred number from a field of your choice; `register_customers` to prepare the initial matching.
- [Backfill](docs/setup.md#backfill) of invoices submitted before go-live.
- Status indicator on the Sales Invoice form. Every failed sync rolls back completely, logs an error and leaves a comment `DATEV: <reason>` on the invoice.
- Invoices that are already in DATEV cannot be cancelled; create a Stornorechnung instead.
- German translations for the whole UI.

## Documentation

- [Setup](docs/setup.md): install, settings, clients, go-live, cancel rules
- [Mapping](docs/mapping.md): how an invoice becomes booking lines, rejected cases, limits

## Installation

```bash
bench get-app https://github.com/EffizienzAgent/datev --branch version-16
bench get-app https://github.com/EffizienzAgent/datev_erpnext --branch version-16
bench --site <site> install-app datev datev_erpnext
```

**Requirements:** ERPNext v16 and the `datev` app. Optional: `eu_einvoice` for e-invoice XML in the document image. Next: the [setup guide](docs/setup.md).

## Professional setup & support

Need help setting this up or integrating it into your ERPNext? Write to oss@effizienzagent.de (English or German). German-speaking SMEs: see [mittelstandshub.de](https://mittelstandshub.de).

## License

GPL-3.0
