import frappe

EINVOICE_APP = "eu_einvoice"
EINVOICE_FUNCTION = "eu_einvoice.european_e_invoice.custom.sales_invoice.attach_xml_to_pdf"


SUPPLIER_FILE_TYPES = ("pdf", "xml")


def _hook_image(invoice) -> tuple[str, bytes] | None:
	for path in frappe.get_hooks("datev_erpnext_document_image"):
		result = frappe.get_attr(path)(invoice)
		if result is not None:
			return result
	return None


def document_image(invoice) -> tuple[str, bytes]:
	result = _hook_image(invoice)
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
	return _read(files[0].name) if files else None


def _read(file: str) -> bytes:
	with open(frappe.get_doc("File", file).get_full_path(), "rb") as handle:
		return handle.read()


def purchase_document_image(invoice) -> tuple[str, bytes] | None:
	result = _hook_image(invoice)
	if result is not None:
		return result
	for file in (_einvoice_file(invoice), _latest_supplier_file(invoice)):
		if file:
			return file.file_name, _read(file.name)
	if invoice.is_return:
		pdf = frappe.get_print("Purchase Invoice", invoice.name, as_pdf=True)
		return invoice.name + ".pdf", pdf
	return None


def _einvoice_file(invoice):
	if EINVOICE_APP not in frappe.get_installed_apps() or not invoice.get("e_invoice_import"):
		return None
	url = frappe.db.get_value("E Invoice Import", invoice.e_invoice_import, "einvoice")
	if not url:
		return None
	files = frappe.get_all(
		"File",
		filters={"file_url": url},
		fields=["name", "file_name", "attached_to_name"],
		order_by="creation desc",
	)
	files.sort(key=lambda file: file.attached_to_name != invoice.e_invoice_import)
	return files[0] if files else None


def _latest_supplier_file(invoice):
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Purchase Invoice", "attached_to_name": invoice.name, "is_folder": 0},
		fields=["name", "file_name"],
		order_by="creation desc, name desc",
	)
	return next(
		(file for file in files if (file.file_name or "").rsplit(".", 1)[-1].lower() in SUPPLIER_FILE_TYPES),
		None,
	)


def _printed_pdf(invoice, print_format) -> bytes:
	pdf = frappe.get_print("Sales Invoice", invoice.name, print_format=print_format or None, as_pdf=True)
	if EINVOICE_APP not in frappe.get_installed_apps():
		return pdf
	try:
		return frappe.get_attr(EINVOICE_FUNCTION)(invoice.name, pdf) or pdf
	except Exception:
		frappe.log_error(f"E-invoice embedding failed for {invoice.name}")
		return pdf
