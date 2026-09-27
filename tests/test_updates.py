"""Regression and integration coverage for clearledger_updates.md.

Complements tests/test_smoke.py and tests/test_bugfixes.py rather than
replacing them; run together via `python -m unittest discover -s tests`
(see run_tests.sh, which the app should be checked with before deploying).
"""
import json
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from ledger import storage, reporting, importing
from ledger.http_app import make_server


class DuplicatePaymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_exact_duplicate_payment_is_idempotent(self):
        csv = 'payment_id,customer_id,invoice_number,amount\nDUP-1,HARBOR,INV-100,50.00\n'
        importing.import_csv(self.db, csv, 'payments')
        result = importing.import_csv(self.db, csv, 'payments')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['skipped'], 1)
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(row['paid'], 50.00)  # not double-counted

    def test_conflicting_duplicate_payment_id_is_rejected_and_original_kept(self):
        importing.import_csv(
            self.db, 'payment_id,customer_id,invoice_number,amount\nDUP-2,HARBOR,INV-100,50.00\n', 'payments')
        result = importing.import_csv(
            self.db, 'payment_id,customer_id,invoice_number,amount\nDUP-2,HARBOR,INV-100,99.00\n', 'payments')
        self.assertEqual(result['rejected'], 1)
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(row['paid'], 50.00)


class SameAmountInvoicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_two_invoices_with_identical_amount_are_billed_independently(self):
        result = importing.import_csv(
            self.db,
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,SAME-1,77.00,2026-09-09\n'
            'MAPLE,SAME-2,77.00,2026-09-09\n',
            'invoices')
        self.assertEqual(result['imported'], 2)
        importing.import_csv(
            self.db, 'payment_id,customer_id,invoice_number,amount\nSAME-PAY,MAPLE,SAME-2,77.00\n', 'payments')
        harbor_row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'SAME-1')
        maple_row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'SAME-2')
        self.assertEqual(harbor_row['paid'], 0.00)
        self.assertEqual(maple_row['paid'], 77.00)


class InvalidHeaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_invalid_invoice_header_rejects_whole_import_and_writes_nothing(self):
        before = len(reporting.invoices(self.db))
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, 'wrong,header,here\nA,B,C\n', 'invoices')
        self.assertEqual(len(reporting.invoices(self.db)), before)

    def test_invalid_payment_header_rejects_whole_import(self):
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, 'id,cust,inv,amt\nX,HARBOR,INV-100,10.00\n', 'payments')

    def test_valid_header_no_rows_is_a_successful_no_op_import(self):
        result = importing.import_csv(self.db, 'customer_id,invoice_number,amount,due_date\n', 'invoices')
        self.assertEqual(
            {k: v for k, v in result.items() if k != 'import_id'},
            {'imported': 0, 'skipped': 0, 'rejected': 0, 'errors': []},
        )


class OverpaymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_overpayment_shows_negative_balance_and_paid_status(self):
        importing.import_csv(
            self.db, 'customer_id,invoice_number,amount,due_date\nHARBOR,OVER-1,50.00,2026-09-09\n', 'invoices')
        importing.import_csv(
            self.db, 'payment_id,customer_id,invoice_number,amount\nOVER-PAY,HARBOR,OVER-1,70.00\n', 'payments')
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'OVER-1')
        self.assertEqual(row['balance'], -20.00)
        self.assertEqual(row['status'], 'paid')

    def test_overpayment_does_not_reduce_other_invoices_balance(self):
        importing.import_csv(
            self.db, 'customer_id,invoice_number,amount,due_date\nHARBOR,OVER-2,50.00,2026-09-09\n', 'invoices')
        importing.import_csv(
            self.db, 'payment_id,customer_id,invoice_number,amount\nOVER-PAY-2,HARBOR,OVER-2,70.00\n', 'payments')
        untouched = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(untouched['balance'], 1250.00)

    def test_overpayment_does_not_go_negative_in_outstanding_total(self):
        importing.import_csv(
            self.db, 'customer_id,invoice_number,amount,due_date\nHARBOR,OVER-3,50.00,2026-09-09\n', 'invoices')
        before = reporting.overview(self.db)['summary']['outstanding']
        importing.import_csv(
            self.db, 'payment_id,customer_id,invoice_number,amount\nOVER-PAY-3,HARBOR,OVER-3,70.00\n', 'payments')
        after = reporting.overview(self.db)['summary']['outstanding']
        # OVER-3 goes from contributing +50.00 to contributing 0 (its
        # negative balance is excluded, never subtracted from the total).
        self.assertEqual(round(before - after, 2), 50.00)


class ImportExportRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_imported_amounts_round_trip_through_export_unchanged(self):
        importing.import_csv(
            self.db, 'customer_id,invoice_number,amount,due_date\nHARBOR,RT-1,123.45,2026-09-09\n', 'invoices')
        importing.import_csv(
            self.db, 'payment_id,customer_id,invoice_number,amount\nRT-PAY,HARBOR,RT-1,23.45\n', 'payments')
        line = next(l for l in reporting.export_csv(self.db).splitlines() if 'RT-1' in l)
        self.assertIn('123.45', line)
        self.assertIn('23.45', line)
        self.assertIn('100.00', line)
        self.assertIn('open', line)


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_import_batch_records_counts_source_timestamp_and_errors(self):
        csv = ('customer_id,invoice_number,amount,due_date\n'
               'HARBOR,AUD-1,10.00,2026-09-09\n'
               'HARBOR,AUD-BAD,not-a-number,2026-09-09\n')
        result = importing.import_csv(self.db, csv, 'invoices', source='audit-test.csv')
        batch = storage.import_batch(self.db, result['import_id'])
        self.assertIsNotNone(batch)
        self.assertEqual(batch['kind'], 'invoices')
        self.assertEqual(batch['source'], 'audit-test.csv')
        self.assertEqual(batch['imported'], 1)
        self.assertEqual(batch['rejected'], 1)
        self.assertTrue(batch['started_at'])
        self.assertEqual(len(batch['errors']), 1)
        self.assertEqual(batch['errors'][0]['line'], 3)

    def test_recent_import_batches_lists_newest_first(self):
        importing.import_csv(
            self.db, 'customer_id,invoice_number,amount,due_date\nHARBOR,AUD-2,10.00,2026-09-09\n',
            'invoices', source='a.csv')
        importing.import_csv(
            self.db, 'customer_id,invoice_number,amount,due_date\nHARBOR,AUD-3,10.00,2026-09-09\n',
            'invoices', source='b.csv')
        batches = storage.recent_import_batches(self.db)
        self.assertGreaterEqual(len(batches), 2)
        self.assertEqual(batches[0]['source'], 'b.csv')


class RestartPersistenceTests(unittest.TestCase):
    def test_newly_imported_data_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'persist.sqlite3'
            db = storage.connect(path)
            storage.seed(db)
            importing.import_csv(
                db, 'customer_id,invoice_number,amount,due_date\nHARBOR,PERSIST-1,42.00,2026-09-09\n', 'invoices')
            db.close()

            db2 = storage.connect(path)  # simulates an app restart
            row = next(r for r in reporting.invoices(db2) if r['invoice_number'] == 'PERSIST-1')
            self.assertEqual(row['amount'], 42.00)
            db2.close()


class HttpIntegrationTests(unittest.TestCase):
    """Exercises the real HTTP server end-to-end, not just the ledger package."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / 'http.sqlite3'
        db = storage.connect(cls.db_path)
        storage.seed(db)
        db.close()
        web_dir = Path(__file__).resolve().parent.parent / 'web'
        cls.server = make_server(cls.db_path, web_dir, 0)  # port 0 = OS picks a free port
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def _url(self, path):
        return f'http://127.0.0.1:{self.port}{path}'

    def test_valid_import_returns_200_with_counts_and_import_id(self):
        body = 'customer_id,invoice_number,amount,due_date\nHARBOR,HTTP-1,15.00,2026-09-09\n'
        req = urllib.request.Request(
            self._url('/api/import?kind=invoices&source=http-test.csv'),
            data=body.encode(), method='POST', headers={'Content-Type': 'text/csv'})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())
        self.assertEqual(data['imported'], 1)
        self.assertIn('import_id', data)

    def test_invalid_header_returns_400_with_error_body(self):
        body = 'not,the,right,header\nA,B,C,D\n'
        req = urllib.request.Request(
            self._url('/api/import?kind=invoices'),
            data=body.encode(), method='POST', headers={'Content-Type': 'text/csv'})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        data = json.loads(ctx.exception.read())
        self.assertIn('error', data)

    def test_invalid_status_filter_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self._url('/api/invoices?status=bogus'))
        self.assertEqual(ctx.exception.code, 400)

    def test_import_batch_is_retrievable_via_the_audit_endpoint(self):
        body = 'customer_id,invoice_number,amount,due_date\nHARBOR,HTTP-2,15.00,2026-09-09\n'
        req = urllib.request.Request(
            self._url('/api/import?kind=invoices&source=batch-check.csv'),
            data=body.encode(), method='POST', headers={'Content-Type': 'text/csv'})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        with urllib.request.urlopen(self._url(f"/api/imports?id={data['import_id']}")) as resp:
            batch = json.loads(resp.read())
        self.assertEqual(batch['source'], 'batch-check.csv')
        self.assertEqual(batch['imported'], 1)


if __name__ == '__main__':
    unittest.main()
