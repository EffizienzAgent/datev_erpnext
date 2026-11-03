import frappe
from datev.intake import add_booking, discard_document, upsert_document
from frappe import _
from frappe.utils import getdate

from datev_erpnext.clients import client_for
from datev_erpnext.images import document_image, purchase_document_image
from datev_erpnext.lines import check_tax_keys, sales_invoice_lines
from datev_erpnext.parties import party_account_for
from datev_erpnext.payments import is_bank_voucher
from datev_erpnext.purchases import document_field as purchase_document_field
from datev_erpnext.purchases import purchase_invoice_lines

SETTINGS = "DATEV ERPNext Settings"
SALES_INVOICE = "Sales Invoice"
PURCHASE_INVOICE = "Purchase Invoice"
PAYMENT_DOCTYPES = ("Payment Entry", "Journal Entry")
SYNC_JOB = "datev_erpnext.sync.sync_sales_invoice"
PURCHASE_JOB = "datev_erpnext.sync.sync_purchase_invoice"
PAYMENT_JOB = "datev_erpnext.payment_sync.sync_payment"
CATCH_UP_LIMIT = 500
ERROR = "datev_erpnext:error"
NOT_TRANSFERRED = "datev_erpnext:not_transferred"
CLIENT_DISABLED = "datev_erpnext:client_disabled"
WAITING = "datev_erpnext:waiting"
SUBJECTS = (ERROR, NOT_TRANSFERRED, CLIENT_DISABLED, WAITING)
SOURCES = {
	"Sales Invoice": {
		"party_type": "'Customer'",
		"party": "voucher.customer",
		"party_name": "voucher.customer_name",
		"amount": "voucher.base_grand_total",
	},
	"Purchase Invoice": {
		"party_type": "'Supplier'",
		"party": "voucher.supplier",
		"party_name": "voucher.supplier_name",
		"amount": "voucher.base_grand_total",
	},
	"Payment Entry": {
		"party_type": "voucher.party_type",
		"party": "voucher.party",
		"party_name": "voucher.party_name",
		"amount": "if(voucher.payment_type = 'Receive', voucher.base_received_amount, voucher.base_paid_amount)",
	},
	"Journal Entry": {
		"party_type": "null",
		"party": "null",
		"party_name": "null",
		"amount": "voucher.total_debit",
		"condition": """and exists (
				select 1 from `tabJournal Entry Account` row
				join `tabAccount` account on account.name = row.account
				where row.parent = voucher.name and account.account_type in ('Bank', 'Cash')
			)""",
	},
}


def source_key(name: str, doctype: str = SALES_INVOICE) -> str:
	return f"{doctype}:{name}"


def enqueue_sync(doctype: str, name: str, **kwargs):
	if doctype == SALES_INVOICE:
		frappe.enqueue(SYNC_JOB, name=name, job_id=f"datev-sync-{name}", deduplicate=True, **kwargs)
	elif doctype == PURCHASE_INVOICE:
		frappe.enqueue(
			PURCHASE_JOB,
			name=name,
			job_id=f"datev-sync-{source_key(name, doctype)}",
			deduplicate=True,
			**kwargs,
		)
	else:
		frappe.enqueue(
			PAYMENT_JOB,
			doctype=doctype,
			name=name,
			job_id=f"datev-sync-{source_key(name, doctype)}",
			deduplicate=True,
			**kwargs,
		)


def _enqueue(name: str, **kwargs):
	enqueue_sync(SALES_INVOICE, name, **kwargs)


def synced_doctypes(settings) -> list[str]:
	doctypes = [SALES_INVOICE]
	if settings.sync_purchase_invoices:
		doctypes.append(PURCHASE_INVOICE)
	if settings.sync_payments:
		doctypes.extend(PAYMENT_DOCTYPES)
	return doctypes


def is_relevant(doc) -> bool:
	return doc.doctype != "Journal Entry" or is_bank_voucher(doc)


def _in_sync_period(invoice) -> bool:
	sync_from_date = frappe.get_single(SETTINGS).sync_from_date
	return bool(sync_from_date) and getdate(invoice.posting_date) >= getdate(sync_from_date)


def _is_opening(invoice) -> bool:
	return invoice.get("is_opening") == "Yes"


def on_submit(doc, method=None):
	messages = len(frappe.local.message_log)
	try:
		settings = frappe.get_single(SETTINGS)
		if (
			settings.enabled
			and settings.sync_from_date
			and not _is_opening(doc)
			and doc.doctype in synced_doctypes(settings)
			and is_relevant(doc)
		):
			_queue_on_submit(doc, getdate(settings.sync_from_date))
	except Exception as error:
		frappe.local.message_log = frappe.local.message_log[:messages]
		frappe.log_error(f"DATEV sync could not be queued for {doc.name}")
		_comment(doc, error)


def _queue_on_submit(doc, sync_from_date):
	if not client_for(doc.company):
		_comment_if_client_disabled(doc)
	elif getdate(doc.posting_date) >= sync_from_date:
		enqueue_sync(doc.doctype, doc.name, queue="short", enqueue_after_commit=True)
	elif getdate(doc.modified) >= sync_from_date:
		_note(doc, NOT_TRANSFERRED, _("posting date before sync from date, not transferred."))


