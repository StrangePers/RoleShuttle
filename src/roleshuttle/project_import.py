"""Import r28 project profiles into a private standalone registry."""

from functools import lru_cache
import hashlib
import json
from uuid import uuid4

from .check_runner import executable_hash
from .local_tasks import database, normalize_project, validate_project_checkout
from .profile import profile_home


def digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_projects(config):
    directory = config / "projects"
    files = sorted(directory.glob("*.json"))
    if not files or len(files) > 100:
        raise ValueError("Expected one to 100 r28 project profiles")
    records = {}
    for file in files:
        if file.is_symlink() or file.stat().st_size > 512_000:
            raise ValueError("Expected a bounded regular r28 project profile")
        values = json.loads(file.read_text(encoding="utf-8-sig"))
        if not isinstance(values, dict):
            raise ValueError("Expected an r28 project object")
        revision = values.pop("revision", None)
        if values.get("id") != file.stem or revision != digest(values):
            raise ValueError("An r28 project profile does not match its saved identity or revision")
        key = file.stem.lower()
        if key in records:
            raise ValueError("r28 project IDs collide after case normalization")
        values.update(id=key, extra_usage_allowed=False, extra_usage_default=False)
        if values.get("source_project"):
            source = values["source_project"]
            values["source_project"] = {**source, "id": source["id"].lower()}
        records[key] = {**normalize_project(values, imported=True), "console_revision": revision}
    return records


def target_projects(db):
    return {key: json.loads(record) for key, record in db.execute("SELECT id, record FROM projects")}


def report(source, target):
    collisions = sorted(key for key, row in source.items()
                        if key in target and row["console_revision"] != target[key].get("console_revision"))
    return {"source_revision": digest({key: row["console_revision"] for key, row in source.items()}),
            "target_revision": digest({key: row["revision"] for key, row in target.items()}),
            "projects": len(source), "new": len(source.keys() - target.keys()),
            "conflicts": collisions, "extra_usage": False}


def preview_projects(config):
    source = source_projects(config)
    file = profile_home() / "local-tasks.sqlite3"
    if not file.is_file():
        return report(source, {})
    with database() as db:
        return report(source, target_projects(db))


def import_projects(config, source_revision, target_revision):
    source = source_projects(config)
    with database(write=True) as db:
        target = target_projects(db)
        preview = report(source, target)
        if preview["source_revision"] != source_revision or preview["target_revision"] != target_revision:
            raise ValueError("Project registry changed. Preview the migration again.")
        if preview["conflicts"]:
            raise ValueError("Existing local project profiles conflict with the r28 import")
        incoming = {key: {**row, "revision": uuid4().hex} for key, row in source.items() if key not in target}
        for key, row in incoming.items():
            binding = row.get("source_project")
            if binding:
                original_parent = source.get(binding["id"])
                parent = target.get(binding["id"]) or incoming.get(binding["id"])
                if original_parent and parent and original_parent["console_revision"] == binding["revision"]:
                    row["source_project"] = {**binding, "revision": parent["revision"]}
                else:
                    row["import_issue"] = "Original linked worktree source revision is stale or missing"
            db.execute("INSERT INTO projects VALUES (?, ?)", (key, json.dumps(row)))
    return {**preview, "inserted": len(incoming), **project_readiness(source.keys())}


def project_readiness(project_ids=None):
    """Inspect checkout bindings and current executable bytes without running a check."""
    @lru_cache(maxsize=None)
    def current_hash(path):
        try:
            return executable_hash(path), None
        except FileNotFoundError:
            return None, "missing"
        except OSError:
            return None, "unreadable"

    blocked, pin_issues, without_checks = {}, {}, []
    with database() as db:
        target = target_projects(db)
        selected = sorted(target if project_ids is None else project_ids)
        for key in selected:
            project = target[key]
            try:
                validate_project_checkout(db, project)
            except (ValueError, OSError):
                blocked[key] = "Checkout or linked worktree needs local reconciliation"
            if not project["checks"]:
                without_checks.append(key)
            for check in project["checks"]:
                actual, error = current_hash(check["argv"][0])
                if actual != check["executable_sha256"]:
                    pin_issues.setdefault(key, []).append({"id": check["id"], "status": error or "changed"})
    unavailable = blocked.keys() | pin_issues.keys() | set(without_checks)
    return {"ready": len(selected) - len(blocked), "blocked": blocked,
            "ready_for_checks": len(selected) - len(unavailable),
            "check_pin_issues": pin_issues, "without_registered_checks": without_checks}
