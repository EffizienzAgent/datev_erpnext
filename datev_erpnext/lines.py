from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal

import frappe
from frappe import _

CENT = Decimal("0.01")


@dataclass(frozen=True)
class Line:
	line_key: str
	contra_account: int
	amount: Decimal
	tax_rate: float
	tax_code: str | None
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


def sales_invoice_lines(invoice) -> list[Line]:
	taxes = _item_taxes(invoice)
	totals = defaultdict(Decimal)
	for item in invoice.items:
		rate, tax = taxes[item.name]
		key = (
			_account_number(item.income_account),
			item.item_tax_template or invoice.taxes_and_charges,
			rate,
		)
		totals[key] += _decimal(item.base_net_amount) + tax

	sign = -1 if invoice.is_return else 1
	side = debit_credit(invoice)
	lines = sorted(
		(
			Line(
				f"{number}:{code or '-'}:{rate:g}",
				number,
				abs(amount),
				rate,
				code or None,
				side if amount * sign >= 0 else _opposite(side),
			)
			for (number, code, rate), amount in totals.items()
		),
		key=lambda line: line.line_key,
	)
	if lines:
		_apply_rounding(invoice, lines, sign, side)
	return lines


def _apply_rounding(invoice, lines: list[Line], sign: int, side: str):
	total = _decimal(invoice.base_rounded_total or invoice.base_grand_total) * sign
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
