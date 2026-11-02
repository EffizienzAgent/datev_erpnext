import io
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from pypdf import PdfWriter

from datev_erpnext import images
from datev_erpnext.tests.factories import make_company, make_invoice

EINVOICE = images.EINVOICE_FUNCTION


def minimal_pdf() -> bytes:
	writer = PdfWriter()
	writer.add_blank_page(width=100, height=100)
	buffer = io.BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


def with_einvoice():
	return patch("frappe.get_installed_apps", return_value=[*frappe.get_installed_apps(), "eu_einvoice"])


def hook_override(invoice):
	return "override.pdf", b"override"


class TestImages(IntegrationTestCase):
	def setUp(self):
		company = make_company()
		frappe.db.savepoint("images")
		self.addCleanup(frappe.db.rollback, save_point="images")
		self.invoice = make_invoice(company, [("MIETE", 1, 100)])
		self.settings = frappe.get_single("DATEV ERPNext Settings")
		renderer = patch("frappe.get_print", return_value=minimal_pdf())
		self.get_print = renderer.start()
		self.addCleanup(renderer.stop)

	def test_print_pdf(self):
		name, content = images.document_image(self.invoice)
		self.assertEqual(name, f"{self.invoice.name}.pdf")
		self.assertTrue(content.startswith(b"%PDF"))
		self.assertTrue(self.get_print.call_args.kwargs["as_pdf"])

	def test_latest_attachment_preferred(self):
		self.settings.document_image_source = "Latest attached PDF"
		self.settings.save()
		pdfs = [minimal_pdf() + b"\n%old", minimal_pdf() + b"\n%new"]
		for content in pdfs:
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "scan.pdf",
					"attached_to_doctype": "Sales Invoice",
					"attached_to_name": self.invoice.name,
					"content": content,
					"is_private": 1,
				}
			).insert()
		self.assertEqual(images.document_image(self.invoice)[1], pdfs[1])
		self.get_print.assert_not_called()

	def test_attachment_source_falls_back_to_print(self):
		self.settings.document_image_source = "Latest attached PDF"
		self.settings.save()
		self.assertTrue(images.document_image(self.invoice)[1].startswith(b"%PDF"))
		self.get_print.assert_called_once()

	def test_override_hook(self):
		hooks = {"datev_erpnext_document_image": ["datev_erpnext.tests.test_images.hook_override"]}
		with patch("frappe.get_hooks", side_effect=lambda name, *a, **k: hooks.get(name, [])):
			self.assertEqual(images.document_image(self.invoice), ("override.pdf", b"override"))

	def test_einvoice_embedding_called_when_installed(self):
		with (
			with_einvoice(),
			patch(EINVOICE, return_value=b"%PDF-embedded") as embed,
		):
			self.assertEqual(images.document_image(self.invoice)[1], b"%PDF-embedded")
		self.assertEqual(embed.call_args.args[0], self.invoice.name)
		self.assertTrue(embed.call_args.args[1].startswith(b"%PDF"))

	def test_einvoice_failure_keeps_plain_pdf(self):
		with (
			with_einvoice(),
			patch(EINVOICE, side_effect=RuntimeError("boom")),
		):
			self.assertTrue(images.document_image(self.invoice)[1].startswith(b"%PDF"))
