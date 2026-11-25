"""Serve the standalone model settings panel on a local authenticated listener."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
import sqlite3
import webbrowser

from . import local_panel
from .model_settings import EFFORTS, LANES, PROVIDERS, ROLES, RevisionConflict
from .model_settings import preset_assignments, read_settings, save_settings
from .recommended import preview_snapshot

POST_LIMITS = {"/api/settings": 16_384, "/api/recommended": 1_048_576}
TASK_ACTIONS = {f"/api/console/{action}": action
                for action in ("prepare", "launch", "cancel", "reconcile", "status", "answer", "diff", "check", "correct", "accept")}
POST_LIMITS.update({path: 16_384 for path in TASK_ACTIONS})
POST_LIMITS["/api/console/correct"] = 65_536
POST_LIMITS["/api/console/answer"] = 32_768
PROJECTS_PATH = "/api/console-projects"
LOCAL_PATH = "/api/local-tasks"
LOCAL_ACTIONS = {f"/api/local/{name}": name for name in local_panel.ACTIONS}
POST_LIMITS.update({path: 65_536 for path in LOCAL_ACTIONS})


class SettingsHandler(BaseHTTPRequestHandler):
    timeout = 5

    def log_message(self, *_):
        pass

    def reply(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, status, value):
        self.reply(status, json.dumps(value).encode("utf-8"))

    def allowed(self, *, write=False):
        if self.headers.get("Host") != self.server.authority:
            self.json_reply(403, {"error": "Unexpected local host"})
            return False
        if self.path in POST_LIMITS or self.path in {PROJECTS_PATH, LOCAL_PATH}:
            authorization = self.headers.get("Authorization", "").encode("latin-1")
            if (not hmac.compare_digest(authorization, b"Bearer " + self.server.token.encode("ascii"))
                    or write and self.headers.get("Origin") != self.server.origin):
                self.json_reply(403, {"error": "Open the panel from RoleShuttle"})
                return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        assets = {"/": ("settings.html", "text/html; charset=utf-8"),
                  "/settings.js": ("settings.js", "text/javascript; charset=utf-8")}
        if self.path in assets:
            name, content_type = assets[self.path]
            self.reply(200, files("roleshuttle").joinpath(name).read_bytes(), content_type)
        elif self.path == "/api/settings":
            try:
                self.json_reply(200, {**read_settings(), "roles": ROLES, "lanes": LANES,
                                      "providers": PROVIDERS, "efforts": EFFORTS, "presets": preset_assignments()})
            except (ValueError, OSError, sqlite3.Error):
                self.json_reply(409, {"error": "Cannot read the local settings database"})
        elif self.path == PROJECTS_PATH:
            try:
                tasks = self.server.console_tasks
                self.json_reply(200, {"enabled": tasks is not None, "items": tasks.projects() if tasks else []})
            except (ValueError, OSError, sqlite3.Error) as error:
                self.json_reply(409, {"error": str(error)})
        elif self.path == LOCAL_PATH:
            try:
                enabled = self.server.autonomous_tasks
                self.json_reply(200, {"enabled": enabled, "history": self.server.history_url,
                                      **(local_panel.listing() if enabled else {})})
            except (ValueError, OSError, sqlite3.Error) as error:
                self.json_reply(409, {"error": str(error)})
        else:
            self.json_reply(404, {"error": "Page unavailable"})

    def do_POST(self):
        if not self.allowed(write=True):
            return
        if self.path not in POST_LIMITS:
            self.json_reply(404, {"error": "Page unavailable"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < size <= POST_LIMITS[self.path]:
                raise ValueError("Expected a bounded JSON request")
            request = json.loads(self.rfile.read(size))
            if self.path in LOCAL_ACTIONS:
                if not self.server.autonomous_tasks:
                    self.json_reply(404, {"error": "Autonomous tasks are unavailable"})
                    return
                self.json_reply(200, local_panel.action(LOCAL_ACTIONS[self.path], request))
                return
            if self.path in TASK_ACTIONS:
                tasks = self.server.console_tasks
                if tasks is None:
                    self.json_reply(404, {"error": "The r28 task service is unavailable"})
                    return
                self.json_reply(200, tasks.action(TASK_ACTIONS[self.path], request))
                return
            if self.path == "/api/recommended":
                self.json_reply(200, preview_snapshot(request))
                return
            if not isinstance(request, dict) or set(request) != {"revision", "settings"} or not isinstance(request["revision"], str):
                raise ValueError("Expected a settings revision")
            self.json_reply(200, save_settings(request["settings"], request["revision"]))
        except RevisionConflict as error:
            self.json_reply(409, {"error": str(error)})
        except ValueError as error:
            self.json_reply(400, {"error": str(error)})
        except (OSError, sqlite3.Error):
            self.json_reply(409, {"error": "Cannot save the local settings database"})


class SettingsServer(ThreadingHTTPServer):
    def __init__(self, port, *, console_tasks=None, autonomous_tasks=False, history_url=None):
        super().__init__(("127.0.0.1", port), SettingsHandler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.authority
        self.token = secrets.token_urlsafe(32)
        self.console_tasks = console_tasks
        self.autonomous_tasks = autonomous_tasks
        self.history_url = history_url


def serve_settings(port, *, autonomous_tasks=False):
    with SettingsServer(port, autonomous_tasks=autonomous_tasks) as server:
        print(f"RoleShuttle model settings listening at {server.origin}", flush=True)
        webbrowser.open(server.origin + "/#token=" + server.token)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
