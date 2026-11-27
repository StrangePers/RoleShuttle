"""Serve the standalone model settings panel on a local authenticated listener."""

import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
from socket import SHUT_RDWR
import sqlite3
from threading import Thread, Timer
from time import monotonic
import webbrowser

from . import advanced_settings, checkout_page, local_panel, project_registration
from .local_tasks import discover_project
from .model_settings import EFFORTS, LANES, PROVIDERS, ROLES, RevisionConflict
from .model_settings import preset_assignments, read_settings, save_settings
from .recommended import preview_snapshot

POST_LIMITS = {"/api/settings": 16_384, "/api/recommended": 1_048_576,
               "/api/advanced-settings": 65_536, "/api/advanced-preview": 1_048_576,
               "/api/advanced-go-status": 64, "/api/advanced-local-status": 512}
TASK_ACTIONS = {f"/api/console/{action}": action
                for action in ("prepare", "launch", "cancel", "reconcile", "status", "answer", "diff", "check", "correct", "accept")}
POST_LIMITS.update({path: 16_384 for path in TASK_ACTIONS})
POST_LIMITS["/api/console/correct"] = 65_536
POST_LIMITS["/api/console/answer"] = 32_768
PROJECTS_PATH = "/api/console-projects"
LOCAL_PATH = "/api/local-tasks"
LOCAL_ACTIONS = {f"/api/local/{name}": name for name in local_panel.ACTIONS}
POST_LIMITS.update({path: 65_536 for path in LOCAL_ACTIONS})
PROJECT_REGISTRATION_ACTIONS = {"/api/local/project-preview": "preview",
                                "/api/local/project-confirm": "confirm"}
