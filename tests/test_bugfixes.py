"""Acceptance coverage for the defects listed in clearledger_bugs.md.

Each test class targets one area of behaviour described in
BUSINESS_RULES.md. These are in addition to tests/test_smoke.py.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from ledger import storage, reporting, importing


ROOT = Path(__file__).resolve().parent.parent


class InvoiceDuplicateIdentityTests(unittest.TestCase):
    """Bugs #1, #2, #18."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _import(self, text):
        return importing.import_csv(self.db, text, 'invoices')

    def test_exact_reimport_is_skipped_not_duplicated(self):
        csv = 'customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,1250.00,2026-09-01\n'
        self._import(csv)
        result = self._import(csv)
        self.assertEqual(
            {k: v for k, v in result.items() if k != 'import_id'},
            {'imported': 0, 'skipped': 1, 'rejected': 0, 'errors': []},
        )
        rows = [r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100']
        self.assertEqual(len(rows), 1)

    def test_same_identity_different_amount_is_rejected(self):
        result = self._import('customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,999.00,2026-09-01\n')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['rejected'], 1)
        original = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(original['amount'], 1250.00)

    def test_same_identity_different_due_date_is_rejected(self):
        result = self._import('customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,1250.00,2026-12-31\n')
        self.assertEqual(result['rejected'], 1)
        original = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(original['due_date'], '2026-09-01')

    def test_same_invoice_number_different_customer_is_allowed(self):
        # (customer_id, invoice_number) is the identity, not invoice_number alone.
        result = self._import('customer_id,invoice_number,amount,due_date\nMAPLE,INV-100,50.00,2026-11-01\n')
        self.assertEqual(result['imported'], 1)

    def test_schema_rejects_duplicate_identity_at_db_level(self):
        cols = {r['name']: r for r in self.db.execute('PRAGMA index_list(invoices)')}
        # A unique index must exist over (customer_id, invoice_number).
        found = any(
            {r['name'] for r in self.db.execute(f"PRAGMA index_info({idx})")} == set()
            for idx in cols
        ) or True  # presence check below is the real assertion
        indexed_cols = set()
        for idx in self.db.execute('PRAGMA index_list(invoices)'):
            if idx['unique']:
                indexed_cols |= {r['name'] for r in self.db.execute(f"PRAGMA index_info({idx['name']})")}
        self.assertIn('customer_id', indexed_cols)
        self.assertIn('invoice_number', indexed_cols)


class InvalidRowIsolationTests(unittest.TestCase):
    """Bugs #3, #4."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_invalid_row_is_rejected_valid_rows_still_import(self):
        csv = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,GOOD-1,10.00,2026-09-09\n'
            'HARBOR,BAD-1,not-a-number,2026-09-09\n'
            'HARBOR,GOOD-2,20.00,2026-09-09\n'
        )
        result = importing.import_csv(self.db, csv, 'invoices')
        self.assertEqual(result['imported'], 2)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(result['errors'], [{'line': 3, 'reason': result['errors'][0]['reason']}])
        self.assertIn('amount', result['errors'][0]['reason'])

    def test_line_numbers_stay_correct_after_a_rejected_row(self):
        csv = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,BAD-1,not-a-number,2026-09-09\n'
            'HARBOR,GOOD-1,10.00,2026-09-09\n'
            'HARBOR,BAD-2,also-bad,2026-09-09\n'
        )
        result = importing.import_csv(self.db, csv, 'invoices')
        lines = [e['line'] for e in result['errors']]
        self.assertEqual(lines, [2, 4])
        self.assertEqual(result['imported'], 1)


class PaymentMatchingTests(unittest.TestCase):
    """Bugs #5, #6, #7."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)
        # Two invoices sharing the same amount to prove amount is not used
        # for identity.
        importing.import_csv(
            self.db,
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,TWIN-A,500.00,2026-09-09\n'
            'MAPLE,TWIN-B,500.00,2026-09-09\n',
            'invoices',
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_payment_matches_by_identity_not_amount(self):
        importing.import_csv(
            self.db,
            'payment_id,customer_id,invoice_number,amount\nPAY-A,HARBOR,TWIN-A,500.00\n',
            'payments',
        )
        harbor_row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'TWIN-A')
        maple_row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'TWIN-B')
        self.assertEqual(harbor_row['paid'], 500.00)
        self.assertEqual(maple_row['paid'], 0.00)

    def test_payment_for_nonexistent_invoice_stays_unmatched(self):
        importing.import_csv(
            self.db,
            'payment_id,customer_id,invoice_number,amount\nPAY-X,HARBOR,NO-SUCH-INVOICE,500.00\n',
            'payments',
        )
        overview = reporting.overview(self.db)
        self.assertEqual(len(overview['unmatched_payments']), 1)
        self.assertEqual(overview['unmatched_payments'][0]['payment_id'], 'PAY-X')
        # It must not have attached to TWIN-A/TWIN-B just because the amount matches.
        for row in overview['invoices']:
            if row['invoice_number'] in ('TWIN-A', 'TWIN-B'):
                self.assertEqual(row['paid'], 0.00)


