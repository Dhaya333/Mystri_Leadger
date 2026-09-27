import re
from datetime import date


HEADERS = {
    'invoices': ['customer_id', 'invoice_number', 'amount', 'due_date'],
    'payments': ['payment_id', 'customer_id', 'invoice_number', 'amount'],
}

_AMOUNT_RE = re.compile(r'\d+(?:\.\d{1,2})?')
MAX_AMOUNT_CENTS = 10_000_000 * 100


def parse_amount_cents(value):
    """Parse a decimal-string amount into an exact integer number of cents.

    Working in integer cents (rather than float dollars) avoids the
    floating-point precision problems that show up later in matching,
    balance calculation and CSV/JSON export.
    """
    if not _AMOUNT_RE.fullmatch(value):
        raise ValueError('amount must be a positive decimal with at most two decimal places')
    if '.' in value:
        whole, frac = value.split('.')
        frac = frac.ljust(2, '0')
    else:
        whole, frac = value, '00'
    cents = int(whole) * 100 + int(frac)
    if not 0 < cents <= MAX_AMOUNT_CENTS:
        raise ValueError('amount must be greater than zero and at most 10000000')
    return cents


def normalize(row, kind, customer_ids):
    result = {}
    for key in HEADERS[kind]:
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{key} is required')
        result[key] = value.strip()
    if result['customer_id'] not in customer_ids:
        raise ValueError('Unknown customer_id')
    result['amount_cents'] = parse_amount_cents(result.pop('amount'))
    if kind == 'invoices':
        try:
            parsed = date.fromisoformat(result['due_date'])
            if parsed.isoformat() != result['due_date']:
                raise ValueError()
        except ValueError:
            raise ValueError('due_date must be YYYY-MM-DD') from None
    return result
