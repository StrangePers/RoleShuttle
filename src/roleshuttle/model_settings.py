"""Versioned local role and lane assignments, with an r28 preview adapter."""

from contextlib import closing
from datetime import datetime, timezone
from importlib.resources import files
import json
import re
import sqlite3
from uuid import uuid4

from .profile import profile_home

ROLES = ("assistant", "coordinator", "implementer", "specialist", "architect", "reviewer")
LANES = ("architecture", "critical")
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
PROVIDERS = ("codex", "opencode_go")
EMPTY = {"roles": {}, "lanes": {}}


class RevisionConflict(ValueError):
    pass


def normalize(settings):
    if not isinstance(settings, dict) or set(settings) != {"roles", "lanes"}:
        raise ValueError("Expected the roles and lanes objects")
    for section, allowed in (("roles", ROLES), ("lanes", LANES)):
        assignments = settings[section]
        if not isinstance(assignments, dict) or set(assignments) - set(allowed):
            raise ValueError(f"Unknown assignment in {section}")
        for assignment in assignments.values():
            if not isinstance(assignment, dict) or not {"provider", "model_id"} <= set(assignment):
                raise ValueError("An assignment needs a provider and model ID")
            provider, model_id = assignment["provider"], assignment["model_id"]
            if provider not in PROVIDERS or not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", model_id):
                raise ValueError("Unknown provider or invalid model ID")
            fields = {"provider", "model_id"}
            if provider == "codex":
                fields.add("effort")
                if assignment.get("effort") not in EFFORTS:
                    raise ValueError("Select an effort for each Codex assignment")
            if set(assignment) != fields:
                raise ValueError("Unexpected assignment fields")
    return settings


def _read(db, validate=normalize, empty=EMPTY):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version != 1:
        raise ValueError("Unsupported RoleShuttle settings database version")
    row = db.execute("SELECT revision, settings FROM revisions ORDER BY seq DESC LIMIT 1").fetchone()
    return {"revision": row[0], "settings": validate(json.loads(row[1]))} if row else {
        "revision": "empty", "settings": empty}


def settings_path():
    return profile_home() / "model-settings.sqlite3"


def read_settings():
    return read_at(settings_path(), normalize, EMPTY)


def read_at(path, validate, empty):
    if not path.is_file():
        return {"revision": "empty", "settings": empty}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return _read(db, validate, empty)


def save_settings(settings, revision):
    return save_at(settings_path(), settings, revision, normalize, EMPTY)


def save_at(path, settings, revision, validate, empty):
    validate(settings)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            if db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                raise ValueError("Unsupported RoleShuttle settings database version")
            db.execute("CREATE TABLE revisions (seq INTEGER PRIMARY KEY, revision TEXT UNIQUE, saved_at TEXT, settings TEXT)")
            db.execute("PRAGMA user_version = 1")
        current = _read(db, validate, empty)
        if current["revision"] != revision:
            raise RevisionConflict("Settings changed. Reload before saving.")
        if current["settings"] == settings:
            return current
        new_revision = uuid4().hex
        db.execute("INSERT INTO revisions(revision, saved_at, settings) VALUES (?, ?, ?)",
                   (new_revision, datetime.now(timezone.utc).isoformat(), json.dumps(settings)))
        return {"revision": new_revision, "settings": settings}


def preview_console(config):
    journal = json.loads((config / "model-preferences.json").read_text(encoding="utf-8-sig"))
    if (not isinstance(journal, dict) or type(journal.get("schema_version")) is not int
            or journal["schema_version"] != 1 or not isinstance(journal.get("history"), list)):
        raise ValueError("Unsupported Agent Console model preferences")
    entry = journal["history"][-1] if journal["history"] else {"settings": EMPTY, "revision": "empty"}
    if not isinstance(entry, dict) or not isinstance(entry.get("settings"), dict):
        raise ValueError("Invalid Agent Console model preferences")
    settings = entry["settings"]
    preset_file = config / "model-preset.json"
    preset = json.loads(preset_file.read_text(encoding="utf-8-sig")) if preset_file.is_file() else {}
    current = read_settings()
    warnings = [f"Unknown section: {key}" for key in sorted(set(settings) - set(EMPTY))]
    assignments = {"roles": {}, "lanes": {}}
    changes = []
    defaults = preset_assignments()["codex"]["roles"]
    for section, allowed in (("roles", ROLES), ("lanes", LANES)):
        source = settings.get(section, {})
        if not isinstance(source, dict):
            raise ValueError(f"Invalid Agent Console {section}")
        warnings.extend(f"Unknown {section} assignment: {key}" for key in sorted(set(source) - set(allowed)))
        for name in allowed:
            if name not in source:
                continue
            value = source[name]
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            if isinstance(value, dict) and set(value) == {"provider", "model_id"} and value["provider"] == "codex":
                effort = defaults.get(name, {}).get("effort", "medium")
                value = {**value, "effort": effort}
                warnings.append(f"Using RoleShuttle preset effort {effort} for {section}.{name}")
            try:
                normalize({"roles": {name: value} if section == "roles" else {},
                           "lanes": {name: value} if section == "lanes" else {}})
            except ValueError:
                warnings.append(f"Unsupported assignment: {section}.{name}")
                continue
            assignments[section][name] = value
            old = current["settings"][section].get(name)
            if old != value:
                changes.append({"section": section, "name": name, "before": old, "after": value})
    return {"console_preset": preset.get("preset") if isinstance(preset, dict) else None,
            "console_revision": entry.get("revision"), "target_revision": current["revision"],
            "assignments": assignments, "changes": changes, "warnings": warnings}


def import_console(config, source_revision, target_revision):
    preview = preview_console(config)
    if preview["console_revision"] != source_revision or preview["target_revision"] != target_revision:
        raise RevisionConflict("Source or target changed. Preview the import again.")
    current = read_settings()
    if current["revision"] != target_revision:
        raise RevisionConflict("Settings changed. Preview the import again.")
    settings = {section: {**current["settings"][section], **preview["assignments"][section]}
                for section in EMPTY}
    return save_settings(settings, target_revision)


def preset_assignments():
    presets = json.loads(files("roleshuttle").joinpath("presets.json").read_text(encoding="utf-8"))
    return {
        name: {section: {key: {"provider": provider, "model_id": item["model"],
                              **({"effort": item["effort"]} if provider == "codex" else {})}
                         for key, item in presets[name].get(section, {}).items()}
               for section in ("roles", "lanes")}
        for name, provider in (("codex", "codex"), ("opencode-go", "opencode_go"))
    }