def _comment_if_client_disabled(doc):
	disabled = frappe.db.get_value("DATEV Client", {"company": doc.company, "enabled": 0}, "name")
	if disabled:
		_note(doc, CLIENT_DISABLED, _("DATEV Client {0} is disabled, not transferred.").format(disabled))


def _comment(doc, error):
	_add_comment(doc, ERROR, _("DATEV: {0}").format(error))


def _note(doc, subject: str, message: str):
	_add_comment(doc, subject, _("DATEV note: {0}").format(message))


def _add_comment(doc, subject: str, text: str):
	try:
		latest = latest_comments([doc.name], doc.doctype).get(doc.name)
		if latest and latest.content == text:
			return
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"comment_email": frappe.session.user,
				"reference_doctype": doc.doctype,
				"reference_name": doc.name,
				"subject": subject,
				"content": text,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(f"DATEV comment could not be added for {doc.name}")


def before_cancel(doc, method=None):
	key = source_key(doc.name, doc.doctype)
	if frappe.db.get_value("DATEV Document", {"source_key": key}, "name", for_update=True):
		discard_document(key)


def _should_sync(invoice, from_backfill: bool) -> bool:
	if invoice.docstatus != 1 or _is_opening(invoice):
		return False
	return from_backfill or _in_sync_period(invoice)


def sync_sales_invoice(name: str, from_backfill: bool = False) -> str | None:
	return _sync_invoice(SALES_INVOICE, name, from_backfill)


def sync_purchase_invoice(name: str, from_backfill: bool = False) -> str | None:
	if not frappe.get_single(SETTINGS).sync_purchase_invoices:
		return None
	return _sync_invoice(PURCHASE_INVOICE, name, from_backfill)


def _lines(invoice):
	if invoice.doctype == PURCHASE_INVOICE:
		return purchase_invoice_lines(invoice)
	return sales_invoice_lines(invoice)


def _image(invoice):
	if invoice.doctype == PURCHASE_INVOICE:
		return purchase_document_image(invoice)
	return document_image(invoice)


def _sync_invoice(doctype: str, name: str, from_backfill: bool) -> str | None:
	if not frappe.get_single(SETTINGS).enabled:
		return None
	invoice = frappe.get_doc(doctype, name)
	if not _should_sync(invoice, from_backfill):
		return None
	try:
		client = client_for(invoice.company)
		if not client:
			_comment_if_client_disabled(invoice)
			return None
		if not _lines(invoice):
			_note(invoice, NOT_TRANSFERRED, _("no booking lines, not transferred."))
			return None
		image = _image(invoice)
		locked = frappe.get_doc(doctype, name, for_update=True)
		if not _should_sync(locked, from_backfill):
			return None
		if locked.modified != invoice.modified:
			image = _image(locked)
		if image is None:
			_note(invoice, NOT_TRANSFERRED, _("no supplier document attached, not transferred."))
			return None
		return _sync(client, locked, image)
	except Exception as error:
		frappe.db.rollback()
		frappe.log_error(f"DATEV sync failed for {doctype} {name}")
		_comment(invoice, error)
		frappe.db.commit()
		return None


def _sync(client, invoice, image: tuple[str, bytes]) -> str | None:
	lines = _lines(invoice)
	if not lines:
		return None
	check_tax_keys(client, lines)
	posting_text = _posting_text(invoice)
	purchase = invoice.doctype == PURCHASE_INVOICE
	if purchase:
		party = party_account_for(client, invoice.supplier, "Supplier")
	else:
		party = party_account_for(client, invoice.customer)
	file_name, content = image
	document = upsert_document(
		client.name,
		source_key(invoice.name, invoice.doctype),
		direction="Incoming" if purchase else "Outgoing",
		file_name=file_name,
		content=content,
		document_date=invoice.posting_date,
		document_number=purchase_document_field(invoice) if purchase else invoice.name,
		amount=abs(invoice.base_rounded_total or invoice.base_grand_total),
		currency=frappe.get_cached_value("Company", invoice.company, "default_currency"),
		source_doctype=invoice.doctype,
		source_name=invoice.name,
		is_return=bool(invoice.is_return),
	)
	for line in lines:
		add_booking(
			document,
			line.line_key,
			kind="Incoming Invoice" if purchase else "Outgoing Invoice",
			party_account=party,
			contra_account=line.contra_account,
			amount=line.amount,
			debit_credit=line.debit_credit,
			tax_rate=line.tax_rate,
			tax_code=line.tax_code,
			posting_text=posting_text,
			due_date=invoice.due_date,
		)
	return document


def _posting_text(invoice) -> str:
	settings = frappe.get_single(SETTINGS)
	if invoice.doctype == PURCHASE_INVOICE:
		template = settings.purchase_posting_text_template or "{supplier_name}"
		values = {
			"supplier_name": invoice.supplier_name or "",
			"bill_no": invoice.bill_no or "",
			"bill_date": frappe.format(invoice.bill_date, "Date") if invoice.bill_date else "",
			"name": invoice.name,
		}
	else:
		template = settings.posting_text_template or ""
		values = {
			"customer_name": invoice.customer_name or "",
			"name": invoice.name,
			"po_no": invoice.po_no or "",
		}
	try:
		return template.format(**values)
	except KeyError, IndexError, ValueError:
		frappe.throw(_("The posting text template {0} is invalid").format(template), frappe.ValidationError)