class StatusFilterTests(unittest.TestCase):
    """Bug #8."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_open_filter_excludes_paid_invoices(self):
        open_rows = reporting.invoices(self.db, 'open')
        self.assertTrue(all(r['status'] == 'open' for r in open_rows))
        self.assertTrue(len(open_rows) < len(reporting.invoices(self.db, 'all')))

    def test_paid_filter_only_contains_paid(self):
        importing.import_csv(
            self.db,
            'payment_id,customer_id,invoice_number,amount\nFULL-1,NORTH,INV-301,100.00\n',
            'payments',
        )
        paid_rows = reporting.invoices(self.db, 'paid')
        self.assertIn('INV-301', [r['invoice_number'] for r in paid_rows])
        open_rows = reporting.invoices(self.db, 'open')
        self.assertNotIn('INV-301', [r['invoice_number'] for r in open_rows])


class MoneyPrecisionTests(unittest.TestCase):
    """Bugs #9, #10, #11, #12."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_repeated_cent_amounts_sum_exactly(self):
        importing.import_csv(
            self.db,
            'customer_id,invoice_number,amount,due_date\nHARBOR,PRECISE-1,10.10,2026-09-09\n',
            'invoices',
        )
        csv = 'payment_id,customer_id,invoice_number,amount\n'
        for i in range(3):
            csv += f'PPAY-{i},HARBOR,PRECISE-1,3.37\n'
        importing.import_csv(self.db, csv, 'payments')
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'PRECISE-1')
        self.assertEqual(row['paid'], 10.11)
        self.assertEqual(row['balance'], -0.01)
        self.assertEqual(row['status'], 'paid')

    def test_export_matches_on_screen_values_exactly(self):
        importing.import_csv(
            self.db,
            'customer_id,invoice_number,amount,due_date\nHARBOR,PRECISE-2,19.99,2026-09-09\n',
            'invoices',
        )
        screen = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'PRECISE-2')
        csv_text = reporting.export_csv(self.db)
        line = next(l for l in csv_text.splitlines() if 'PRECISE-2' in l)
        self.assertIn(f"{screen['amount']:.2f}", line)
        self.assertIn(f"{screen['balance']:.2f}", line)


class ExistingRegisterMigrationTests(unittest.TestCase):
    """Migration correctness for the pre-existing register (Bug #18 backstop
    plus the schema-change requirement in BUSINESS_RULES.md)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'restored.sqlite3'
        shutil.copy2(ROOT / 'fixtures' / 'existing-register.sqlite3', self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrated_register_matches_expected_totals(self):
        db = storage.connect(self.path)
        overview = reporting.overview(db)
        self.assertEqual(overview['summary']['invoice_count'], 9)
        self.assertEqual(overview['summary']['open_count'], 7)
        self.assertEqual(overview['summary']['outstanding'], 3698.19)
        self.assertEqual(len(overview['unmatched_payments']), 1)
        db.close()

    def test_migrated_register_survives_restart_and_accepts_new_imports(self):
        db = storage.connect(self.path)
        db.close()
        db = storage.connect(self.path)  # simulate app restart
        result = importing.import_csv(
            db, 'customer_id,invoice_number,amount,due_date\nHARBOR,POST-MIGRATE-1,5.00,2026-12-01\n', 'invoices')
        self.assertEqual(result['imported'], 1)
        db.close()


if __name__ == '__main__':
    unittest.main()
