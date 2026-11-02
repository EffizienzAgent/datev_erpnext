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


def _decimal(value) -> Decimal:
	return Decimal(str(value or 0)).quantize(CENT)


def _account_number(account: str) -> int:
	number = frappe.db.get_value("Account", account, "account_number")
	try:
		return int(str(number).strip())
	except TypeError, ValueError:
		frappe.throw(_("Account {0} has no numeric account number").format(account), frappe.ValidationError)


def _item_taxes(invoice) -> dict[str, tuple[float, Decimal]]:
	taxes = defaultdict(lambda: (0.0, Decimal(0)))
	for row in invoice.get("item_wise_tax_details") or []:
		rate, amount = taxes[row.item_row]
		taxes[row.item_row] = (rate + (row.rate or 0), amount + _decimal(row.amount))
	return taxes


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

	lines = sorted(
		(
			Line(f"{number}:{code or '-'}:{rate:g}", number, abs(amount), rate, code or None)
			for (number, code, rate), amount in totals.items()
		),
		key=lambda line: line.line_key,
	)
	if not lines:
		return lines

	difference = _decimal(abs(invoice.base_rounded_total or invoice.base_grand_total)) - sum(
		line.amount for line in lines
	)
	if difference:
		largest = max(range(len(lines)), key=lambda i: lines[i].amount)
		lines[largest] = replace(lines[largest], amount=lines[largest].amount + difference)
	return lines


def debit_credit(invoice) -> str:
	return "H" if invoice.is_return else "S"
