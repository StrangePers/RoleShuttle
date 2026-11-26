"""Private, revisioned execution options and an offline r28 import."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

from . import model_settings
from .profile import profile_home

LANES = ("bounded", "medium", "complex", "high", "critical", "architecture")
TIMEOUTS = {"local_health_seconds": 10, "task_default_seconds": 60, "task_max_seconds": 600}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}")


def defaults():
    return {"routes": {}, "route_catalog": {}, "timeouts": dict(TIMEOUTS), "go_models": None,
            "local_servers": [], "fallbacks": {"roles": {}, "lanes": {}}, "go_console_workspace": ""}


def valid_id(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def validate_list(value):
    if (not isinstance(value, list) or len(value) > 300 or
            not all(valid_id(item) for item in value) or len(set(value)) != len(value)):
        raise ValueError("Expected a list of distinct model or route identifiers")


def normalize(value):
    sections = set(defaults()) - {"go_console_workspace"}
    if not isinstance(value, dict) or set(value) - {"go_console_workspace"} != sections:
        raise ValueError("Expected all advanced settings sections")
    workspace = value.get("go_console_workspace", "")
    if not isinstance(workspace, str) or (workspace and not re.fullmatch(r"wrk_[A-Za-z0-9]{1,80}", workspace)):
        raise ValueError("Enter an OpenCode Console workspace ID beginning with wrk_, or leave it blank")
    value = {**value, "go_console_workspace": workspace}
    routes, catalog, times = value["routes"], value["route_catalog"], value["timeouts"]
    fields = {"worker", "provider", "model", "tier", "pool", "boundary"}
    if not isinstance(catalog, dict) or len(catalog) > 300:
        raise ValueError("Expected a bounded route catalog")
    for route_id, route in catalog.items():
        if (not valid_id(route_id) or not isinstance(route, dict) or not fields <= set(route)
                or set(route) - fields - {"effort", "role"} or type(route["tier"]) is not int
                or not 0 <= route["tier"] <= 4
                or not all(valid_id(route[k]) for k in ("worker", "provider", "model", "boundary"))
                or route["pool"] is not None and not valid_id(route["pool"])
                or "effort" in route and route["effort"] not in model_settings.EFFORTS
                or "role" in route and route["role"] not in model_settings.ROLES):
            raise ValueError("Invalid route catalog entry")
    if not isinstance(routes, dict) or set(routes) - set(LANES):
        raise ValueError("Unknown routing category")
    for order in routes.values():
        validate_list(order)
        if set(order) - set(catalog):
            raise ValueError("Route order references an unknown route")
    if (not isinstance(times, dict) or set(times) != set(TIMEOUTS)
            or any(type(t) is not int or not 1 <= t <= 86_400 for t in times.values())
            or times["task_default_seconds"] > times["task_max_seconds"]):
        raise ValueError("Timeouts must be bounded and the default must fit the maximum")
    if value["go_models"] is not None:
        validate_list(value["go_models"])
    servers = value["local_servers"]
    if not isinstance(servers, list) or len(servers) > 25:
        raise ValueError("Expected a bounded list of local servers")
    known = set()
    for server in servers:
        if (not isinstance(server, dict) or set(server) != {"id","base_url","models","default_model"}
                or not valid_id(server["id"]) or server["id"] in known
                or not isinstance(server["base_url"], str) or len(server["base_url"]) > 512):
            raise ValueError("Invalid local server")
        url = urlsplit(server["base_url"])
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment):
            raise ValueError("Expected an HTTP(S) server address without credentials")
        known.add(server["id"])
        validate_list(server["models"])
        if server["default_model"] not in server["models"]:
            raise ValueError("Default model must be in the server catalog")
    fallback = value["fallbacks"]
    if not isinstance(fallback, dict) or set(fallback) != {"roles","lanes"}:
        raise ValueError("Expected role and category fallback lists")
    for group, names in (("roles",model_settings.ROLES),("lanes",model_settings.LANES)):
        if not isinstance(fallback[group], dict) or set(fallback[group])-set(names):
            raise ValueError("Expected fallback mappings")
        for name, candidates in fallback[group].items():
            if not isinstance(candidates, list) or len(candidates)>10:
                raise ValueError("Invalid ordered fallback list")
            for candidate in candidates:
                entry={"roles":{},"lanes":{}}
                entry[group][name]=candidate
                model_settings.normalize(entry)
    return value


def db_path():
    return profile_home() / "advanced-settings.sqlite3"


def read():
    path=db_path()
    if not path.is_file():
        return {"revision":"empty","settings":defaults(),"history":[]}
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as db:
        db.execute("BEGIN")
        value=model_settings._read(db, normalize, defaults())
        value["history"]=[{"revision":key,"saved_at":when} for key,when in db.execute(
            "SELECT revision,saved_at FROM revisions ORDER BY seq DESC LIMIT 30")]
        return value


def save(settings, revision):
    model_settings.save_at(db_path(), normalize(settings), revision, normalize, defaults())
    return read()


def restore(source, revision):
    if not isinstance(source,str):
        raise ValueError("Expected a saved advanced settings revision")
    path=db_path()
    if not path.is_file():
        raise ValueError("No advanced settings revisions exist")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as db:
        row=db.execute("SELECT settings FROM revisions WHERE revision=?",(source,)).fetchone()
    if not row:
        raise ValueError("Unknown advanced settings revision")
    return save(json.loads(row[0]),revision)


def enforce_go(model_id, advanced=None):
    permitted=(read() if advanced is None else advanced)["settings"]["go_models"]
    if permitted is not None and model_id not in permitted:
        raise ValueError("This model is outside the RoleShuttle OpenCode Go allowlist")


def preview_routing(snapshot):
    from .recommended import preview_snapshot, task_lane

    current=read()
    settings=current["settings"]
    if not isinstance(snapshot,dict) or not isinstance(snapshot.get("policy"),dict) or not isinstance(snapshot.get("task"),dict):
        raise ValueError("Expected a complete Recommended preview snapshot")
    snapshot=deepcopy(snapshot)
    if settings["routes"]:
        snapshot["baseline_routing"]=settings["routes"]
        snapshot["policy"].update(routing=settings["routes"], routes=settings["route_catalog"])
    if settings["go_models"] is not None:
        snapshot["policy"]["go_model_allowlist"]=settings["go_models"]
    snapshot["task"]["allow_extra_usage"]=False
    result = preview_snapshot(snapshot)
    roles = model_settings.read_settings()["settings"]
    role=result.get("role") or snapshot["task"].get("role","implementer")
    lane=task_lane(snapshot["task"])
    fallback=settings["fallbacks"]
    primary=roles["lanes"].get(lane) or roles["roles"].get(role)
    candidates=([primary] if primary else []) + fallback["lanes"].get(lane,[]) + fallback["roles"].get(role,[])
    distinct=[]
    for candidate in candidates:
        if candidate not in distinct:
            distinct.append(candidate)
    return {**result,"advanced_revision":current["revision"],"saved_candidates":distinct}


def go_status():
    from .go_stage import configured, configured_stage, STATUS, USAGE
    if not configured():
        return {"configured":False,"models":[],"extra_usage_off":None,"quota":{}}
    stage=configured_stage()
    status=stage.request(STATUS)
    raw=stage.request(USAGE).get("usage",{})
    quota={name:{"status":meter.get("status"),"percent":meter.get("percent")}
           for name,meter in raw.items() if name in {"rolling","weekly","monthly"} and isinstance(meter,dict)}
    catalog=stage.request("https://opencode.ai/inference/go/v1/models").get("data",[])
    return {"configured":True,"models":sorted({item["id"] for item in catalog
             if isinstance(item,dict) and valid_id(item.get("id"))}),"quota":quota,
            "extra_usage_off": status.get("useBalance") is False}


def local_status(server_id):
    current=read()["settings"]
    server=next((item for item in current["local_servers"] if item["id"]==server_id),None)
    if server is None:
        raise ValueError("Choose a saved local model server")
    mesh=shutil.which("mesh")
    registry=Path.home()/".agent-mesh/models.toml"
    if not mesh or not registry.is_file():
        return {"available":False,"reason":"Mesh registry or executable unavailable","models":[]}
    try:
        entry=tomllib.loads(registry.read_text(encoding="utf-8-sig"))["servers"][server_id]
        if entry["base_url"].rstrip("/")!=server["base_url"].rstrip("/"):
            return {"available":False,"reason":"Address differs from the registered Mesh server","models":[]}
        completed=subprocess.run([mesh,"models",server_id,"--registry",str(registry),"--json"],
                                 capture_output=True,timeout=current["timeouts"]["local_health_seconds"])
        if completed.returncode or len(completed.stdout)>512*1024:
            raise ValueError("No usable model status")
        [observed]=json.loads(completed.stdout)["servers"]
        if observed["name"]!=server_id or observed["base_url"].rstrip("/")!=server["base_url"].rstrip("/"):
            raise ValueError("Model status did not match the saved server")
        return {"available":True,"models":[{"id":item["id"],"loaded":item.get("loaded") is True,
                   "loading":item.get("is_loading") is True,"allowed":item["id"] in server["models"]}
                 for item in observed["models"] if isinstance(item,dict) and valid_id(item.get("id"))]}
    except (TypeError, ValueError, KeyError, OSError, subprocess.SubprocessError):
        return {"available":False,"reason":"Mesh did not confirm connection and model status","models":[]}


def offline_import(config, registry=None):
    journal=json.loads((config/"policy.json").read_text(encoding="utf-8-sig"))
    preferences=json.loads((config/"model-preferences.json").read_text(encoding="utf-8-sig"))
    entries=journal["history"]
    if journal["schema_version"]!=1 or preferences["schema_version"]!=1:
        raise ValueError("Unsupported r28 config journals")
    policy=entries[-1]["policy"]
    extensions=journal.get("extensions",{}).get("revisions",{})
    result=defaults()
    result.update(routes=policy["routing"],route_catalog=policy["routes"],timeouts=policy["timeouts"])
    for entry in entries:
        extension=extensions.get(entry["revision"],{})
        if "go_model_allowlist" in extension:
            result["go_models"]=extension["go_model_allowlist"]
    for group, assignments in preferences["history"][-1]["settings"].items():
        for name, value in assignments.items():
            if isinstance(value,list) and len(value)>1:
                result["fallbacks"][group][name]=value[1:]
    if registry is not None:
        for name, item in tomllib.loads(registry.read_text(encoding="utf-8-sig"))["servers"].items():
            result["local_servers"].append({"id":name,**{k:item[k] for k in ("base_url","models","default_model")}})
    return normalize(result)


if __name__ == "__main__":
    if len(sys.argv) not in {3,4} or sys.argv[1]!="preview-r28":
        raise SystemExit("Usage: python -m roleshuttle.advanced_settings preview-r28 CONFIG [MESH_REGISTRY]")
    print(json.dumps(offline_import(Path(sys.argv[2]),Path(sys.argv[3]) if len(sys.argv)==4 else None),indent=2))
