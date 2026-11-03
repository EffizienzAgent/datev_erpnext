from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal

import frappe
from frappe import _

CENT = Decimal("0.01")
RATE_TOLERANCE = 0.001
ITEM_TAX_TEMPLATE = "Item Tax Template"
SALES_TAX_TEMPLATE = "Sales Taxes and Charges Template"
PURCHASE_TAX_TEMPLATE = "Purchase Taxes and Charges Template"
ON_NET_TOTAL = "On Net Total"


@dataclass(frozen=True)
class Line:
	line_key: str
	contra_account: int
	amount: Decimal
	tax_rate: float
	tax_code: str
	debit_credit: str
	reverse_charge: bool = False


def _decimal(value) -> Decimal:
	return Decimal(str(value or 0)).quantize(CENT)


def account_number(account: str) -> int:
	number = frappe.get_cached_value("Account", account, "account_number")
	try:
		return int(str(number).strip())
	except TypeError, ValueError:
		frappe.throw(_("Account {0} has no numeric account number").format(account), frappe.ValidationError)


def _is_tax_account(account: str) -> bool:
	return frappe.get_cached_value("Account", account, "account_type") == "Tax"


def tax_rows(invoice) -> dict[str, str]:
	rows = {}
	for tax in invoice.get("taxes") or []:
		amount = _decimal(tax.base_tax_amount_after_discount_amount)
		if _is_tax_account(tax.account_head):
			if tax.charge_type != ON_NET_TOTAL and amount:
				frappe.throw(
					_("Tax row on account {0} uses charge type {1}, which is not supported yet.").format(
						tax.account_head, _(tax.charge_type)
					),
					frappe.ValidationError,
				)
			rows[tax.name] = tax.account_head
		elif amount:
			frappe.throw(
				_("Charges on account {0} are not supported yet").format(tax.account_head),
				frappe.ValidationError,
			)
	return rows


def _item_taxes(invoice, rows: dict[str, str]) -> dict[str, tuple[float, Decimal]]:
	taxes = defaultdict(lambda: (0.0, Decimal(0)))
	for row in invoice.get("item_wise_tax_details") or []:
		if row.tax_row not in rows:
			continue
		rate, amount = taxes[row.item_row]
		taxes[row.item_row] = (rate + (row.rate or 0), amount + _decimal(row.amount))
	return taxes


def _opposite(side: str) -> str:
	return "S" if side == "H" else "H"


def _tax_code(invoice, item, account: str, template: str) -> tuple[str, str]:
	if item.item_tax_template:
		return ITEM_TAX_TEMPLATE, item.item_tax_template
	if invoice.taxes_and_charges:
		return template, invoice.taxes_and_charges
	if template == SALES_TAX_TEMPLATE:
		message = _(
			"Line on account {0} has no tax template. Set an item tax template or a sales taxes template."
		)
	else:
		message = _(
			"Line on account {0} has no tax template. Set an item tax template or a purchase taxes template."
		)
	frappe.throw(message.format(account), frappe.ValidationError)


def _template_rates(doctype: str, name: str) -> list[tuple[str, float]]:
	template = frappe.get_cached_doc(doctype, name)
	if doctype == ITEM_TAX_TEMPLATE:
		rows = [(row.tax_type, row.tax_rate) for row in template.taxes if not row.not_applicable]
	else:
		rows = [
			(row.account_head, -row.rate if row.get("add_deduct_tax") == "Deduct" else row.rate)
			for row in template.taxes
			if row.charge_type == ON_NET_TOTAL
		]
	return [(account, rate or 0) for account, rate in rows if _is_tax_account(account)]


def _expected_rate(invoice, doctype: str, name: str, accounts: set[str], excluded: set[str]) -> float:
	rates = [(account, rate) for account, rate in _template_rates(doctype, name) if account not in excluded]
	on_invoice = [rate for account, rate in rates if account in accounts]
	if not on_invoice and any(rate for _account, rate in rates):
		frappe.throw(
			_("None of the tax accounts of tax template {0} is in the taxes of invoice {1}.").format(
				name, invoice.name
			),
			frappe.ValidationError,
		)
	return sum(on_invoice)


def _check_rate(account: str, code: str, expected: float, applied: float):
	if abs(expected - applied) > RATE_TOLERANCE:
		frappe.throw(
			_("Line on account {0} with tax template {1} expects a rate of {2} but {3} is applied.").format(
				account, code, f"{expected:g}%", f"{applied:g}%"
			),
			frappe.ValidationError,
		)


def reject_write_off(invoice):
	if _decimal(invoice.base_write_off_amount):
		frappe.throw(
			_("Invoice {0} has a write-off. Write-offs are not supported yet.").format(invoice.name),
			frappe.ValidationError,
		)


def sales_invoice_lines(invoice) -> list[Line]:
	reject_write_off(invoice)
	return invoice_lines(
		invoice, "income_account", SALES_TAX_TEMPLATE, debit_credit(invoice), tax_rows(invoice)
	)


def invoice_lines(
	invoice,
	account_field: str,
	template: str,
	side: str,
	rows: dict[str, str],
	reverse_charge: frozenset[str] = frozenset(),
) -> list[Line]:
	regular = {name: account for name, account in rows.items() if name not in reverse_charge}
	accounts = set(regular.values())
	excluded = {rows[name] for name in reverse_charge}
	taxes = _item_taxes(invoice, regular)
	charged = _reverse_charge_items(invoice, reverse_charge)
	expected = {}
	totals = defaultdict(Decimal)
	reverse_charge_keys = set()
	for item in invoice.items:
		account = item.get(account_field)
		rate, tax = taxes[item.name]
		doctype, code = _tax_code(invoice, item, account, template)
		if (doctype, code) not in expected:
			expected[doctype, code] = _expected_rate(invoice, doctype, code, accounts, excluded)
		_check_rate(account, code, expected[doctype, code], rate)
		key = (account_number(account), code, rate)
		totals[key] += _decimal(item.base_net_amount) + tax
		if item.name in charged:
			reverse_charge_keys.add(key)

	sign = -1 if invoice.is_return else 1
	lines = sorted(
		(
			Line(
				f"{number}:{code}:{rate:g}",
				number,
				abs(amount),
				rate,
				code,
				side if amount * sign >= 0 else _opposite(side),
				(number, code, rate) in reverse_charge_keys,
			)
			for (number, code, rate), amount in totals.items()
			if amount
		),
		key=lambda line: line.line_key,
	)
	if lines:
		_apply_rounding(invoice, lines, sign, side)
	return lines


def _reverse_charge_items(invoice, reverse_charge: frozenset[str]) -> set[str]:
	return {
		row.item_row
		for row in invoice.get("item_wise_tax_details") or []
		if row.tax_row in reverse_charge and _decimal(row.amount)
	}


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


def check_tax_keys(client, lines):
	keys = {row.tax_code: row.bu_key for row in client.tax_keys}
	for line in lines:
		if line.tax_code not in keys:
			frappe.throw(
				_(
					"Tax code {0} has no row in the tax keys of DATEV Client {1}. Add it to the DATEV Client's tax keys; leave the BU key empty for automatic accounts."
				).format(line.tax_code, client.name),
				frappe.ValidationError,
			)
		if line.reverse_charge and not keys[line.tax_code]:
			frappe.throw(
				_(
					"Tax code {0} is a reverse charge template and needs a BU key in the tax keys of DATEV Client {1}."
				).format(line.tax_code, client.name),
				frappe.ValidationError,
			)
