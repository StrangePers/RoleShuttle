"""Apply an operator-supplied text patch inside a pinned local worktree."""

import hashlib
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

from .local_tasks import database, load_project, load_task, show_task, stamp, update_task
from .local_worktrees import _operation_lock, verify_task_worktree
from .profile import profile_home
from .review import _git, MAX_FILES, snapshot
from .scope import check_scope, safe_path

MAX_PATCH_BYTES = 2 * 1024 * 1024
UNSUPPORTED = re.compile(
    rb"^(?:(?:rename|copy) (?:from|to) |(?:old|new) mode |GIT binary patch|"
    rb"(?:new|deleted) file mode (?!100(?:644|755)\r?$))", re.MULTILINE)


def read_patch(path):
    path = Path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Supply a regular patch file")
    with path.open("rb") as file:
        content = file.read(MAX_PATCH_BYTES + 1)
    return validate_patch(content)


def validate_patch(content):
    if not content or len(content) > MAX_PATCH_BYTES:
        raise ValueError("Expected a nonempty patch up to 2 MiB")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Expected a UTF-8 text patch") from None
    if b"\0" in content or UNSUPPORTED.search(content):
        raise ValueError("Binary patches, renames, copies and mode changes are unsupported")
    return content


def _apply(root, patch_path, *options):
    return _git(root, "-c", "core.fsmonitor=false", "apply", "--whitespace=nowarn",
                *options, "--", str(patch_path))


def _paths(root, patch_path, allowed):
    paths = []
    rows = _apply(root, patch_path, "--numstat", "-z").split(b"\0")
    for row in rows:
        if not row:
            continue
        fields = row.split(b"\t", 2)
        if len(fields) != 3 or not all(count.isdigit() for count in fields[:2]):
            raise ValueError("Expected a text patch with unambiguous file paths")
        name = fields[2].decode("utf-8")
        if safe_path(name) != name:
            raise ValueError("Expected canonical repository-relative patch paths")
        paths.append(name)
    if not paths or len(paths) > MAX_FILES:
        raise ValueError("Expected a bounded patch with file changes")
    if check_scope(root, paths, allowed):
        raise ValueError("Patch touches paths outside the task scope")
    for name in paths:
        try:
            entry = (root / name).stat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
            raise ValueError("Patch targets must be regular files without hard links")
    return set(paths)


def _review(root, record):
    contract = {**record["contract"], "task_id": record["task_id"]}
    return snapshot(root, contract, contract.get("generated_paths", ()))


def _start(record, digest, expected_files, model_operation, before):
    with database(write=True) as db:
        current = load_task(db, record["task_id"])
        if current["revision"] != record["revision"] or current["phase"] not in {"worktree_ready", "model_revision_ready"}:
            raise ValueError("Task changed before execution")
        contract = current["contract"]
        if load_project(db, contract["project_id"])["revision"] != contract["project_revision"]:
            raise ValueError("Project revision changed. Prepare a new task.")
        attempt = {"id": uuid4().hex, "executor": "local-patch", "patch_sha256": digest,
                   "expected_files": sorted(expected_files), "started_at": stamp(), "status": "applying"}
        if model_operation:
            executor = {"opencode_go":"opencode-go-patch","mac-omlx":"mesh-local-patch"}.get(
                model_operation.get("provider"),"codex-patch")
            attempt.update(executor=executor, model_operation_id=model_operation["id"])
            if "base_fingerprint" in model_operation:
                attempt.update(base_fingerprint=before["fingerprint"], base_files=before["files"],
                               corrects_attempt=model_operation["corrects_attempt"])
        return update_task(db, current, phase="applying_patch", status="running", active=True,
                           attempts=[*current["attempts"], attempt])


def _finish(record, evidence, *, recovered=False):
    with database(write=True) as db:
        current = load_task(db, record["task_id"])
        if current["revision"] != record["revision"]:
            raise ValueError("Task changed during execution. Inspect the worktree.")
        outcome = "review" if evidence["reviewable"] else "needs_review"
        result = {key: evidence[key] for key in ("fingerprint", "files", "violations", "reviewable")}
        attempts = current["attempts"]
        last = {**attempts[-1], "finished_at": stamp(), "status": outcome, "receipt": result}
        if recovered:
            last["reconciled"] = True
        return update_task(db, current, phase="patch_applied", status=outcome, active=False,
                           attempts=[*attempts[:-1], last])


