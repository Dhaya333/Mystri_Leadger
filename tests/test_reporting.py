"""Coverage for ledger.reporting's empty-state behavior and combined
multi-invoice scenarios, complementing the pairwise checks already in
tests/test_bugfixes.py and tests/test_updates.py.
"""
import tempfile
import unittest
from pathlib import Path
from ledger import storage, importing, reporting


class EmptyDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Deliberately no storage.seed() — a completely empty register.
        self.db = storage.connect(Path(self.tmp.name) / 'empty.sqlite3')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_invoices_on_empty_database_returns_empty_list(self):
        self.assertEqual(reporting.invoices(self.db), [])
        self.assertEqual(reporting.invoices(self.db, 'open'), [])
        self.assertEqual(reporting.invoices(self.db, 'paid'), [])

    def test_overview_on_empty_database_is_all_zeroed_no_exceptions(self):
        overview = reporting.overview(self.db)
        self.assertEqual(overview['invoices'], [])
        self.assertEqual(overview['unmatched_payments'], [])
        self.assertEqual(
            overview['summary'],
            {'invoice_count': 0, 'open_count': 0, 'outstanding': 0.0},
        )

    def test_export_csv_on_empty_database_is_header_only(self):
        csv_text = reporting.export_csv(self.db)
        lines = csv_text.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], 'customer_id,invoice_number,amount,paid,balance,status')


class CombinedStatusScenarioTests(unittest.TestCase):
    """Several open, several paid, and one overpaid invoice all at once -
    confirms the counts and total agree with each other, not just pairwise."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)  # 6 invoices, 5 open / 1 paid, INR 3,209.99 outstanding

        importing.import_csv(
            self.db,
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,MIX-OPEN-1,40.00,2026-09-09\n'
            'HARBOR,MIX-OPEN-2,60.00,2026-09-09\n'
            'MAPLE,MIX-PAID-1,25.00,2026-09-09\n'
            'MAPLE,MIX-OVER-1,50.00,2026-09-09\n',
            'invoices',
        )
        importing.import_csv(
            self.db,
            'payment_id,customer_id,invoice_number,amount\n'
            'MIX-PAY-1,MAPLE,MIX-PAID-1,25.00\n'      # exact payment -> paid
            'MIX-PAY-2,MAPLE,MIX-OVER-1,70.00\n',      # overpayment -> paid, negative balance
            'payments',
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_open_and_paid_filters_partition_all_invoices_without_overlap(self):
        all_rows = reporting.invoices(self.db)
        open_rows = reporting.invoices(self.db, 'open')
        paid_rows = reporting.invoices(self.db, 'paid')
        self.assertEqual(len(open_rows) + len(paid_rows), len(all_rows))
        open_numbers = {r['invoice_number'] for r in open_rows}
        paid_numbers = {r['invoice_number'] for r in paid_rows}
        self.assertEqual(open_numbers & paid_numbers, set())
        self.assertIn('MIX-OPEN-1', open_numbers)
        self.assertIn('MIX-OPEN-2', open_numbers)
        self.assertIn('MIX-PAID-1', paid_numbers)
        self.assertIn('MIX-OVER-1', paid_numbers)  # overpaid counts as paid

    def test_summary_counts_and_outstanding_agree_with_the_full_invoice_list(self):
        overview = reporting.overview(self.db)
        all_rows = overview['invoices']
        self.assertEqual(overview['summary']['invoice_count'], len(all_rows))
        self.assertEqual(
            overview['summary']['open_count'],
            sum(1 for r in all_rows if r['status'] == 'open'),
        )
        # Outstanding must equal the sum of positive balances only - the
        # overpaid invoice's negative balance must not reduce the total.
        expected_outstanding = round(sum(max(0.0, r['balance']) for r in all_rows), 2)
        self.assertEqual(overview['summary']['outstanding'], expected_outstanding)
        over_row = next(r for r in all_rows if r['invoice_number'] == 'MIX-OVER-1')
        self.assertEqual(over_row['balance'], -20.00)

    def test_export_csv_lists_every_invoice_with_matching_status(self):
        csv_text = reporting.export_csv(self.db)
        by_number = {}
        lines = csv_text.splitlines()[1:]
        for line in lines:
            fields = line.split(',')
            by_number[fields[1]] = fields
        for row in reporting.invoices(self.db):
            fields = by_number[row['invoice_number']]
            self.assertEqual(fields[-1], row['status'])
            self.assertEqual(fields[2], f"{row['amount']:.2f}")


if __name__ == '__main__':
    unittest.main()
