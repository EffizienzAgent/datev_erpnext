import frappe

EINVOICE_APP = "eu_einvoice"
EINVOICE_FUNCTION = "eu_einvoice.european_e_invoice.custom.sales_invoice.attach_xml_to_pdf"


def document_image(invoice) -> tuple[str, bytes]:
	for path in frappe.get_hooks("datev_erpnext_document_image"):
		result = frappe.get_attr(path)(invoice)
		if result is not None:
			return result
	settings = frappe.get_single("DATEV ERPNext Settings")
	if settings.document_image_source == "Latest attached PDF":
		attached = _latest_attached_pdf(invoice)
		if attached is not None:
			return invoice.name + ".pdf", attached
	return invoice.name + ".pdf", _printed_pdf(invoice, settings.print_format)


def _latest_attached_pdf(invoice) -> bytes | None:
	files = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Sales Invoice",
			"attached_to_name": invoice.name,
			"file_name": ("like", "%.pdf"),
		},
		order_by="creation desc",
		limit=1,
	)
	if not files:
		return None
	with open(frappe.get_doc("File", files[0].name).get_full_path(), "rb") as handle:
		return handle.read()


def _printed_pdf(invoice, print_format) -> bytes:
	pdf = frappe.get_print("Sales Invoice", invoice.name, print_format=print_format or None, as_pdf=True)
	if EINVOICE_APP not in frappe.get_installed_apps():
		return pdf
	try:
		return frappe.get_attr(EINVOICE_FUNCTION)(invoice.name, pdf) or pdf
	except Exception:
		frappe.log_error(f"E-invoice embedding failed for {invoice.name}")
		return pdf
