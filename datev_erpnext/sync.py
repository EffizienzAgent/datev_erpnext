import frappe
from datev.intake import add_booking, discard_document, upsert_document
from frappe import _
from frappe.utils import getdate

from datev_erpnext.clients import client_for
from datev_erpnext.images import document_image
from datev_erpnext.lines import sales_invoice_lines
from datev_erpnext.parties import party_account_for

SETTINGS = "DATEV ERPNext Settings"
SYNC_JOB = "datev_erpnext.sync.sync_sales_invoice"
CATCH_UP_LIMIT = 500
ERROR = "datev_erpnext:error"
NOT_TRANSFERRED = "datev_erpnext:not_transferred"
CLIENT_DISABLED = "datev_erpnext:client_disabled"
SUBJECTS = (ERROR, NOT_TRANSFERRED, CLIENT_DISABLED)


def source_key(name: str) -> str:
	return f"Sales Invoice:{name}"


def _enqueue(name: str, **kwargs):
	frappe.enqueue(SYNC_JOB, name=name, job_id=f"datev-sync-{name}", deduplicate=True, **kwargs)


def _in_sync_period(invoice) -> bool:
	sync_from_date = frappe.get_single(SETTINGS).sync_from_date
	return bool(sync_from_date) and getdate(invoice.posting_date) >= getdate(sync_from_date)


def _is_opening(invoice) -> bool:
	return invoice.is_opening == "Yes"


def on_submit(doc, method=None):
	messages = len(frappe.local.message_log)
	try:
		settings = frappe.get_single(SETTINGS)
		if settings.enabled and settings.sync_from_date and not _is_opening(doc):
			_queue_on_submit(doc, getdate(settings.sync_from_date))
	except Exception as error:
		frappe.local.message_log = frappe.local.message_log[:messages]
		frappe.log_error(f"DATEV sync could not be queued for {doc.name}")
		_comment(doc, error)


def _queue_on_submit(doc, sync_from_date):
	if not client_for(doc.company):
		_comment_if_client_disabled(doc)
	elif getdate(doc.posting_date) >= sync_from_date:
		_enqueue(doc.name, queue="short", enqueue_after_commit=True)
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
		latest = latest_comments([doc.name]).get(doc.name)
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
	key = source_key(doc.name)
	if frappe.db.get_value("DATEV Document", {"source_key": key}, "name", for_update=True):
		discard_document(key)


def _should_sync(invoice, from_backfill: bool) -> bool:
	if invoice.docstatus != 1 or _is_opening(invoice):
		return False
	return from_backfill or _in_sync_period(invoice)


def sync_sales_invoice(name: str, from_backfill: bool = False) -> str | None:
	if not frappe.get_single(SETTINGS).enabled:
		return None
	invoice = frappe.get_doc("Sales Invoice", name)
	if not _should_sync(invoice, from_backfill):
		return None
	try:
		client = client_for(invoice.company)
		if not client:
			_comment_if_client_disabled(invoice)
			return None
		if not sales_invoice_lines(invoice):
			_note(invoice, NOT_TRANSFERRED, _("no booking lines, not transferred."))
			return None
		image = document_image(invoice)
		locked = frappe.get_doc("Sales Invoice", name, for_update=True)
		if not _should_sync(locked, from_backfill):
			return None
		if locked.modified != invoice.modified:
			image = document_image(locked)
		return _sync(client, locked, image)
	except Exception as error:
		frappe.db.rollback()
		frappe.log_error(f"DATEV sync failed for {name}")
		_comment(invoice, error)
		frappe.db.commit()
		return None


def _sync(client, invoice, image: tuple[str, bytes]) -> str | None:
	lines = sales_invoice_lines(invoice)
	if not lines:
		return None
	_check_tax_keys(client, lines)
	posting_text = _posting_text(invoice)
	party = party_account_for(client, invoice.customer)
	file_name, content = image
	document = upsert_document(
		client.name,
		source_key(invoice.name),
		direction="Outgoing",
		file_name=file_name,
		content=content,
		document_date=invoice.posting_date,
		document_number=invoice.name,
		amount=abs(invoice.base_rounded_total or invoice.base_grand_total),
		currency=frappe.get_cached_value("Company", invoice.company, "default_currency"),
		source_doctype="Sales Invoice",
		source_name=invoice.name,
		is_return=bool(invoice.is_return),
	)
	for line in lines:
		add_booking(
			document,
			line.line_key,
			kind="Outgoing Invoice",
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


def _check_tax_keys(client, lines):
	known = {row.tax_code for row in client.tax_keys}
	for line in lines:
		if line.tax_code not in known:
			frappe.throw(
				_(
					"Tax code {0} has no row in the tax keys of DATEV Client {1}. Add it to the DATEV Client's tax keys; leave the BU key empty for automatic accounts."
				).format(line.tax_code, client.name),
				frappe.ValidationError,
			)


def _posting_text(invoice) -> str:
	template = frappe.get_single(SETTINGS).posting_text_template or ""
	try:
		return template.format(
			customer_name=invoice.customer_name or "", name=invoice.name, po_no=invoice.po_no or ""
		)
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
	for invoice in missing_invoices(companies, settings.sync_from_date, limit=CATCH_UP_LIMIT):
		_enqueue(invoice.name, queue="long")


def missing_invoices(companies: list[str], from_date, to_date=None, limit: int | None = None) -> list:
	if not companies:
		return []
	to_date_filter = "and invoice.posting_date <= %(to_date)s" if to_date else ""
	limit_clause = "limit %(limit)s" if limit else ""
	return frappe.db.sql(
		f"""
		select invoice.name, invoice.company, invoice.posting_date, invoice.customer,
			invoice.customer_name, invoice.base_grand_total
		from `tabSales Invoice` invoice
		where invoice.company in %(companies)s
			and invoice.docstatus = 1
			and ifnull(invoice.is_opening, '') != 'Yes'
			and invoice.posting_date >= %(from_date)s
			{to_date_filter}
			and not exists (
				select 1 from `tabDATEV Document` document
				where document.source_key = concat('Sales Invoice:', invoice.name)
			)
		order by exists (
				select 1 from `tabComment` comment
				where comment.reference_doctype = 'Sales Invoice'
					and comment.reference_name = invoice.name
					and comment.subject in %(subjects)s
					and comment.creation >= invoice.modified
			),
			invoice.posting_date, invoice.name
		{limit_clause}
		""",
		{
			"companies": tuple(companies),
			"from_date": from_date,
			"to_date": to_date,
			"subjects": SUBJECTS,
			"limit": limit,
		},
		as_dict=True,
	)


def latest_comments(names: list[str]) -> dict[str, frappe._dict]:
	if not names:
		return {}
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Sales Invoice",
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
def datev_status(name: str) -> dict:
	frappe.has_permission("Sales Invoice", "read", doc=name, throw=True)
	document = frappe.db.get_value(
		"DATEV Document",
		{"source_key": source_key(name)},
		["name", "status", "external_id", "modified"],
		as_dict=True,
	)
	comment = latest_comments([name]).get(name)
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
