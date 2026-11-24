from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from roleshuttle.cli import main
from roleshuttle.model_settings import (
    RevisionConflict, import_console, preview_console, read_settings, save_settings, settings_path,
)
from roleshuttle.profile import write_json
from roleshuttle.settings_server import SettingsServer
from test_recommended import snapshot


def choices(model):
    return {"roles": {"implementer": {"provider": "codex", "model_id": model, "effort": "high"}}, "lanes": {}}


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict("os.environ", {"ROLESHUTTLE_HOME": str(self.root / "target")})
        environment.start()
        self.addCleanup(environment.stop)

    def test_simultaneous_saves_reject_the_stale_revision(self):
        def save(model):
            try:
                return save_settings(choices(model), "empty")
            except RevisionConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ("example-a", "example-b")))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(read_settings(), next(result for result in results if result))
        current = read_settings()
        self.assertEqual(save_settings(current["settings"], current["revision"]), current)

    def test_newer_version_refuses_writes(self):
        save_settings(choices("example"), "empty")
        with sqlite3.connect(settings_path()) as db:
            db.execute("PRAGMA user_version = 99")
        db.close()
        before = settings_path().read_bytes()
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            save_settings(choices("changed"), "empty")
        self.assertEqual(settings_path().read_bytes(), before)

    def test_console_preview_and_import_preserve_inputs_and_other_roles(self):
        config = self.root / "console"
        source = config / "model-preferences.json"
        write_json(source, {"schema_version": 1, "history": [{"revision": "console-1", "settings": {
            "roles": {"assistant": {"provider": "opencode_go", "model_id": "local-example"},
                      "reviewer": [{"provider": "codex", "model_id": "one"}, {"provider": "codex", "model_id": "two"}]},
            "lanes": {}, "future": {},
        }}]})
        write_json(config / "model-preset.json", {"preset": "recommended"})
        before = source.read_bytes()
        initial = save_settings(choices("keep"), "empty")
        preview = preview_console(config)
        self.assertEqual(preview["console_preset"], "recommended")
        self.assertEqual(preview["target_revision"], initial["revision"])
        self.assertEqual(len(preview["changes"]), 1)
        self.assertEqual(len(preview["warnings"]), 2)
        with self.assertRaises(RevisionConflict):
            import_console(config, "old-revision", initial["revision"])
        applied = import_console(config, "console-1", initial["revision"])
        self.assertEqual(applied["settings"]["roles"]["implementer"]["model_id"], "keep")
        self.assertEqual(applied["settings"]["roles"]["assistant"]["model_id"], "local-example")
        self.assertEqual(import_console(config, "console-1", applied["revision"]), applied)
        self.assertEqual(source.read_bytes(), before)

    def test_codex_stage_uses_saved_assignment_only_on_request(self):
        save_settings(choices("saved-model"), "empty")
        text = self.root / "input.txt"
        text.write_text("Example task", encoding="utf-8")
        with redirect_stdout(io.StringIO()), patch("roleshuttle.cli.codex_stage", return_value={"status": "returned"}) as call:
            self.assertEqual(main(["codex-stage", "--role", "implementer", "--input", str(text), "--use-profile"]), 0)
        call.assert_awaited_once_with("implementer", "Example task", model="saved-model", effort="high")

    def test_http_authorization_and_revision_conflict(self):
        server = SettingsServer(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)

        def request(method, body=None, path="/api/settings", **headers):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(method, path, body, headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        auth = {"Authorization": "Bearer " + server.token}
        body = json.dumps({"revision": "empty", "settings": choices("example")})
        headers = {**auth, "Content-Type": "application/json"}
        self.assertEqual(request("GET")[0], 403)
        self.assertEqual(request("GET", **auth)[1]["revision"], "empty")
        self.assertEqual(request("POST", body, Origin="https://example.invalid", **headers)[0], 403)
        self.assertEqual(request("POST", body, Origin=server.origin, **headers)[0], 200)
        self.assertEqual(request("POST", body, Origin=server.origin, **headers)[0], 409)

        path = "/api/recommended"
        data = json.dumps(snapshot())
        self.assertEqual(request("POST", data, path, Origin=server.origin)[0], 403)
        self.assertEqual(request("POST", data, path, Origin="https://example.invalid", **headers)[0], 403)
        self.assertEqual(request("POST", "{}", path, Origin=server.origin, **headers)[0], 400)
        self.assertEqual(request("POST", None, path, Origin=server.origin,
                                 **{**headers, "Content-Length": "1048577"})[0], 400)
        code, plan = request("POST", data, path, Origin=server.origin, **headers)
        self.assertEqual(code, 200)
        self.assertEqual(plan["selected_route"], "codex_engineer")
        self.assertFalse(plan["executed"])
        self.assertEqual(request("GET", **auth)[1]["settings"], choices("example"))
