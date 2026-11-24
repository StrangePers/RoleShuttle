"""Exercise documented entry points using only synthetic local data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from roleshuttle import cli
from roleshuttle.companion import LABELS, task_state
from roleshuttle.review import snapshot


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
             "-c", "commit.gpgSign=false", "-C", str(self.root), *args], text=True
        ).strip()

    def test_worktree_scope_and_fingerprint(self):
        self.git("init", "-q")
        source = self.root / "note.txt"
        source.write_text("before\n", encoding="utf-8")
        self.git("add", "note.txt")
        self.git("commit", "-qm", "Synthetic base")
        task = dict(task_id="sample", base_revision=self.git("rev-parse", "HEAD"),
                    allowed_paths=["note.txt"], mode="write")
        base = snapshot(self.root, task)
        source.write_text("after\n", encoding="utf-8")
        changed = snapshot(self.root, task)
        self.assertTrue(changed["reviewable"])
        self.assertNotEqual(base["fingerprint"], changed["fingerprint"])
        self.assertIn("+after", changed["diff"])
        (self.root / "outside.txt").write_text("private sample contents", encoding="utf-8")
        blocked = snapshot(self.root, task)
        self.assertFalse(blocked["reviewable"])
        self.assertNotIn("private sample contents", blocked["diff"])

    def test_bridge_forwards_arguments_without_reparsing(self):
        capture = self.root / "capture.json"
        script = self.root / "runtime stub.py"
        script.write_text("import json, sys\nfrom pathlib import Path\n"
                          "Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\nsys.exit(7)\n",
                          encoding="utf-8")
        argv = json.dumps([sys.executable, str(script), str(capture)])
        args = ["answer-decision", "--answer", "use selected rows & keep quotes"]
        with patch.dict(os.environ, {"ROLESHUTTLE_RUNTIME_ARGV": argv}):
            self.assertEqual(cli.main(["task", *args]), 7)
        self.assertEqual(json.loads(capture.read_text()), ["task", *args])
        dashboard = ["--port", "18767", "--task-id", "a" * 32, "--no-browser"]
        with patch.dict(os.environ, {"ROLESHUTTLE_RUNTIME_ARGV": argv}):
            self.assertEqual(cli.main(["dashboard", *dashboard]), 7)
        self.assertEqual(json.loads(capture.read_text()), ["serve", *dashboard])

    def test_missing_runtime_is_explicit(self):
        with patch.dict(os.environ, {"ROLESHUTTLE_RUNTIME_ARGV": ""}):
            self.assertEqual(cli.main(["task", "show", "--task-id", "sample"]), 2)

    def test_private_profile_publishes_distinct_runtime_tasks(self):
        profile = self.root / "private"
        profile.mkdir()
        script = self.root / "runtime.py"
        script.write_text(
            "import json, sys\n"
            "assert sys.argv[1:4] == ['task', 'show', '--task-id']\n"
            "assert sys.argv[5:] == ['--wait-seconds', '0']\n"
            "task_id = sys.argv[4]\n"
            "if task_id.startswith('c'):\n"
            "    raise SystemExit(2)\n"
            "print(json.dumps(dict(task_id=task_id, status='running', "
            "owner_decision_pending=task_id.startswith('b'), worktree='private-path')))\n",
            encoding="utf-8",
        )
        (profile / "runtime.json").write_text(json.dumps({
            "schema_version": 1, "argv": [sys.executable, str(script)]
        }), encoding="utf-8")
        running, waiting, missing = "a" * 32, "b" * 32, "c" * 32
        environment = dict(os.environ, ROLESHUTTLE_HOME=str(profile))
        environment.pop("ROLESHUTTLE_RUNTIME_ARGV", None)
        path = profile / "agentwhisker-status.json"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(cli.main(["companion", "--task-id", running, "--task-id", waiting]), 0)
            before = path.read_bytes()
            payload = json.loads(before)
            self.assertEqual(payload["state"], "waiting")
            self.assertEqual([(item["id"], item["state"]) for item in payload["items"]],
                             [(waiting, "waiting"), (running, "running")])
            self.assertNotIn(b"private-path", before)
            self.assertEqual(cli.main(["companion", "--task-id", running, "--task-id", missing]), 2)
            self.assertEqual(path.read_bytes(), before)

    def test_companion_filters_input(self):
        report = self.root / "report.json"
        cases = [
            ({"status": "running", "active": True}, "running"),
            ({"status": "blocked", "owner_decision_pending": True}, "waiting"),
            ({"status": "review", "lifecycle_outcome": "consumed"}, "idle"),
            ({"status": "failed"}, "failed"),
        ]
        with patch("pathlib.Path.home", return_value=self.root):
            for fields, state in cases:
                with self.subTest(state=state):
                    report.write_text(json.dumps({**fields, "instructions": "private sample"}))
                    self.assertEqual(cli.main(["companion", "--report", str(report)]), 0)
                    payload = json.loads((self.root / ".roleshuttle/agentwhisker-status.json").read_text())
                    self.assertEqual(payload, {"state": state, "title": LABELS[state]})
            with self.assertRaises(ValueError):
                task_state({"status": "unknown"})
