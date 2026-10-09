import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkcheck_md.checker import BROKEN, OK, WARNING, HttpChecker, Options, check_files  # noqa: E402

# Os testes usam só um servidor local; nenhum proxy do sistema deve interferir.
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self._respond(include_body=False)

    def do_GET(self):
        self._respond(include_body=True)

    def _respond(self, include_body):
        server = self.server
        with server.lock:
            server.requests.append((self.command, self.path, self.headers.get("User-Agent", "")))
            hits = sum(1 for _, path, _ in server.requests if path == self.path)
        status, headers = 200, {}
        if self.path == "/nao-existe":
            status = 404
        elif self.path == "/antigo":
            status, headers = 301, {"Location": "/ok"}
        elif self.path == "/antigo-308":
            status, headers = 308, {"Location": "http://127.0.0.1:%d/ok" % server.server_port}
        elif self.path == "/temporario":
            status, headers = 302, {"Location": "/ok"}
        elif self.path == "/so-get" and self.command == "HEAD":
            status = 405
        elif self.path == "/lento":
            time.sleep(1.0)
        elif self.path == "/instavel" and hits <= 2:
            status = 503
        elif self.path == "/limite":
            status = 429
        elif self.path == "/ciclo":
            status, headers = 301, {"Location": "/ciclo"}
        body = b"ok" if include_body else b""
        try:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class HttpCheckerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Server(("127.0.0.1", 0), _Handler)
        cls.server.lock = threading.Lock()
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        with self.server.lock:
            self.server.requests.clear()
        self.sleeps = []
        self.checker = HttpChecker(timeout=3, retries=2, backoff=0.01, sleep=self.sleeps.append)

    def requests_for(self, path):
        with self.server.lock:
            return [(method, p) for method, p, _ in self.server.requests if p == path]

    def test_ok(self):
        status = self.checker.check(self.base + "/ok")
        self.assertEqual((status.status, status.code), (OK, 200))
        self.assertEqual(self.requests_for("/ok"), [("HEAD", "/ok")])

    def test_not_found(self):
        status = self.checker.check(self.base + "/nao-existe")
        self.assertEqual(status.status, BROKEN)
        self.assertEqual(status.code, 404)
        self.assertEqual(status.reason, "HTTP 404 (não encontrado)")

    def test_permanent_redirect_is_a_warning_with_new_url(self):
        for path, code in (("/antigo", 301), ("/antigo-308", 308)):
            status = self.checker.check(self.base + path)
            self.assertEqual(status.status, WARNING)
            self.assertEqual(status.code, code)
            self.assertEqual(status.new_url, self.base + "/ok")
            self.assertIn(f"redirecionamento permanente ({code})", status.reason)

    def test_temporary_redirect_is_ok(self):
        self.assertEqual(self.checker.check(self.base + "/temporario").status, OK)

    def test_head_not_allowed_falls_back_to_get(self):
        status = self.checker.check(self.base + "/so-get")
        self.assertEqual(status.status, OK)
        self.assertEqual(self.requests_for("/so-get"), [("HEAD", "/so-get"), ("GET", "/so-get")])

    def test_slow_endpoint_times_out(self):
        checker = HttpChecker(timeout=0.3, retries=0)
        status = checker.check(self.base + "/lento")
        self.assertEqual(status.status, BROKEN)
        self.assertIn("tempo esgotado", status.reason)

    def test_retries_with_backoff_on_5xx(self):
        status = self.checker.check(self.base + "/instavel")
        self.assertEqual(status.status, OK)
        self.assertEqual(len(self.requests_for("/instavel")), 3)
        self.assertEqual(self.sleeps, [0.01, 0.02])

    def test_rate_limit_is_a_warning_after_retries(self):
        status = self.checker.check(self.base + "/limite")
        self.assertEqual((status.status, status.code), (WARNING, 429))
        self.assertEqual(len(self.requests_for("/limite")), 3)

    def test_redirect_loop(self):
        status = self.checker.check(self.base + "/ciclo")
        self.assertEqual(status.status, BROKEN)
        self.assertEqual(status.reason, "redirecionamentos demais")

    def test_cache_ignores_fragment(self):
        self.checker.check(self.base + "/ok#parte-1")
        self.checker.check(self.base + "/ok#parte-2")
        self.checker.check(self.base + "/ok")
        self.assertEqual(len(self.requests_for("/ok")), 1)

    def test_custom_user_agent(self):
        HttpChecker(timeout=3, user_agent="TesteExemplo/1.0").check(self.base + "/ok")
        with self.server.lock:
            agents = [agent for _, path, agent in self.server.requests if path == "/ok"]
        self.assertEqual(agents, ["TesteExemplo/1.0"])
        self.checker.check(self.base + "/temporario")
        with self.server.lock:
            self.assertTrue(self.server.requests[-1][2].startswith("linkcheck-md/"))

    def test_connection_refused(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        status = self.checker.check("http://127.0.0.1:%d/" % port)
        self.assertEqual(status.status, BROKEN)
        self.assertEqual(status.reason, "conexão recusada")

    def test_check_files_in_parallel(self):
        with tempfile.TemporaryDirectory() as folder:
            readme = Path(folder) / "README.md"
            readme.write_text(
                "# Links\n"
                f"[a]({self.base}/ok) [b]({self.base}/nao-existe)\n"
                f"[c]({self.base}/antigo) [d]({self.base}/so-get)\n"
                f"[e]({self.base}/ok#secao)\n",
                encoding="utf-8",
            )
            report = check_files([readme], Options(workers=4, timeout=3, retries=0))
        self.assertEqual([r.status for r in report.results], [OK, BROKEN, WARNING, OK, OK])
        self.assertEqual(report.results[1].line, 2)
        self.assertEqual(report.results[2].new_url, self.base + "/ok")
        self.assertEqual(len(self.requests_for("/ok")), 2)


if __name__ == "__main__":
    unittest.main()
