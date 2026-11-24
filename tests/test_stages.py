"""Exercise recorded standalone calls and the combined companion snapshot."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
from threading import Barrier, Event
import unittest
from unittest.mock import patch

from roleshuttle.cli import main
from roleshuttle.companion import publish_sources
from roleshuttle.profile import write_json
from roleshuttle.stages import read_stage, run_stage


class StageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict("os.environ", {"ROLESHUTTLE_HOME": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def test_two_running_stages_keep_distinct_receipts(self):
        started = Barrier(3)
        finish = Event()

        def work(result, state):
            def call():
                started.wait(5)
                if not finish.wait(5):
                    raise RuntimeError("test did not release the worker")
                return {"result": result}
            return run_stage("test", call, record=True, outcome=lambda _: state)["stage_id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(work, "private response A", "waiting")
            b = pool.submit(work, "private response B", "review")
            try:
                started.wait(5)
                path = publish_sources(None)
                running = json.loads(path.read_text())
                self.assertEqual([item["state"] for item in running["items"]], ["running", "running"])
            finally:
                finish.set()
            first, second = a.result(), b.result()
        self.assertNotEqual(first, second)
        self.assertEqual(read_stage(first)["receipt"]["result"], "private response A")
        self.assertEqual(read_stage(second)["receipt"]["result"], "private response B")
        publish_sources(None)
        payload = path.read_text()
        self.assertNotIn("private response", payload)
        self.assertEqual([(item["id"], item["state"]) for item in json.loads(payload)["items"]],
                         [(first, "waiting"), (second, "review")])

    def test_bridge_staleness_and_private_fields(self):
        source = self.root / "bridge.json"
        now = datetime.now(timezone.utc)
        task_id = "a" * 32
        snapshot = {"updatedAt": now.isoformat(), "items": [
            {"id": task_id, "state": "running", "title": "Private project", "detail": "Private question"}
        ]}
        write_json(source, snapshot)
        path = publish_sources(source)
        self.assertEqual(json.loads(path.read_text())["items"][0]["id"], task_id)
        self.assertNotIn("Private", path.read_text())
        snapshot["updatedAt"] = (now - timedelta(minutes=2)).isoformat()
        write_json(source, snapshot)
        publish_sources(source)
        payload = json.loads(path.read_text())
        self.assertTrue(payload["offline"])
        self.assertEqual(payload["items"], [])

    def test_cli_records_only_on_request_and_can_read_receipt(self):
        input_path = self.root / "input.txt"
        input_path.write_text("private prompt", encoding="utf-8")
        arguments = ["codex-stage", "--role", "implementer", "--input", str(input_path)]
        output = io.StringIO()
        with patch("roleshuttle.cli.codex_stage", return_value={"status": "needs_input", "result": "a question"}):
            with redirect_stdout(output):
                self.assertEqual(main(arguments), 0)
            self.assertFalse((self.root / "stages").exists())
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([*arguments, "--record"]), 0)
        stage_id = json.loads(output.getvalue())["stage_id"]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["stage", stage_id]), 0)
        self.assertEqual(json.loads(output.getvalue())["state"], "waiting")
        self.assertNotIn("private prompt", output.getvalue())