def execute_patch(task_id, patch_file=None):
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        record = show_task(task_id)
        operation = record.get("model_operation")
        if patch_file is None:
            if not operation or operation["state"] != "returned":
                raise ValueError("This task has no saved model patch")
            content = validate_patch(operation["patch"].encode("utf-8"))
        else:
            if operation:
                raise ValueError("Use the saved model patch for this task")
            content = read_patch(patch_file)
        digest = hashlib.sha256(content).hexdigest()
        if (record["phase"] == "patch_applied" and record["attempts"][-1]["patch_sha256"] == digest
                and (not operation or record["attempts"][-1].get("model_operation_id") == operation["id"])):
            return record
        revision_base = operation.get("base_fingerprint") if operation else None
        phase = "model_revision_ready" if revision_base else "worktree_ready"
        if record["phase"] != phase or record["contract"]["mode"] != "write":
            raise ValueError("Expected a writable task with a ready worktree and no unresolved attempt")
        root = verify_task_worktree(home, record, clean=revision_base is None)
        before = _review(root, record)
        if not before["reviewable"] or (revision_base and before["fingerprint"] != revision_base):
            raise ValueError("Worktree changed after review or has an unsafe Git index")
        if not revision_base and before["files"]:
            raise ValueError("Worktree has changes before applying the initial patch")
        with tempfile.TemporaryDirectory(prefix="patch-", dir=home) as temporary:
            staged = Path(temporary) / "input.patch"
            staged.write_bytes(content)
            expected_files = _paths(root, staged, record["contract"]["allowed_paths"])
            # Generated diffs may omit context. The task already verifies the exact clean base.
            options = ("--unidiff-zero",) if operation else ()
            _apply(root, staged, *options, "--check")
            record = _start(record, digest, expected_files, operation, before)
            try:
                _apply(root, staged, *options)
                verify_task_worktree(home, record, clean=False)
                evidence = _review(root, record)
            except (ValueError, OSError) as error:
                raise ValueError("Patch outcome is uncertain. Inspect the worktree before retrying.") from error
        if not paths_match(record["attempts"][-1], evidence):
            evidence["violations"].append("Applied paths differ from the checked patch")
            evidence["reviewable"] = False
        return _finish(record, evidence)


def paths_match(attempt, evidence):
    expected = set(attempt["expected_files"])
    observed = set(evidence["files"])
    if "base_fingerprint" not in attempt:
        return observed == expected
    return (bool(observed) and evidence["fingerprint"] != attempt["base_fingerprint"]
            and observed.symmetric_difference(attempt["base_files"]) <= expected)


def inspect_result(task_id):
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        task = show_task(task_id)
        if task["phase"] not in {"applying_patch", "checking_patch", "patch_applied"}:
            raise ValueError("No local execution attempt to inspect")
        root = verify_task_worktree(home, task, clean=False)
        evidence = _review(root, task)
        receipt = task["attempts"][-1].get("receipt")
        return {**evidence, "task_id": task_id, "phase": task["phase"],
                "matches_receipt": bool(receipt and evidence["fingerprint"] == receipt["fingerprint"])}


def reconcile_patch(task_id, fingerprint, *, confirm_git_stopped):
    if confirm_git_stopped is not True:
        raise ValueError("Confirm the interrupted Git process has stopped")
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        task = show_task(task_id)
        if task["phase"] != "applying_patch":
            raise ValueError("Task has no interrupted patch attempt")
        root = verify_task_worktree(home, task, clean=False)
        evidence = _review(root, task)
        if evidence["fingerprint"] != fingerprint or not evidence["reviewable"]:
            raise ValueError("Inspect the current reviewable result and provide its fingerprint")
        previous = task["attempts"][-1]
        revision_base = previous.get("base_fingerprint")
        unchanged = evidence["fingerprint"] == revision_base if revision_base else not evidence["files"]
        if not unchanged:
            if not paths_match(previous, evidence):
                raise ValueError("Recovered paths do not match the recorded patch")
            return _finish(task, evidence, recovered=True)
        verify_task_worktree(home, task, clean=revision_base is None)
        with database(write=True) as db:
            current = load_task(db, task_id)
            if current["revision"] != task["revision"]:
                raise ValueError("Task revision changed during recovery")
            failed = {**previous, "status": "no_change", "finished_at": stamp(), "reconciled": True}
            phase = "model_revision_ready" if revision_base else "worktree_ready"
            return update_task(db, current, phase=phase, status="planned", active=False,
                               attempts=[*current["attempts"][:-1], failed])
