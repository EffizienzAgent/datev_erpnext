import re

import frappe
from datev.intake import add_booking, discard_document, discard_open_bookings, upsert_document
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from datev_erpnext import sync
from datev_erpnext.clients import client_for
from datev_erpnext.parties import party_account_for
from datev_erpnext.payments import Waiting, allocation_lines, voucher_lines

DOCUMENT = "DATEV Document"
CHECK = "DATEV Payment Check"
BOOKING = "DATEV Booking"
DELIVERED = ("Transferred", "Exported")
REVERSAL = "storno:"
REVERSAL_TEXT = "Storno"
COMPARED = ("party_account", "account", "contra_account", "debit_credit", "document_field_1")
VERSION = re.compile(r"#\d+$")


def sync_payment(doctype: str, name: str, from_backfill: bool = False) -> str | None:
	settings = frappe.get_single(sync.SETTINGS)
	if not settings.enabled or not settings.sync_payments:
		return None
	voucher = frappe.get_doc(doctype, name)
	if not _should_sync(voucher, from_backfill):
		return None
	checked_at = now_datetime()
	try:
		client = client_for(voucher.company)
		if not client:
			sync._comment_if_client_disabled(voucher)
			return None
		locked = frappe.get_doc(doctype, name, for_update=True)
		if not _should_sync(locked, from_backfill):
			return None
		document = _sync(client, locked)
		mark_checked(doctype, name, checked_at)
		return document
	except Waiting as waiting:
		sync._note(voucher, sync.WAITING, str(waiting))
		mark_checked(doctype, name, checked_at, waiting)
		return None
	except Exception as error:
		frappe.db.rollback()
		frappe.log_error(f"DATEV sync failed for {doctype} {name}")
		sync._comment(voucher, error)
		frappe.db.commit()
		return None


def _should_sync(voucher, from_backfill: bool) -> bool:
	if voucher.docstatus != 1 or sync._is_opening(voucher) or not sync.is_relevant(voucher):
		return False
	return from_backfill or sync._in_sync_period(voucher)


def _sync(client, voucher) -> str | None:
	lines = voucher_lines(voucher.doctype, voucher.name)
	key = sync.source_key(voucher.name, voucher.doctype)
	document = frappe.db.get_value(DOCUMENT, {"source_key": key}, "name")
	if not document and not lines:
		sync._note(voucher, sync.NOT_TRANSFERRED, _("no booking lines, not transferred."))
		return None
	if not document:
		document = upsert_document(
			client.name,
			key,
			direction="Other",
			content=None,
			document_date=voucher.posting_date,
			document_number=voucher.name,
			amount=_amount(voucher),
			currency=frappe.get_cached_value("Company", voucher.company, "default_currency"),
			source_doctype=voucher.doctype,
			source_name=voucher.name,
		)
	allocations = allocation_lines(voucher)
	apply_lines(document, key, [_values(client, line) for line in lines])
	for row, (posting_date, row_lines) in allocations.items():
		_sync_allocation(client, voucher, row, posting_date, row_lines)
	for row_key in _allocation_keys(voucher):
		if row_key.rsplit(":", 1)[-1] not in allocations:
			apply_lines(frappe.db.get_value(DOCUMENT, {"source_key": row_key}, "name"), row_key, [])
	return document


def _allocation_keys(voucher) -> list[str]:
	prefix = sync.source_key(voucher.name, voucher.doctype) + ":"
	return frappe.get_all(DOCUMENT, filters={"source_key": ("like", f"{prefix}%")}, pluck="source_key")


def _sync_allocation(client, voucher, row: str, posting_date, lines: list) -> None:
	key = f"{sync.source_key(voucher.name, voucher.doctype)}:{row}"
	document = frappe.db.get_value(DOCUMENT, {"source_key": key}, "name")
	if not document and not lines:
		return
	if not document:
		document = upsert_document(
			client.name,
			key,
			direction="Other",
			content=None,
			document_date=posting_date,
			document_number=voucher.name,
			amount=sum(line.amount for line in lines),
			currency=frappe.get_cached_value("Company", voucher.company, "default_currency"),
			source_doctype=voucher.doctype,
			source_name=voucher.name,
		)
	apply_lines(document, key, [_values(client, line) for line in lines])


