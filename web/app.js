const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' });
const money = n => currency.format(n);
const MAX_IMPORT_BYTES = 2 * 1024 * 1024; // must match the server's own limit
const text = (tag, value, className = '') => {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
};

async function refresh() {
  const status = document.querySelector('#status').value;
  const responses = await Promise.all([fetch('/api/overview'), fetch(`/api/invoices?status=${status}`)]);
  if (responses.some(r => !r.ok)) throw new Error('Could not refresh the register.');
  const [data, rows] = await Promise.all(responses.map(r => r.json()));
  document.querySelector('#invoice-count').textContent = data.summary.invoice_count;
  document.querySelector('#open-count').textContent = data.summary.open_count;
  document.querySelector('#outstanding').textContent = money(data.summary.outstanding);
  const body = document.querySelector('#invoices');
  body.replaceChildren();
  rows.forEach(r => {
    const row = document.createElement('tr');
    [r.customer_name, r.invoice_number, r.due_date].forEach(v => row.append(text('td', v)));
    [r.amount, r.paid, r.balance].forEach(v => row.append(text('td', money(v), 'number')));
    row.append(text('td', r.status));
    body.append(row);
  });
  const unmatched = document.querySelector('#unmatched');
  unmatched.replaceChildren(...data.unmatched_payments.map(p => text('li', `${p.payment_id} · ${p.customer_id} / ${p.invoice_number} · ${money(p.amount)}`)));
  if (!data.unmatched_payments.length) unmatched.append(text('li', 'No unmatched payments.'));
  document.querySelector('#page-error').textContent = '';
}

// Client-side checks that mirror the server's own rules, so an obviously
// bad file is rejected before anything is uploaded.
function validateFile(file) {
  if (!file) return 'Choose a CSV file first.';
  const looksLikeCsv = file.name.toLowerCase().endsWith('.csv')
    && (file.type === '' || file.type === 'text/csv' || file.type === 'application/vnd.ms-excel');
  if (!looksLikeCsv) return 'Only .csv files are accepted.';
  if (file.size === 0) return 'The selected file is empty.';
  if (file.size > MAX_IMPORT_BYTES) return 'File is larger than 2 MB.';
  return null;
}

async function submitImport(form) {
  // Guards against a double-fire (e.g. a fast repeat click/Enter) on top of
  // disabling the button, so a second submit can never start while one is
  // already in flight for this form.
  if (form.dataset.submitting === 'true') return;
  const feedback = form.querySelector('.feedback');
  const button = form.querySelector('button');
  const file = form.querySelector('input').files[0];

  const problem = validateFile(file);
  if (problem) {
    feedback.textContent = problem;
    return;
  }

  form.dataset.submitting = 'true';
  button.disabled = true;
  feedback.textContent = 'Reading file…';
  try {
    const csv = await file.text();
    feedback.textContent = 'Importing…';
    const query = new URLSearchParams({ kind: form.dataset.kind, source: file.name });
    const response = await fetch(`/api/import?${query}`, {
      method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: csv
    });
    const data = await response.json();
    // The response status and body are both checked before anything is
    // treated as a success — a failed request must never be reported as one,
    // and the visible register is only refreshed once the server confirms
    // the import actually happened.
    if (!response.ok) throw new Error(data.error || 'Import failed.');
    let message = `Imported ${data.imported}, skipped ${data.skipped}, rejected ${data.rejected}.`;
    if (data.errors && data.errors.length) {
      const details = data.errors.map(e => `line ${e.line}: ${e.reason}`).join('; ');
      message += ` Rejected rows — ${details}`;
    }
    feedback.textContent = message;
    await refresh();
  } catch (error) {
    feedback.textContent = `Import failed: ${error.message}`;
  } finally {
    button.disabled = false;
    form.dataset.submitting = 'false';
  }
}

document.querySelector('#status').addEventListener('change', () => refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; }));
document.querySelectorAll('form[data-kind]').forEach(form => form.addEventListener('submit', e => { e.preventDefault(); submitImport(form); }));
refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; });
