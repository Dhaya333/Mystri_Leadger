from .storage import invoice_by_key


def find_invoice(db, payment):
    """Attach a payment only by exact (customer_id, invoice_number) identity.

    Amount is never used to establish identity: two invoices can share an
    amount, and a payment for a nonexistent invoice must stay unmatched
    rather than latch onto an unrelated invoice that happens to match on
    amount.
    """
    invoice = invoice_by_key(db, payment['customer_id'], payment['invoice_number'])
    return invoice['id'] if invoice else None