POST_LIMITS.update({path: 16_384 for path in PROJECT_REGISTRATION_ACTIONS})


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
        elif self.path == "/api/advanced-settings":
            try:
                self.json_reply(200, advanced_settings.read())
            except (ValueError, OSError, sqlite3.Error):
                self.json_reply(409, {"error": "Cannot read the advanced settings database"})
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
            if self.path in PROJECT_REGISTRATION_ACTIONS:
                if not self.server.autonomous_tasks:
                    self.json_reply(404, {"error": "Autonomous tasks are unavailable"})
                    return
                action = PROJECT_REGISTRATION_ACTIONS[self.path]
                self.json_reply(200, getattr(self.server.project_registration, action)(request))
                return
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
            if self.path == "/api/advanced-local-status":
                if not isinstance(request,dict) or set(request)!={"server_id"} or not isinstance(request["server_id"],str):
                    raise ValueError("Expected a saved server ID")
                self.json_reply(200, advanced_settings.local_status(request["server_id"]))
                return
            if self.path == "/api/advanced-go-status":
                if request != {}:
                    raise ValueError("Expected an empty Go status request")
                self.json_reply(200, advanced_settings.go_status())
                return
            if self.path == "/api/advanced-preview":
                self.json_reply(200, advanced_settings.preview_routing(request))
                return
            if self.path == "/api/advanced-settings":
                if not isinstance(request, dict) or not isinstance(request.get("revision"),str):
                    raise ValueError("Expected an advanced settings revision")
                if set(request)=={"settings","revision"}:
                    self.json_reply(200, advanced_settings.save(request["settings"],request["revision"]))
                elif set(request)=={"restore","revision"}:
                    self.json_reply(200, advanced_settings.restore(request["restore"],request["revision"]))
                else:
                    raise ValueError("Expected settings or an earlier revision to restore")
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
    handler = SettingsHandler
    def __init__(self, port, *, console_tasks=None, autonomous_tasks=False, history_url=None):
        super().__init__(("127.0.0.1", port), self.handler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.authority
        self.token = secrets.token_urlsafe(32)
        self.console_tasks = console_tasks
        self.autonomous_tasks = autonomous_tasks
        self.project_registration = project_registration.ProjectRegistration()
        self.history_url = history_url


CHECKOUT_PREFILL = "/api/checkout"
CHECKOUT_CANCEL = "/api/checkout/cancel"
CHECKOUT_TIMEOUT = 300


class CheckoutHandler(SettingsHandler):
    def checkout_allowed(self, *, write=False):
        if (self.headers.get("Host") != self.server.authority
                or not hmac.compare_digest(self.headers.get("Authorization", "").encode("latin-1"),
                                           b"Bearer " + self.server.token.encode("ascii"))
                or write and self.headers.get("Origin") != self.server.origin):
            self.json_reply(403, {"error": "Open this page from RoleShuttle"})
            return False
        if self.server.result is not None or monotonic() >= self.server.deadline:
            self.json_reply(410, {"error": "This registration session has ended"})
            return False
        return True

    def do_GET(self):
        if self.headers.get("Host") != self.server.authority:
            self.json_reply(403, {"error": "Unexpected local host"})
        elif self.path == "/":
            self.reply(200, checkout_page.HTML, "text/html; charset=utf-8")
        elif self.path == "/checkout.js":
            self.reply(200, checkout_page.JS, "text/javascript; charset=utf-8")
        elif self.path == CHECKOUT_PREFILL:
            if self.checkout_allowed():
                self.json_reply(200, self.server.prefill)
        else:
            self.json_reply(404, {"error": "Page unavailable"})

    def do_POST(self):
        if not self.checkout_allowed(write=True):
            return
        if self.path != CHECKOUT_CANCEL and self.path not in PROJECT_REGISTRATION_ACTIONS:
            self.json_reply(404, {"error": "Page unavailable"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < size <= POST_LIMITS.get(self.path, 64):
                raise ValueError("Expected a bounded JSON request")
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError("Incomplete request body")
            request = json.loads(body)
            if monotonic() >= self.server.deadline:
                self.json_reply(410, {"error": "This registration session has ended"})
                return
            if self.path == CHECKOUT_CANCEL:
                if request != {}:
                    raise ValueError("Expected an empty cancellation request")
                response = self.server.result = {"status": "CANCELLED"}
            elif PROJECT_REGISTRATION_ACTIONS[self.path] == "preview":
                response = self.server.project_registration.preview(request, expected_root=self.server.prefill["path"])
            else:
                response = self.server.result = self.server.project_registration.confirm(request)
        except ValueError as error:
            self.json_reply(400, {"error": str(error)})
        except (OSError, sqlite3.Error):
            self.json_reply(409, {"error": "Cannot complete project registration"})
        else:
            self.json_reply(200, response)


class CheckoutServer(SettingsServer):
    handler = CheckoutHandler

    def process_request(self, request, client_address):
        def stop_socket():
            try:
                request.shutdown(SHUT_RDWR)
            except OSError:
                pass

        # A socket timeout alone allows continual trickle traffic to extend reads.
        timer = Timer(min(CheckoutHandler.timeout, max(0, self.deadline - monotonic())), stop_socket)
        timer.daemon = True
        timer.start()
        try:
            HTTPServer.process_request(self, request, client_address)
        finally:
            timer.cancel()


def register_checkout(root):
    """Register the caller's canonical checkout and return its project or terminal status."""
    with CheckoutServer(0) as server:
        server.prefill = {"path": root, "id": "checkout-" + secrets.token_hex(4)}
        discovery = discover_project(root)
        if discovery.get("status") == "RESOLVED" and discovery.get("resolution") == "inherited":
            server.prefill["inherited"] = True
            server.prefill["source_project_id"] = discovery["profile"]["source_project"]["id"]
        server.result = None
        server.deadline = monotonic() + CHECKOUT_TIMEOUT
        url = server.origin + "/#token=" + server.token
        Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        try:
            while server.result is None and (remaining := server.deadline - monotonic()) > 0:
                server.timeout = remaining
                server.handle_request()
        except KeyboardInterrupt:
            return server.result if server.result is not None else {"status": "CANCELLED"}
        return server.result if server.result is not None else {"status": "EXPIRED"}


def serve_settings(port, *, autonomous_tasks=False):
    with SettingsServer(port, autonomous_tasks=autonomous_tasks) as server:
        print(f"RoleShuttle model settings listening at {server.origin}", flush=True)
        webbrowser.open(server.origin + "/#token=" + server.token)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
