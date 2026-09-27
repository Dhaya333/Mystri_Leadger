import csv
import io


def _load_invoices(db):
    """Load invoices with money kept in exact integer cents throughout."""
    data = db.execute('''
        SELECT i.id, i.customer_id, c.name AS customer_name, i.invoice_number,
               i.amount_cents, i.due_date, COALESCE(SUM(p.amount_cents), 0) AS paid_cents
        FROM invoices i JOIN customers c ON c.customer_id=i.customer_id
        LEFT JOIN payments p ON p.invoice_id=i.id
        GROUP BY i.id ORDER BY i.id
    ''').fetchall()
    result = []
    for row in data:
        balance_cents = row['amount_cents'] - row['paid_cents']
        result.append({
            'id': row['id'],
            'customer_id': row['customer_id'],
            'customer_name': row['customer_name'],
            'invoice_number': row['invoice_number'],
            'due_date': row['due_date'],
            'amount_cents': row['amount_cents'],
            'paid_cents': row['paid_cents'],
            'balance_cents': balance_cents,
            # 'open' means a positive balance; 'paid' means zero or negative
            # (an overpayment still counts as paid).
            'status': 'paid' if balance_cents <= 0 else 'open',
        })
    return result


def _display(row):
    return {
        'id': row['id'],
        'customer_id': row['customer_id'],
        'customer_name': row['customer_name'],
        'invoice_number': row['invoice_number'],
        'amount': row['amount_cents'] / 100,
        'due_date': row['due_date'],
        'paid': row['paid_cents'] / 100,
        'balance': row['balance_cents'] / 100,
        'status': row['status'],
    }


def invoices(db, status='all'):
    if status not in ('all', 'open', 'paid'):
        raise ValueError('status must be all, open or paid')
    rows = _load_invoices(db)
    if status != 'all':
        rows = [r for r in rows if r['status'] == status]
    return [_display(r) for r in rows]


def overview(db):
    rows = _load_invoices(db)
    unmatched = [
        {
            'payment_id': r['payment_id'],
            'customer_id': r['customer_id'],
            'invoice_number': r['invoice_number'],
            'amount': r['amount_cents'] / 100,
        }
        for r in db.execute('''SELECT payment_id, customer_id, invoice_number, amount_cents
            FROM payments WHERE invoice_id IS NULL ORDER BY payment_id''')
    ]
    outstanding_cents = sum(max(0, r['balance_cents']) for r in rows)
    return {
        'invoices': [_display(r) for r in rows],
        'unmatched_payments': unmatched,
        'summary': {
            'invoice_count': len(rows),
            'open_count': sum(r['status'] == 'open' for r in rows),
            'outstanding': outstanding_cents / 100,
        },
    }


def export_csv(db):
    output = io.StringIO(newline='')
    fields = ['customer_id', 'invoice_number', 'amount', 'paid', 'balance', 'status']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in _load_invoices(db):
        writer.writerow({
            'customer_id': row['customer_id'],
            'invoice_number': row['invoice_number'],
            'amount': f"{row['amount_cents'] / 100:.2f}",
            'paid': f"{row['paid_cents'] / 100:.2f}",
            'balance': f"{row['balance_cents'] / 100:.2f}",
            'status': row['status'],
        })
    return output.getvalue()
