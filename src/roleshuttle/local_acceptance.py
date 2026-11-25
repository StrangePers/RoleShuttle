"""Run registered checks on a clean baseline or a reviewed patch."""

from pathlib import Path
import re
from uuid import uuid4

from .check_runner import check_directory, executable_hash, run_check
from .local_execution import _review
from .local_tasks import database, identifier, load_project, load_task, show_task, stamp, update_task
from .local_worktrees import _operation_lock, verify_task_worktree
from .profile import profile_home


def current_checks(db, task, target):
    contract = task["contract"]
    project = load_project(db, contract["project_id"])
    if project["revision"] != contract["project_revision"]:
        raise ValueError("Project revision changed. Prepare a new task.")
    checks = project.get("checks", [])
    if not checks:
        raise ValueError("Register project checks before preparing a task for acceptance")
    for check in checks:
        check_directory(check, target)
        if executable_hash(check["argv"][0]) != check["executable_sha256"]:
            raise ValueError("Registered check executable changed")
    return project, checks


def approved_result(home, task, fingerprint):
    if task["phase"] != "patch_applied" or task["status"] not in {"review", "accepted"}:
        raise ValueError("Expected a completed, reviewable patch attempt")
    last = task["attempts"][-1]
    receipt = last["receipt"]
    if not receipt["reviewable"] or receipt["fingerprint"] != fingerprint:
        raise ValueError("Use the fingerprint of the saved reviewable result")
    target = verify_task_worktree(home, task, clean=False)
    evidence = _review(target, task)
    if not evidence["reviewable"] or evidence["fingerprint"] != fingerprint:
        raise ValueError("Worktree changed after review")
    return target, last["id"]


def unchanged(home, task, target, fingerprint):
    try:
        verify_task_worktree(home, task, clean=False)
        after = _review(target, task)
        return after["reviewable"] and after["fingerprint"] == fingerprint
    except (ValueError, OSError):
        return False


def baseline_unchanged(home, task):
    try:
        verify_task_worktree(home, task)
        return True
    except (ValueError, OSError):
        return False


def read_check_log(task_id, receipt_id, check_id):
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", receipt_id):
        raise ValueError("Invalid check receipt ID")
    identifier(check_id)
    with database() as db:
        load_task(db, task_id)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'check_logs'").fetchone():
            row = db.execute(
                "SELECT text, truncated FROM check_logs WHERE task_id = ? AND receipt_id = ? AND check_id = ?",
                (task_id, receipt_id, check_id)).fetchone()
            if row is not None:
                return {"receipt_id": receipt_id, "check_id": check_id, "text": row[0], "truncated": bool(row[1])}
    raise ValueError("Check log is unavailable for this task and receipt")


def run_registered_checks(home, task, target, binding, *, baseline):
    with database(write=True) as db:
        _, checks = current_checks(db, task, target)
        operation = {**binding, "id": uuid4().hex,
                     "project_revision": task["contract"]["project_revision"], "started_at": stamp()}
        task = update_task(db, task, phase="checking_baseline" if baseline else "checking_patch",
                           status="running", active=True, check_operation=operation, check_receipt=None)
    rows = []
    log = None
    stable = True
    for check in checks:
        try:
            row = run_check(check, target)
            log = row.pop("log", None)
            rows.append(row)
        except (ValueError, OSError):
            rows.append({"id": check["id"], "status": "failed", "exit_code": None})
        stable = (baseline_unchanged(home, task) if baseline
                  else unchanged(home, task, target, binding["fingerprint"]))
        if not stable or rows[-1]["status"] != "passed":
            break
    with database(write=True) as db:
        current = load_task(db, task["task_id"])
        if current["revision"] != task["revision"]:
            raise ValueError("Task changed while checks were running")
        passed = stable and len(rows) == len(checks) and all(row["status"] == "passed" for row in rows)
        receipt = {**operation, "finished_at": stamp(), "worktree_unchanged": stable,
                   "checks": rows, "passed": passed}
        changes = {}
        if log is not None:
            check_id = rows[-1]["id"]
            db.execute("""CREATE TABLE IF NOT EXISTS check_logs (
                task_id TEXT NOT NULL, receipt_id TEXT NOT NULL, check_id TEXT NOT NULL,
                text TEXT NOT NULL, truncated INTEGER NOT NULL,
                PRIMARY KEY (task_id, receipt_id, check_id)
            )""")
            db.execute("INSERT INTO check_logs VALUES (?, ?, ?, ?, ?)",
                       (task["task_id"], receipt["id"], check_id, log["text"], log["truncated"]))
            changes["last_check_log"] = {"receipt_id": receipt["id"], "check_id": check_id}
        status = ("completed" if passed else "failed") if baseline else "review"
        return update_task(db, current, phase="baseline_checked" if baseline else "patch_applied",
                           status=status if stable else "needs_review", active=False, check_receipt=receipt, **changes)


def check_task(task_id, fingerprint):
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        task = show_task(task_id)
        if task["status"] == "accepted":
            raise ValueError("Task has already been accepted")
        target, attempt_id = approved_result(home, task, fingerprint)
        return run_registered_checks(home, task, target, {"fingerprint": fingerprint, "attempt_id": attempt_id},
                                     baseline=False)


