import hashlib
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import getdate

from datev_erpnext.lines import CENT, account_number
from datev_erpnext.purchases import document_field as purchase_document_field

BANK_TYPES = ("Bank", "Cash")
PARTY_ACCOUNTS = {("Receivable", "Asset"): "Customer", ("Payable", "Liability"): "Supplier"}
MAX_KEY_LENGTH = 120
SETTINGS = "DATEV ERPNext Settings"


class Waiting(frappe.ValidationError):
	def __init__(self, message: str, doctype: str | None = None, name: str | None = None):
		super().__init__(message)
		self.doctype, self.name = doctype, name


@dataclass(frozen=True)
class PaymentLine:
	line_key: str
	party_type: str | None
	party: str | None
	account_number: int | None
	contra_account_number: int
	amount: Decimal
	debit_credit: str
	document_field: str
	depends_on: str | None
	posting_text: str


def is_bank_voucher(doc) -> bool:
	if doc.doctype == "Payment Entry":
		return True
	return any(_is_bank(row.account) for row in doc.get("accounts") or [])


def _is_bank(account: str) -> bool:
	return frappe.get_cached_value("Account", account, "account_type") in BANK_TYPES


def pivot_account(voucher) -> str | None:
	if voucher.doctype == "Journal Entry":
		return next((row.account for row in voucher.accounts if _is_bank(row.account)), None)
	account = voucher.paid_to if voucher.payment_type == "Receive" else voucher.paid_from
	if not _is_bank(account):
		frappe.throw(
			_("Payment Entry {0} has no bank or cash account on its bank side.").format(voucher.name),
			frappe.ValidationError,
		)
	return account


def is_party_line(account: str, party_type: str | None) -> bool:
	account_type, root_type = frappe.get_cached_value("Account", account, ["account_type", "root_type"])
	return bool(party_type) and PARTY_ACCOUNTS.get((account_type, root_type)) == party_type


def voucher_lines(doctype: str, name: str) -> list[PaymentLine]:
	voucher = frappe.get_doc(doctype, name)
	pivot = pivot_account(voucher)
	if not pivot:
		return []
	contra = account_number(pivot)
	totals = _totals(voucher, pivot)
	lines = [_line(voucher, key, amount, contra) for key, amount in totals.items() if amount]
	return sorted(lines, key=lambda line: line.line_key)


def allocation_rows(voucher) -> set[str]:
	if voucher.doctype != "Payment Entry" or not voucher.get(
		"book_advance_payments_in_separate_party_account"
	):
		return set()
	return {row.name for row in voucher.references}


def _gl_entries(voucher) -> list:
	return frappe.get_all(
		"GL Entry",
		filters={"voucher_type": voucher.doctype, "voucher_no": voucher.name, "is_cancelled": 0},
		fields=[
			"account",
			"party_type",
			"party",
			"against_voucher_type",
			"against_voucher",
			"debit",
			"credit",
			"voucher_detail_no",
			"posting_date",
		],
		order_by="creation, name",
	)


def allocation_lines(voucher) -> dict[str, tuple]:
	rows = allocation_rows(voucher)
	entries = defaultdict(list)
	for entry in _gl_entries(voucher):
		if entry.voucher_detail_no in rows:
			entries[entry.voucher_detail_no].append(entry)
	return {row: _allocation(voucher, row, row_entries) for row, row_entries in entries.items()}


def _allocation(voucher, row: str, entries: list) -> tuple:
	party = [entry for entry in entries if is_party_line(entry.account, entry.party_type)]
	advance = [entry for entry in entries if entry not in party]
	posting_date = max(entry.posting_date for entry in entries)
	if not party or not advance:
		frappe.throw(
			_(
				"The advance allocation of row {0} in {1} cannot be mapped: it needs one party line on a receivable or payable account and one line on a separate advance account."
			).format(row, voucher.name),
			frappe.ValidationError,
		)
	contra = account_number(advance[0].account)
	totals = defaultdict(Decimal)
	for entry in party:
		key = (
			entry.account,
			entry.party_type,
			entry.party,
			entry.against_voucher_type or None,
			entry.against_voucher or None,
		)
		totals[key] += _decimal(entry.debit) - _decimal(entry.credit)
	lines = [_line(voucher, key, amount, contra) for key, amount in totals.items() if amount]
	return posting_date, sorted(lines, key=lambda line: line.line_key)


