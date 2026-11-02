from unittest.mock import patch

import frappe
from frappe.desk.query_report import run
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from datev_erpnext import sync
from datev_erpnext.tests.factories import make_client, make_company, make_invoice
from datev_erpnext.tests.test_images import minimal_pdf

SYNC_JOB = "datev_erpnext.sync.sync_sales_invoice"


class TestCatchUp(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		frappe.db.savepoint("catch_up")
		self.addCleanup(frappe.db.rollback, save_point="catch_up")
		for target in ("frappe.get_print", "frappe.enqueue"):
			patcher = patch(target, return_value=minimal_pdf() if target == "frappe.get_print" else None)
			mock = patcher.start()
			self.addCleanup(patcher.stop)
			if target == "frappe.enqueue":
				self.enqueue = mock
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_from_date", nowdate())
		make_client(self.company)

	def _submitted(self, **overrides):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)], **overrides)
		invoice.submit()
		return invoice

	def _queued(self):
		return {
			call.kwargs["name"]: call.kwargs
			for call in self.enqueue.call_args_list
			if call.args and call.args[0] == SYNC_JOB
		}

	def test_catch_up_retries_missing(self):
		synced = self._submitted()
		missing = self._submitted()
		older = self._submitted(posting_date=add_days(nowdate(), -1))
		sync.sync_sales_invoice(synced.name)
		self.enqueue.reset_mock()

		sync.catch_up()

		queued = self._queued()
		self.assertNotIn(synced.name, queued)
		self.assertNotIn(older.name, queued)
		self.assertEqual(
			queued[missing.name],
			{
				"name": missing.name,
				"job_id": f"datev-sync-{missing.name}",
				"deduplicate": True,
				"queue": "long",
			},
		)

	def test_catch_up_is_limited(self):
		self._submitted()
		self._submitted()
		self.enqueue.reset_mock()
		with patch.object(sync, "CATCH_UP_LIMIT", 1):
			sync.catch_up()
		self.assertEqual(len(self._queued()), 1)

	def test_catch_up_skips_existing(self):
		invoice = self._submitted()
		sync.sync_sales_invoice(invoice.name)
		self.enqueue.reset_mock()
		sync.catch_up()
		self.assertNotIn(invoice.name, self._queued())

	def test_catch_up_needs_enabled_and_sync_from_date(self):
		self._submitted()
		self.enqueue.reset_mock()
		frappe.db.set_single_value("DATEV ERPNext Settings", "enabled", 0)
		sync.catch_up()
		frappe.db.set_single_value("DATEV ERPNext Settings", "enabled", 1)
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_from_date", None)
		sync.catch_up()
		self.assertEqual(self._queued(), {})

	def test_report_lists_missing(self):
		synced = self._submitted()
		missing = self._submitted()
		sync.sync_sales_invoice(synced.name)
		sync._comment(missing, "renderer down")

		report = run("Sales Invoices without DATEV", filters={"company": self.company})

		self.assertIn("reason", [column["fieldname"] for column in report["columns"]])
		reasons = {row["name"]: row["reason"] for row in report["result"]}
		self.assertNotIn(synced.name, reasons)
		self.assertEqual(reasons[missing.name], "DATEV: renderer down")

	def test_catch_up_prefers_invoices_without_new_comment(self):
		failing = self._submitted()
		self._submitted()
		sync._comment(failing, "renderer down")
		self.enqueue.reset_mock()
		with patch.object(sync, "CATCH_UP_LIMIT", 1):
			sync.catch_up()
		self.assertNotIn(failing.name, self._queued())

	def test_report_shows_notes(self):
		invoice = self._submitted()
		sync._note(invoice, sync.NOT_TRANSFERRED, "no booking lines, not transferred.")
		report = run("Sales Invoices without DATEV", filters={"company": self.company})
		reasons = {row["name"]: row["reason"] for row in report["result"]}
		self.assertEqual(reasons[invoice.name], "DATEV note: no booking lines, not transferred.")
