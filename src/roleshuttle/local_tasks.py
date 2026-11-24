"""Local project registry and durable, first-attempt task preparation."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
import hashlib
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .check_runner import executable_hash
from .profile import profile_home
from .review import _generated, _git, repository_head
from .scope import check_scope, safe_path

BOUNDARIES = {"external_code_allowed", "shared_mac_allowed", "extra_usage_allowed",
              "extra_usage_default", "worktrees_allowed"}
PROJECT_FIELDS = BOUNDARIES | {"id", "name", "repository", "allowed_paths", "checks",
                               "automatic_cloud", "availability_file", "local_capability",
                               "acceptance_mode", "project_checks", "review_paths",
                               "generated_paths", "source_project"}
TASK_FIELDS = {"project_id", "title", "mode", "allowed_paths", "allow_extra_usage"}

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value):
        raise ValueError("Expected a lowercase identifier of up to 80 characters")
    return value


def path_list(values):
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError("Expected at most 100 literal paths")
    paths = [safe_path(value, directory=True) for value in values]
    if len(set(paths)) != len(paths):
        raise ValueError("Paths must be unique")
    return paths


def registered_checks(values, repository, paths, *, imported=False):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError("Expected up to 20 registered project checks")
    checks = []
    for row in values:
        if not isinstance(row, dict) or not {"id", "argv"} <= set(row) <= {"id", "argv", "timeout", "timeout_seconds", "title", "cwd", "executable_sha256"}:
            raise ValueError("A project check requires an ID and argv")
        argv, timeout = row["argv"], row.get("timeout_seconds", row.get("timeout", 60))
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 80
                or not all(isinstance(arg, str) and 0 < len(arg) <= 4000 and "\0" not in arg for arg in argv)
                or type(timeout) is not int or not 1 <= timeout <= 3600):
            raise ValueError("Invalid check arguments or timeout")
        executable = Path(argv[0])
        if not executable.is_absolute() or executable.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ValueError("Register an absolute check executable")
        executable = executable.resolve()
        if executable.is_relative_to(repository):
            relative = executable.relative_to(repository).as_posix()
            if any(relative == path or path.endswith("/") and relative.startswith(path) for path in paths):
                raise ValueError("Check executable is inside editable project paths")
        pin = row.get("executable_sha256") if imported else executable_hash(executable)
        if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin):
            raise ValueError("A trusted check requires a saved executable hash")
        check = {"id": identifier(row["id"].lower() if imported else row["id"]),
                 "argv": [str(executable), *argv[1:]], "timeout": timeout, "executable_sha256": pin}
        if "cwd" in row:
            check["cwd"] = safe_path(row["cwd"], directory=True)
        if "title" in row:
            check["title"] = row["title"]
        checks.append(check)
    if len({row["id"] for row in checks}) != len(checks):
        raise ValueError("Check IDs must be unique")
    return checks


def stamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database(*, write=False):
    file = profile_home() / "local-tasks.sqlite3"
    if write:
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(file, timeout=35)
    else:
        if not file.is_file():
            raise ValueError("No local projects or tasks have been registered")
        connection = sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)
    with closing(connection) as db, db:
        if write:
            db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if write and version == 0 and not db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            db.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, record TEXT NOT NULL)")
            db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, record TEXT NOT NULL)")
            db.execute("PRAGMA user_version = 1")
        elif version != 1:
            raise ValueError("Unsupported local task database version")
        yield db


def load_project(db, project_id, *, validate=True):
    row = db.execute("SELECT record FROM projects WHERE id = ?", (identifier(project_id),)).fetchone()
    if not row:
        raise ValueError("Unknown local project")
    project = json.loads(row[0])
    if validate:
        validate_project_checkout(db, project)
    return project


def common_dir(root):
    return Path(os.fsdecode(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").rstrip())).resolve()


def validate_project_checkout(db, project):
    if project.get("import_issue"):
        raise ValueError(project["import_issue"])
    root = Path(project["repository"])
    base = repository_head(root)
    _generated(root, {"allowed_paths": project["allowed_paths"], "base_revision": base},
               project.get("generated_paths", []))
    source = project.get("source_project")
    if source:
        parent = load_project(db, source["id"], validate=False)
        if (parent.get("source_project") or parent.get("import_issue") or not parent.get("worktrees_allowed")
                or parent["revision"] != source["revision"]):
            raise ValueError("Linked worktree needs its current authorized source project")
        inherited = ("allowed_paths", "external_code_allowed", "shared_mac_allowed", "extra_usage_allowed",
                     "extra_usage_default", "automatic_cloud", "availability_file", "review_paths",
                     "generated_paths", "acceptance_mode", "project_checks")
        if project.get("worktrees_allowed") or any(project.get(key) != parent.get(key) for key in inherited):
            raise ValueError("Linked worktree permissions and acceptance must match its source project")
        parent_root = Path(parent["repository"])
        repository_head(parent_root)
        listed = _git(parent_root, "worktree", "list", "--porcelain", "-z").split(b"\0")
        roots = {Path(os.fsdecode(row[9:])).resolve() for row in listed if row.startswith(b"worktree ")}
        if root == parent_root or root not in roots or common_dir(root) != common_dir(parent_root):
            raise ValueError("Linked worktree must share its source project's Git repository")
    return base


def normalize_project(values, *, imported=False):
    if not isinstance(values, dict) or not {"id", "repository", "allowed_paths"} <= set(values) <= PROJECT_FIELDS:
        raise ValueError("Expected a local project ID, repository, allowed paths and optional permissions")
    project_id = identifier(values["id"])
    if not isinstance(values["repository"], str) or not Path(values["repository"]).is_absolute():
        raise ValueError("Project repository must be an absolute path")
    repository = Path(values["repository"]).resolve()
    profile = profile_home().resolve()
    if profile.is_relative_to(repository) or repository.is_relative_to(profile):
        raise ValueError("Keep the private profile and registered repository separate")
    permissions = {field: values.get(field, False) for field in BOUNDARIES}
    if any(type(value) is not bool for value in permissions.values()):
        raise ValueError("Project permissions must be boolean")
    automatic = values.get("automatic_cloud", [])
    if (not isinstance(automatic, list) or any(provider not in ("codex", "opencode_go") for provider in automatic)
            or len(set(automatic)) != len(automatic)
            or automatic and not permissions["external_code_allowed"]
            or permissions["extra_usage_default"] and not permissions["extra_usage_allowed"]):
        raise ValueError("Invalid automatic cloud or Extra Usage authorization")
    mode, requirements = values.get("acceptance_mode", "console"), values.get("project_checks", [])
    if (mode not in ("console", "project") or not isinstance(requirements, list) or len(requirements) > 20
            or any(not isinstance(rule, str) or not 1 <= len(rule) <= 2000 for rule in requirements)
            or mode == "project" and not requirements):
        raise ValueError("Project acceptance requires documented checks")
    review = path_list(values.get("review_paths", []))
    if any(path.endswith("/") for path in review):
        raise ValueError("Review paths must name files")
    paths = path_list(values["allowed_paths"])
    canonical = {**values, "id": project_id, "repository": str(repository), "allowed_paths": paths,
                 "checks": registered_checks(values.get("checks", []), repository, paths, imported=imported),
                 "automatic_cloud": automatic, "acceptance_mode": mode, "project_checks": requirements,
                 "review_paths": review, "generated_paths": path_list(values.get("generated_paths", [])),
                 **permissions}
    source = canonical.get("source_project")
    if source is not None:
        if (not isinstance(source, dict) or set(source) != {"id", "revision"} or identifier(source["id"]) == project_id
                or not isinstance(source["revision"], str) or not re.fullmatch(r"[0-9a-f]{32,64}", source["revision"])):
            raise ValueError("Invalid linked worktree source project")
    return canonical


def register_project(values, revision, *, retain_check_pins=False):
    canonical = normalize_project(values, imported=retain_check_pins)
    project_id = canonical["id"]
    with database(write=True) as db:
        row = db.execute("SELECT record FROM projects WHERE id = ?", (project_id,)).fetchone()
        current = json.loads(row[0]) if row else None
        if (current["revision"] if current else "empty") != revision:
            raise ValueError("Project revision changed. Read it again before registering.")
        validate_project_checkout(db, canonical)
        if current and all(current.get(key, [] if key == "checks" else None) == value
                           for key, value in canonical.items()):
            return current
        result = {**canonical, "revision": uuid4().hex}
        db.execute("INSERT INTO projects VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                   (project_id, json.dumps(result)))
        return result


def show_project(project_id):
    with database() as db:
        return load_project(db, project_id, validate=False)


def resolve_project(repository):
    root = Path(repository).resolve()
    repository_head(root)
    with database() as db:
        profiles = [json.loads(row[0]) for row in db.execute("SELECT record FROM projects ORDER BY id")]
        exact = [p for p in profiles if Path(p["repository"]) == root]
        if len(exact) > 1:
            raise ValueError("Multiple profiles for this checkout. Select a project ID.")
        if exact:
            project = exact[0]
            try:
                validate_project_checkout(db, project)
                return project
            except (ValueError, OSError):
                if not project.get("source_project"):
                    raise
            candidates = [load_project(db, project["source_project"]["id"])]
        else:
            shared = common_dir(root)
            candidates = []
            for project in profiles:
                if project.get("source_project") or not project.get("worktrees_allowed"):
                    continue
                try:
                    validate_project_checkout(db, project)
                    if common_dir(Path(project["repository"])) == shared:
                        candidates.append(project)
                except (ValueError, OSError):
                    continue
        if len(candidates) != 1 or not candidates[0].get("worktrees_allowed"):
            raise ValueError("Linked worktree needs exactly one authorized source project")
        parent = candidates[0]
    values = {key: value for key, value in parent.items() if key in PROJECT_FIELDS}
    suffix = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    child_id = exact[0]["id"] if exact else parent["id"][:60] + "-wt-" + suffix
    values.update(id=child_id, repository=str(root), name=parent.get("name", parent["id"])[:120] + " (worktree)",
                  worktrees_allowed=False, source_project={"id": parent["id"], "revision": parent["revision"]})
    return register_project(values, exact[0]["revision"] if exact else "empty", retain_check_pins=True)


def prepare_task(values, *, request_id=None, panel_input=None):
    if not isinstance(values, dict) or not TASK_FIELDS - {"allow_extra_usage"} <= set(values) <= TASK_FIELDS:
        raise ValueError("Expected the local task contract fields")
    title = values["title"]
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or values["mode"] not in ("read", "write"):
        raise ValueError("Expected a task title and read or write mode")
    paths = path_list(values["allowed_paths"])
    if values["mode"] == "write" and not paths:
        raise ValueError("Writing tasks require allowed paths")
    extra = values.get("allow_extra_usage", False)
    if type(extra) is not bool:
        raise ValueError("Extra Usage permission must be boolean")
    intent = {"project_id": identifier(values["project_id"]), "title": title.strip(), "mode": values["mode"],
              "allowed_paths": paths, "allow_extra_usage": extra}
    if request_id is not None:
        identifier(request_id)
    with database(write=True) as db:
        row = db.execute("SELECT record FROM tasks WHERE request_id = ?", (request_id,)).fetchone()
        if row:
            existing = json.loads(row[0])
            if (any(existing["contract"][key] != value for key, value in intent.items())
                    or existing.get("panel_input") != panel_input):
                raise ValueError("Request ID is already bound to another task contract")
            return existing
        project = load_project(db, intent["project_id"])
        root = Path(project["repository"])
        if extra and not project["extra_usage_allowed"]:
            raise ValueError("Project does not authorize Extra Usage")
        if check_scope(root, paths, project["allowed_paths"], directories=True):
            raise ValueError("Task paths exceed the registered project scope")
        contract = {**intent, "repository": str(root), "base_revision": repository_head(root),
                    "project_revision": project["revision"], "external_code_allowed": project["external_code_allowed"],
                    "shared_mac_allowed": project["shared_mac_allowed"],
                    "review_paths": project.get("review_paths", []),
                    "generated_paths": project.get("generated_paths", []),
                    "acceptance_mode": project.get("acceptance_mode", "console"),
                    "project_checks": project.get("project_checks", [])}
        record = {"task_id": uuid4().hex, "revision": uuid4().hex, "status": "planned", "phase": "prepared",
                  "active": False, "created_at": stamp(), "contract": contract, "attempts": []}
        if panel_input is not None:
            record["panel_input"] = panel_input
        db.execute("INSERT INTO tasks VALUES (?, ?, ?)", (record["task_id"], request_id, json.dumps(record)))
        return record


def load_task(db, task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ValueError("Invalid local task ID")
    row = db.execute("SELECT record FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown local task")
    return json.loads(row[0])


def update_task(db, record, **changes):
    updated = {**record, **changes, "revision": uuid4().hex, "updated_at": stamp()}
    db.execute("UPDATE tasks SET record = ? WHERE id = ?", (json.dumps(updated), record["task_id"]))
    return updated


def show_task(task_id):
    with database() as db:
        return load_task(db, task_id)
