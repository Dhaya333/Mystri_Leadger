"""Coverage for ledger.storage's schema, migration, and insert behavior,
independent of the import pipeline that normally sits on top of it.
"""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from ledger import storage

ROOT = Path(__file__).resolve().parent.parent


class FreshDatabaseTests(unittest.TestCase):
    def test_connect_on_new_path_creates_full_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nested' / 'fresh.sqlite3'
            db = storage.connect(path)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(
                tables,
                {'customers', 'invoices', 'payments', 'import_batches', 'import_errors', 'sqlite_sequence'},
            )
            db.close()

    def test_connect_creates_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a' / 'b' / 'c' / 'fresh.sqlite3'
            db = storage.connect(path)
            self.assertTrue(path.parent.is_dir())
            db.close()


class ReconnectIdempotencyTests(unittest.TestCase):
    def test_reconnecting_a_fresh_database_does_not_duplicate_seed_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'demo.sqlite3'
            db1 = storage.connect(path)
            storage.seed(db1)
            db1.close()

            db2 = storage.connect(path)  # second connect must not re-run migration/seed
            count = db2.execute('SELECT COUNT(*) FROM invoices').fetchone()[0]
            self.assertEqual(count, 6)  # seed count, not doubled
            db2.close()

    def test_reconnecting_an_already_migrated_database_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'migrated.sqlite3'
            shutil.copy2(ROOT / 'fixtures' / 'existing-register.sqlite3', path)
            storage.connect(path).close()  # first connect migrates the legacy schema

            db = storage.connect(path)  # second connect: schema already has amount_cents
            count = db.execute('SELECT COUNT(*) FROM invoices').fetchone()[0]
            self.assertEqual(count, 9)  # fixture count, unchanged by the second connect
            db.close()


class DirectInsertIdentityTests(unittest.TestCase):
    """Confirms the identity guarantee lives in storage.py itself, not just
    in the import loop that happens to call it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_insert_invoice_called_directly_skips_identical_duplicate(self):
        row = {'customer_id': 'HARBOR', 'invoice_number': 'INV-100',
               'amount_cents': 125000, 'due_date': '2026-09-01'}
        self.assertEqual(storage.insert_invoice(self.db, row), 'skipped')

    def test_insert_invoice_called_directly_rejects_conflicting_duplicate(self):
        row = {'customer_id': 'HARBOR', 'invoice_number': 'INV-100',
               'amount_cents': 99999, 'due_date': '2026-09-01'}
        with self.assertRaises(ValueError):
            storage.insert_invoice(self.db, row)

    def test_insert_payment_called_directly_skips_identical_duplicate(self):
        row = {'payment_id': 'SEED-1', 'customer_id': 'HARBOR',
               'invoice_number': 'INV-101', 'amount_cents': 30000}
        self.assertEqual(storage.insert_payment(self.db, row, invoice_id=None), 'skipped')

    def test_insert_payment_called_directly_rejects_conflicting_duplicate(self):
        row = {'payment_id': 'SEED-1', 'customer_id': 'HARBOR',
               'invoice_number': 'INV-101', 'amount_cents': 1}
        with self.assertRaises(ValueError):
            storage.insert_payment(self.db, row, invoice_id=None)


class ForeignKeyEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_foreign_keys_pragma_is_enabled(self):
        self.assertEqual(self.db.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_payment_with_nonexistent_invoice_id_is_rejected(self):
        row = {'payment_id': 'FK-TEST', 'customer_id': 'HARBOR',
               'invoice_number': 'INV-100', 'amount_cents': 500}
        with self.assertRaises(sqlite3.IntegrityError):
            storage.insert_payment(self.db, row, invoice_id=999999)

    def test_invoice_with_unknown_customer_id_is_rejected(self):
        row = {'customer_id': 'GHOST', 'invoice_number': 'FK-INV',
               'amount_cents': 500, 'due_date': '2026-09-09'}
        with self.assertRaises(sqlite3.IntegrityError):
            storage.insert_invoice(self.db, row)


if __name__ == '__main__':
    unittest.main()
