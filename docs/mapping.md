# Mapping

How a Sales Invoice becomes DATEV records. All amounts are in company currency (`base_*` fields).

## Document

One DATEV Document per invoice, source key `Sales Invoice:<name>`, direction Outgoing, with the invoice date, number (the invoice name), gross total (`abs` of the rounded total) and the PDF. Stornorechnungen are their own documents with `is_return`.

## Lines

Items are grouped by revenue account number, tax code and tax rate. Each group is one booking:

- **Tax code:** the item's tax template, otherwise the invoice's taxes and charges template. The core maps the tax code to a BU key in the client's tax keys table; an empty BU key means automatic account. Configure one row per tax code on the DATEV Client.
- **Rate:** the sum of the item-wise rates of the tax rows on Tax accounts.
- **Gross:** net amount plus the item-wise tax amount of that group.
- **Line key:** `<account>:<tax code>:<rate>`, stable across re-runs, so a repeated sync updates instead of duplicating.
- **Party account:** the debtor of the customer, the **contra account** is the revenue account number.

### Debit/credit side

An invoice is booked on the debit side (`S`) of the debtor, a Stornorechnung on the credit side (`H`). Amounts are always positive; a group with a negative total (for example a discount line larger than its revenue) gets the opposite side of the document.

### Rounding

The lines must add up to the invoice's rounded total, considering the sides. A difference up to the invoice's rounding adjustment plus one cent per line is added to the largest line. A larger difference is rejected.

## Rejected cases

The invoice is not synced, everything is rolled back, an error is logged, and a comment `DATEV: <reason>` appears on the invoice. The status indicator stays grey (`not transferred`).

| Case | Reason |
| --- | --- |
| Non-tax charges | A row in Taxes and Charges on an account that is not of type Tax with a non-zero amount (for example freight) is not supported yet. |
| Missing account number | A revenue account without a numeric account number. |
| Rounding gap too large | The lines differ from the total by more than the tolerance above. |
| Invalid posting text template | The template uses unknown placeholders. |
| Client ambiguity | More than one enabled DATEV Client for the Company. |

## Not supported yet

- Purchase invoices (incoming invoices).
- Payments and bank bookings.
- Charges outside tax accounts.
- Multi-currency postings: the base amounts are sent in company currency.