def check_baseline(task_id):
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        task = show_task(task_id)
        contract = task["contract"]
        if (contract["mode"] != "read" or contract["allow_extra_usage"]
                or task.get("model_operation") or task["attempts"]
                or task["phase"] not in {"worktree_ready", "baseline_checked"} or task["status"] == "needs_review"):
            raise ValueError("Expected a local baseline task with a ready worktree")
        target = verify_task_worktree(home, task)
        return run_registered_checks(home, task, target, {"base_revision": contract["base_revision"]}, baseline=True)


def verify_project_proof(proof, task, attempt_id, fingerprint, requirements, target):
    expected = {"task_id": task["task_id"], "revision": task["revision"],
                "attempt": attempt_id, "fingerprint": fingerprint}
    if (not isinstance(proof, dict) or set(proof) != expected.keys() | {"checks"}
            or any(proof[key] != value for key, value in expected.items())
            or not isinstance(proof["checks"], list) or len(proof["checks"]) != len(requirements)):
        raise ValueError("Project proof must match this task revision, attempt and fingerprint")
    for rule, check in zip(requirements, proof["checks"]):
        if (not isinstance(check, dict) or set(check) != {"id", "status", "evidence", "evidence_sha256"}
                or check["id"] != rule or check["status"] != "passed"
                or not isinstance(check["evidence"], str)):
            raise ValueError("Every project check needs an ordered successful evidence receipt")
        path = Path(check["evidence"])
        if (not path.is_absolute() or not path.is_file() or path.resolve().is_relative_to(target)
                or not 1 <= path.stat().st_size <= 4_000_000
                or any(parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
                       for parent in (path, *path.parents))
                or executable_hash(path) != check["evidence_sha256"]):
            raise ValueError("Project evidence must be unchanged, bounded files outside the task worktree")
    return proof


def accept_task(task_id, fingerprint, *, proof=None):
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        task = show_task(task_id)
        target, attempt_id = approved_result(home, task, fingerprint)
        receipt = task.get("check_receipt")
        if (not receipt or receipt.get("passed") is not True or receipt["fingerprint"] != fingerprint
                or receipt["attempt_id"] != attempt_id
                or receipt["project_revision"] != task["contract"]["project_revision"]):
            raise ValueError("Passing project checks for this exact result are required")
        with database(write=True) as db:
            project, checks = current_checks(db, task, target)
            if [check["id"] for check in checks] != [row["id"] for row in receipt["checks"]]:
                raise ValueError("Project check coverage changed")
            acceptance = {"fingerprint": fingerprint, "check_run_id": receipt["id"], "accepted_at": stamp()}
            if project.get("acceptance_mode") == "project":
                if proof is None:
                    raise ValueError("This project requires evidence from its acceptance workflow")
                completed = task.get("acceptance", {}).get("project_proof")
                if completed is not None:
                    if completed != proof:
                        raise ValueError("Task was accepted with different project proof")
                    verify_project_proof(proof, {**task, "revision": completed["revision"]},
                                         attempt_id, fingerprint, project["project_checks"], target)
                    return task
                acceptance["project_proof"] = verify_project_proof(
                    proof, task, attempt_id, fingerprint, project["project_checks"], target)
            elif proof is not None:
                raise ValueError("Project workflow proof is only for project acceptance mode")
            if task["status"] == "accepted":
                return task
            return update_task(db, task, status="accepted", acceptance=acceptance)


def reconcile_checks(task_id, fingerprint, *, confirm_checks_stopped):
    if confirm_checks_stopped is not True:
        raise ValueError("Confirm the interrupted check process tree has stopped")
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        task = show_task(task_id)
        check_operation = task.get("check_operation") or {}
        prior_receipt = task.get("check_receipt")
        baseline = task["phase"] in {"checking_baseline", "baseline_checked"}
        interrupted = task["phase"] in {"checking_patch", "checking_baseline"}
        changed = (task["phase"] in {"patch_applied", "baseline_checked"} and task["status"] == "needs_review"
                   and prior_receipt and prior_receipt["worktree_unchanged"] is False)
        binding = "base_revision" if baseline else "fingerprint"
        if not (interrupted or changed) or check_operation.get(binding) != fingerprint:
            raise ValueError("Expected a check requiring reconciliation with this fingerprint")
        if baseline:
            verify_task_worktree(home, task)
        else:
            approved_result(home, {**task, "phase": "patch_applied", "status": "review"}, fingerprint)
        with database(write=True) as db:
            current = load_task(db, task_id)
            if current["revision"] != task["revision"]:
                raise ValueError("Task changed during check recovery")
            operation = {**check_operation, "interrupted": interrupted, "reconciled_at": stamp()}
            if interrupted:
                operation["finished_at"] = operation["reconciled_at"]
            history = task.get("check_recoveries", [])
            return update_task(db, current, phase="worktree_ready" if baseline else "patch_applied",
                               status="planned" if baseline else "review", active=False,
                               check_operation=operation, check_receipt=None,
                               check_recoveries=[*history, {"operation": operation, "receipt": prior_receipt}])
