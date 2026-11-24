"""Authenticated web actions for privately stored autonomous tasks."""

import asyncio
from datetime import datetime, timezone
import json
import time

from .codex_stage import native_client, subscription_limits
from .local_acceptance import accept_task, check_task
from .local_execution import execute_patch, inspect_result
from .local_model import answer_question, cancel_model, propose_patch, revise_patch
from .local_tasks import database, prepare_task, show_task
from .local_worktrees import create_worktree
from .model_settings import preset_assignments, read_settings
from .profile import profile_home
from .project_import import target_projects

ACTIONS = {"prepare", "launch", "status", "apply", "diff", "check", "accept", "revise", "answer", "cancel"}


def assignment():
    selected = read_settings()["settings"]["roles"].get("implementer", {})
    if selected.get("provider") != "codex":
        selected = preset_assignments()["codex"]["roles"]["implementer"]
    return {**selected}


def listing():
    if not (profile_home() / "local-tasks.sqlite3").is_file():
        return {"projects": [], "tasks": [], "assignment": assignment()}
    with database() as db:
        projects = []
        for row in target_projects(db).values():
            projects.append({"id": row["id"], "name": row.get("name", row["id"]),
                             "paths": row["allowed_paths"],
                             "codex_allowed": row["external_code_allowed"] and "codex" in row.get("automatic_cloud", [])})
        tasks = []
        for record, in db.execute("SELECT record FROM tasks ORDER BY rowid DESC LIMIT 200"):
            row = json.loads(record)
            tasks.append({"id": row["task_id"], "title": row["contract"]["title"], "status": row["status"]})
    return {"projects": projects, "tasks": tasks, "assignment": assignment()}


async def live_snapshot(selected):
    model, effort = selected["model_id"], selected["effort"]
    try:
        async with native_client() as (client, _, unused), asyncio.timeout(30):
            limits = await subscription_limits(client)
            routes = await client.request("model/list", {"limit": 100})
            match = next((row for row in routes["data"] if row.get("model") == model), None)
            if not match or effort not in {value.get("reasoningEffort") for value in match.get("supportedReasoningEfforts", [])}:
                raise ValueError("The native Codex catalog cannot confirm this model and effort")
    except (TimeoutError, KeyError, TypeError, OSError) as error:
        raise ValueError("Codex availability could not be verified") from error
    bucket = limits["rateLimits"]
    usage = [bucket[key]["usedPercent"] for key in ("primary", "secondary") if bucket.get(key)]
    if bucket.get("individualLimit"):
        usage.append(100 - bucket["individualLimit"]["remainingPercent"])
    now = int(time.time())
    observed = datetime.fromtimestamp(now, timezone.utc).isoformat()
    route = "codex_engineer"
    return {
        "now": now, "baseline_routing": {"medium": [route]},
        "task": {"type": "feature", "complexity": "medium", "risk": "low", "priority": "normal",
                 "role": "implementer", "required_tools": ["read", "edit"],
                 "external_code_allowed": True, "allow_extra_usage": False, "context_tokens": 4096},
        "policy": {"schema_version": 2, "extra_usage_supported": False, "activate_saved_resets": False,
                   "snapshot_max_age_seconds": 120, "admission": {"mode": "provider_authoritative"},
                   "routing": {"medium": [route]}, "routes": {route: {"worker": "codex", "provider": "codex",
                       "model": model, "effort": effort, "tier": 3, "pool": "codex", "boundary": "external"}}},
        "availability": {"observed_at": observed, "routes": {route: {"available": True, "adapter_available": True,
                        "model": model, "effort": effort, "capabilities": ["read", "edit"], "context_tokens": 4096}},
                         "pools": {"codex": {"status": "known", "used_percent": max(usage), "observed_at": observed,
                                            "extra_usage_disabled": True}}},
    }


def action(name, request):
    if not isinstance(request, dict):
        raise ValueError("Expected a task request")
    if name == "prepare":
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 16_384:
            raise ValueError("Supply approved instructions and excerpts up to 16 KiB")
        if request.get("assignment") != assignment():
            raise ValueError("Model assignment changed. Refresh the panel before preparing the task.")
        contract = {key: request.get(key) for key in ("project_id", "title", "allowed_paths")}
        return prepare_task({**contract, "mode": "write", "allow_extra_usage": False},
                            request_id=request.get("request_id"),
                            panel_input={"prompt": prompt, "assignment": request["assignment"]})
    task_id = request.get("task_id")
    task = show_task(task_id)
    fingerprint = request.get("fingerprint")
    if name == "status":
        return task
    if name == "apply":
        return execute_patch(task_id)
    if name == "diff":
        return inspect_result(task_id)
    if name == "check":
        return check_task(task_id, fingerprint)
    if name == "accept":
        return accept_task(task_id, fingerprint, proof=request.get("proof"))
    if name == "cancel":
        return cancel_model(task_id, request.get("operation_id"))
    panel = task.get("panel_input")
    if not panel:
        raise ValueError("This task has no operator-approved panel input")
    if name == "launch" and task["phase"] == "prepared":
        create_worktree(task_id)
    snapshot = asyncio.run(live_snapshot(panel["assignment"]))
    if name == "launch":
        return propose_patch(task_id, panel["prompt"], snapshot)
    if name == "answer":
        return answer_question(task_id, panel["prompt"], snapshot, request.get("operation_id"), request.get("message"))
    if name == "revise":
        return revise_patch(task_id, panel["prompt"], snapshot, fingerprint, request.get("message"))
    raise ValueError("Unknown local action")
