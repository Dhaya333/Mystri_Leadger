"""Malformed-input edge cases for ledger.importing.import_csv, beyond the
single-bad-row-in-a-small-file scenario already covered elsewhere. Real CSVs
from spreadsheet exports bring line-ending, BOM, and ragged-row surprises
that are worth pinning down explicitly.
"""
import tempfile
import unittest
from pathlib import Path
from ledger import storage, importing, reporting


class LineEndingAndEncodingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_windows_line_endings_import_identically_to_unix(self):
        csv_crlf = 'customer_id,invoice_number,amount,due_date\r\nHARBOR,CRLF-1,10.00,2026-09-09\r\n'
        result = importing.import_csv(self.db, csv_crlf, 'invoices')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['rejected'], 0)
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'CRLF-1')
        self.assertEqual(row['amount'], 10.00)

    def test_utf8_bom_is_stripped_and_import_succeeds(self):
        csv_bom = '\ufeffcustomer_id,invoice_number,amount,due_date\nHARBOR,BOM-1,10.00,2026-09-09\n'
        result = importing.import_csv(self.db, csv_bom, 'invoices')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['rejected'], 0)
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'BOM-1')
        self.assertEqual(row['amount'], 10.00)


class MalformedHeaderAndRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_header_with_correct_columns_in_wrong_order_is_rejected(self):
        # Same column names as a valid invoices header, but reordered -
        # the header check must compare the exact list, not just the set.
        csv = 'invoice_number,customer_id,amount,due_date\nSWAP-1,HARBOR,10.00,2026-09-09\n'
        before = len(reporting.invoices(self.db))
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, csv, 'invoices')
        self.assertEqual(len(reporting.invoices(self.db)), before)

    def test_row_with_missing_trailing_column_is_rejected_not_crashed(self):
        # due_date column is entirely absent from this data row.
        csv = 'customer_id,invoice_number,amount,due_date\nHARBOR,SHORT-1,10.00\n'
        result = importing.import_csv(self.db, csv, 'invoices')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(result['errors'][0]['line'], 2)
        self.assertIn('due_date', result['errors'][0]['reason'])

    def test_row_with_extra_trailing_column_does_not_crash_the_import(self):
        # A stray extra comma anywhere in a data row is common in exports;
        # csv.DictReader collects it under a synthetic key that normalize()
        # simply ignores, so this row should still import cleanly.
        csv = 'customer_id,invoice_number,amount,due_date\nHARBOR,EXTRA-1,10.00,2026-09-09,unexpected\n'
        result = importing.import_csv(self.db, csv, 'invoices')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['rejected'], 0)


class LargeMixedImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_counts_and_line_numbers_stay_correct_across_a_large_mixed_file(self):
        lines = ['customer_id,invoice_number,amount,due_date']
        expected_bad_lines = []
        for i in range(300):
            line_no = i + 2  # header is line 1
            if i % 7 == 0:
                lines.append(f'HARBOR,BULK-{i},not-a-number,2026-09-09')
                expected_bad_lines.append(line_no)
            else:
                lines.append(f'HARBOR,BULK-{i},{1 + (i % 50)}.00,2026-09-09')
        csv = '\n'.join(lines) + '\n'

        result = importing.import_csv(self.db, csv, 'invoices')

        good_count = 300 - len(expected_bad_lines)
        self.assertEqual(result['imported'], good_count)
        self.assertEqual(result['rejected'], len(expected_bad_lines))
        self.assertEqual(result['imported'] + result['rejected'], 300)
        self.assertEqual([e['line'] for e in result['errors']], expected_bad_lines)
        # Every valid row actually landed, not just the counters.
        self.assertEqual(
            len([r for r in reporting.invoices(self.db) if r['invoice_number'].startswith('BULK-')]),
            good_count,
        )


if __name__ == '__main__':
    unittest.main()
