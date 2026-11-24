"""Codex App Server transport for r28 with provider-confirmed interruption."""
import argparse
from collections import Counter
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time


def key_values(attributes):
    return {item["key"]: item["value"].get("stringValue") for item in attributes}


def metrics(body, observed):
    for resource in body.get("resourceMetrics", []):
        for scope in resource["scopeMetrics"]:
            for metric in scope["metrics"]:
                if metric["name"] not in {"codex.websocket.event", "codex.websocket.event.count"}:
                    continue
                for point in metric.get("sum", {}).get("dataPoints", []):
                    attrs = key_values(point.get("attributes", []))
                    kind = attrs.get("kind", "")
                    count = int(point.get("asInt", "0"))
                    if kind in {"response.created", "response.completed", "response.incomplete",
                                "response.interrupt.accepted", "response.interrupt.failed"}:
                        observed[kind] = max(observed[kind], count)


def open_responses(observed):
    return observed["response.created"] - observed["response.completed"] - observed["response.incomplete"]


def usage_summary(token_usage):
    names = {"input_tokens": "inputTokens", "cached_input_tokens": "cachedInputTokens",
             "output_tokens": "outputTokens"}
    return {name: token_usage[field] for name, field in names.items()
            if type(token_usage.get(field)) is int}


class Protocol:
    def __init__(self, command, env):
        self.proc = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, encoding="utf-8")
        self.inbound = queue.Queue()
        self.serial = 0
        self.pending = []
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            for line in self.proc.stdout:
                self.inbound.put(json.loads(line))
        finally:
            self.inbound.put(None)

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params):
        self.serial += 1
        self.send({"id": self.serial, "method": method, "params": params})
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            item = self.receive(deadline)
            if item.get("id") == self.serial:
                if "error" in item:
                    raise RuntimeError(f"{method} failed: {item['error'].get('code')}")
                return item["result"]
            self.pending.append(item)
        raise RuntimeError(f"{method} did not confirm")

    def receive(self, deadline):
        item = self.inbound.get(timeout=max(0, deadline - time.monotonic()))
        if item is None:
            raise RuntimeError("Codex closed its transport")
        if item.get("method") and "id" in item:
            self.send({"id": item["id"], "error": {"code": -32601, "message": "No approvals in bounded pilot"}})
        return item

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("-m", dest="model", required=True)
    parser.add_argument("-c", dest="settings", action="append", default=[])
    parser.add_argument("--disable", action="append")
    parser.add_argument("--skip-git-repo-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-o", dest="report", type=Path, required=True)
    parser.add_argument("input", choices=["-"])
    args = parser.parse_args()
    report = args.report.resolve()
    attempt = report.parent
    task_dir = attempt.parent
    console_home = Path(os.environ["AGENT_CONSOLE_HOME"]).resolve()
    if report.name != "report.md" or task_dir.parent != console_home / "tasks" or not attempt.name.startswith("attempt-"):
        raise ValueError("Invalid r28 attempt location")
    record = json.loads((task_dir / "console.json").read_bytes())
    if record["contract"].get("allow_extra_usage") is not False or not record["active"]:
        raise ValueError("This Codex transport requires an active r28 task with Extra Usage disabled")
    identity = {"task_id": record["task_id"], "nonce": record["nonce"]}
    prompt = sys.stdin.read()
    settings = dict(setting.split("=", 1) for setting in args.settings)
    effort = json.loads(settings["model_reasoning_effort"])
    sandbox = json.loads(settings["sandbox_mode"])
    if sandbox not in {"read-only", "workspace-write"}:
        raise ValueError("Invalid worker sandbox")
    binary = os.environ["ROLESHUTTLE_CODEX_BINARY"]
    control = attempt / "provider-turn.json"
    request = attempt / "provider-stop.request"
    proof = attempt / "provider-stop.json"
    observed = Counter()
    token = secrets.token_urlsafe(24)

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/" + token or not 0 < (size := int(self.headers.get("Content-Length", "0"))) <= 2_000_000:
                self.send_error(403)
                return
            data = self.rfile.read(size)
            if self.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            metrics(json.loads(data), observed)
            response = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    with ThreadingHTTPServer(("127.0.0.1", 0), Collector) as collector:
        threading.Thread(target=collector.serve_forever, daemon=True).start()
        uri = f"http://127.0.0.1:{collector.server_port}/{token}"
        command = [binary, "-c", "features.interrupt_on_cancel=true",
                   "-c", "features.instant_interrupt=false", "--disable", "multi_agent",
                   "-c", 'otel.exporter="none"', "-c", 'otel.trace_exporter="none"',
                   "-c", f'otel.metrics_exporter={{otlp-http={{endpoint="{uri}",protocol="json"}}}}',
                   "app-server", "--listen", "stdio://"]
        client = Protocol(command, {**os.environ, "OTEL_METRIC_EXPORT_INTERVAL": "300",
                                    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "cumulative",
                                    "AGENT_CONSOLE_EXTRA_USAGE": "0"})
        try:
            client.request("initialize", {"clientInfo": {"name": "roleshuttle_cancel_pilot", "version": "0.1.0"}})
            client.send({"method": "initialized"})
            account = client.request("account/read", {"refreshToken": False})
            if (account.get("account") or {}).get("type") != "chatgpt":
                raise ValueError("The native ChatGPT subscription is required")
            limits = client.request("account/rateLimits/read", {})
            from agent_console.cloud_codex import _normalize_limits
            pool, _, _ = _normalize_limits(limits, time.time())
            if pool.get("extra_usage_disabled") is not True or limits.get("ordinaryUsageAllowed") is not True:
                raise ValueError("Codex must have ordinary subscription usage available and Extra Usage disabled")
            result = client.request("thread/start", {"cwd": str(Path.cwd()), "model": args.model,
                                                    "sandbox": sandbox, "approvalPolicy": "never"})
            thread_id = result["thread"]["id"]
            if result["model"] != args.model:
                raise ValueError("Codex selected a different model")
            print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
            turn = client.request("turn/start", {"threadId": thread_id, "effort": effort, "input": [
                {"type": "text", "text": prompt, "text_elements": []}]})["turn"]["id"]
            identity.update(thread_id=thread_id, turn_id=turn)
            print(json.dumps({"type": "turn.started"}), flush=True)
            last_message = ""
            usage = None
            terminal = None
            before = None
            while terminal is None:
                snapshot = observed.copy()
                if (snapshot["response.completed"] >= 1 and open_responses(snapshot) == 1
                        and not control.exists()):
                    save(control, identity)
                if before is None and request.is_file() and json.loads(request.read_bytes()) == identity:
                    before = snapshot
                    client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn})
                try:
                    item = client.pending.pop(0) if client.pending else client.receive(time.monotonic() + .2)
                except queue.Empty:
                    continue
                params = item.get("params", {})
                if params.get("threadId") != thread_id:
                    continue
                if item.get("method") == "item/completed" and params["item"]["type"] == "agentMessage":
                    last_message = params["item"]["text"]
                if item.get("method") == "thread/tokenUsage/updated":
                    usage = usage_summary(params.get("tokenUsage", {}).get("total", {}))
                if item.get("method") == "turn/completed" and params["turn"]["id"] == turn:
                    terminal = params["turn"]["status"]
            if before is None and terminal == "completed":
                report.write_text(last_message, encoding="utf-8")
                print(json.dumps({"type": "turn.completed", "usage": usage}), flush=True)
                return
            if before is not None and open_responses(before) == 1 and terminal == "interrupted":
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    after = observed.copy()
                    if (after["response.created"] == before["response.created"]
                            and after["response.interrupt.accepted"] == before["response.interrupt.accepted"] + 1
                            and after["response.incomplete"] == before["response.incomplete"] + 1
                            and after["response.interrupt.failed"] == before["response.interrupt.failed"]):
                        save(proof, {**identity, "confirmed": True, "before": dict(before), "after": dict(after)})
                        # Keep the r28 attempt active until the controller fences and stops it.
                        time.sleep(20)
                        break
                    time.sleep(.05)
            raise ValueError("Codex turn ended without an ordinary result")
        finally:
            client.close()
            collector.shutdown()


def save(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
