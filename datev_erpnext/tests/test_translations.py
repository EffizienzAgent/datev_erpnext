import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.pofile import read_po
from frappe.gettext import translate
from frappe.tests import IntegrationTestCase

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"


def read_catalog(path: Path):
	with open(path, "rb") as f:
		return read_po(f)


def message_ids(catalog) -> set[str]:
	return {message.id for message in catalog if message.id}


class TestTranslations(IntegrationTestCase):
	def test_every_message_translated(self):
		pot = read_catalog(LOCALE_DIR / "main.pot")
		german = read_catalog(LOCALE_DIR / "de.po")
		untranslated = [
			message.id
			for message in pot
			if message.id and not (german.get(message.id) and german.get(message.id).string)
		]
		self.assertEqual(untranslated, [])

	def test_pot_is_current(self):
		with tempfile.TemporaryDirectory() as directory:
			generated = Path(directory) / "main.pot"
			with patch.object(translate, "get_pot_path", return_value=generated):
				translate.generate_pot("datev_erpnext")
			self.assertEqual(
				message_ids(read_catalog(generated)), message_ids(read_catalog(LOCALE_DIR / "main.pot"))
			)

	def test_german_is_loaded(self):
		translate.compile_translations("datev_erpnext", "de", force=True)
		frappe.cache.delete_key("lang_user_translations")
		frappe.local.lang = "de"
		try:
			self.assertEqual(frappe._("Customer Number Field"), "Kundennummernfeld")
			self.assertEqual(frappe._("DATEV ERPNext Settings"), "DATEV-ERPNext-Einstellungen")
		finally:
			frappe.local.lang = "en"
