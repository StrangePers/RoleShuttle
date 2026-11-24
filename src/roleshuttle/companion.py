"""Publish a private status snapshot for the optional desktop companion."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

from .profile import profile_home, write_json
from .stages import stage_items

LABELS = {
    "idle": "No active task",
    "running": "Working on a task",
    "waiting": "A task needs attention",
    "failed": "A task could not finish",
    "review": "A task finished",
}


def runtime_task_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("Expected a 32-digit lowercase hexadecimal runtime task ID")
    return value


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


def report_snapshot(reports):
    """Project only status and validated task identities from an explicit snapshot."""
    items = []
    seen = set()
    observed = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    for report in reports:
        state = task_state(report)
        task_id = report.get("task_id")
        if task_id is None and len(reports) == 1:
            return state, []
        runtime_task_id(task_id)
        if task_id in seen:
            raise ValueError("Duplicate runtime task ID in companion snapshot")
        seen.add(task_id)
        items.append(dict(id=task_id, state=state, title=LABELS[state], updatedAt=observed))
    priority = {"waiting": 0, "running": 1, "failed": 2, "idle": 3}
    items.sort(key=lambda item: priority[item["state"]])
    return items[0]["state"], items


def publish(state, items=(), *, offline=False, observed=None):
    destination = profile_home() / "agentwhisker-status.json"
    payload = {"state": state, "title": LABELS[state]}
    if observed or items:
        payload["items"] = items
        payload["updatedAt"] = observed or items[0]["updatedAt"]
    if offline:
        payload["offline"] = True
    write_json(destination, payload)
    return destination


def bridge_items(path, now):
    """Read the existing Console status bridge and discard private fields."""
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
        observed = datetime.fromisoformat(snapshot["updatedAt"])
        if (not timedelta(seconds=-5) <= now - observed <= timedelta(seconds=45)
                or snapshot.get("offline") is True or not isinstance(snapshot.get("items"), list)):
            return None
        if snapshot.get("state") == "failed" and not snapshot["items"]:
            return None
        items = {}
        for row in snapshot["items"]:
            task_id = runtime_task_id(row["id"])
            if row["state"] not in LABELS:
                return None
            # Only a fresh sample can advertise liveness. Terminal transitions
            # keep their original timestamp when the bridge supplied one.
            updated = snapshot["updatedAt"] if row["state"] in {"running", "waiting"} else row.get("updatedAt")
            if not isinstance(updated, str):
                updated = snapshot["updatedAt"]
            items[task_id] = {"id": task_id, "source": "agent-console", "state": row["state"],
                              "updatedAt": updated}
        return list(items.values())
    except (OSError, KeyError, ValueError, TypeError):
        return None


def publish_sources(runtime_status):
    now = datetime.now(timezone.utc)
    runtime = bridge_items(runtime_status, now) if runtime_status else []
    items = list(stage_items(now)) + (runtime or [])
    priority = {"waiting": 0, "running": 1, "failed": 2, "review": 3, "idle": 4}
    items.sort(key=lambda item: item["updatedAt"], reverse=True)
    items.sort(key=lambda item: priority[item["state"]])
    for item in items:
        item["title"] = LABELS[item["state"]]
    return publish(items[0]["state"] if items else ("failed" if runtime is None else "idle"),
                   items[:8], offline=runtime is None and not items, observed=now.isoformat())


def follow(runtime_status):
    try:
        while True:
            publish_sources(runtime_status)
            time.sleep(2)
    except KeyboardInterrupt:
        return
