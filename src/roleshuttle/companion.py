"""Write a minimal status file for the optional desktop companion."""
import json
import os
from pathlib import Path
import tempfile

LABELS = {
    "idle": "No active task",
    "running": "Working on a task",
    "waiting": "A task needs attention",
    "failed": "A task could not finish",
}


def task_state(report):
    if not isinstance(report, dict):
        raise ValueError("Expected a task report object")
    status = report.get("status")
    if report.get("owner_decision_pending") is True or report.get("requires_reconciliation") is True:
        return "waiting"
    if status in ("failed", "rejected"):
        return "failed"
    if report.get("active") is True or status == "running":
        return "running"
    if report.get("lifecycle_outcome") in ("accepted", "project_accepted", "completed", "consumed"):
        return "idle"
    if status in ("planned", "review", "needs_review", "blocked"):
        return "waiting"
    if status in ("accepted", "cancelled"):
        return "idle"
    raise ValueError("Unrecognized task status")


def publish(state):
    directory = Path.home() / ".roleshuttle"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / "agentwhisker-status.json"
    stream = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump({"state": state, "title": LABELS[state]}, stream)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
