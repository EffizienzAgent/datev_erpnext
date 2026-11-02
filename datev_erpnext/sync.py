import frappe
from datev.intake import add_booking, upsert_document
from frappe import _

from datev_erpnext.clients import client_for
from datev_erpnext.images import document_image
from datev_erpnext.lines import sales_invoice_lines
from datev_erpnext.parties import party_account_for

SETTINGS = "DATEV ERPNext Settings"
SYNC_JOB = "datev_erpnext.sync.sync_sales_invoice"


def source_key(name: str) -> str:
	return f"Sales Invoice:{name}"


def on_submit(doc, method=None):
	try:
		if frappe.get_single(SETTINGS).enabled and client_for(doc.company):
			frappe.enqueue(SYNC_JOB, name=doc.name, queue="short", enqueue_after_commit=True)
	except Exception:
		frappe.log_error(f"DATEV sync could not be queued for {doc.name}")


def sync_sales_invoice(name: str) -> str | None:
	invoice = frappe.get_doc("Sales Invoice", name)
	if invoice.docstatus != 1:
		return None
	frappe.db.savepoint("datev_sync")
	try:
		client = client_for(invoice.company)
		return _sync(client, invoice) if client else None
	except frappe.ValidationError as error:
		frappe.db.rollback(save_point="datev_sync")
		frappe.log_error(f"DATEV sync failed for {name}")
		invoice.add_comment("Comment", _("DATEV: {0}").format(error))
		return None


def _sync(client, invoice) -> str:
	lines = sales_invoice_lines(invoice)
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


def _posting_text(invoice) -> str:
	template = frappe.get_single(SETTINGS).posting_text_template or ""
	try:
		return template.format(
			customer_name=invoice.customer_name or "", name=invoice.name, po_no=invoice.po_no or ""
		)
	except KeyError, IndexError, ValueError:
		frappe.throw(_("The posting text template {0} is invalid").format(template), frappe.ValidationError)


@frappe.whitelist()
def backfill(company: str, from_date: str, to_date: str | None = None) -> int:
	frappe.only_for("DATEV Manager")
	if not client_for(company):
		frappe.throw(_("No enabled DATEV Client is assigned to company {0}.").format(company))

	dates = ("between", [from_date, to_date]) if to_date else (">=", from_date)
	names = frappe.get_all(
		"Sales Invoice",
		filters={"company": company, "docstatus": 1, "posting_date": dates},
		pluck="name",
		order_by="posting_date, name",
	)
	synced = set(
		frappe.get_all(
			"DATEV Document",
			filters={"source_key": ("in", [source_key(name) for name in names])},
			pluck="source_name",
		)
		if names
		else []
	)
	missing = [name for name in names if name not in synced]
	for name in missing:
		frappe.enqueue(SYNC_JOB, name=name, queue="long")
	return len(missing)


@frappe.whitelist()
def datev_status(name: str) -> dict:
	frappe.has_permission("Sales Invoice", "read", doc=name, throw=True)
	document = frappe.db.get_value(
		"DATEV Document", {"source_key": source_key(name)}, ["name", "status", "external_id"], as_dict=True
	)
	if not document:
		return {}
	bookings = frappe.get_all(
		"DATEV Booking",
		filters={"document": document.name},
		fields=["line_key", "status", "batch"],
		order_by="line_key",
	)
	return {
		"document": {"status": document.status, "external_id": document.external_id},
		"bookings": bookings,
	}
