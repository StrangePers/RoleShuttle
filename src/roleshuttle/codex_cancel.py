"""Collect provider interruption counters for one private native process."""

from collections import Counter
from contextlib import contextmanager
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import secrets
import threading

from .r28_codex_interrupt import metrics, open_responses

LIMIT = 2_000_000


@contextmanager
def cancellation_evidence():
    observed = Counter()
    lock = threading.Lock()
    token = secrets.token_urlsafe(32)

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.path != "/" + token or not 0 < size <= LIMIT:
                    self.send_error(403)
                    return
                self.connection.settimeout(3)
                data = self.rfile.read(size)
                if self.headers.get("Content-Encoding") == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                        data = stream.read(LIMIT + 1)
                if len(data) > LIMIT:
                    raise ValueError("Metrics payload exceeds its limit")
                incoming = Counter()
                metrics(json.loads(data), incoming)
            except (ValueError, OSError, EOFError, KeyError, TypeError, AttributeError):
                self.send_error(400)
                return
            with lock:
                for event, count in incoming.items():
                    observed[event] = max(observed[event], count)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

    def read():
        with lock:
            return observed.copy()

    with ThreadingHTTPServer(("127.0.0.1", 0), Collector) as server:
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/{token}"
        flags = ["-c", "features.interrupt_on_cancel=true", "-c", "features.instant_interrupt=false",
                 "-c", 'otel.exporter="none"', "-c", 'otel.trace_exporter="none"',
                 "-c", f'otel.metrics_exporter={{otlp-http={{endpoint="{url}",protocol="json"}}}}']
        env = {**os.environ, "OTEL_METRIC_EXPORT_INTERVAL": "300",
               "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "cumulative"}
        try:
            yield read, flags, env
        finally:
            server.shutdown()
            thread.join()


def interruption_confirmed(before, after):
    return (open_responses(before) == 1 and open_responses(after) == 0
            and after["response.created"] == before["response.created"]
            and after["response.completed"] == before["response.completed"]
            and after["response.interrupt.accepted"] == before["response.interrupt.accepted"] + 1
            and after["response.incomplete"] == before["response.incomplete"] + 1
            and after["response.interrupt.failed"] == before["response.interrupt.failed"])