@frappe.whitelist(methods=["POST"])
def backfill(company: str, from_date: str, to_date: str | None = None) -> int:
	frappe.only_for("DATEV Manager")
	if not frappe.get_single(SETTINGS).enabled:
		frappe.throw(_("DATEV sync is disabled in DATEV ERPNext Settings."))
	if not client_for(company):
		frappe.throw(_("No enabled DATEV Client is assigned to company {0}.").format(company))

	missing = missing_invoices([company], from_date, to_date)
	for invoice in missing:
		_enqueue(invoice.name, queue="long", from_backfill=True)
	return len(missing)


def catch_up():
	settings = frappe.get_single(SETTINGS)
	if not settings.enabled or not settings.sync_from_date:
		return
	companies = frappe.get_all("DATEV Client", filters={"enabled": 1}, pluck="company", distinct=True)
	for doctype in synced_doctypes(settings):
		for voucher in missing_invoices(
			companies, settings.sync_from_date, limit=CATCH_UP_LIMIT, doctype=doctype
		):
			enqueue_sync(doctype, voucher.name, queue="long")
	if settings.sync_payments:
		from datev_erpnext.payment_sync import changed_payments

		for doctype, name in changed_payments(companies, limit=CATCH_UP_LIMIT):
			enqueue_sync(doctype, name, queue="long")


def missing_invoices(
	companies: list[str], from_date, to_date=None, limit: int | None = None, doctype: str = SALES_INVOICE
) -> list:
	if not companies:
		return []
	source = SOURCES[doctype]
	to_date_filter = "and voucher.posting_date <= %(to_date)s" if to_date else ""
	limit_clause = "limit %(limit)s" if limit else ""
	return frappe.db.sql(
		f"""
		select %(doctype)s as voucher_type, voucher.name, voucher.company, voucher.posting_date,
			{source["party_type"]} as party_type, {source["party"]} as party,
			{source["party_name"]} as party_name, {source["amount"]} as amount
		from `tab{doctype}` voucher
		where voucher.company in %(companies)s
			and voucher.docstatus = 1
			and ifnull(voucher.is_opening, '') != 'Yes'
			and voucher.posting_date >= %(from_date)s
			{to_date_filter}
			{source.get("condition", "")}
			and not exists (
				select 1 from `tabDATEV Document` document
				where document.source_key = concat(%(doctype)s, ':', voucher.name)
			)
		order by exists (
				select 1 from `tabComment` comment
				where comment.reference_doctype = %(doctype)s
					and comment.reference_name = voucher.name
					and comment.subject in %(subjects)s
					and comment.creation >= voucher.modified
			),
			voucher.posting_date, voucher.name
		{limit_clause}
		""",
		{
			"doctype": doctype,
			"companies": tuple(companies),
			"from_date": from_date,
			"to_date": to_date,
			"subjects": SUBJECTS,
			"limit": limit,
		},
		as_dict=True,
	)


def latest_comments(names: list[str], doctype: str = SALES_INVOICE) -> dict[str, frappe._dict]:
	if not names:
		return {}
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": doctype,
			"reference_name": ("in", names),
			"comment_type": "Comment",
			"subject": ("in", SUBJECTS),
		},
		fields=["reference_name", "subject", "content", "creation"],
		order_by="creation desc, name desc",
	)
	latest = {}
	for comment in comments:
		latest.setdefault(comment.reference_name, comment)
	return latest


@frappe.whitelist()
def datev_status(name: str, doctype: str = SALES_INVOICE) -> dict:
	if doctype not in SOURCES:
		frappe.throw(_("DATEV status is not available for {0}.").format(_(doctype)))
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	document = frappe.db.get_value(
		"DATEV Document",
		{"source_key": source_key(name, doctype)},
		["name", "status", "external_id", "modified"],
		as_dict=True,
	)
	if not document and not _shows_status(doctype, name):
		return {"hidden": True}
	comment = latest_comments([name], doctype).get(name)
	error = (
		bool(comment) and comment.subject == ERROR and (not document or comment.creation > document.modified)
	)
	notice = comment.subject.split(":")[1] if comment and comment.subject != ERROR and not document else None
	if not document:
		return {"error": error, "notice": notice}
	bookings = frappe.get_all(
		"DATEV Booking",
		filters={"document": document.name},
		fields=["line_key", "status", "batch"],
		order_by="line_key",
	)
	return {
		"document": {"status": document.status, "external_id": document.external_id},
		"bookings": bookings,
		"error": error,
		"notice": notice,
	}


def _shows_status(doctype: str, name: str) -> bool:
	settings = frappe.get_single(SETTINGS)
	if doctype not in synced_doctypes(settings):
		return False
	return is_relevant(frappe.get_doc(doctype, name))
