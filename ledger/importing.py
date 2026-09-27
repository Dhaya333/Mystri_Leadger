import csv
import io
import sqlite3
from .validation import HEADERS, normalize
from .storage import insert_invoice, insert_payment, start_import_batch, finish_import_batch
from .matching import find_invoice


def import_csv(db, text, kind, source=None):
    """Import a CSV of the given kind ('invoices' or 'payments').

    Header validation happens first and rejects the whole import (writing
    nothing) if it fails. Once the header is accepted, every data row is
    validated and inserted independently: a bad row is rejected on its own
    with its original CSV line number and a reason, and does not affect any
    other row. Re-importing an identical row is idempotent (it is skipped);
    re-using an identity with different details is rejected.

    `source` is an optional caller-supplied identifier (e.g. the uploaded
    file name) recorded against the import batch for later troubleshooting.
    """
    if kind not in HEADERS:
        raise ValueError('Unknown import kind')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if reader.fieldnames != HEADERS[kind]:
        raise ValueError('Expected CSV header: ' + ','.join(HEADERS[kind]))
    customers = {r[0] for r in db.execute('SELECT customer_id FROM customers')}
    raw_rows = list(reader)
    result = {'imported': 0, 'skipped': 0, 'rejected': 0, 'errors': []}
    with db:
        batch_id = start_import_batch(db, kind, source)
        # Each row is validated AND inserted inside this loop, one at a
        # time, so a bad row's ValueError is recorded against that row only
        # and every other row is still processed with its correct line
        # number (header is line 1, so data rows start at line 2).
        for line, raw_row in enumerate(raw_rows, 2):
            try:
                row = normalize(raw_row, kind, customers)
                if kind == 'invoices':
                    outcome = insert_invoice(db, row)
                else:
                    outcome = insert_payment(db, row, find_invoice(db, row))
                result[outcome] += 1
            except (ValueError, sqlite3.IntegrityError) as exc:
                result['rejected'] += 1
                result['errors'].append({'line': line, 'reason': str(exc)})
        finish_import_batch(db, batch_id, result)
    result['import_id'] = batch_id
    return result