def _amount(voucher) -> float:
	if voucher.doctype == "Journal Entry":
		return voucher.total_debit
	if voucher.payment_type == "Receive":
		return voucher.base_received_amount
	return voucher.base_paid_amount


def _values(client, line) -> frappe._dict:
	return frappe._dict(
		line_key=line.line_key,
		party_account=party_account_for(client, line.party, line.party_type) if line.party else None,
		account=line.account_number,
		contra_account=line.contra_account_number,
		amount=line.amount,
		debit_credit=line.debit_credit,
		document_field_1=line.document_field,
		depends_on_document=line.depends_on,
		posting_text=line.posting_text,
		general_reversal=False,
	)


def apply_lines(document: str, source_key: str, lines: list) -> None:
	frappe.db.get_value(DOCUMENT, document, "name", for_update=True)
	bookings = frappe.get_all(
		BOOKING,
		filters={"document": document},
		fields=["name", "line_key", "status", "batch", "amount", "posting_text", *COMPARED],
	)
	delivered = [booking for booking in bookings if booking.status in DELIVERED]
	target = _target(delivered, lines)
	pending = [booking for booking in bookings if booking.status not in DELIVERED]
	if all(_matches(booking, target.get(booking.line_key)) for booking in pending) and len(pending) == len(
		target
	):
		return
	_check_not_sending(pending)
	if any(
		booking.line_key not in target
		or (booking.status == "Batched" and not _matches(booking, target[booking.line_key]))
		for booking in pending
	):
		discard_open_bookings(source_key)
	for values in target.values():
		_add(document, values)


def _target(delivered: list, lines: list) -> dict:
	reversed_keys = {
		booking.line_key[len(REVERSAL) :] for booking in delivered if booking.line_key.startswith(REVERSAL)
	}
	active = {
		VERSION.sub("", booking.line_key): booking
		for booking in delivered
		if not booking.line_key.startswith(REVERSAL) and booking.line_key not in reversed_keys
	}
	used = {booking.line_key for booking in delivered}
	target = {}
	for line in lines:
		sent = active.pop(line.line_key, None)
		if sent and _matches(sent, line):
			continue
		if sent:
			target[REVERSAL + sent.line_key] = _reversal(sent)
		key = _free_key(line.line_key, used)
		target[key] = frappe._dict(line, line_key=key)
	for sent in active.values():
		target[REVERSAL + sent.line_key] = _reversal(sent)
	return target


def _free_key(key: str, used: set) -> str:
	version = 1
	candidate = key
	while candidate in used:
		version += 1
		candidate = f"{key}#{version}"
	return candidate


def _reversal(booking) -> frappe._dict:
	return frappe._dict(
		line_key=REVERSAL + booking.line_key,
		party_account=booking.party_account,
		account=booking.account,
		contra_account=booking.contra_account,
		amount=booking.amount,
		debit_credit=booking.debit_credit,
		document_field_1=booking.document_field_1,
		depends_on_document=None,
		posting_text=f"{REVERSAL_TEXT} {booking.posting_text or ''}".strip(),
		general_reversal=True,
	)


def _matches(booking, line) -> bool:
	if line is None:
		return False
	same = all((booking.get(field) or None) == (line.get(field) or None) for field in COMPARED)
	return same and flt(booking.amount, 2) == flt(line.amount, 2)


def _check_not_sending(bookings: list) -> None:
	for booking in bookings:
		if booking.status != "Batched":
			continue
		if frappe.db.get_value("DATEV Batch", booking.batch, "status") != "Awaiting Approval":
			frappe.throw(
				_(
					"DATEV line {0} is in batch {1}, which is being sent. Try again once the batch is done."
				).format(booking.line_key, booking.batch)
			)


def _add(document: str, values) -> None:
	add_booking(
		document,
		values.line_key,
		kind="Payment",
		party_account=values.party_account,
		account=values.account,
		contra_account=values.contra_account,
		amount=values.amount,
		debit_credit=values.debit_credit,
		posting_text=values.posting_text,
		depends_on_document=values.depends_on_document,
		general_reversal=values.general_reversal,
		document_field_1=values.document_field_1,
	)


