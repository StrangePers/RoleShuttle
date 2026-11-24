"""Propose one patch through a bounded subscription stage and save it privately."""

import asyncio
from collections import Counter
import hashlib
import json
import time
from uuid import uuid4

from .codex_cancel import interruption_confirmed
from .codex_stage import codex_stage
from .local import MAX_INPUT
from .local_acceptance import approved_result
from .local_execution import _review, validate_patch
from .local_tasks import database, load_project, load_task, show_task, stamp, update_task
from .local_worktrees import _operation_lock, verify_task_worktree
from .profile import profile_home
from .recommended import number, preview_snapshot


INSTRUCTIONS = (
    "Implement the operator's request using only the supplied file excerpts. "
    "Return a single UTF-8 unified git diff beginning with diff --git, with correct hunk counts, "
    "and context lines wherever available. No Markdown fences or prose. Limit edits to approved paths. "
    "Ask a question instead of guessing when the provided context is insufficient."
)


def propose_patch(task_id, prompt, snapshot):
    return request_patch(task_id, prompt, snapshot)


def answer_question(task_id, prompt, snapshot, question_id, answer):
    if not isinstance(question_id, str) or not question_id or not isinstance(answer, str):
        raise ValueError("Supply the saved question ID and a nonempty answer")
    answer = answer.strip()
    if not answer or len(answer.encode("utf-8")) > 4096:
        raise ValueError("Supply a nonempty answer up to 4096 bytes")
    return request_patch(task_id, prompt, snapshot, question_id=question_id, answer=answer)


def revise_patch(task_id, prompt, snapshot, fingerprint, comment):
    if not isinstance(comment, str) or not comment.strip() or len(comment.strip().encode("utf-8")) > 4096:
        raise ValueError("Supply a nonempty correction comment up to 4096 bytes")
    return request_patch(task_id, prompt, snapshot, correction=(fingerprint, comment.strip()))


def verify_model_base(home, task, fingerprint):
    root = verify_task_worktree(home, task, clean=fingerprint is None)
    if fingerprint is not None:
        evidence = _review(root, task)
        if not evidence["reviewable"] or evidence["fingerprint"] != fingerprint:
            raise ValueError("Worktree changed after review")


def earlier_context(operations):
    rows = []
    for operation in operations:
        item = {key: operation[key] for key in ("answer", "correction_comment", "question", "patch")
                if key in operation}
        if item:
            rows.append(item)
    return rows


