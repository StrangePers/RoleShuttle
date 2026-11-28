"""Authenticated web actions for privately stored autonomous tasks."""

import asyncio
from datetime import datetime, timezone
import json
import time

from . import advanced_settings, go_stage, launch_routing
from .codex_stage import native_client, subscription_limits
from .local_acceptance import accept_task, check_baseline, check_task, read_check_log
from .local_check_setup import check_setup_status, configure_checks
from .local_execution import execute_patch, inspect_result
from .local_model import answer_question, cancel_model, close_go, codex_route, propose_patch, reconcile_go, revise_patch
from .local_tasks import database, load_project, prepare_task, show_task
from .local_worktrees import create_worktree
from .mesh_stage import MeshStage
from .model_settings import preset_assignments, read_settings
from .profile import profile_home
from .recommended import project_route_reasons
from .project_import import target_projects
from .r28_archive import archive_card, list_archive

ACTIONS = {"check-setup-status", "configure-checks", "prepare", "prepare-baseline", "launch", "status", "apply", "diff", "check", "check-baseline",
           "check-log", "accept", "revise", "answer", "cancel", "reconcile-go", "close-go", "archive-list", "archive-card"}


def model_route(models=None):
    models = read_settings()["settings"] if models is None else models
    configured = models["roles"].get("implementer")
    selected = configured
    if not selected or selected["provider"] == "opencode_go" and not go_stage.configured():
        selected = preset_assignments()["codex"]["roles"]["implementer"]
    return {"configured_assignment": configured, "assignment": selected}


def model_allowed(project, provider):
    return project["external_code_allowed"] and provider in project.get("automatic_cloud", [])


