import frappe
from frappe.utils import getdate, nowdate

COMPANY_NAME = "Datev Test GmbH"
CUSTOMER_NAME = "Muster Automaten GmbH"
SUPPLIER_NAME = "Muster Lieferant GmbH"
TAX_ID = "DE123456789"
PRICE_LIST = "Standard Selling"
ACCOUNT_TEMPLATE = "DE 19 % alle Konten"

ACCOUNTS = (
	("Erlöse 19 % USt", "4400", "Income Account", "Direct Income", 0),
	("Steuerfreie Umsätze", "4100", "Income Account", "Direct Income", 0),
	("Erlöse Zubehör", "4200", "Income Account", "Direct Income", 0),
	("Umsatzsteuer 19 %", "3806", "Tax", "Duties and Taxes", 19),
	("USt 7 %", "3801", "Tax", "Duties and Taxes", 7),
	("Vorsteuer 19 %", "1406", "Tax", "Tax Assets", 19),
	("Forderungen aus Lieferungen und Leistungen", "1200", "Receivable", "Accounts Receivable", 0),
	("Verbindlichkeiten aus Lieferungen und Leistungen", "3300", "Payable", "Accounts Payable", 0),
	("Geleistete Anzahlungen", "1186", "Payable", "Loans and Advances (Assets)", 0),
	("Bank", "1800", "Bank", "Bank Accounts", 0),
	("Sparkasse", "1810", "Bank", "Bank Accounts", 0),
	("Nebenkosten des Geldverkehrs", "6855", "Expense Account", "Indirect Expenses", 0),
	("Bürobedarf", "6815", "Expense Account", "Indirect Expenses", 0),
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
	_ensure_supplier()
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
		("Supplier Group", "supplier_group_name", "All Supplier Groups", "Services"),
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
	for field, number in (("default_receivable_account", "1200"), ("default_payable_account", "3300")):
		if company.get(field) != _account(company, number):
			company.db_set(field, _account(company, number))


def _account(company, number) -> str:
	return account(company.name, number)


def account(company: str, number: str) -> str:
	return frappe.db.get_value("Account", {"company": company, "account_number": number}, "name")


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
	_ensure_account_template(company)


def _ensure_account_template(company):
	if frappe.db.exists("Item Tax Template", f"{ACCOUNT_TEMPLATE} - {company.abbr}"):
		return
	frappe.get_doc(
		{
			"doctype": "Item Tax Template",
			"title": ACCOUNT_TEMPLATE,
			"company": company.name,
			"taxes": [
				{"tax_type": _account(company, "1406"), "tax_rate": 19},
				{"tax_type": _account(company, "3806"), "tax_rate": 19},
				{"tax_type": _account(company, "3801"), "tax_rate": 7, "not_applicable": 1},
			],
		}
	).insert()


def _ensure_items(company):
	for code, account, template in (
		("MIETE", "4400", f"DE 19 % - {company.abbr}"),
		("VERS", "4100", f"DE steuerfrei - {company.abbr}"),
		("ZUB", "4200", f"DE 19 % - {company.abbr}"),
		("SERVICE", "4400", None),
		("GERAET", "4400", f"{ACCOUNT_TEMPLATE} - {company.abbr}"),
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


def _ensure_supplier():
	if frappe.db.exists("Supplier", {"supplier_name": SUPPLIER_NAME}):
		return
	frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": SUPPLIER_NAME,
			"supplier_type": "Company",
			"supplier_group": "Services",
			"country": "Germany",
			"tax_id": "DE987654321",
		}
	).insert()


def customer() -> str:
	return frappe.db.get_value("Customer", {"customer_name": CUSTOMER_NAME})


def supplier() -> str:
	return frappe.db.get_value("Supplier", {"supplier_name": SUPPLIER_NAME})


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
		invoice.append_taxes_from_master()
	return invoice.insert()


def make_payment(company, references=(), amount=None, deductions=(), **overrides):
	payment_type = overrides.pop("payment_type", "Receive")
	party_type = overrides.pop("party_type", "Customer" if payment_type == "Receive" else "Supplier")
	party = overrides.pop("party", customer() if party_type == "Customer" else supplier())
	party_account = account(company, "1200" if party_type == "Customer" else "3300")
	bank = account(company, "1800")
	fee = sum(value for _number, value in deductions)
	paid = amount if amount is not None else sum(allocated for _name, allocated in references) - fee
	values = {
		"doctype": "Payment Entry",
		"company": company,
		"payment_type": payment_type,
		"posting_date": nowdate(),
		"party_type": party_type,
		"party": party,
		"paid_from": party_account if payment_type == "Receive" else bank,
		"paid_to": bank if payment_type == "Receive" else party_account,
		"paid_amount": paid,
		"received_amount": paid,
		"reference_no": "Bank",
		"reference_date": nowdate(),
		"references": [
			{
				"reference_doctype": "Sales Invoice" if party_type == "Customer" else "Purchase Invoice",
				"reference_name": name,
				"allocated_amount": allocated,
			}
			for name, allocated in references
		],
		"deductions": [
			{
				"account": account(company, number),
				"cost_center": frappe.get_cached_value("Company", company, "cost_center"),
				"amount": value,
			}
			for number, value in deductions
		],
	}
	values.update(overrides)
	payment = frappe.get_doc(values)
	payment.setup_party_account_field()
	payment.set_missing_values()
	payment.set_missing_ref_details()
	return payment.insert()


def make_journal(company, rows, **overrides):
	values = {
		"doctype": "Journal Entry",
		"company": company,
		"posting_date": nowdate(),
		"cheque_no": "Bank",
		"cheque_date": nowdate(),
		"accounts": [
			{
				"account": account(company, number),
				"debit_in_account_currency": debit,
				"credit_in_account_currency": credit,
				"cost_center": frappe.get_cached_value("Company", company, "cost_center"),
				**extra,
			}
			for number, debit, credit, *rest in rows
			for extra in [rest[0] if rest else {}]
		],
	}
	values.update(overrides)
	return frappe.get_doc(values).insert()
