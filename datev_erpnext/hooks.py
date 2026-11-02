app_name = "datev_erpnext"
app_title = "DATEV for ERPNext"
app_publisher = "EffizienzAgent (Tobias Mende)"
app_description = "ERPNext adapter for the datev app: Sales Invoices to DATEV with Beleglink"
app_email = "oss@effizienzagent.de"
app_license = "gpl-3.0"

required_apps = ["erpnext", "datev"]

after_install = "datev_erpnext.setup.apply_defaults"
after_migrate = "datev_erpnext.setup.apply_defaults"

doctype_js = {"Sales Invoice": "public/js/sales_invoice.js"}

doc_events = {
	"Sales Invoice": {
		"on_submit": "datev_erpnext.sync.on_submit",
		"before_cancel": "datev_erpnext.sync.before_cancel",
	}
}

scheduler_events = {"daily": ["datev_erpnext.sync.catch_up"]}