def request_patch(task_id, prompt, snapshot, *, question_id=None, answer=None, correction=None):
    show_task(task_id)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > MAX_INPUT - 4096:
        raise ValueError("Supply bounded task instructions and approved excerpts")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        task = show_task(task_id)
        existing = task.get("model_operation")
        history = task.get("model_history", [])
        operations = [*history, existing] if existing else history
        wanted_phase = "worktree_ready"
        base_fingerprint, previous_attempt = None, None
        if correction is not None:
            fingerprint, comment = correction
            replay = next((row for row in operations if "correction_comment" in row
                           and row["base_fingerprint"] == fingerprint), None)
            if replay:
                if (replay["prompt_sha256"] == digest and replay["correction_comment"] == comment
                        and replay["state"] in {"returned", "needs_input"}):
                    return task
                raise ValueError("This result already has a correction request. Inspect its saved outcome.")
            if (not existing or existing["state"] != "returned" or task["status"] != "review"
                    or not task["attempts"] or task["attempts"][-1].get("model_operation_id") != existing["id"]):
                raise ValueError("Corrections require a reviewable, unaccepted model result")
            _, previous_attempt = approved_result(home, task, fingerprint)
            base_fingerprint, wanted_phase = fingerprint, "patch_applied"
        elif question_id is not None:
            replay = next((row for row in operations if row.get("answer_to") == question_id), None)
            if replay:
                if (replay["prompt_sha256"] == digest and replay["answer"] == answer
                        and replay["state"] in {"returned", "needs_input"}):
                    return task
                raise ValueError("This question already has an answer request. Inspect its saved outcome.")
            if not existing or existing["state"] != "needs_input" or existing["id"] != question_id:
                raise ValueError("Use the ID of the current unanswered model question")
            wanted_phase = "waiting_model_input"
            base_fingerprint = existing.get("base_fingerprint")
            previous_attempt = existing.get("corrects_attempt")
        elif existing:
            if existing["prompt_sha256"] == digest and existing["state"] in {"returned", "needs_input"}:
                return task
            raise ValueError("This task already has a model request. Inspect its outcome before further work.")
        if existing and existing["prompt_sha256"] != digest:
            raise ValueError("The original approved prompt has changed")
        contract = task["contract"]
        if (task["phase"] != wanted_phase or contract["mode"] != "write"
                or contract["allow_extra_usage"] is not False or contract["external_code_allowed"] is not True):
            raise ValueError("Model proposal requires a ready writable task, external-code consent and Extra Usage off")
        sent_prompt = f"{INSTRUCTIONS}\nApproved paths: {contract['allowed_paths']}\n\nOriginal task:\n{prompt}"
        if existing:
            context = earlier_context(operations)
            if base_fingerprint:
                sent_prompt += "\nThe worktree reflects all prior patches. Apply the latest correction and return an incremental diff against that state."
            if correction is not None:
                context.append({"correction_comment": correction[1]})
            else:
                context.append({"answer": answer})
            sent_prompt += "\n\nEarlier model results and operator decisions (JSON):\n" + json.dumps(context, ensure_ascii=False)
        if len(sent_prompt.encode("utf-8")) > MAX_INPUT:
            raise ValueError("Approved prompt and clarifications exceed the stage limit")
        task_input = snapshot.get("task", {}) if isinstance(snapshot, dict) else {}
        observed = snapshot.get("now") if isinstance(snapshot, dict) else None
        if (not isinstance(task_input, dict) or task_input.get("risk") != "low"
                or task_input.get("role") != "implementer" or task_input.get("allow_extra_usage") is not False
                or not number(observed) or not -5 <= time.time() - observed <= 120):
            raise ValueError("Use a recent low-risk implementer snapshot with Extra Usage off")
        verify_model_base(home, task, base_fingerprint)
        decision = preview_snapshot(snapshot, project_id=contract["project_id"])
        if (decision["project_revision"] != contract["project_revision"]
                or decision["execution_admissible"] is not True or decision["assigned_provider"] != "codex"
                or decision.get("role") != "implementer" or not decision.get("effort")):
            raise ValueError("No admissible Codex implementer route for the pinned project")
        operation = {"id": uuid4().hex, "state": "requesting", "started_at": stamp(),
                     "prompt_sha256": digest, "provider": "codex", "model": decision["assigned_model"],
                     "effort": decision["effort"], "route": decision["selected_route"], "allow_extra_usage": False}
        if existing:
            if any(operation[field] != existing[field] for field in ("model", "effort", "route")):
                raise ValueError("The original model route is no longer admissible")
            history = operations
        if base_fingerprint:
            operation.update(base_fingerprint=base_fingerprint, corrects_attempt=previous_attempt)
        if correction is not None:
            operation["correction_comment"] = correction[1]
        if question_id is not None:
            operation.update(answer_to=question_id, answer=answer)
        with database(write=True) as db:
            current = load_task(db, task_id)
            project = load_project(db, contract["project_id"])
            if current["revision"] != task["revision"] or project["revision"] != contract["project_revision"]:
                raise ValueError("Task or project changed before the model request")
            changes = {}
            if correction is not None:
                previous = {**current["attempts"][-1], "check_receipt": current.get("check_receipt")}
                changes = {"attempts": [*current["attempts"][:-1], previous], "check_receipt": None}
            task = update_task(db, current, phase="requesting_model", status="running", active=True,
                               model_operation=operation, model_history=history, **changes)
        try:
            response = asyncio.run(codex_stage(
                "implementer", sent_prompt,
                model=operation["model"], effort=operation["effort"],
                cancellation=lambda thread, turn, active: native_cancellation(
                    task_id, operation["id"], thread, turn, active)))
            verify_model_base(home, task, base_fingerprint)
            if (response["model"] != operation["model"] or response["effort"] != operation["effort"]
                    or response["status"] not in {"returned", "needs_input", "cancelled"}):
                raise ValueError("Unexpected model response")
            if response["status"] == "cancelled":
                return finish_request(task, {**operation, "usage": response.get("usage"),
                                             "admission": "chatgpt_subscription_zero_credits",
                                             "interruption": response["interruption"]},
                                      "cancelled", "cancelled", "model_cancelled")
            text = response["result"].replace("\r\n", "\n")
            if response["status"] == "returned":
                if not text.startswith("diff --git "):
                    raise ValueError("Expected a bare unified git diff")
                text = validate_patch((text.rstrip("\n") + "\n").encode("utf-8")).decode("utf-8")
            elif not text.strip() or len(text.encode("utf-8")) > 4096:
                raise ValueError("Invalid model question")
        except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            finish_request(task, operation, "unsettled", "needs_review", "model_unsettled")
            raise ValueError(f"Model proposal stopped. No automatic retry. {error}") from error
        state = response["status"]
        ready = "model_revision_ready" if base_fingerprint else "worktree_ready"
        return finish_request(task, {**operation, "usage": response.get("usage"),
                                     "admission": "chatgpt_subscription_zero_credits",
                                     "patch" if state == "returned" else "question": text},
                              state, "planned" if state == "returned" else "waiting",
                              ready if state == "returned" else "waiting_model_input")


