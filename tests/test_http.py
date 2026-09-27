"""HTTP-level edge cases against a real running server, complementing the
happy-path/basic-error coverage already in test_updates.HttpIntegrationTests.
"""
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from ledger import storage
from ledger.http_app import make_server


class HttpEdgeCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / 'http_edge.sqlite3'
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

    def _get(self, path):
        try:
            with urllib.request.urlopen(self._url(path)) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _post(self, path, body, headers=None):
        req = urllib.request.Request(
            self._url(path), data=body, method='POST',
            headers={'Content-Type': 'text/csv', **(headers or {})})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    # -- unknown routes -----------------------------------------------

    def test_get_to_unknown_path_returns_404(self):
        status, body = self._get('/api/nope')
        self.assertEqual(status, 404)
        self.assertIn('error', body)

    def test_post_to_unknown_path_returns_404(self):
        status, body = self._post('/api/nope', b'x')
        self.assertEqual(status, 404)
        self.assertIn('error', body)

    # -- malformed import requests -------------------------------------

    def test_import_with_no_kind_param_returns_400(self):
        status, body = self._post('/api/import', b'customer_id,invoice_number,amount,due_date\n')
        self.assertEqual(status, 400)
        self.assertIn('error', body)

    def test_import_with_non_utf8_body_returns_400_not_500(self):
        # Invalid as UTF-8 (an unpaired continuation byte).
        status, body = self._post('/api/import?kind=invoices', b'\xff\xfe\x00\x01')
        self.assertEqual(status, 400)
        self.assertIn('error', body)

    def test_content_length_far_exceeding_the_cap_is_rejected_without_reading_the_body(self):
        # A Content-Length far above the server's stated 2 MB limit must be
        # rejected immediately (before the server ever calls rfile.read),
        # so this must never hang even though no real body is sent.
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as sock:
            request = (
                'POST /api/import?kind=invoices HTTP/1.1\r\n'
                'Host: 127.0.0.1\r\n'
                'Content-Type: text/csv\r\n'
                'Content-Length: 999999999\r\n'
                'Connection: close\r\n'
                '\r\n'
            )
            sock.sendall(request.encode())
            sock.settimeout(5)
            response = sock.recv(4096).decode(errors='replace')
        status_line = response.split('\r\n', 1)[0]
        self.assertIn('400', status_line)

    # -- audit endpoint --------------------------------------------------

    def test_imports_with_nonexistent_id_returns_404(self):
        status, body = self._get('/api/imports?id=999999')
        self.assertEqual(status, 404)
        self.assertIn('error', body)

    def test_imports_with_non_integer_id_returns_400(self):
        status, body = self._get('/api/imports?id=notanumber')
        self.assertEqual(status, 400)
        self.assertIn('error', body)

    def test_imports_with_no_id_returns_200_and_a_list(self):
        status, body = self._get('/api/imports')
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)

    # -- CORS -------------------------------------------------------------

    def test_cross_origin_import_request_is_rejected(self):
        status, body = self._post(
            '/api/import?kind=invoices',
            b'customer_id,invoice_number,amount,due_date\n',
            headers={'Origin': 'http://evil.example'})
        self.assertEqual(status, 403)
        self.assertIn('error', body)


if __name__ == '__main__':
    unittest.main()
