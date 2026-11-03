# Mapping

How Sales Invoices, Purchase Invoices and payments become DATEV records. All amounts are in company currency (`base_*` fields).

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

The invoice is not synced, everything is rolled back, an error is logged, and a comment `DATEV: <reason>` appears on the invoice. The status indicator shows a red `DATEV: error`, and the invoice is listed in the report **Vouchers without DATEV**.

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

## Purchase invoices

One DATEV Document per Purchase Invoice, source key `Purchase Invoice:<name>`, direction Incoming, document number = Belegfeld 1 = the supplier invoice number (`bill_no`). A debit note (return) without its own `bill_no` uses the `bill_no` of the invoice it returns, so the open item clears. Lines follow the [sales rules](#lines) with these differences:

- **Account:** the item's expense account (or asset account, as ERPNext books it); the creditor of the supplier is the party account.
- **Tax code:** item tax template, else the invoice's Purchase Taxes and Charges Template. For the expected rate, `Deduct` rows of the template count negative.
- **Side:** an invoice is booked on the credit side (`H`) of the creditor, a debit note on the debit side (`S`).

### Reverse charge

Reverse charge (§ 13b UStG) and intra-EU acquisitions are expected as a template with an input tax row (`Add`) and an output tax row (`Deduct`) on Tax accounts at the same rate. The pair must come from the invoice's Purchase Taxes and Charges Template, and the `Add` account must not be a regular input tax account (one used in a template of the company without any `Deduct` row); use dedicated accounts for reverse charge input tax. Such a pair is recognised as reverse charge: the line goes out with its **net** amount and rate 0, and the tax code's row in the client's tax keys must carry a BU key (DATEV computes both taxes from it). An empty BU key is rejected.

### Rejected purchase invoices

| Case | Reason |
| --- | --- |
| No supplier invoice number | `bill_no` is empty (and, for a debit note, also on the returned invoice). |
| Goods receipts | A line on a Stock, Stock Received But Not Billed or Asset Received But Not Billed account. Receipts are not sent to DATEV, so the clearing account would stay open there. |
| Valuation taxes | A tax row with category `Valuation` or `Valuation and Total`. |
| Other deductions | A `Deduct` row on a Tax account without a matching `Add` row at the same rate. |
| Paid on the invoice | `is_paid` is set (cash or card purchases). |
| Deferred expense | A line with deferred expense. |
| Reverse charge without BU key | See above. |

All rejected cases of sales invoices apply as well.

## Payments

One DATEV Document without file per voucher, source key `Payment Entry:<name>` or `Journal Entry:<name>`, direction Other, document number = voucher name. Each line is a booking of kind `Payment`.

- **Bank account (pivot):** Payment Entry: `paid_to` for Receive, `paid_from` for Pay and Internal Transfer. Journal Entry: the first row on a Bank or Cash account. Its account number is the contra account of every line. A Payment Entry whose bank side is not a Bank or Cash account is rejected.
- **Lines:** every other GL entry of the voucher, grouped by account, party and referenced voucher. Amount = |debit − credit|, side from the line's perspective (debit `S`, credit `H`). Lines with an amount of 0 are dropped. Further bank or cash rows of a Journal Entry (transfer between own accounts) are lines like any other.
- **Konto:** the debtor or creditor of the party if the GL account is a Receivable account on the asset side (Customer) or a Payable account on the liability side (Supplier). Every other line, including separate advance accounts, employees or shareholders, books to the GL account number, which must be numeric.
- **Allocation:** receivable and payable lines are split by the Payment Ledger, so allocations made later (Payment Reconciliation, advance allocation, Unreconcile Payment) are reflected without cancelling the voucher.
- **Belegfeld 1:** Sales Invoice → its name; Purchase Invoice → its `bill_no` (rules as above); anything else (unallocated amount, fees, interest, transfers) → the voucher name.
- **Dependency:** a line for an invoice on or after **Sync from date** depends on that invoice's DATEV Document and waits in the core until it is in DATEV.
- **Posting text:** the party name, else the account name.
- **Line key:** `<account number>:<party>:<referenced voucher>`. A changed line gets a new key with `#2`, `#3`, …; reversals use `storno:<key>`.

Bank fees, interest and other deductions are booked to the account chosen in ERPNext, with or without a party.

### Advances on separate advance accounts

With **Book Advance Payments in Separate Party Account**, the receipt or payment itself books the advance account against the bank. When ERPNext later allocates the advance to an invoice, it moves the amount from the advance account to the receivable or payable account inside the payment, dated by the company's "Reconciliation Takes Effect On". These allocation entries do not touch the bank: each reference row of the payment becomes its own DATEV Document (source key `Payment Entry:<name>:<reference row>`, without file), dated at the allocation's posting date, with one line debtor/creditor against the advance account number and the invoice number in Belegfeld 1. Unreconciling reverses that document; cancelling the payment cancels it too.

### Re-allocation and dates

All lines of a payment, including reversals and new lines after a re-allocation, carry the payment's posting date (except allocations on separate advance accounts, see above). A re-allocation months later therefore books into the payment's month. If that month is locked in DATEV (Festschreibung) or its fiscal year is closed, the export of these lines fails and the bookings show up as Stuck or Failed in the DATEV status report; nothing is moved silently into another period. Decide with your tax advisor how to post such corrections.

## Not supported yet

- Goods receipts and stock purchases, landed costs.
- Purchase invoices paid on the invoice (`is_paid`), deferred expense.
- Charges outside tax accounts.
- Write-offs.
- Opening entries (`is_opening`); they are skipped.
- Multi-currency postings: the base amounts are sent in company currency.
- Document images for payments: in DATEV the bank statement is the voucher.
