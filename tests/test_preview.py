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

    def test_missing_runtime_is_explicit(self):
        with patch.dict(os.environ, {"ROLESHUTTLE_RUNTIME_ARGV": ""}):
            self.assertEqual(cli.main(["task", "show", "--task-id", "sample"]), 2)

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
