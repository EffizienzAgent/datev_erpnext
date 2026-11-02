app_name = "datev_erpnext"
app_title = "DATEV for ERPNext"
app_publisher = "EffizienzAgent (Tobias Mende)"
app_description = "ERPNext adapter for the datev app: Sales Invoices to DATEV with Beleglink"
app_email = "oss@effizienzagent.de"
app_license = "gpl-3.0"

required_apps = ["erpnext", "datev"]

after_install = "datev_erpnext.setup.apply_defaults"
after_migrate = "datev_erpnext.setup.apply_defaults"
