"""Prepare a local task for an exact registered checkout."""

from pathlib import Path

from .local_tasks import discover_project, identifier, prepare_task, validate_task_intent


def prepare_for_checkout(values, checkout, *, request_id=None, register=None):
    """Register if needed, then prepare without starting a worker.

    The registration callback receives the canonical root string and returns
    the confirmed project record or a result with status CANCELLED or EXPIRED.
    """
    intent = validate_task_intent(values, checkout=True)
    if intent["allow_extra_usage"]:
        raise ValueError("Extra Usage must be false with --checkout")
    if request_id is not None:
        identifier(request_id)

    discovery = discover_project(str(checkout))
    status = discovery["status"]
    if status == "RESOLVED" and discovery.get("resolution") == "exact":
        project = discovery["profile"]
    elif (status == "PROFILE_MISSING"
          or (status == "RESOLVED" and discovery.get("resolution") == "inherited")):
        root = discovery["checkout_root"]
        if register is None:
            from .settings_server import register_checkout

            register = register_checkout
        try:
            project = register(root)
        except KeyboardInterrupt:
            return {"status": "CANCELLED"}
        if project.get("status") in {"CANCELLED", "EXPIRED"}:
            return {"status": project["status"]}
        if Path(project["repository"]) != Path(root):
            raise ValueError("Registration confirmed a different checkout")
    else:
        raise ValueError(f"Cannot prepare for checkout ({status}). Next action: {discovery['next_action']}")

    return prepare_task(
        {**values, "project_id": project["id"], "allow_extra_usage": False},
        request_id=request_id,
        expected_project=(discovery["checkout_root"], project["revision"]),
    )
