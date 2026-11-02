from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal

import frappe
from frappe import _

CENT = Decimal("0.01")
RATE_TOLERANCE = 0.001
ITEM_TAX_TEMPLATE = "Item Tax Template"
SALES_TAX_TEMPLATE = "Sales Taxes and Charges Template"


@dataclass(frozen=True)
class Line:
	line_key: str
	contra_account: int
	amount: Decimal
	tax_rate: float
	tax_code: str
	debit_credit: str


def _decimal(value) -> Decimal:
	return Decimal(str(value or 0)).quantize(CENT)


def _account_number(account: str) -> int:
	number = frappe.get_cached_value("Account", account, "account_number")
	try:
		return int(str(number).strip())
	except TypeError, ValueError:
		frappe.throw(_("Account {0} has no numeric account number").format(account), frappe.ValidationError)


def _is_tax_account(account: str) -> bool:
	return frappe.get_cached_value("Account", account, "account_type") == "Tax"


def _tax_rows(invoice) -> set[str]:
	tax_rows = set()
	for tax in invoice.get("taxes") or []:
		if _is_tax_account(tax.account_head):
			tax_rows.add(tax.name)
		elif _decimal(tax.base_tax_amount_after_discount_amount):
			frappe.throw(
				_("Charges on account {0} are not supported yet").format(tax.account_head),
				frappe.ValidationError,
			)
	return tax_rows


def _item_taxes(invoice) -> dict[str, tuple[float, Decimal]]:
	tax_rows = _tax_rows(invoice)
	taxes = defaultdict(lambda: (0.0, Decimal(0)))
	for row in invoice.get("item_wise_tax_details") or []:
		if row.tax_row not in tax_rows:
			continue
		rate, amount = taxes[row.item_row]
		taxes[row.item_row] = (rate + (row.rate or 0), amount + _decimal(row.amount))
	return taxes


def _opposite(side: str) -> str:
	return "S" if side == "H" else "H"


def _tax_code(invoice, item) -> tuple[str, str]:
	if item.item_tax_template:
		return ITEM_TAX_TEMPLATE, item.item_tax_template
	if invoice.taxes_and_charges:
		return SALES_TAX_TEMPLATE, invoice.taxes_and_charges
	frappe.throw(
		_(
			"Line on account {0} has no tax template. Set an item tax template or a sales taxes template."
		).format(item.income_account),
		frappe.ValidationError,
	)


def _expected_rate(doctype: str, name: str) -> float:
	template = frappe.get_cached_doc(doctype, name)
	if doctype == ITEM_TAX_TEMPLATE:
		rows = [(row.tax_type, row.tax_rate) for row in template.taxes]
	else:
		rows = [(row.account_head, row.rate) for row in template.taxes if row.charge_type == "On Net Total"]
	return sum(rate or 0 for account, rate in rows if _is_tax_account(account))


def _check_rate(item, code: str, expected: float, applied: float):
	if abs(expected - applied) > RATE_TOLERANCE:
		frappe.throw(
			_("Line on account {0} with tax template {1} expects a rate of {2} but {3} is applied.").format(
				item.income_account, code, f"{expected:g}%", f"{applied:g}%"
			),
			frappe.ValidationError,
		)


def sales_invoice_lines(invoice) -> list[Line]:
	if _decimal(invoice.base_write_off_amount):
		frappe.throw(
			_("Invoice {0} has a write-off. Write-offs are not supported yet.").format(invoice.name),
			frappe.ValidationError,
		)
	taxes = _item_taxes(invoice)
	expected = {}
	totals = defaultdict(Decimal)
	for item in invoice.items:
		rate, tax = taxes[item.name]
		doctype, code = _tax_code(invoice, item)
		if (doctype, code) not in expected:
			expected[doctype, code] = _expected_rate(doctype, code)
		_check_rate(item, code, expected[doctype, code], rate)
		totals[_account_number(item.income_account), code, rate] += _decimal(item.base_net_amount) + tax

	sign = -1 if invoice.is_return else 1
	side = debit_credit(invoice)
	lines = sorted(
		(
			Line(
				f"{number}:{code}:{rate:g}",
				number,
				abs(amount),
				rate,
				code,
				side if amount * sign >= 0 else _opposite(side),
			)
			for (number, code, rate), amount in totals.items()
			if amount
		),
		key=lambda line: line.line_key,
	)
	if lines:
		_apply_rounding(invoice, lines, sign, side)
	return lines


def _total(invoice) -> Decimal:
	return _decimal(invoice.base_rounded_total or invoice.base_grand_total)


def _apply_rounding(invoice, lines: list[Line], sign: int, side: str):
	if not _total(invoice):
		frappe.throw(
			_(
				"Invoice {0} has a total of zero but booking lines that are not zero. DATEV cannot take a document without an amount."
			).format(invoice.name),
			frappe.ValidationError,
		)
	total = _total(invoice) * sign
	booked = sum(line.amount if line.debit_credit == side else -line.amount for line in lines)
	difference = total - booked
	if not difference:
		return
	tolerance = abs(_decimal(invoice.base_rounding_adjustment)) + CENT * len(lines)
	if abs(difference) > tolerance:
		frappe.throw(
			_("Booking lines of invoice {0} differ from its total by {1}").format(invoice.name, difference),
			frappe.ValidationError,
		)
	largest = max(range(len(lines)), key=lambda i: lines[i].amount)
	line = lines[largest]
	adjustment = difference if line.debit_credit == side else -difference
	lines[largest] = replace(line, amount=line.amount + adjustment)


def debit_credit(invoice) -> str:
	return "H" if invoice.is_return else "S"
