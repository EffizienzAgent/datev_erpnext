import frappe
from frappe.utils import getdate, nowdate

COMPANY_NAME = "Datev Test GmbH"
CUSTOMER_NAME = "Muster Automaten GmbH"
TAX_ID = "DE123456789"
PRICE_LIST = "Standard Selling"

ACCOUNTS = (
	("Erlöse 19 % USt", "4400", "Income Account", "Direct Income", 0),
	("Steuerfreie Umsätze", "4100", "Income Account", "Direct Income", 0),
	("Erlöse Zubehör", "4200", "Income Account", "Direct Income", 0),
	("Umsatzsteuer 19 %", "3806", "Tax", "Duties and Taxes", 19),
	("Forderungen aus Lieferungen und Leistungen", "1200", "Receivable", "Accounts Receivable", 0),
)


def make_company(abbr="DTV") -> str:
	_ensure_masters()
	if not frappe.db.exists("Company", COMPANY_NAME):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": COMPANY_NAME,
				"abbr": abbr,
				"country": "Germany",
				"default_currency": "EUR",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert()
	company = frappe.get_doc("Company", COMPANY_NAME)
	_ensure_accounts(company)
	_ensure_fiscal_year(company)
	_ensure_templates(company)
	_ensure_items(company)
	_ensure_customer()
	return company.name


def _ensure_masters():
	if not frappe.db.exists("UOM", "Nos"):
		frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert()
	if not frappe.db.exists("Price List", PRICE_LIST):
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": PRICE_LIST,
				"currency": "EUR",
				"selling": 1,
				"enabled": 1,
			}
		).insert()
	if not frappe.db.exists("Warehouse Type", "Transit"):
		frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert()
	for doctype, field, root, leaf in (
		("Item Group", "item_group_name", "All Item Groups", "Services"),
		("Customer Group", "customer_group_name", "All Customer Groups", "Commercial"),
		("Territory", "territory_name", "All Territories", "Germany"),
	):
		if not frappe.db.exists(doctype, root):
			frappe.get_doc({"doctype": doctype, field: root, "is_group": 1}).insert()
		if not frappe.db.exists(doctype, leaf):
			frappe.get_doc(
				{"doctype": doctype, field: leaf, f"parent_{frappe.scrub(doctype)}": root}
			).insert()


def _ensure_accounts(company):
	for name, number, account_type, parent, rate in ACCOUNTS:
		if frappe.db.exists("Account", {"company": company.name, "account_number": number}):
			continue
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": name,
				"account_number": number,
				"company": company.name,
				"parent_account": f"{parent} - {company.abbr}",
				"account_type": account_type,
				"tax_rate": rate or None,
				"is_group": 0,
			}
		).insert()
	receivable = _account(company, "1200")
	if company.default_receivable_account != receivable:
		company.db_set("default_receivable_account", receivable)


def _account(company, number) -> str:
	return frappe.db.get_value("Account", {"company": company.name, "account_number": number}, "name")


def _ensure_fiscal_year(company):
	today = getdate(nowdate())
	name = str(today.year)
	if not frappe.db.exists("Fiscal Year", name):
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": name,
				"year_start_date": f"{today.year}-01-01",
				"year_end_date": f"{today.year}-12-31",
			}
		).insert()
	fiscal_year = frappe.get_doc("Fiscal Year", name)
	if not any(row.company == company.name for row in fiscal_year.companies):
		fiscal_year.append("companies", {"company": company.name})
		fiscal_year.save()


def _ensure_templates(company):
	abbr = company.abbr
	vat = _account(company, "3806")
	for rate, title in ((19, "DE 19 %"), (0, "DE steuerfrei")):
		item_tax_template = f"{title} - {abbr}"
		if not frappe.db.exists("Item Tax Template", item_tax_template):
			frappe.get_doc(
				{
					"doctype": "Item Tax Template",
					"title": title,
					"company": company.name,
					"taxes": [{"tax_type": vat, "tax_rate": rate}],
				}
			).insert()
		if not frappe.db.exists("Sales Taxes and Charges Template", item_tax_template):
			frappe.get_doc(
				{
					"doctype": "Sales Taxes and Charges Template",
					"title": title,
					"company": company.name,
					"is_default": 1 if rate else 0,
					"taxes": [
						{
							"charge_type": "On Net Total",
							"account_head": vat,
							"description": "Umsatzsteuer",
							"rate": rate,
						}
					],
				}
			).insert()


def _ensure_items(company):
	for code, account, template in (
		("MIETE", "4400", f"DE 19 % - {company.abbr}"),
		("VERS", "4100", f"DE steuerfrei - {company.abbr}"),
		("ZUB", "4200", f"DE 19 % - {company.abbr}"),
		("SERVICE", "4400", None),
	):
		if frappe.db.exists("Item", code):
			continue
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": "Services",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"item_defaults": [{"company": company.name, "income_account": _account(company, account)}],
				"taxes": [{"item_tax_template": template}] if template else [],
			}
		).insert()


def _ensure_customer():
	if frappe.db.exists("Customer", {"customer_name": CUSTOMER_NAME}):
		return
	frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": CUSTOMER_NAME,
			"customer_type": "Company",
			"customer_group": "Commercial",
			"territory": "Germany",
			"tax_id": TAX_ID,
		}
	).insert()


def make_client(company, **overrides):
	abbr = frappe.db.get_value("Company", company, "abbr")
	values = {
		"doctype": "DATEV Client",
		"title": f"Test Client {frappe.generate_hash(length=8)}",
		"company": company,
		"consultant_number": 29098,
		"client_number": 55003,
		"chart": "SKR04",
		"account_length": 4,
		"storage_connector": "E-mail to DUo",
		"duo_email": "belege@example.com",
		"export_connector": "EXTF download",
		"master_data_connector": "EXTF",
		"debtor_numbering": "Source system",
		"creditor_numbering": "Source system",
		"tax_keys": [
			{"tax_code": f"DE 19 % - {abbr}", "bu_key": ""},
			{"tax_code": f"DE steuerfrei - {abbr}", "bu_key": ""},
		],
	}
	values.update(overrides)
	return frappe.get_doc(values).insert()


def make_invoice(company, items, **overrides):
	values = {
		"doctype": "Sales Invoice",
		"company": company,
		"customer": frappe.db.get_value("Customer", {"customer_name": CUSTOMER_NAME}),
		"currency": "EUR",
		"selling_price_list": PRICE_LIST,
		"posting_date": nowdate(),
		"set_posting_time": 1,
		"items": [{"item_code": code, "qty": qty, "rate": rate} for code, qty, rate in items],
	}
	values.update(overrides)
	invoice = frappe.get_doc(values)
	invoice.set_missing_values()
	for row in invoice.items:
		if row.item_tax_template:
			continue
		template = frappe.db.get_value(
			"Item Tax", {"parent": row.item_code, "parenttype": "Item"}, "item_tax_template"
		)
		row.item_tax_template = template
	if not invoice.taxes:
		invoice.taxes_and_charges = f"DE 19 % - {frappe.db.get_value('Company', company, 'abbr')}"
		invoice.set_taxes()
	return invoice.insert()
