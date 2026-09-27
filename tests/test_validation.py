"""Unit-level coverage for ledger.validation, isolated from the database.

These pin down the exact parsing/validation contract that import_csv relies
on indirectly. Run alongside the rest via:
    python -m unittest discover -s tests
"""
import unittest
from ledger.validation import parse_amount_cents, normalize, MAX_AMOUNT_CENTS


class ParseAmountCentsTests(unittest.TestCase):
    def test_three_decimal_places_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_amount_cents('12.345')

    def test_non_numeric_amount_is_rejected(self):
        for bad in ('12,50', '$5', 'abc', '5.', '.50', '5-00', ''):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_amount_cents(bad)

    def test_zero_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_amount_cents('0')
        with self.assertRaises(ValueError):
            parse_amount_cents('0.00')

    def test_amount_at_maximum_boundary_is_accepted(self):
        self.assertEqual(parse_amount_cents('10000000.00'), MAX_AMOUNT_CENTS)

    def test_amount_one_cent_over_maximum_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_amount_cents('10000000.01')

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_amount_cents('-5.00')

    def test_whole_dollar_amount_defaults_to_zero_cents(self):
        self.assertEqual(parse_amount_cents('42'), 4200)

    def test_single_decimal_digit_is_padded_to_cents(self):
        self.assertEqual(parse_amount_cents('42.5'), 4250)

    def test_two_decimal_digits_parsed_exactly(self):
        self.assertEqual(parse_amount_cents('19.99'), 1999)


class NormalizeTests(unittest.TestCase):
    CUSTOMERS = {'HARBOR', 'MAPLE'}

    def test_blank_required_field_is_rejected_with_field_specific_reason(self):
        row = {'customer_id': 'HARBOR', 'invoice_number': '', 'amount': '10.00', 'due_date': '2026-09-09'}
        with self.assertRaises(ValueError) as ctx:
            normalize(row, 'invoices', self.CUSTOMERS)
        self.assertIn('invoice_number', str(ctx.exception))

    def test_whitespace_only_required_field_is_rejected(self):
        row = {'customer_id': 'HARBOR', 'invoice_number': '   ', 'amount': '10.00', 'due_date': '2026-09-09'}
        with self.assertRaises(ValueError):
            normalize(row, 'invoices', self.CUSTOMERS)

    def test_missing_field_is_rejected(self):
        row = {'customer_id': 'HARBOR', 'amount': '10.00', 'due_date': '2026-09-09'}  # no invoice_number
        with self.assertRaises(ValueError):
            normalize(row, 'invoices', self.CUSTOMERS)

    def test_leading_and_trailing_whitespace_is_trimmed(self):
        row = {'customer_id': ' HARBOR ', 'invoice_number': ' INV-1 ',
               'amount': ' 42.50 ', 'due_date': ' 2026-09-09 '}
        result = normalize(row, 'invoices', self.CUSTOMERS)
        self.assertEqual(result['customer_id'], 'HARBOR')
        self.assertEqual(result['invoice_number'], 'INV-1')
        self.assertEqual(result['amount_cents'], 4250)
        self.assertEqual(result['due_date'], '2026-09-09')

    def test_wrong_format_due_date_is_rejected(self):
        row = {'customer_id': 'HARBOR', 'invoice_number': 'INV-1', 'amount': '10.00', 'due_date': '09/01/2026'}
        with self.assertRaises(ValueError):
            normalize(row, 'invoices', self.CUSTOMERS)

    def test_calendar_invalid_due_date_is_rejected(self):
        row = {'customer_id': 'HARBOR', 'invoice_number': 'INV-1', 'amount': '10.00', 'due_date': '2026-02-30'}
        with self.assertRaises(ValueError):
            normalize(row, 'invoices', self.CUSTOMERS)

    def test_unknown_customer_id_is_rejected(self):
        row = {'customer_id': 'GHOST', 'invoice_number': 'INV-1', 'amount': '10.00', 'due_date': '2026-09-09'}
        with self.assertRaises(ValueError):
            normalize(row, 'invoices', self.CUSTOMERS)

    def test_payments_kind_does_not_require_or_produce_due_date(self):
        row = {'payment_id': 'P-1', 'customer_id': 'HARBOR', 'invoice_number': 'INV-1', 'amount': '10.00'}
        result = normalize(row, 'payments', self.CUSTOMERS)
        self.assertNotIn('due_date', result)
        self.assertEqual(result['amount_cents'], 1000)


if __name__ == '__main__':
    unittest.main()
