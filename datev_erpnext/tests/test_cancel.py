from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from datev_erpnext import sync
from datev_erpnext.tests.factories import make_client, make_company, make_invoice
from datev_erpnext.tests.test_images import minimal_pdf


class TestCancel(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		frappe.db.savepoint("cancel")
		self.addCleanup(frappe.db.rollback, save_point="cancel")
		for target in ("frappe.get_print", "frappe.enqueue"):
			patcher = patch(target, return_value=minimal_pdf() if target == "frappe.get_print" else None)
			patcher.start()
			self.addCleanup(patcher.stop)

	def _synced_invoice(self):
		make_client(self.company)
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.submit()
		return invoice, sync.sync_sales_invoice(invoice.name)

	def test_cancel_before_upload_discards(self):
		invoice, document = self._synced_invoice()
		invoice.cancel()
		self.assertEqual(invoice.docstatus, 2)
		self.assertEqual(frappe.db.get_value("DATEV Document", document, "status"), "Discarded")
		self.assertFalse(frappe.db.count("DATEV Booking", {"document": document}))

	def test_cancel_after_upload_blocked(self):
		invoice, document = self._synced_invoice()
		frappe.db.set_value("DATEV Document", document, "status", "Uploaded")
		with self.assertRaises(frappe.ValidationError) as raised:
			invoice.cancel()
		self.assertIn("Create a cancellation invoice instead", str(raised.exception))
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice.name, "docstatus"), 1)

	def test_cancel_without_datev_document(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.submit()
		invoice.cancel()
		self.assertEqual(invoice.docstatus, 2)

	def test_cancel_when_disabled_with_document(self):
		invoice, document = self._synced_invoice()
		frappe.db.set_single_value("DATEV ERPNext Settings", "enabled", 0)
		invoice.cancel()
		self.assertEqual(frappe.db.get_value("DATEV Document", document, "status"), "Discarded")