def before_cancel(doc, method=None):
	for key in _allocation_keys(doc):
		_cancel(key)
	_cancel(sync.source_key(doc.name, doc.doctype))


def _cancel(key: str):
	document = frappe.db.get_value(DOCUMENT, {"source_key": key}, "name", for_update=True)
	if not document:
		return
	if frappe.db.exists(BOOKING, {"document": document, "status": ("in", DELIVERED)}):
		apply_lines(document, key, [])
	else:
		discard_open_bookings(key)
		discard_document(key)


def on_update_after_submit(doc, method=None):
	_queue_recompute(doc.doctype, doc.name)


def on_unreconcile(doc, method=None):
	_queue_recompute(doc.voucher_type, doc.voucher_no)


def _queue_recompute(doctype: str, name: str):
	if doctype not in sync.PAYMENT_DOCTYPES:
		return
	try:
		if frappe.db.exists(DOCUMENT, {"source_key": sync.source_key(name, doctype)}):
			sync.enqueue_sync(doctype, name, queue="short", enqueue_after_commit=True)
	except Exception:
		frappe.log_error(f"DATEV recompute could not be queued for {doctype} {name}")


def mark_checked(doctype: str, name: str, checked_at, waiting: Waiting | None = None) -> None:
	key = sync.source_key(name, doctype)
	values = {
		"checked_at": checked_at,
		"waiting_doctype": waiting.doctype if waiting else None,
		"waiting_name": waiting.name if waiting else None,
		"waiting_reason": str(waiting) if waiting else None,
	}
	if frappe.db.exists(CHECK, key):
		frappe.db.set_value(CHECK, key, values, update_modified=False)
	else:
		frappe.get_doc({"doctype": CHECK, "source_key": key, **values}).insert(ignore_permissions=True)


WAITING_RECHECK_DAYS = 7


def recheck_condition(key_sql: str) -> str:
	return f"""not exists (
		select 1 from `tabDATEV Payment Check` checked
		where checked.name = {key_sql}
			and ifnull(checked.waiting_name, '') != ''
			and checked.checked_at > now() - interval {WAITING_RECHECK_DAYS} day
			and not exists (
				select 1 from `tabDATEV Document` waited
				where waited.source_key = concat(checked.waiting_doctype, ':', checked.waiting_name)
					and waited.creation > checked.checked_at
			)
			and not exists (
				select 1 from `tabSales Invoice` invoice
				where checked.waiting_doctype = 'Sales Invoice'
					and invoice.name = checked.waiting_name and invoice.modified > checked.checked_at
			)
			and not exists (
				select 1 from `tabPurchase Invoice` invoice
				where checked.waiting_doctype = 'Purchase Invoice'
					and invoice.name = checked.waiting_name and invoice.modified > checked.checked_at
			)
	)"""


def changed_payments(companies: list[str], doctype: str, limit: int | None = None) -> list[str]:
	if not companies:
		return []
	return frappe.db.sql_list(
		f"""
		select name from (
			select voucher.name,
				greatest(
					ifnull((select max(ledger.modified) from `tabPayment Ledger Entry` ledger
						where ledger.voucher_type = %(doctype)s and ledger.voucher_no = voucher.name), '1970-01-01'),
					ifnull((select max(entry.modified) from `tabGL Entry` entry
						where entry.voucher_type = %(doctype)s and entry.voucher_no = voucher.name), '1970-01-01')
				) as changed_at,
				ifnull(
					(select checked.checked_at from `tabDATEV Payment Check` checked
					where checked.name = document.source_key),
					document.creation
				) as checked_at
			from `tab{doctype}` voucher
			join `tabDATEV Document` document
				on document.source_key = concat(%(doctype)s, ':', voucher.name)
			where voucher.company in %(companies)s
				and voucher.docstatus = 1
				and {recheck_condition("document.source_key")}
		) candidates
		where changed_at > checked_at
		order by changed_at desc
		{"limit %(limit)s" if limit else ""}
		""",
		{"doctype": doctype, "companies": tuple(companies), "limit": limit},
	)