def listing():
    models = read_settings()["settings"]
    advanced = advanced_settings.read()
    route = model_route(models)
    primary = route["configured_assignment"] if advanced["revision"] != "empty" and route["configured_assignment"] else route["assignment"]
    plans = {category: launch_routing.plan(category,models,advanced["settings"],primary)
             for category in advanced_settings.LANES}
    route.update(routing_categories=list(plans), ordered_routing=advanced["revision"] != "empty")
    if not (profile_home() / "local-tasks.sqlite3").is_file():
        return {"projects": [], "tasks": [], **route}
    with database() as db:
        projects = []
        for row in target_projects(db).values():
            projects.append({"id": row["id"], "name": row.get("name", row["id"]),
                             "paths": row["allowed_paths"], "has_checks": bool(row.get("checks")),
                             "codex_allowed": model_allowed(row, "codex"),
                             "model_allowed": any(launch_routing.eligible(item,row) for item in plans["medium"]),
                             "allowed_categories": [key for key,plan in plans.items()
                                 if any(launch_routing.eligible(item,row) for item in plan)]})
        tasks = []
        for record, in db.execute("SELECT record FROM tasks ORDER BY rowid DESC LIMIT 200"):
            row = json.loads(record)
            tasks.append({"id": row["task_id"], "title": row["contract"]["title"], "status": row["status"]})
    return {"projects": projects, "tasks": tasks, **route}


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
    if name == "archive-list":
        return list_archive()
    if name == "archive-card":
        return archive_card(request.get("task_id"))
    if name == "prepare":
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 16_384:
            raise ValueError("Supply approved instructions and excerpts up to 16 KiB")
        models = read_settings()["settings"]
        selected = model_route(models)["assignment"]
        if request.get("assignment") != selected:
            raise ValueError("Model assignment changed. Refresh the panel before preparing the task.")
        advanced=advanced_settings.read()
        category=request.get("category","medium")
        primary = models["roles"].get("implementer") if advanced["revision"] != "empty" else selected
        candidates=launch_routing.plan(category,models,advanced["settings"],primary or selected)
        with database() as db:
            project = load_project(db, request.get("project_id"))
        if not any(launch_routing.eligible(row,project) for row in candidates):
            raise ValueError("The project does not authorize any saved model route")
        contract = {key: request.get(key) for key in ("project_id", "title", "allowed_paths")}
        return prepare_task({**contract, "mode": "write", "allow_extra_usage": False},
                            request_id=request.get("request_id"),
                            panel_input={"prompt": prompt, "assignment": request["assignment"],
                                         "advanced_revision":advanced["revision"],"category":category,
                                         "candidates":candidates})
    if name == "prepare-baseline":
        project_id = request.get("project_id")
        with database() as db:
            project = load_project(db, project_id)
        if not project.get("checks"):
            raise ValueError("Register project checks before preparing a baseline task")
        return prepare_task({"project_id": project_id, "title": f"Baseline checks: {project_id}",
                             "mode": "read", "allowed_paths": [], "allow_extra_usage": False},
                            request_id=request.get("request_id"))
    task_id = request.get("task_id")
    if name == "check-setup-status":
        return check_setup_status(task_id)
    if name == "configure-checks":
        return configure_checks(request)
    if name == "check-log":
        return read_check_log(task_id, request.get("receipt_id"), request.get("check_id"))
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
    if name == "check-baseline":
        if task["phase"] == "prepared" and task["contract"]["mode"] == "read":
            create_worktree(task_id)
        return check_baseline(task_id)
    if name == "accept":
        return accept_task(task_id, fingerprint, proof=request.get("proof"))
    if name == "cancel":
        return cancel_model(task_id, request.get("operation_id"))
    if name == "reconcile-go":
        return reconcile_go(task_id, request.get("operation_id"))
    if name == "close-go":
        return close_go(task_id, request.get("operation_id"), request.get("comment"))
    if task["phase"] == "model_closed":
        raise ValueError("This task is closed manually. Its saved history is read-only.")
    panel = task.get("panel_input")
    if not panel:
        raise ValueError("This task has no operator-approved panel input")
    if name == "launch" and task["phase"] in {"prepared", "worktree_ready"}:
        if check_setup_status(task_id)["requires_setup"]:
            raise ValueError("No checks are registered. Add task checks before launching.")
    if name == "launch" and task["phase"] == "prepared":
        create_worktree(task_id)
    choice = None
    if "candidates" in panel:
        if name == "launch" and task.get("model_operation"):
            return propose_patch(task_id, panel["prompt"], None)
        advanced = advanced_settings.read()
        if advanced["revision"] != panel["advanced_revision"]:
            raise ValueError("Advanced settings changed. Prepare a task against the current revision.")
        contract=task["contract"]
        with database() as db:
            project=load_project(db,contract["project_id"])
        if project["revision"] != contract["project_revision"]:
            raise ValueError("Project policy changed. Prepare a task against the current revision.")

        def observe(row):
            if row["provider"] == "mac-omlx":
                if project_route_reasons(project,row,{"required_tools":["read","edit"]},time.time()):
                    raise ValueError("Local model capability evidence is not valid")
                stage=MeshStage(row["model"],advanced["settings"])
                stage.authorize()
                return stage
            if row["provider"] == "codex":
                result=asyncio.run(live_snapshot({"model_id":row["model"],"effort":row["effort"]}))
                codex_route(result,contract)
                return result
            advanced_settings.enforce_go(row["model"],advanced)
            stage=go_stage.configured_stage()
            if advanced["revision"] != "empty":
                stage.health_timeout=advanced["settings"]["timeouts"]["local_health_seconds"]
                stage.task_timeout=advanced["settings"]["timeouts"]["task_default_seconds"]
            stage.check_model(row["model"])
            stage.authorize()
            return stage

        choice=launch_routing.choose(panel["candidates"],project,observe,existing=task.get("model_operation"))
        snapshot=choice[1] if choice[0]["provider"]=="codex" else None
    else:
        selected=panel["assignment"]
        snapshot=asyncio.run(live_snapshot(selected)) if selected["provider"]=="codex" else None
    if name == "launch":
        return propose_patch(task_id, panel["prompt"], snapshot, choice=choice)
    if name == "answer":
        return answer_question(task_id, panel["prompt"], snapshot, request.get("operation_id"), request.get("message"), choice=choice)
    if name == "revise":
        return revise_patch(task_id, panel["prompt"], snapshot, fingerprint, request.get("message"), choice=choice)
    raise ValueError("Unknown local action")
