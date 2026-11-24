"""Host-side entry point for the selected Agent Console's TaskService."""

import json
from pathlib import Path
import sys
import time


QUESTION_PREFIX = "ROLESHUTTLE_QUESTION: "
QUESTION_PROTOCOL = (
    "\n\nRoleShuttle owner questions: If an owner decision is necessary before making changes, "
    "leave the worktree unchanged and write only one line to your final report: "
    "ROLESHUTTLE_QUESTION: <one plain-text question of at most 300 characters>. "
    "Finish the attempt after emitting that line. If a correction includes a RoleShuttle owner answer, "
    "use that answer and continue the original task."
)


def question_checkpoint(tasks, record):
    """Recognize a question only in a settled, successful, no-edit model report."""
    from agent_console.task_support import read_json
    from agent_console.tasks import decision_question

    attempt = record.get("attempt")
    if (record["active"] or record["status"] != "review" or type(attempt) is not int or attempt < 1
            or record["contract"].get("allow_extra_usage") is not False
            or not record["contract"]["instructions"].endswith(QUESTION_PROTOCOL)):
        return None
    directory = tasks.path(record["task_id"]) / f"attempt-{attempt}"
    try:
        report = directory / "report.md"
        if directory.is_symlink() or report.resolve() != report or not report.is_file():
            return None
        receipt = read_json(directory / "receipt.json")
        if (receipt.get("task_id") != record["task_id"] or receipt.get("executed") is not True
                or receipt.get("status") != "review" or receipt.get("changed_paths") != []):
            return None
        with report.open("rb") as stream:
            content = stream.read(2049)
        if len(content) > 2048:
            return None
        line = content.decode("utf-8-sig").strip()
        if line.startswith(QUESTION_PREFIX):
            return decision_question(line[len(QUESTION_PREFIX):])
    except (OSError, ValueError):
        pass
    return None


def advance_question(tasks, record, result, question):
    """Use native owner-decision history as the checkpoint for resuming the same ID."""
    if not question or result["owner_decision_pending"]:
        return result, None
    task_id, revision = record["task_id"], record["revision"]
    history = next((entry for entry in reversed(result.get("owner_decision_history", []))
                    if entry.get("attempt") == record["attempt"] and entry.get("question") == question), None)
    try:
        if record["requires_reconciliation"]:
            raise ValueError("r28 requires reconciliation before this task can continue")
        if history is None:
            return tasks.request_owner_decision(task_id, revision, question), None
        if history.get("resolution") != "answered":
            raise ValueError("This model question was closed without an answer")
        view = tasks.diff(task_id)
        if not view["reviewable"] or view["files"]:
            raise ValueError("The question checkpoint changed; inspect the worktree before continuing")
        evidence = record.get("evidence")
        if not evidence:
            return tasks.start(task_id, revision, "check", view["fingerprint"]), None
        if (evidence.get("attempt") != record["attempt"] or evidence.get("diff_review") != "passed"
                or evidence.get("fingerprint") != view["fingerprint"]):
            raise ValueError("r28 could not verify the question checkpoint")
        feedback = f"RoleShuttle owner answer\nQuestion: {question}\nAnswer: {history['answer']}"
        return tasks.start(task_id, revision, "correct", view["fingerprint"], feedback=feedback), None
    except (ValueError, OSError) as error:
        return tasks.detail(task_id), str(error)[:400]


def can_reconcile_cancel(record):
    from agent_console.task_support import writer_status

    if not record["requires_reconciliation"] or not any(
        reason.startswith("Cancellation requested.") for reason in record.get("reasons", [])
    ):
        return False
    owners = (record.get("owner"), (record.get("handoff") or {}).get("owner"))
    return all(owner is None or writer_status(owner) == "stopped" for owner in owners)


def reconcile_cancelled(TaskService, store, body):
    class CancelledTaskService(TaskService):
        def public(self, record, **kwargs):
            # r28 calls public inside its mutation lock before reconciliation.
            if record["requires_reconciliation"]:
                self.require_revision(record, body["revision"])
                if not can_reconcile_cancel(record):
                    raise ValueError("Only a stopped, cancelled panel task can be reconciled here")
            return super().public(record, **kwargs)

    return CancelledTaskService(store).reconcile(
        body["task_id"], body["reason"], confirmed_stopped=body["local_stopped"] is True and body["provider_stopped"] is True
    )


def provider_control(tasks, record):
    from agent_console.task_support import read_json

    if not record["active"] or record["requires_reconciliation"] or record.get("operation") not in {"execute", "correct"}:
        return None
    for path in tasks.path(record["task_id"]).glob("attempt-*/provider-turn.json"):
        try:
            control = read_json(path)
        except (OSError, ValueError):
            continue
        if control.get("nonce") == record.get("nonce") and control.get("task_id") == record["task_id"]:
            return path.parent, control
    return None


def cancel_task(tasks, TaskService, store, body):
    from agent_console.process_lineage import descendants
    from agent_console.task_support import read_json, write_json, writer_status

    task_id, revision = body["task_id"], body["revision"]
    current = tasks.detail(task_id)
    tasks.require_revision(current, revision)
    if not current["can_cancel"]:
        raise ValueError("r28 cannot stop this task in its current state")
    record = tasks.load(task_id)
    control = provider_control(tasks, record)
    if not control:
        return tasks.cancel(task_id, revision)
    attempt, identity = control
    owner = record.get("owner")
    if not owner:
        return tasks.cancel(task_id, revision)
    children, errors = descendants([owner])
    write_json(attempt / "provider-stop.request", identity)
    deadline = time.monotonic() + 17
    confirmed = False
    while time.monotonic() < deadline:
        try:
            proof = read_json(attempt / "provider-stop.json")
        except (FileNotFoundError, ValueError):
            time.sleep(.05)
            continue
        confirmed = proof.get("confirmed") is True and all(proof.get(k) == v for k, v in identity.items())
        break
    stopped = tasks.cancel(task_id, revision)
    if confirmed and not errors and all(writer_status(entry) == "stopped" for entry in [owner, *children]):
        return reconcile_cancelled(TaskService, store, {
            "task_id": task_id, "revision": stopped["revision"], "local_stopped": True, "provider_stopped": True,
            "reason": "Codex provider confirmed interruption and sent response.incomplete for the active turn.",
        })
    return stopped


