"""Opt-in local receipts for standalone text stages."""

from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4

from .profile import profile_home, write_json


def stage_path(stage_id):
    if not isinstance(stage_id, str) or re.fullmatch(r"rs-[0-9a-f]{32}", stage_id) is None:
        raise ValueError("Invalid RoleShuttle stage ID")
    return profile_home() / "stages" / (stage_id + ".json")


def read_stage(stage_id):
    try:
        return json.loads(stage_path(stage_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Unknown RoleShuttle stage") from None


def run_stage(kind, call, *, record, outcome):
    if not record:
        return call()
    stage_id = "rs-" + uuid4().hex
    path = stage_path(stage_id)
    entry = {"id": stage_id, "kind": kind, "state": "running",
             "updatedAt": datetime.now(timezone.utc).isoformat()}
    write_json(path, entry)
    try:
        result = call()
        entry.update(state=outcome(result), receipt=result)
    except (Exception, KeyboardInterrupt):
        entry["state"] = "failed"
        raise
    finally:
        entry["updatedAt"] = datetime.now(timezone.utc).isoformat()
        write_json(path, entry)
    return {**result, "stage_id": stage_id}


def stage_items(now):
    for path in (profile_home() / "stages").glob("rs-*.json"):
        try:
            entry = read_stage(path.stem)
            updated = datetime.fromisoformat(entry["updatedAt"])
            state = entry["state"]
            if state not in {"running", "waiting", "review", "failed"} or updated.utcoffset() is None:
                continue
            if state == "running" and now - updated > timedelta(minutes=10):
                state = "failed"
            yield {"id": path.stem, "source": "roleshuttle", "state": state,
                   "updatedAt": entry["updatedAt"]}
        except (ValueError, OSError, KeyError, TypeError):
            continue
