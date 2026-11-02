# Mapping

How a Sales Invoice becomes DATEV records. All amounts are in company currency (`base_*` fields).

## Document

One DATEV Document per invoice, source key `Sales Invoice:<name>`, direction Outgoing, with the invoice date, number (the invoice name), gross total (`abs` of the rounded total) and the PDF. Stornorechnungen are their own documents with `is_return`.

## Lines

Items are grouped by revenue account number, tax code and tax rate. Each group is one booking:

- **Tax code:** the item's tax template, otherwise the invoice's taxes and charges template. A line without either is rejected, even if tax rows were typed in by hand. The core maps the tax code to a BU key in the client's tax keys table; an empty BU key means automatic account. Configure one row per tax code on the DATEV Client; a tax code without a row is rejected.
- **Rate:** the sum of the item-wise rates of the tax rows on Tax accounts. It must match the rate the tax code stands for: the sum of the template's rates on those Tax accounts that are also tax rows of the invoice (rows marked "Not Applicable" in an Item Tax Template count as 0; for a Sales Taxes and Charges Template only `On Net Total` rows count). So a template that also lists input tax or other output tax accounts, as the templates of German charts often do, still matches its 19 % on the invoice. A difference above 0.001 is rejected, because the BU key would not fit the booked tax. A template with a non-zero rate none of whose accounts is on the invoice is rejected as well.
- **Gross:** net amount plus the item-wise tax amount of that group.
- **Zero lines:** groups with a total of 0 are dropped. An invoice without any remaining line is not sent and gets the comment `DATEV note: no booking lines, not transferred.`
- **Line key:** `<account>:<tax code>:<rate>`, stable across re-runs, so a repeated sync updates instead of duplicating.
- **Party account:** the debtor of the customer, the **contra account** is the revenue account number.

### Debit/credit side

An invoice is booked on the debit side (`S`) of the debtor, a Stornorechnung on the credit side (`H`). Amounts are always positive; a group with a negative total (for example a discount line larger than its revenue) gets the opposite side of the document.

### Rounding

The lines must add up to the invoice's rounded total, considering the sides. A difference up to the invoice's rounding adjustment plus one cent per line is added to the largest line. A larger difference is rejected.

## Rejected cases

The invoice is not synced, everything is rolled back, an error is logged, and a comment `DATEV: <reason>` appears on the invoice. The status indicator shows a red `DATEV: error`, and the invoice is listed in the report **Sales Invoices without DATEV**.

| Case | Reason |
| --- | --- |
| Non-tax charges | A row in Taxes and Charges on an account that is not of type Tax with a non-zero amount (for example freight) is not supported yet. |
| Missing account number | A revenue account without a numeric account number. |
| Rounding gap too large | The lines differ from the total by more than the tolerance above. |
| Invalid posting text template | The template uses unknown placeholders. |
| Unknown tax code | A line's tax code has no row in the DATEV Client's tax keys. |
| No tax template | A line has neither an item tax template nor a taxes and charges template on the invoice. |
| Rate mismatch | The applied rate of a line differs from the rate of its tax template (for example the template says 19 % but 0 % is applied). |
| Tax template not on invoice | None of the tax accounts of the line's tax template is a tax row of the invoice. |
| Unsupported charge type | A tax row on a Tax account with a charge type other than `On Net Total` and a non-zero amount. |
| Write-off | The invoice has a write-off amount. Write-offs are not supported yet. |
| Zero total with lines | The invoice total is zero but its lines are not (for example a revenue line and a credit line on different accounts). DATEV cannot take a document without an amount. |
| Client ambiguity | More than one enabled DATEV Client for the Company. |

## Not supported yet

- Purchase invoices (incoming invoices).
- Payments and bank bookings.
- Charges outside tax accounts.
- Write-offs.
- Opening invoices (`is_opening`); they are skipped.
- Multi-currency postings: the base amounts are sent in company currency.