def _totals(voucher, pivot: str) -> dict[tuple, Decimal]:
	totals = defaultdict(Decimal)
	party_entries = defaultdict(list)
	excluded = allocation_rows(voucher)
	for entry in _gl_entries(voucher):
		if entry.account == pivot or entry.voucher_detail_no in excluded:
			continue
		amount = _decimal(entry.debit) - _decimal(entry.credit)
		key = (entry.account, entry.party_type or None, entry.party or None)
		reference = (entry.against_voucher_type or None, entry.against_voucher or None)
		if is_party_line(entry.account, entry.party_type):
			party_entries[key].append((reference, amount))
		else:
			totals[key + reference] += amount
	for key, entries in party_entries.items():
		allocations = _allocations(voucher, *key, excluded) or entries
		for reference, amount in allocations:
			totals[key + reference] += amount
	return totals


def _allocations(voucher, account: str, party_type: str, party: str, excluded: set[str]) -> list:
	rows = frappe.get_all(
		"Payment Ledger Entry",
		filters={
			"voucher_type": voucher.doctype,
			"voucher_no": voucher.name,
			"account": account,
			"party_type": party_type,
			"party": party,
			"delinked": 0,
		},
		fields=["against_voucher_type", "against_voucher_no", "amount", "account_type", "voucher_detail_no"],
	)
	return [
		(
			(row.against_voucher_type, row.against_voucher_no),
			_decimal(row.amount) * (1 if row.account_type == "Receivable" else -1),
		)
		for row in rows
		if row.voucher_detail_no not in excluded
	]


def _decimal(value) -> Decimal:
	return Decimal(str(value or 0)).quantize(CENT)


def _line(voucher, key: tuple, amount: Decimal, contra: int) -> PaymentLine:
	account, party_type, party, against_type, against = key
	document_field, depends_on = reference(voucher, against_type, against)
	party_line = is_party_line(account, party_type)
	number = frappe.get_cached_value("Account", account, "account_number") or account
	return PaymentLine(
		line_key=_line_key(f"{number}:{party or ''}:{against or voucher.name}"),
		party_type=party_type if party_line else None,
		party=party if party_line else None,
		account_number=None if party_line else account_number(account),
		contra_account_number=contra,
		amount=abs(amount.quantize(CENT)),
		debit_credit="S" if amount > 0 else "H",
		document_field=document_field,
		depends_on=depends_on,
		posting_text=_posting_text(account, party_type, party),
	)


def _line_key(key: str) -> str:
	if len(key) <= MAX_KEY_LENGTH:
		return key
	return key[: MAX_KEY_LENGTH - 17] + ":" + hashlib.sha1(key.encode()).hexdigest()[:16]


def reference(voucher, against_type: str | None, against: str | None) -> tuple[str, str | None]:
	if against_type == "Sales Invoice":
		return against, _dependency(against_type, against)
	if against_type == "Purchase Invoice":
		return _purchase_reference(against)
	return voucher.name, None


def _purchase_reference(name: str) -> tuple[str, str | None]:
	invoice = frappe.get_doc("Purchase Invoice", name)
	if _in_sync_period(invoice) and not frappe.db.get_single_value(SETTINGS, "sync_purchase_invoices"):
		raise Waiting(
			_("Line for purchase invoice {0} is waiting for purchase invoices to be synced.").format(name),
			"Purchase Invoice",
			name,
		)
	return purchase_document_field(invoice), _dependency("Purchase Invoice", name)


def _in_sync_period(invoice) -> bool:
	sync_from_date = frappe.db.get_single_value(SETTINGS, "sync_from_date")
	return (
		invoice.is_opening != "Yes"
		and bool(sync_from_date)
		and getdate(invoice.posting_date) >= getdate(sync_from_date)
	)


def _dependency(doctype: str, name: str) -> str | None:
	if not _in_sync_period(frappe.get_doc(doctype, name)):
		return None
	document = frappe.db.get_value("DATEV Document", {"source_key": f"{doctype}:{name}"}, "name")
	if not document:
		raise Waiting(
			_("Waiting for {0} {1} to be transferred to DATEV.").format(_(doctype), name), doctype, name
		)
	return document


def _posting_text(account: str, party_type: str | None, party: str | None) -> str:
	if party_type and party:
		field = f"{frappe.scrub(party_type)}_name"
		meta = frappe.get_meta(party_type)
		return (frappe.db.get_value(party_type, party, field) if meta.has_field(field) else None) or party
	return frappe.get_cached_value("Account", account, "account_name")
