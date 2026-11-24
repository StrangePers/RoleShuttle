from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from roleshuttle.local_acceptance import accept_task, check_task, reconcile_checks
from roleshuttle.local_execution import execute_patch, inspect_result, reconcile_patch
from roleshuttle.local_panel import action as panel_action
from roleshuttle.local_model import answer_question, cancel_model, propose_patch, revise_patch
from roleshuttle.local_tasks import prepare_task, register_project, resolve_project, show_task
from roleshuttle.local_worktrees import create_worktree
from roleshuttle.project_import import digest, import_projects, preview_projects
from roleshuttle.recommended import preview_snapshot
from test_recommended import snapshot


class LocalExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "project"
        self.repo.mkdir()
        env = patch.dict(os.environ, {"ROLESHUTTLE_HOME": str(self.root / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "src").mkdir()
        (self.repo / "src/allowed.txt").write_bytes(b"before\n")
        (self.repo / "outside.txt").write_bytes(b"outside\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Fixture")
        self.project = {"id": "example", "repository": str(self.repo), "allowed_paths": ["src/"]}
        self.project_revision = register_project(self.project, "empty")["revision"]
        self.contract = {"project_id": "example", "title": "Apply example", "mode": "write", "allowed_paths": ["src/"]}
        self.task = create_worktree(prepare_task(self.contract)["task_id"])
        self.worktree = Path(self.task["worktree"])
        self.input = self.root / "input.patch"
        self.input.write_bytes(self.text_patch("src/allowed.txt", "before", "after"))

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), "-c", "user.name=Fixture",
                                        "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args])

    @staticmethod
    def text_patch(name, before, after):
        return (f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
                f"@@ -1 +1 @@\n-{before}\n+{after}\n").encode()

    @staticmethod
    def env():
        return {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def model_task(self):
        (self.repo / "src/allowed.txt").write_bytes(b"before\n\n")
        self.git("add", "src/allowed.txt")
        self.git("commit", "-qm", "Fixture with trailing context")
        check = {"id": "verify", "argv": [sys.executable, "-I", "-S", "-c",
                                          "from pathlib import Path; assert Path('src/allowed.txt').read_text() in {'after\\n\\n', 'final\\n\\n'}"]}
        register_project({**self.project, "external_code_allowed": True, "automatic_cloud": ["codex"],
                          "checks": [check]}, self.project_revision)
        task = create_worktree(prepare_task(self.contract)["task_id"])
        item = snapshot()
        item["now"] = int(time.time())
        observed = datetime.fromtimestamp(item["now"], timezone.utc).isoformat()
        item["task"].update(role="implementer", allow_extra_usage=False)
        item["availability"]["observed_at"] = observed
        for pool in item["availability"]["pools"].values():
            pool["observed_at"] = observed
        return task, item

    def test_panel_baseline_check_uses_pinned_clean_worktree(self):
        check = {"id": "python-unit", "argv": [sys.executable, "-I", "-S", "-c",
                 "from pathlib import Path; assert Path('src/allowed.txt').read_bytes() == b'before\\n'"]}
        register_project({**self.project, "checks": [check]}, self.project_revision)
        (self.repo / "src/allowed.txt").write_bytes(b"local edits\n")
        prepared = panel_action("prepare-baseline", {"project_id": "example", "request_id": "baseline-test"})
        task_id = prepared["task_id"]
        self.assertEqual(prepared["contract"]["base_revision"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertFalse(prepared["contract"]["allow_extra_usage"])
        checked = panel_action("check-baseline", {"task_id": task_id})
        receipt = checked["check_receipt"]
        self.assertTrue(receipt["passed"])
        self.assertEqual(receipt["base_revision"], prepared["contract"]["base_revision"])
        self.assertEqual(receipt["checks"], [{"id": "python-unit", "status": "passed", "exit_code": 0}])
        self.assertEqual(checked["attempts"], [])
        self.assertNotIn("model_operation", checked)
        self.assertEqual((self.repo / "src/allowed.txt").read_bytes(), b"local edits\n")
        with self.assertRaises(ValueError):
            accept_task(task_id, None)
        (Path(checked["worktree"]) / "untracked.txt").write_bytes(b"unexpected\n")
        with self.assertRaisesRegex(ValueError, "Worktree contains changes"):
            panel_action("check-baseline", {"task_id": task_id})

    def test_baseline_mutation_needs_reconciliation_before_retry(self):
        check = {"id": "python-unit", "argv": [sys.executable, "-I", "-S", "-c", "pass"]}
        register_project({**self.project, "checks": [check]}, self.project_revision)
        task = panel_action("prepare-baseline", {"project_id": "example"})
        task_id = task["task_id"]
        commit = task["contract"]["base_revision"]

        def mutate(check, target):
            (target / "src/allowed.txt").write_bytes(b"changed by check\n")
            return {"id": check["id"], "status": "passed", "exit_code": 0}

        with patch("roleshuttle.local_acceptance.run_check", side_effect=mutate):
            checked = panel_action("check-baseline", {"task_id": task_id})
        self.assertEqual(checked["status"], "needs_review")
        self.assertFalse(checked["check_receipt"]["worktree_unchanged"])
        self.assertFalse(checked["check_receipt"]["passed"])
        with self.assertRaisesRegex(ValueError, "Worktree contains changes"):
            reconcile_checks(task_id, commit, confirm_checks_stopped=True)
        (Path(checked["worktree"]) / "src/allowed.txt").write_bytes(b"before\n")
        with self.assertRaises(ValueError):
            panel_action("check-baseline", {"task_id": task_id})
        reconciled = reconcile_checks(task_id, commit, confirm_checks_stopped=True)
        self.assertIsNone(reconciled["check_receipt"])
        self.assertTrue(panel_action("check-baseline", {"task_id": task_id})["check_receipt"]["passed"])

    def test_model_patch_is_replayed_without_second_request_and_accepted(self):
        task, item = self.model_task()
        task_id = task["task_id"]
        response = {"model": "example-codex", "effort": "medium", "status": "returned",
                    "result": self.input.read_text(encoding="utf-8")}
        with patch("roleshuttle.local_model.codex_stage", return_value=response) as stage:
            saved = propose_patch(task_id, "Approved excerpts", item)
            self.assertEqual(propose_patch(task_id, "Approved excerpts", item), saved)
            stage.assert_awaited_once()
        with self.assertRaisesRegex(ValueError, "saved model patch"):
            execute_patch(task_id, self.input)
        applied = execute_patch(task_id)
        attempt = applied["attempts"][0]
        self.assertEqual(attempt["model_operation_id"], saved["model_operation"]["id"])
        self.assertEqual(execute_patch(task_id), applied)
        fingerprint = attempt["receipt"]["fingerprint"]
        self.assertTrue(check_task(task_id, fingerprint)["check_receipt"]["passed"])
        self.assertEqual(accept_task(task_id, fingerprint)["status"], "accepted")
        self.assertEqual((self.repo / "src/allowed.txt").read_bytes(), b"before\n\n")

    def test_answer_binds_question_and_runs_under_the_same_task(self):
        task, item = self.model_task()
        task_id = task["task_id"]
        metadata = {"model": "example-codex", "effort": "medium"}
        question = {**metadata, "status": "needs_input", "result": "Which scope?"}
        second = {**metadata, "status": "needs_input", "result": "Which encoding?"}
        response = {**metadata, "status": "returned", "result": self.input.read_text(encoding="utf-8")}
        with patch("roleshuttle.local_model.codex_stage", side_effect=[question, second, response]) as stage:
            waiting = propose_patch(task_id, "Approved excerpts", item)
            question_id = waiting["model_operation"]["id"]
            self.assertEqual(waiting["phase"], "waiting_model_input")
            with self.assertRaisesRegex(ValueError, "unanswered model question"):
                answer_question(task_id, "Approved excerpts", item, "other", "Selected")
            with self.assertRaisesRegex(ValueError, "prompt has changed"):
                answer_question(task_id, "New excerpts", item, question_id, "Selected")
            waiting = answer_question(task_id, "Approved excerpts", item, question_id, "Selected")
            next_id = waiting["model_operation"]["id"]
            result = answer_question(task_id, "Approved excerpts", item, next_id, "CSV")
            self.assertEqual(result["task_id"], task_id)
            self.assertEqual(result["model_operation"]["answer_to"], next_id)
            self.assertEqual([row["id"] for row in result["model_history"]], [question_id, next_id])
            self.assertIn('"answer": "Selected"', stage.call_args.args[1])
            self.assertIn('"answer": "CSV"', stage.call_args.args[1])
            self.assertEqual(answer_question(task_id, "Approved excerpts", item, next_id, "CSV"), result)
            self.assertEqual(answer_question(task_id, "Approved excerpts", item, question_id, "Selected"), result)
            with self.assertRaisesRegex(ValueError, "already has an answer request"):
                answer_question(task_id, "Approved excerpts", item, next_id, "JSON")
            self.assertEqual(stage.await_count, 3)
        applied = execute_patch(task_id)
        fingerprint = applied["attempts"][0]["receipt"]["fingerprint"]
        self.assertTrue(check_task(task_id, fingerprint)["check_receipt"]["passed"])
        self.assertEqual(accept_task(task_id, fingerprint)["status"], "accepted")

    def test_revision_requires_new_check_and_keeps_previous_receipts(self):
        task, item = self.model_task()
        task_id = task["task_id"]
        metadata = {"model": "example-codex", "effort": "medium", "status": "returned"}
        first_patch = self.input.read_text(encoding="utf-8")
        revised_patch = self.text_patch("src/allowed.txt", "after", "final").decode("utf-8")
        with patch("roleshuttle.local_model.codex_stage", side_effect=[
                {**metadata, "result": first_patch}, {**metadata, "result": revised_patch}]) as stage:
            propose_patch(task_id, "Approved excerpts", item)
            first = execute_patch(task_id)["attempts"][0]
            old = first["receipt"]["fingerprint"]
            first_check = check_task(task_id, old)["check_receipt"]
            self.assertTrue(first_check["passed"])
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                revise_patch(task_id, "Approved excerpts", item, "bad", "Use final")
            revised = revise_patch(task_id, "Approved excerpts", item, old, "Use final")
            self.assertEqual(revised["phase"], "model_revision_ready")
            self.assertEqual(revised["attempts"][0]["check_receipt"], first_check)
            self.assertIsNone(revised["check_receipt"])
            self.assertEqual(revise_patch(task_id, "Approved excerpts", item, old, "Use final"), revised)
            self.assertIn("incremental diff", stage.call_args.args[1])
            self.assertIn("Use final", stage.call_args.args[1])
            self.assertEqual(stage.await_count, 2)
        applied = execute_patch(task_id)
        self.assertEqual(applied["task_id"], task_id)
        last = applied["attempts"][-1]
        self.assertEqual(last["corrects_attempt"], first["id"])
        new = last["receipt"]["fingerprint"]
        self.assertNotEqual(old, new)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            accept_task(task_id, old)
        with self.assertRaisesRegex(ValueError, "Passing project checks"):
            accept_task(task_id, new)
        checked = check_task(task_id, new)
        self.assertTrue(checked["check_receipt"]["passed"])
        self.assertNotEqual(checked["check_receipt"]["id"], first_check["id"])
        self.assertEqual(accept_task(task_id, new)["status"], "accepted")

    def test_active_cancellation_is_fenced_and_keeps_provider_receipt(self):
        task, item = self.model_task()
        task_id = task["task_id"]

        async def stage(role, prompt, *, model, effort, cancellation):
            operation_id = show_task(task_id)["model_operation"]["id"]
            with self.assertRaisesRegex(ValueError, "current model operation"):
                cancel_model(task_id, "old")
            with self.assertRaisesRegex(ValueError, "active provider response"):
                cancel_model(task_id, operation_id)
            self.assertFalse(cancellation("thread", "turn", True))
            requested = cancel_model(task_id, operation_id)
            self.assertEqual(cancel_model(task_id, operation_id), requested)
            self.assertTrue(cancellation("thread", "turn", True))
            before = {"response.created": 2, "response.completed": 1}
            after = {**before, "response.interrupt.accepted": 1, "response.incomplete": 1}
            return {"model": model, "effort": effort, "status": "cancelled",
                    "interruption": {"thread_id": "thread", "turn_id": "turn", "native_terminal": "interrupted",
                                     "native_acknowledged": True, "before": before, "after": after}}

        with patch("roleshuttle.local_model.codex_stage", side_effect=stage) as adapter:
            result = propose_patch(task_id, "Approved excerpts", item)
            with self.assertRaisesRegex(ValueError, "already has a model request"):
                propose_patch(task_id, "Approved excerpts", item)
            adapter.assert_awaited_once()
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual((result["status"], result["phase"], result["active"]),
                         ("cancelled", "model_cancelled", False))
        self.assertEqual(result["attempts"], [])
        self.assertEqual(cancel_model(task_id, result["model_operation"]["id"]), result)
        with self.assertRaisesRegex(ValueError, "no saved model patch"):
            execute_patch(task_id)

    def test_interrupted_model_request_blocks_replay_and_patch_application(self):
        task, item = self.model_task()
        task_id = task["task_id"]
        with patch("roleshuttle.local_model.codex_stage", side_effect=KeyboardInterrupt) as stage:
            with self.assertRaises(KeyboardInterrupt):
                propose_patch(task_id, "Approved excerpts", item)
            with self.assertRaisesRegex(ValueError, "already has a model request"):
                propose_patch(task_id, "Approved excerpts", item)
            stage.assert_awaited_once()
        self.assertEqual(show_task(task_id)["phase"], "requesting_model")
        with self.assertRaisesRegex(ValueError, "no saved model patch"):
            execute_patch(task_id)

    def test_imported_rules_pin_linked_worktree_routing_and_project_acceptance(self):
        from contextlib import redirect_stdout
        from io import StringIO

        from roleshuttle.cli import main

        config = self.root / "r28"
        (config / "projects").mkdir(parents=True)
        linked = self.root / "linked"
        self.git("worktree", "add", "--detach", str(linked))
        check = {"id": "verify", "title": "Run inside src", "cwd": "src/", "timeout_seconds": 600,
                 "argv": [sys.executable, "-I", "-S", "-c",
                          "from pathlib import Path; assert Path('allowed.txt').read_bytes() == b'after\\n'"],
                 "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()}
        parent = {**self.project, "id": "imported", "name": "Fixture", "worktrees_allowed": True,
                  "external_code_allowed": True, "automatic_cloud": ["opencode_go"],
                  "extra_usage_allowed": True, "extra_usage_default": True, "checks": [check],
                  "acceptance_mode": "project", "project_checks": ["Project verification"],
                  "review_paths": ["outside.txt"]}
        parent_revision = digest(parent)
        child = {**parent, "id": "imported-linked", "repository": str(linked), "worktrees_allowed": False,
                 "source_project": {"id": "imported", "revision": parent_revision}}
        for row in (parent, child):
            (config / "projects" / (row["id"] + ".json")).write_text(
                json.dumps({**row, "revision": digest(row)}), encoding="utf-8")
        plan = preview_projects(config)
        with self.assertRaisesRegex(ValueError, "registry changed"):
            import_projects(config, "stale", plan["target_revision"])
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["local-project", "import-console", str(config), "--source-revision",
                                   plan["source_revision"], "--target-revision", plan["target_revision"]]), 0)
        migrated = resolve_project(linked)
        self.assertEqual(migrated["source_project"]["id"], "imported")
        self.assertEqual(migrated["checks"][0]["timeout"], 600)
        self.assertFalse(migrated["extra_usage_allowed"])
        choice = preview_snapshot(snapshot(), project_id="imported-linked")
        self.assertEqual(choice["assigned_provider"], "opencode-go")
        self.assertFalse(choice["execution_admissible"])
        self.assertFalse(choice["executed"])
        with self.assertRaisesRegex(ValueError, "Extra Usage"):
            prepare_task({**self.contract, "project_id": "imported-linked", "allow_extra_usage": True})

        task = create_worktree(prepare_task({**self.contract, "project_id": "imported-linked"})["task_id"])
        task_id = task["task_id"]
        result = execute_patch(task_id, self.input)
        attempt = result["attempts"][0]
        fingerprint = attempt["receipt"]["fingerprint"]
        self.assertEqual(inspect_result(task_id)["review_context"][0]["path"], "outside.txt")
        checked = check_task(task_id, fingerprint)
        self.assertTrue(checked["check_receipt"]["passed"])
        with self.assertRaisesRegex(ValueError, "acceptance workflow"):
            accept_task(task_id, fingerprint)
        evidence = self.root / "evidence.txt"
        evidence.write_text("Verified sample acceptance.", encoding="utf-8")
        proof = {"task_id": task_id, "revision": checked["revision"], "attempt": attempt["id"],
                 "fingerprint": fingerprint,
                 "checks": [{"id": "Project verification", "status": "passed", "evidence": str(evidence),
                             "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
        invalid = deepcopy(proof)
        invalid["checks"][0]["evidence_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "evidence must be unchanged"):
            accept_task(task_id, fingerprint, proof=invalid)
        accepted = accept_task(task_id, fingerprint, proof=proof)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accept_task(task_id, fingerprint, proof=proof), accepted)
        self.assertEqual((linked / "src/allowed.txt").read_bytes(), b"before\n")

        checks = [{key: value for key, value in check.items() if key != "executable_sha256"}]
        register_project({**parent, "checks": checks, "automatic_cloud": [],
                          "extra_usage_allowed": False, "extra_usage_default": False},
                         migrated["source_project"]["revision"])
        with self.assertRaisesRegex(ValueError, "current authorized source project"):
            prepare_task({**self.contract, "project_id": "imported-linked"})
        rebound = resolve_project(linked)
        self.assertNotEqual(rebound["source_project"]["revision"], migrated["source_project"]["revision"])
        self.assertEqual(rebound["automatic_cloud"], [])

    def test_cli_persists_receipt_reviews_and_detects_later_edits(self):
        task_id = self.task["task_id"]
        def run(*args):
            return json.loads(subprocess.check_output(
                [sys.executable, "-B", "-m", "roleshuttle", "local-task", *args], env=self.env()))
        result = run("execute", task_id, "--patch", str(self.input))
        self.assertEqual(result["status"], "review")
        self.assertEqual(run("execute", task_id, "--patch", str(self.input)), result)
        receipt = result["attempts"][0]["receipt"]
        self.assertTrue(receipt["reviewable"])
        self.assertEqual(receipt["files"], ["src/allowed.txt"])
        self.assertEqual(run("show", task_id), result)
        review = run("review", task_id)
        self.assertTrue(review["matches_receipt"])
        self.assertIn("+after", review["diff"])
        self.assertEqual((self.repo / "src/allowed.txt").read_bytes(), b"before\n")
        (self.worktree / "src/allowed.txt").write_bytes(b"later\n")
        self.assertFalse(run("review", task_id)["matches_receipt"])

    def test_scope_and_rename_rejection_leave_git_and_task_unchanged(self):
        task_id = self.task["task_id"]
        self.input.write_bytes(self.text_patch("outside.txt", "outside", "unauthorized"))
        with self.assertRaisesRegex(ValueError, "outside the task scope"):
            execute_patch(task_id, self.input)
        self.input.write_bytes(b"diff --git a/outside.txt b/src/copy.txt\nsimilarity index 100%\n"
                               b"rename from outside.txt\nrename to src/copy.txt\n")
        with self.assertRaisesRegex(ValueError, "renames"):
            execute_patch(task_id, self.input)
        self.assertEqual((self.worktree / "outside.txt").read_bytes(), b"outside\n")
        self.assertFalse((self.worktree / "src/copy.txt").exists())
        self.assertEqual(show_task(task_id), self.task)

    def test_project_change_refuses_to_run_and_git_overrides_do_not_escape(self):
        task_id = self.task["task_id"]
        with patch.dict(os.environ, {"GIT_WORK_TREE": str(self.repo), "GIT_DIR": str(self.repo / ".git"),
                                     "GIT_INDEX_FILE": str(self.repo / ".git/index")}):
            self.assertEqual(execute_patch(task_id, self.input)["status"], "review")
        self.assertEqual((self.repo / "src/allowed.txt").read_bytes(), b"before\n")
        second = create_worktree(prepare_task(self.contract)["task_id"])
        register_project({**self.project, "external_code_allowed": True}, self.project_revision)
        with self.assertRaisesRegex(ValueError, "Project revision changed"):
            execute_patch(second["task_id"], self.input)
        self.assertEqual(show_task(second["task_id"]), second)

    def test_interruption_after_git_write_fences_next_execution(self):
        task_id = self.task["task_id"]
        script = """
import os, sys
from unittest.mock import patch
from roleshuttle.local_execution import execute_patch
from roleshuttle.local_worktrees import verify_task_worktree
def before_receipt(home, record, *, clean=True):
    if not clean:
        os._exit(73)
    return verify_task_worktree(home, record, clean=clean)
with patch("roleshuttle.local_execution.verify_task_worktree", side_effect=before_receipt):
    execute_patch(sys.argv[1], sys.argv[2])
"""
        stopped = subprocess.run([sys.executable, "-B", "-c", script, task_id, str(self.input)],
                                 env=self.env(), timeout=30)
        self.assertEqual(stopped.returncode, 73)
        task = show_task(task_id)
        self.assertEqual(task["phase"], "applying_patch")
        self.assertEqual(task["attempts"][0]["status"], "applying")
        with self.assertRaisesRegex(ValueError, "unresolved attempt"):
            execute_patch(task_id, self.input)
        self.assertFalse(inspect_result(task_id)["matches_receipt"])
        fingerprint = inspect_result(task_id)["fingerprint"]
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            reconcile_patch(task_id, "incorrect", confirm_git_stopped=True)
        recovered = reconcile_patch(task_id, fingerprint, confirm_git_stopped=True)
        self.assertTrue(recovered["attempts"][-1]["reconciled"])
        self.assertEqual(recovered["attempts"][-1]["id"], task["attempts"][-1]["id"])
        self.assertTrue(inspect_result(task_id)["matches_receipt"])
        self.assertEqual((self.worktree / "src/allowed.txt").read_bytes(), b"after\n")
        self.assertEqual((self.repo / "src/allowed.txt").read_bytes(), b"before\n")

    def _task_with_check(self, command, *, timeout=15, second=None):
        commands = [command] if second is None else [command, second]
        project = {**self.project, "checks": [
            {"id": f"fixture-{index}", "argv": [sys.executable, "-I", "-S", "-B", "-c", script],
             "timeout": timeout}
            for index, script in enumerate(commands, 1)
        ]}
        self.project_revision = register_project(project, self.project_revision)["revision"]
        task = create_worktree(prepare_task(self.contract)["task_id"])
        applied = execute_patch(task["task_id"], self.input)
        return applied, applied["attempts"][-1]["receipt"]["fingerprint"]

    def test_cli_checks_and_accepts_exact_reviewed_result(self):
        task, fingerprint = self._task_with_check(
            "from pathlib import Path; assert Path('src/allowed.txt').read_bytes() == b'after\\n'")
        task_id = task["task_id"]
        with self.assertRaisesRegex(ValueError, "Passing project checks"):
            accept_task(task_id, fingerprint)
        def run(command):
            return json.loads(subprocess.check_output([sys.executable, "-B", "-m", "roleshuttle", "local-task",
                                                       command, task_id, "--fingerprint", fingerprint], env=self.env()))
        result = run("check")
        self.assertTrue(result["check_receipt"]["passed"])
        self.assertEqual(result["check_receipt"]["attempt_id"], task["attempts"][-1]["id"])
        path = Path(task["worktree"], "src/allowed.txt")
        path.write_bytes(b"changed after checks\n")
        with self.assertRaisesRegex(ValueError, "Worktree changed"):
            accept_task(task_id, fingerprint)
        path.write_bytes(b"after\n")
        accepted = run("accept")
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["acceptance"]["check_run_id"], result["check_receipt"]["id"])
        self.assertEqual((self.repo / "src/allowed.txt").read_bytes(), b"before\n")

    def test_registered_check_uses_and_removes_a_private_home(self):
        marker = self.root / "check-home.txt"
        original = Path.home()
        task, fingerprint = self._task_with_check(
            "import os, shlex; from pathlib import Path; "
            "home = Path.home(); assert home.is_dir(); "
            "assert home == Path(os.environ['HOME']) == Path(os.environ['USERPROFILE']); "
            "assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'; "
            "assert shlex.split(os.environ['PYTEST_ADDOPTS']) == ['-o', 'cache_dir=' + str(home / 'pytest-cache')]; "
            f"assert home != Path({str(original)!r}); "
            f"Path({str(marker)!r}).write_text(str(home), encoding='utf-8'); "
            "(home / 'probe.txt').write_text('isolated', encoding='utf-8')")
        result = check_task(task["task_id"], fingerprint)
        self.assertTrue(result["check_receipt"]["passed"])
        self.assertFalse(Path(marker.read_text(encoding="utf-8")).exists())

    def test_failed_or_mutating_checks_cannot_authorize_acceptance(self):
        task, fingerprint = self._task_with_check("raise SystemExit(7)")
        task_id = task["task_id"]
        cli = subprocess.run([sys.executable, "-B", "-m", "roleshuttle", "local-task", "check",
                              task_id, "--fingerprint", fingerprint], env=self.env(), capture_output=True)
        self.assertEqual(cli.returncode, 1, cli.stderr)
        rejected = json.loads(cli.stdout)
        self.assertEqual(rejected["check_receipt"]["checks"][0]["exit_code"], 7)
        with self.assertRaisesRegex(ValueError, "Passing project checks"):
            accept_task(task_id, fingerprint)
        changed, fingerprint = self._task_with_check(
            "from pathlib import Path; Path('src/allowed.txt').write_bytes(b'changed');",
            second="from pathlib import Path; Path('unexpected').touch()")
        result = check_task(changed["task_id"], fingerprint)
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["check_receipt"]["worktree_unchanged"])
        self.assertFalse(Path(changed["worktree"], "unexpected").exists())
        with self.assertRaises(ValueError):
            accept_task(changed["task_id"], fingerprint)

        with self.assertRaisesRegex(ValueError, "Worktree changed"):
            reconcile_checks(changed["task_id"], fingerprint, confirm_checks_stopped=True)
        (Path(changed["worktree"]) / "src/allowed.txt").write_bytes(b"after\n")
        recovered = reconcile_checks(changed["task_id"], fingerprint, confirm_checks_stopped=True)
        self.assertEqual(recovered["status"], "review")
        self.assertIsNone(recovered["check_receipt"])
        self.assertEqual(recovered["check_recoveries"][-1]["receipt"], result["check_receipt"])

    def test_interrupted_checks_leave_no_pass_and_can_be_retried(self):
        task, fingerprint = self._task_with_check("import time; time.sleep(2)", timeout=1)
        task_id = task["task_id"]
        with patch("roleshuttle.local_acceptance.run_check", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                check_task(task_id, fingerprint)
        self.assertIsNone(show_task(task_id)["check_receipt"])
        with self.assertRaisesRegex(ValueError, "Confirm"):
            reconcile_checks(task_id, fingerprint, confirm_checks_stopped=False)
        self.assertEqual(reconcile_checks(task_id, fingerprint, confirm_checks_stopped=True)["status"], "review")
        result = check_task(task_id, fingerprint)
        self.assertEqual(result["check_receipt"]["checks"][0]["status"], "timed_out")
        self.assertFalse(result["check_receipt"]["passed"])