def native_cancellation(task_id, operation_id, thread, turn, active):
    if not active:
        return False
    with database() as db:
        current = load_task(db, task_id)
        operation = current["model_operation"]
        if current["phase"] != "requesting_model" or operation["id"] != operation_id:
            raise ValueError("Native cancellation no longer matches the model request")
        if operation.get("native_turn"):
            return "cancel_requested_at" in operation
    with database(write=True) as db:
        current = load_task(db, task_id)
        operation = current["model_operation"]
        native = {"thread_id": thread, "turn_id": turn, "provider_active_at": stamp()}
        update_task(db, current, model_operation={**operation, "native_turn": native})
        return "cancel_requested_at" in operation


def cancel_model(task_id, operation_id):
    with database(write=True) as db:
        task = load_task(db, task_id)
        operation = task.get("model_operation")
        if not operation or operation["id"] != operation_id:
            raise ValueError("Use the current model operation ID")
        if "cancel_requested_at" in operation:
            return task
        if task["phase"] != "requesting_model" or not operation.get("native_turn"):
            raise ValueError("No confirmed active provider response for this model operation")
        return update_task(db, task, model_operation={**operation, "cancel_requested_at": stamp()})


def finish_request(task, operation, state, status, phase):
    with database(write=True) as db:
        current = load_task(db, task["task_id"])
        live = current.get("model_operation", {})
        if current["phase"] != "requesting_model" or live.get("id") != operation["id"]:
            raise ValueError("Task changed while model request was running")
        if state == "cancelled":
            native = live.get("native_turn", {})
            proof = operation["interruption"]
            if ("cancel_requested_at" not in live or not native
                    or any(proof[key] != native[key] for key in ("thread_id", "turn_id"))
                    or proof.get("native_terminal") != "interrupted" or proof.get("native_acknowledged") is not True
                    or not interruption_confirmed(Counter(proof["before"]), Counter(proof["after"]))):
                raise ValueError("Interruption does not match the requested native turn")
        return update_task(db, current, phase=phase, status=status, active=False,
                           model_operation={**live, **operation, "state": state, "finished_at": stamp()})
