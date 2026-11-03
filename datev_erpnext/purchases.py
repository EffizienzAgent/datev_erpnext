import frappe
from frappe import _

from datev_erpnext.lines import (
	PURCHASE_TAX_TEMPLATE,
	RATE_TOLERANCE,
	Line,
	_decimal,
	_is_tax_account,
	invoice_lines,
	reject_write_off,
	tax_rows,
)

GOODS_RECEIPT_ACCOUNT_TYPES = ("Stock", "Stock Received But Not Billed", "Asset Received But Not Billed")
VALUATION_CATEGORIES = ("Valuation", "Valuation and Total")


def purchase_invoice_lines(invoice) -> list[Line]:
	_reject_unsupported(invoice)
	rows = tax_rows(invoice)
	return invoice_lines(
		invoice,
		"expense_account",
		PURCHASE_TAX_TEMPLATE,
		debit_credit(invoice),
		rows,
		_reverse_charge_rows(invoice),
	)


def debit_credit(invoice) -> str:
	return "S" if invoice.is_return else "H"


def _reject_unsupported(invoice):
	if invoice.is_paid:
		frappe.throw(
			_("Purchase invoice {0} is paid on the invoice. This is not supported yet.").format(invoice.name),
			frappe.ValidationError,
		)
	reject_write_off(invoice)
	for item in invoice.items:
		account_type = frappe.get_cached_value("Account", item.expense_account, "account_type")
		if account_type in GOODS_RECEIPT_ACCOUNT_TYPES:
			frappe.throw(
				_("Line on account {0}: goods receipts are not supported yet.").format(item.expense_account),
				frappe.ValidationError,
			)
		if item.get("enable_deferred_expense"):
			frappe.throw(
				_("Line on account {0} uses deferred expense, which is not supported yet.").format(
					item.expense_account
				),
				frappe.ValidationError,
			)


def _reverse_charge_rows(invoice) -> frozenset[str]:
	added, deducted = [], []
	for tax in invoice.get("taxes") or []:
		amount = _decimal(tax.base_tax_amount_after_discount_amount)
		if tax.get("category") in VALUATION_CATEGORIES and amount:
			frappe.throw(
				_("Tax row on account {0} has category {1}, which is not supported yet.").format(
					tax.account_head, _(tax.category)
				),
				frappe.ValidationError,
			)
		if not _is_tax_account(tax.account_head):
			continue
		(deducted if tax.get("add_deduct_tax") == "Deduct" else added).append(tax)
	template = _template_rows(invoice)
	pairs = set()
	for deduct in deducted:
		match = next(
			(
				tax
				for tax in added
				if tax.name not in pairs
				and abs((tax.rate or 0) - (deduct.rate or 0)) < RATE_TOLERANCE
				and _is_pair(template, tax, deduct, invoice.company)
			),
			None,
		)
		if not match or not deduct.rate:
			frappe.throw(
				_(
					"Tax row on account {0} deducts tax without a matching input tax row. Only reverse charge pairs are supported."
				).format(deduct.account_head),
				frappe.ValidationError,
			)
		pairs.update((match.name, deduct.name))
	return frozenset(pairs)


def _template_rows(invoice) -> set[tuple[str, str, float]]:
	if not invoice.taxes_and_charges:
		return set()
	template = frappe.get_cached_doc(PURCHASE_TAX_TEMPLATE, invoice.taxes_and_charges)
	return {(row.account_head, row.add_deduct_tax or "Add", row.rate or 0) for row in template.taxes}


def _is_pair(template: set, added, deducted, company: str) -> bool:
	return (
		(added.account_head, "Add", added.rate or 0) in template
		and (deducted.account_head, "Deduct", deducted.rate or 0) in template
		and not _regular_input_tax(added.account_head, company)
	)


def _regular_input_tax(account: str, company: str) -> bool:
	return bool(
		frappe.db.sql(
			"""
			select 1 from `tabPurchase Taxes and Charges` row
			join `tabPurchase Taxes and Charges Template` template on template.name = row.parent
			where row.parenttype = 'Purchase Taxes and Charges Template'
				and template.company = %(company)s
				and row.account_head = %(account)s
				and ifnull(row.add_deduct_tax, 'Add') = 'Add'
				and not exists (
					select 1 from `tabPurchase Taxes and Charges` other
					where other.parent = row.parent and other.parenttype = row.parenttype
						and other.add_deduct_tax = 'Deduct'
				)
			limit 1
			""",
			{"company": company, "account": account},
		)
	)


def document_field(invoice) -> str:
	bill_no = invoice.bill_no
	if not bill_no and invoice.is_return and invoice.return_against:
		bill_no = frappe.db.get_value("Purchase Invoice", invoice.return_against, "bill_no")
	if not bill_no:
		frappe.throw(
			_("Purchase invoice {0} has no supplier invoice number.").format(invoice.name),
			frappe.ValidationError,
		)
	return bill_no
