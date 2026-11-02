import frappe
from datev.intake import add_booking, discard_document, upsert_document
from frappe import _
from frappe.query_builder.functions import IfNull
from frappe.utils import getdate
from pypika.functions import Concat

from datev_erpnext.clients import client_for
from datev_erpnext.images import document_image
from datev_erpnext.lines import sales_invoice_lines
from datev_erpnext.parties import party_account_for

SETTINGS = "DATEV ERPNext Settings"
SYNC_JOB = "datev_erpnext.sync.sync_sales_invoice"
CATCH_UP_LIMIT = 500
COMMENT_PREFIX = "DATEV: "


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
		_comment(doc, _("posting date before sync from date, not transferred."))


def _comment_if_client_disabled(doc):
	disabled = frappe.db.get_value("DATEV Client", {"company": doc.company, "enabled": 0}, "name")
	if disabled:
		_comment(doc, _("DATEV Client {0} is disabled, not transferred.").format(disabled))


def _comment(doc, error):
	text = _("DATEV: {0}").format(error)
	try:
		if not frappe.db.exists(
			"Comment",
			{
				"reference_doctype": doc.doctype,
				"reference_name": doc.name,
				"comment_type": "Comment",
				"content": text,
			},
		):
			doc.add_comment("Comment", text)
	except Exception:
		frappe.log_error(f"DATEV comment could not be added for {doc.name}")


def before_cancel(doc, method=None):
	key = source_key(doc.name)
	if frappe.db.get_value("DATEV Document", {"source_key": key}, "name", for_update=True):
		discard_document(key)


def sync_sales_invoice(name: str, from_backfill: bool = False) -> str | None:
	if not frappe.get_single(SETTINGS).enabled:
		return None
	invoice = frappe.get_doc("Sales Invoice", name, for_update=True)
	if invoice.docstatus != 1 or _is_opening(invoice):
		return None
	if not from_backfill and not _in_sync_period(invoice):
		return None
	try:
		client = client_for(invoice.company)
		if not client:
			_comment_if_client_disabled(invoice)
			return None
		return _sync(client, invoice)
	except Exception as error:
		frappe.db.rollback()
		frappe.log_error(f"DATEV sync failed for {name}")
		_comment(invoice, error)
		frappe.db.commit()
		return None


def _sync(client, invoice) -> str | None:
	lines = sales_invoice_lines(invoice)
	if not lines:
		return None
	_check_tax_keys(client, lines)
	posting_text = _posting_text(invoice)
	party = party_account_for(client, invoice.customer)
	file_name, content = document_image(invoice)
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
	invoice = frappe.qb.DocType("Sales Invoice")
	document = frappe.qb.DocType("DATEV Document")
	synced = frappe.qb.from_(document).select(document.source_key)
	query = (
		frappe.qb.from_(invoice)
		.select(
			invoice.name,
			invoice.company,
			invoice.posting_date,
			invoice.customer,
			invoice.customer_name,
			invoice.base_grand_total,
		)
		.where(invoice.company.isin(companies))
		.where(invoice.docstatus == 1)
		.where(IfNull(invoice.is_opening, "") != "Yes")
		.where(invoice.posting_date >= from_date)
		.where(Concat("Sales Invoice:", invoice.name).notin(synced))
		.orderby(invoice.posting_date)
		.orderby(invoice.name)
	)
	if to_date:
		query = query.where(invoice.posting_date <= to_date)
	if limit:
		query = query.limit(limit)
	return query.run(as_dict=True)


def latest_comments(names: list[str]) -> dict[str, frappe._dict]:
	if not names:
		return {}
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Sales Invoice",
			"reference_name": ("in", names),
			"comment_type": "Comment",
			"content": ("like", f"{COMMENT_PREFIX}%"),
		},
		fields=["reference_name", "content", "creation"],
		order_by="creation desc",
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
	error = bool(comment) and (not document or comment.creation > document.modified)
	if not document:
		return {"error": error}
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
	}
