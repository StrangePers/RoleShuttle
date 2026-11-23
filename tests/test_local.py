from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from roleshuttle.local import consult, delegate
from roleshuttle.cli import main


class LocalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.metadata = {"model_info": {"general.architecture": "example"}}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append((self.path, body))
                result = outer.metadata if self.path == "/api/show" else {
                    "model": body["model"], "done": True, "done_reason": "stop",
                    "message": {"role": "assistant", "content": "Check the selected rows."},
                    "prompt_eval_count": 12, "eval_count": 5,
                    "load_duration": 100, "eval_duration": 550, "prompt_eval_duration": -1,
                }
                raw = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.profile = Path(temp.name) / "profile.json"
        self.profile.write_text(json.dumps({"port": server.server_port,
            "roles": {"research": "small:1", "specialist": "design:3", "review": "larger:2"}}))

    def test_consult_routes_proposal_and_original_task_to_fresh_reviewer(self):
        task = 'Fix export with "quoted" names'
        stage = self.profile.parent / "question.txt"
        stage.write_text(task, encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["consult", "--profile", str(self.profile), "--input", str(stage),
                         "--max-output-tokens", "64"])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result["status"]), (0, "returned"))
        calls = [body for path, body in self.calls if path == "/api/chat"]
        self.assertEqual([c["model"] for c in calls], ["design:3", "larger:2"])
        self.assertEqual(len(calls[1]["messages"]), 2)
        self.assertNotEqual(calls[0]["messages"][0], calls[1]["messages"][0])
        self.assertEqual(json.loads(calls[1]["messages"][1]["content"]),
                         {"task": task, "proposal_to_review": result["calls"][0]["result"]})
        self.assertTrue(all(c["options"]["num_predict"] == 64 for c in calls))

    def test_consult_validates_review_tag_before_first_call(self):
        self.profile.write_text(json.dumps({"roles": {"specialist": "one:1", "review": "other:cloud"}}))
        with self.assertRaises(ValueError):
            consult(self.profile, "Decide")
        self.assertEqual(self.calls, [])

    def test_consult_preserves_completed_receipts_on_partial_routes(self):
        partial = {"done_reason": "length", "result": ""}
        complete = {"done_reason": "stop", "result": "proposal"}
        for replies, status, completed in (
                ([partial], "specialist_incomplete", [partial]),
                ([complete, partial], "review_incomplete", [complete, partial]),
                ([complete, OSError("unavailable")], "review_failed", [complete])):
            with self.subTest(status=status), patch("roleshuttle.local._call", side_effect=replies) as call:
                result = consult(self.profile, "Decide")
                self.assertEqual(result["status"], status)
                self.assertEqual(result["calls"], completed)
                self.assertEqual(call.call_count, len(replies))

    def test_different_roles_route_and_return_usage(self):
        for role, model in (("research", "small:1"), ("review", "larger:2")):
            result = delegate(self.profile, role, "Review selected rows")
            self.assertEqual(result["model"], model)
            self.assertEqual(result["usage"]["eval_count"], 5)
            self.assertIsNone(result["usage"]["total_duration"])
            self.assertEqual(result["usage"]["load_duration"], 100)
            self.assertEqual(result["usage"]["eval_duration"], 550)
            self.assertIsNone(result["usage"]["prompt_eval_duration"])
            self.assertEqual(self.calls[-1][1]["options"]["num_predict"], 2048)
            self.assertEqual(self.calls[-1][1]["messages"][1]["content"], "Review selected rows")

    def test_remote_metadata_stops_before_sending_task(self):
        self.metadata["remote_host"] = "https://example.invalid"
        with self.assertRaisesRegex(ValueError, "locally installed"):
            delegate(self.profile, "research", "Private test input")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("Private test input", str(self.calls))

    def test_missing_role_does_not_contact_server(self):
        with self.assertRaises(ValueError):
            delegate(self.profile, "implement", "Sample")
        self.assertEqual(self.calls, [])

    def test_invalid_output_limit_does_not_contact_server(self):
        for limit in (0, 2049, True, 0.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                delegate(self.profile, "review", "Sample", max_output_tokens=limit)
        self.assertEqual(self.calls, [])

    def test_controller_collects_and_passes_a_result_to_another_role(self):
        stage = self.profile.parent / "stage.txt"
        stage.write_text("Find edge cases", encoding="utf-8")
        for role in ("research", "review"):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["delegate", "--profile", str(self.profile), "--role", role, "--input", str(stage),
                             "--max-output-tokens", "48"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls[-1][1]["options"]["num_predict"], 48)
            receipt = json.loads(output.getvalue())
            self.assertEqual(receipt["role"], role)
            stage.write_text("Review this finding: " + receipt["result"], encoding="utf-8")
        self.assertEqual(self.calls[-1][1]["model"], "larger:2")
        self.assertIn("Review this finding: Check the selected rows.", str(self.calls[-1][1]["messages"]))
