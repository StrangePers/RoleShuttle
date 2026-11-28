"""Configure checks for one task before any execution has started."""

from pathlib import Path

from .local_tasks import database, load_project, load_task, registered_checks, show_task, update_task
from .local_worktrees import _operation_lock
from .profile import profile_home


def effective_checks(task, project):
    return task.get("task_checks", project.get("checks", []))


def _project(db, task):
    contract = task["contract"]
    project = load_project(db, contract["project_id"])
    if project["revision"] != contract["project_revision"]:
        raise ValueError("Project revision changed. Prepare a new task.")
    return project


def _can_configure(task, project):
    return (task["contract"]["mode"] == "write"
            and task["phase"] in {"prepared", "worktree_ready"}
            and not task["active"]
            and not task.get("model_operation")
            and not task["attempts"]
            and not task.get("check_operation")
            and not effective_checks(task, project))


def check_setup_status(task_id):
    with database() as db:
        db.execute("BEGIN")
        task = load_task(db, task_id)
        project = _project(db, task)
        return {"requires_setup": task["contract"]["mode"] == "write" and not effective_checks(task, project),
                "can_configure": _can_configure(task, project)}


def configure_checks(request):
    if (set(request) != {"task_id", "revision", "checks", "confirmed"}
            or request["confirmed"] is not True):
        raise ValueError("Explicitly confirm the task checks before saving.")
    task_id = request["task_id"]
    show_task(task_id)
    with _operation_lock(profile_home().resolve(), task_id), database(write=True) as db:
        task = load_task(db, task_id)
        if task["revision"] != request["revision"]:
            raise ValueError("Task revision changed. Refresh the task and confirm again.")
        project = _project(db, task)
        if not _can_configure(task, project):
            raise ValueError("Checks can only be added to an unstarted task without checks.")
        checks = request["checks"]
        if (not isinstance(checks, list) or not checks
                or any(not isinstance(row, dict) or "executable_sha256" in row for row in checks)):
            raise ValueError("Supply at least one command without executable pins.")
        normalized = registered_checks(checks, Path(project["repository"]), project["allowed_paths"])
        return update_task(db, task, task_checks=normalized)