def main():
    source, home, action = sys.argv[1:]
    sys.path.insert(0, str(Path(source) / "src"))
    from agent_console.policy import PolicyStore
    from agent_console.tasks import TaskService

    store = PolicyStore(Path(home))
    tasks = TaskService(store)
    body = json.load(sys.stdin)
    if action == "projects":
        result = [{"id": project["id"], "name": project["name"], "allowed_paths": project["allowed_paths"],
                   "checks": [{"id": check["id"], "title": check.get("title", check["id"])}
                              for check in project["checks"]]}
                  for project in tasks.projects.list()]
    else:
        task_id = body.get("task_id")
        question_error = None
        if action == "prepare":
            instructions = body["instructions"]
            if not isinstance(instructions, str):
                raise ValueError("Task instructions must be text")
            result = tasks.prepare({**body, "instructions": instructions + QUESTION_PROTOCOL,
                                    "allow_extra_usage": False})
        elif action in {"status", "answer"}:
            if action == "answer":
                result = tasks.answer_owner_decision(task_id, body["revision"], body["request_id"], body["answer"])
            else:
                result = tasks.detail(task_id)
            if result["status"] == "review" and not result["active"]:
                record = tasks.load(task_id)
                if record["revision"] == result["revision"]:
                    result, question_error = advance_question(tasks, record, result, question_checkpoint(tasks, record))
        elif action in {"launch", "correct"}:
            record = tasks.load(task_id)
            if record["contract"].get("allow_extra_usage") is not False:
                raise ValueError("The prepared task has extra usage enabled")
            if action == "launch":
                if record.get("decision", {}).get("execution_admissible") is not True:
                    raise ValueError("The prepared route was not admitted")
                result = tasks.start(task_id, body["revision"], "execute")
            else:
                if tasks.owner_decision_pending(record) or question_checkpoint(tasks, record):
                    raise ValueError("Answer the model question before continuing")
                result = tasks.start(task_id, body["revision"], "correct", body["fingerprint"],
                                     feedback=body["feedback"])
        elif action == "cancel":
            result = cancel_task(tasks, TaskService, store, body)
        elif action == "reconcile":
            result = reconcile_cancelled(TaskService, store, body)
        elif action == "diff":
            revision = tasks.load(task_id)["revision"]
            review = tasks.diff(task_id)
            tasks.require_revision(tasks.load(task_id), revision)
            result = {"task_id": task_id, "revision": revision,
                      **{key: review.get(key) for key in ("diff", "fingerprint", "files", "violations",
                                                         "truncated", "reviewable")}}
            return result
        elif action in {"check", "accept"}:
            record = tasks.load(task_id)
            if tasks.owner_decision_pending(record) or question_checkpoint(tasks, record):
                raise ValueError("Answer the model question before continuing")
            if action == "check":
                result = tasks.start(task_id, body["revision"], "check", body["fingerprint"])
            else:
                result = tasks.accept(task_id, body["revision"], body["fingerprint"])
        else:
            raise ValueError("Unknown panel operation")
        decision = result.get("decision", {})
        fields = ("task_id", "revision", "status", "phase", "title", "active", "can_launch", "can_cancel", "can_check",
                  "can_accept", "requires_reconciliation", "fingerprint", "reasons", "attempt",
                  "owner_decision_pending", "owner_decision_question", "owner_decision_request_id")
        checks = [{key: check.get(key) for key in ("id", "status")} for check in result.get("checks", [])]
        can_diff = bool(result.get("worktree")) and not result["active"]
        provider_cancel_ready = bool(result["can_cancel"] and provider_control(tasks, tasks.load(result["task_id"])))
        can_reconcile = False
        if result["requires_reconciliation"]:
            record = tasks.load(result["task_id"])
            can_reconcile = record["revision"] == result["revision"] and can_reconcile_cancel(record)
        correction_fingerprint = None
        if result["can_check"] and can_diff:
            record = tasks.load(result["task_id"])
            evidence = record.get("evidence") or {}
            if (record["revision"] == result["revision"] and not record.get("artifact_recovery")
                    and record["contract"].get("allow_extra_usage") is False
                    and evidence.get("attempt") == result.get("attempt")):
                correction_fingerprint = evidence.get("fingerprint")
        result = {**{key: result.get(key) for key in fields}, "can_diff": can_diff, "checks": checks,
                  "can_reconcile": can_reconcile, "provider_cancel_ready": provider_cancel_ready,
                  "correction_fingerprint": correction_fingerprint,
                  "question_error": question_error}
        result["decision"] = {key: decision.get(key) for key in ("assigned_model", "assigned_provider", "effort",
                                                                 "execution_admissible", "reason")}
    return result


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, "result": main()}, ensure_ascii=True))
    except (ValueError, OSError) as error:
        # Only operator-facing validation messages, no traceback or provider output.
        print(json.dumps({"ok": False, "error": str(error)[:400]}, ensure_ascii=True))
