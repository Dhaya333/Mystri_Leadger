import sqlite3
from pathlib import Path


SCHEMA_SQL = '''
    CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY, name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id TEXT NOT NULL REFERENCES customers(customer_id),
        invoice_number TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        due_date TEXT NOT NULL,
        UNIQUE (customer_id, invoice_number)
    );
    CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL REFERENCES customers(customer_id),
        invoice_number TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        invoice_id INTEGER REFERENCES invoices(id)
    );
    CREATE TABLE IF NOT EXISTS import_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        source TEXT,
        started_at TEXT NOT NULL,
        imported INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        rejected INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS import_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL REFERENCES import_batches(id),
        line INTEGER NOT NULL,
        reason TEXT NOT NULL
    );
'''


def _column_names(db, table):
    return {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}


def _needs_migration(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'invoices' not in tables:
        return False
    cols = _column_names(db, 'invoices')
    # Starter schema stored a floating-point 'amount' column and had no
    # uniqueness constraint. Its absence of 'amount_cents' is our signal.
    return 'amount' in cols and 'amount_cents' not in cols


def _migrate_legacy_schema(db):
    """Migrate the starter schema (REAL amount, no identity constraint) to
    the integer-cents schema with a UNIQUE(customer_id, invoice_number)
    constraint, preserving every row's id/identity/allocations.
    """
    db.execute('ALTER TABLE invoices RENAME TO invoices_legacy')
    db.execute('ALTER TABLE payments RENAME TO payments_legacy')
    db.executescript(SCHEMA_SQL)
    for row in db.execute('SELECT * FROM invoices_legacy ORDER BY id'):
        cents = round(row['amount'] * 100)
        db.execute('''INSERT INTO invoices (id, customer_id, invoice_number, amount_cents, due_date)
                      VALUES (?, ?, ?, ?, ?)''',
                   (row['id'], row['customer_id'], row['invoice_number'], cents, row['due_date']))
    for row in db.execute('SELECT rowid, * FROM payments_legacy ORDER BY rowid'):
        cents = round(row['amount'] * 100)
        db.execute('''INSERT INTO payments (payment_id, customer_id, invoice_number, amount_cents, invoice_id)
                      VALUES (?, ?, ?, ?, ?)''',
                   (row['payment_id'], row['customer_id'], row['invoice_number'], cents, row['invoice_id']))
    db.execute('DROP TABLE invoices_legacy')
    db.execute('DROP TABLE payments_legacy')
    db.execute("DELETE FROM sqlite_sequence WHERE name IN ('invoices_legacy', 'payments_legacy')")


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    if _needs_migration(db):
        # Foreign keys are kept off for the duration of the migration so the
        # rename/rebuild/drop sequence can't be blocked by a transient
        # dangling reference; normal operation always re-enables them below.
        db.execute('PRAGMA foreign_keys = OFF')
        with db:
            _migrate_legacy_schema(db)
    db.execute('PRAGMA foreign_keys = ON')
    db.executescript(SCHEMA_SQL)
    return db


def seed(db):
    if db.execute('SELECT COUNT(*) FROM customers').fetchone()[0]:
        return
    with db:
        db.executemany('INSERT INTO customers VALUES (?, ?)', [
            ('HARBOR', 'Harbor Design'), ('MAPLE', 'Maple Studio'),
            ('NORTH', 'North Workshop'),
        ])
        db.executemany('''INSERT INTO invoices
            (customer_id, invoice_number, amount_cents, due_date) VALUES (?, ?, ?, ?)''', [
            ('HARBOR', 'INV-100', 125000, '2026-09-01'),
            ('MAPLE', 'INV-200', 125000, '2026-09-02'),
            ('NORTH', 'INV-300', 1999, '2026-09-03'),
            ('HARBOR', 'INV-101', 30000, '2026-09-04'),
            ('MAPLE', 'INV-201', 60000, '2026-09-05'),
            ('NORTH', 'INV-301', 10000, '2026-09-06'),
        ])
        for pid, customer, number, amount_cents in [
            ('SEED-1', 'HARBOR', 'INV-101', 30000),
            ('SEED-2', 'NORTH', 'INV-300', 1000),
        ]:
            iid = db.execute('SELECT id FROM invoices WHERE customer_id=? AND invoice_number=?',
                             (customer, number)).fetchone()[0]
            db.execute('INSERT INTO payments VALUES (?, ?, ?, ?, ?)',
                       (pid, customer, number, amount_cents, iid))


def invoice_by_key(db, customer_id, invoice_number):
    return db.execute('SELECT * FROM invoices WHERE customer_id=? AND invoice_number=? ORDER BY id',
                      (customer_id, invoice_number)).fetchone()


def insert_invoice(db, row):
    existing = invoice_by_key(db, row['customer_id'], row['invoice_number'])
    if existing is not None:
        if (existing['amount_cents'] == row['amount_cents']
                and existing['due_date'] == row['due_date']):
            return 'skipped'
        raise ValueError('Invoice already exists with a different amount or due date')
    db.execute('''INSERT INTO invoices (customer_id, invoice_number, amount_cents, due_date)
                  VALUES (:customer_id, :invoice_number, :amount_cents, :due_date)''', row)
    return 'imported'


def insert_payment(db, row, invoice_id):
    old = db.execute('SELECT * FROM payments WHERE payment_id=?', (row['payment_id'],)).fetchone()
    if old:
        if (old['customer_id'] == row['customer_id']
                and old['invoice_number'] == row['invoice_number']
                and old['amount_cents'] == row['amount_cents']):
            return 'skipped'
        raise ValueError('Payment ID already exists with different details')
    db.execute('''INSERT INTO payments (payment_id, customer_id, invoice_number, amount_cents, invoice_id)
                  VALUES (:payment_id, :customer_id, :invoice_number, :amount_cents, :invoice_id)''',
               {**row, 'invoice_id': invoice_id})
    return 'imported'


def start_import_batch(db, kind, source):
    """Record the start of an import for auditability. Returns the batch id,
    which callers can hand back to the client for troubleshooting."""
    cur = db.execute(
        "INSERT INTO import_batches (kind, source, started_at) VALUES (?, ?, datetime('now'))",
        (kind, source),
    )
    return cur.lastrowid


def finish_import_batch(db, batch_id, result):
    """Persist final counts and any rejected-row details for a batch."""
    db.execute(
        'UPDATE import_batches SET imported=?, skipped=?, rejected=? WHERE id=?',
        (result['imported'], result['skipped'], result['rejected'], batch_id),
    )
    if result['errors']:
        db.executemany(
            'INSERT INTO import_errors (batch_id, line, reason) VALUES (?, ?, ?)',
            [(batch_id, e['line'], e['reason']) for e in result['errors']],
        )


def import_batch(db, batch_id):
    """Fetch one import batch and its rejected-row detail, for troubleshooting."""
    batch = db.execute('SELECT * FROM import_batches WHERE id=?', (batch_id,)).fetchone()
    if batch is None:
        return None
    errors = db.execute(
        'SELECT line, reason FROM import_errors WHERE batch_id=? ORDER BY line', (batch_id,)
    ).fetchall()
    return {**dict(batch), 'errors': [dict(e) for e in errors]}


def recent_import_batches(db, limit=20):
    """List the most recent import batches, newest first."""
    return [dict(r) for r in db.execute(
        'SELECT * FROM import_batches ORDER BY id DESC LIMIT ?', (limit,)
    )]
