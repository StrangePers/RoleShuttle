"""Preview and confirm new profiles for local Git checkouts."""

import json
from pathlib import Path
from secrets import token_urlsafe
from subprocess import CalledProcessError
from threading import Lock
from time import monotonic

from .local_tasks import discover_project, normalize_project, register_project, repository_head


PREVIEW_TTL = 300
MAX_PREVIEWS = 32
OPTIONS = {
    "allowed_paths", "worktrees_allowed", "shared_mac_allowed",
    "external_code_allowed", "automatic_cloud",
}


def _registration_discovery(path):
    discovery = discover_project(path)
    if discovery["status"] == "PROFILE_MISSING":
        return discovery["checkout_root"], None
    if discovery["status"] == "RESOLVED" and discovery.get("resolution") == "inherited":
        return discovery["checkout_root"], discovery["profile"]
    raise ValueError("Select an unregistered Git checkout with an authorized source if linked.")


class ProjectRegistration:
    def __init__(self):
        self._lock = Lock()
        self._previews = {}

    def preview(self, request, *, expected_root=None):
        if (not isinstance(request, dict)
                or not {"path", "id"} <= request.keys() <= OPTIONS | {"path", "id"}
                or not isinstance(request["path"], str) or not request["path"]):
            raise ValueError("Expected a checkout path, project ID and optional permissions.")
        if expected_root is not None and request["path"] != expected_root:
            raise ValueError("Select the checkout that initiated registration.")
        root, inherited = _registration_discovery(request["path"])
        if expected_root is not None and root != expected_root:
            raise ValueError("Only the requested checkout can be registered here.")
        if inherited is None:
            project = normalize_project({
                "allowed_paths": [],
                **{key: request[key] for key in OPTIONS if key in request},
                "id": request["id"],
                "repository": root,
                "extra_usage_allowed": False,
                "extra_usage_default": False,
            })
        else:
            if set(request) != {"path", "id"}:
                raise ValueError("A linked worktree accepts only a checkout path and project ID.")
            project = normalize_project(
                {**inherited, "id": request["id"]}, imported=True,
            )
        try:
            head = repository_head(Path(project["repository"]))
        except CalledProcessError as error:
            raise ValueError("The checkout must have a readable Git HEAD.") from error
        snapshot = json.dumps(project)
        with self._lock:
            now = monotonic()
            self._previews = {
                key: entry for key, entry in self._previews.items() if entry[0] > now
            }
            if len(self._previews) >= MAX_PREVIEWS:
                self._previews.pop(next(iter(self._previews)))
            preview_id = token_urlsafe(24)
            self._previews[preview_id] = (now + PREVIEW_TTL, snapshot, head)
        return {"preview_id": preview_id, "project": project}

    def confirm(self, request):
        if (not isinstance(request, dict) or set(request) != {"preview_id", "confirmed"}
                or not isinstance(request["preview_id"], str) or request["confirmed"] is not True):
            raise ValueError("Expected a preview ID and explicit confirmation.")
        with self._lock:
            entry = self._previews.pop(request["preview_id"], None)
            if entry is None or entry[0] <= monotonic():
                raise ValueError("The preview is unavailable. Create a new preview.")
            _, snapshot, head = entry
            project = json.loads(snapshot)
            root = Path(project["repository"])
            try:
                discovered_root, inherited = _registration_discovery(str(root))
                source = project.get("source_project")
                discovered_source = inherited["source_project"] if inherited is not None else None
                if Path(discovered_root) != root or discovered_source != source:
                    raise ValueError("The checkout or source project changed. Create a new preview.")
                return register_project(
                    project, "empty", retain_check_pins=source is not None,
                    unique_checkout=True, expected_head=head,
                    confirmed_inherited=source is not None,
                )
            except CalledProcessError as error:
                raise ValueError("The checkout could not be revalidated. Create a new preview.") from error
