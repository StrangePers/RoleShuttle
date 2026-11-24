from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from roleshuttle.local_tasks import prepare_task, register_project, show_project, show_task
from roleshuttle.local_worktrees import _make_worktree, create_worktree, reconcile_worktree
from roleshuttle.project_import import project_readiness


class LocalTasksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = self.root / "repo"
        self.repository.mkdir()
        env = patch.dict("os.environ", {"ROLESHUTTLE_HOME": str(self.root / "profile")})
        env.start()
        self.addCleanup(env.stop)
        self.git("init", "-q")
        self.git("commit", "--allow-empty", "-qm", "Fixture")
        self.project = {"id": "example", "repository": str(self.repository), "allowed_paths": ["src/"]}
        self.contract = {"project_id": "example", "title": "Example task", "mode": "write",
                         "allowed_paths": ["src/"]}

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repository), "-c", "user.name=Example",
                                        "-c", "user.email=example@example.invalid", "-c", "commit.gpgsign=false",
                                        *args], text=True).strip()

    def test_pins_project_and_commit_and_preserves_identity_on_replay(self):
        before = self.git("rev-parse", "HEAD")
        project = register_project(self.project, "empty")
        task = prepare_task(self.contract, request_id="original")
        self.assertEqual(task["contract"]["base_revision"], before)
        self.assertEqual(task["contract"]["project_revision"], project["revision"])
        self.assertFalse(task["active"])
        self.assertFalse(task["contract"]["allow_extra_usage"])
        self.git("commit", "--allow-empty", "-qm", "Later commit")
        register_project({**self.project, "external_code_allowed": True}, project["revision"])
        self.assertEqual(prepare_task(self.contract, request_id="original"), task)
        self.assertEqual(show_task(task["task_id"]), task)
        with self.assertRaisesRegex(ValueError, "another task contract"):
            prepare_task({**self.contract, "title": "Different task"}, request_id="original")
        self.assertEqual(len(self.git("worktree", "list", "--porcelain").split("worktree ")), 2)

    def test_refuses_expanded_scope_and_permissions(self):
        project = register_project(self.project, "empty")
        with self.assertRaisesRegex(ValueError, "revision changed"):
            register_project({**self.project, "external_code_allowed": True}, "empty")
        with self.assertRaisesRegex(ValueError, "Extra Usage"):
            prepare_task({**self.contract, "allow_extra_usage": True})
        with self.assertRaisesRegex(ValueError, "registered project scope"):
            prepare_task({**self.contract, "allowed_paths": ["private.txt"]})
        with self.assertRaisesRegex(ValueError, "Git internals"):
            prepare_task({**self.contract, "allowed_paths": ["src/.git/config"]})
        self.assertEqual(show_project("example"), project)

    def test_audit_distinguishes_checkout_from_executable_readiness(self):
        executable = self.root / "fixture.exe"
        executable.write_bytes(b"original fixture")
        values = {**self.project, "checks": [{"id": "verify", "argv": [str(executable)]}]}
        original = register_project(values, "empty")
        self.assertEqual(project_readiness()["ready_for_checks"], 1)
        executable.write_bytes(b"changed fixture")
        changed = project_readiness()
        self.assertEqual((changed["ready"], changed["ready_for_checks"]), (1, 0))
        self.assertEqual(changed["check_pin_issues"], {"example": [{"id": "verify", "status": "changed"}]})
        self.assertEqual(show_project("example"), original)
        executable.unlink()
        missing = project_readiness()
        self.assertEqual(missing["check_pin_issues"], {"example": [{"id": "verify", "status": "missing"}]})
        self.assertEqual(show_project("example"), original)

    def test_concurrent_replays_return_one_id(self):
        register_project(self.project, "empty")
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(pool.map(lambda _: prepare_task(self.contract, request_id="same"), range(4)))
        self.assertTrue(all(record == records[0] for record in records))

    def test_cli_recovers_prepared_record_across_processes(self):
        tracked = self.repository / "tracked.txt"
        tracked.write_text("committed contents", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-qm", "Track fixture")
        tracked.write_text("uncommitted source edit", encoding="utf-8")
        project = self.root / "project.json"
        contract = self.root / "task.json"
        project.write_text(json.dumps(self.project), encoding="utf-8")
        contract.write_text(json.dumps(self.contract), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

        def run(*args):
            return json.loads(subprocess.check_output([sys.executable, "-B", "-m", "roleshuttle", *args], env=env))

        self.assertEqual(run("local-project", "register", str(project))["id"], "example")
        task = run("local-task", "prepare", str(contract), "--request-id", "restart")
        self.assertEqual(run("local-task", "show", task["task_id"]), task)
        ready = run("local-task", "worktree", task["task_id"])
        self.assertEqual(ready["phase"], "worktree_ready")
        self.assertEqual(Path(ready["worktree"], "tracked.txt").read_text(), "committed contents")
        self.assertEqual(tracked.read_text(), "uncommitted source edit")
        self.assertEqual(run("local-task", "worktree", task["task_id"]), ready)
        self.assertEqual(run("local-task", "show", task["task_id"]), ready)

    def test_crash_after_git_creation_recovers_same_tree(self):
        register_project(self.project, "empty")
        task_id = prepare_task(self.contract)["task_id"]
        script = """
import os, sys
from unittest.mock import patch
from roleshuttle.local_worktrees import create_worktree
with patch("roleshuttle.local_worktrees._verify_worktree", side_effect=lambda *args: os._exit(73)):
    create_worktree(sys.argv[1])
"""
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        stopped = subprocess.run([sys.executable, "-B", "-c", script, task_id], env=env, timeout=30)
        self.assertEqual(stopped.returncode, 73)
        record = show_task(task_id)
        self.assertEqual(record["phase"], "creating_worktree")
        self.assertIsNone(record.get("worktree"))
        with self.assertRaisesRegex(ValueError, "unresolved"):
            create_worktree(task_id)
        with self.assertRaisesRegex(ValueError, "Confirm"):
            reconcile_worktree(task_id, confirm_git_stopped=False)
        ready = json.loads(subprocess.check_output(
            [sys.executable, "-B", "-m", "roleshuttle", "local-task", "reconcile-worktree",
             task_id, "--confirm-git-stopped"], env=env, timeout=30))
        self.assertEqual(ready["task_id"], task_id)
        self.assertEqual(ready["phase"], "worktree_ready")
        self.assertEqual(ready["worktree"], record["worktree_operation"]["destination"])
        self.assertEqual(ready["worktree_operation"]["id"], record["worktree_operation"]["id"])
        self.assertTrue(Path(ready["worktree"], ".git").is_file())
        self.assertEqual(len(self.git("worktree", "list", "--porcelain").split("worktree ")), 3)

    def test_absent_tree_can_retry_but_dirty_tree_stays_fenced(self):
        register_project(self.project, "empty")
        task_id = prepare_task(self.contract)["task_id"]
        with patch("roleshuttle.local_worktrees._make_worktree", side_effect=RuntimeError("missing")):
            with self.assertRaises(RuntimeError):
                create_worktree(task_id)
        self.assertEqual(reconcile_worktree(task_id, confirm_git_stopped=True)["phase"], "prepared")

        def dirty(repository, target, commit):
            _make_worktree(repository, target, commit)
            (target / "untracked.txt").write_text("keep me", encoding="utf-8")
            raise RuntimeError("interrupted")
        with patch("roleshuttle.local_worktrees._make_worktree", side_effect=dirty):
            with self.assertRaises(RuntimeError):
                create_worktree(task_id)
        with self.assertRaisesRegex(ValueError, "contains changes"):
            reconcile_worktree(task_id, confirm_git_stopped=True)
        record = show_task(task_id)
        self.assertEqual(record["phase"], "creating_worktree")
        self.assertEqual(Path(record["worktree_operation"]["destination"], "untracked.txt").read_text(), "keep me")

    def test_concurrent_worktree_operation_cannot_reconcile(self):
        register_project(self.project, "empty")
        task_id = prepare_task(self.contract)["task_id"]
        started, proceed = Event(), Event()
        def delayed(*args):
            started.set()
            if not proceed.wait(15):
                raise RuntimeError("test timeout")
            return _make_worktree(*args)
        with patch("roleshuttle.local_worktrees._make_worktree", side_effect=delayed), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(create_worktree, task_id)
            try:
                self.assertTrue(started.wait(15))
                with self.assertRaisesRegex(ValueError, "operation is in progress"):
                    create_worktree(task_id)
                with self.assertRaisesRegex(ValueError, "operation is in progress"):
                    reconcile_worktree(task_id, confirm_git_stopped=True)
            finally:
                proceed.set()
            self.assertEqual(future.result()["phase"], "worktree_ready")

    def test_stale_project_revision_and_base_refuse_creation(self):
        project = register_project(self.project, "empty")
        task_id = prepare_task(self.contract)["task_id"]
        self.git("commit", "--allow-empty", "-qm", "Later commit")
        with self.assertRaisesRegex(ValueError, "HEAD changed"):
            create_worktree(task_id)
        register_project({**self.project, "external_code_allowed": True}, project["revision"])
        with self.assertRaisesRegex(ValueError, "revision changed"):
            create_worktree(task_id)
        self.assertEqual(show_task(task_id)["phase"], "prepared")
