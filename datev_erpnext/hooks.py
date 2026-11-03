app_name = "datev_erpnext"
app_title = "DATEV for ERPNext"
app_publisher = "EffizienzAgent (Tobias Mende)"
app_description = "ERPNext adapter for the datev app: sales invoices, purchase invoices and payments to DATEV"
app_email = "oss@effizienzagent.de"
app_license = "gpl-3.0"

required_apps = ["erpnext", "datev"]

after_install = "datev_erpnext.setup.apply_defaults"
after_migrate = "datev_erpnext.setup.apply_defaults"

doctype_js = {
	"Sales Invoice": "public/js/datev_status.js",
	"Purchase Invoice": "public/js/datev_status.js",
	"Payment Entry": "public/js/datev_status.js",
	"Journal Entry": "public/js/datev_status.js",
}

doc_events = {
	"Sales Invoice": {
		"on_submit": "datev_erpnext.sync.on_submit",
		"before_cancel": "datev_erpnext.sync.before_cancel",
	},
	"Purchase Invoice": {
		"on_submit": "datev_erpnext.sync.on_submit",
		"before_cancel": "datev_erpnext.sync.before_cancel",
	},
	"Payment Entry": {
		"on_submit": "datev_erpnext.sync.on_submit",
		"before_cancel": "datev_erpnext.payment_sync.before_cancel",
		"on_update_after_submit": "datev_erpnext.payment_sync.on_update_after_submit",
	},
	"Journal Entry": {
		"on_submit": "datev_erpnext.sync.on_submit",
		"before_cancel": "datev_erpnext.payment_sync.before_cancel",
		"on_update_after_submit": "datev_erpnext.payment_sync.on_update_after_submit",
	},
	"Unreconcile Payment": {"on_submit": "datev_erpnext.payment_sync.on_unreconcile"},
}

scheduler_events = {"daily": ["datev_erpnext.sync.catch_up"]}
