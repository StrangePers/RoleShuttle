from http.client import HTTPConnection
import json
import os
import sys
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic
import unittest
from unittest.mock import Mock, patch

from roleshuttle.agent_registration import prepare_for_checkout
from roleshuttle.local_acceptance import accept_task, check_task
from roleshuttle.local_execution import execute_patch, inspect_result
from roleshuttle.local_model import propose_patch
from roleshuttle.local_panel import action
from roleshuttle.local_tasks import database, discover_project, prepare_task, register_project, resolve_project, show_project
from roleshuttle.local_worktrees import create_worktree
from roleshuttle.project_registration import PREVIEW_TTL, ProjectRegistration
from roleshuttle.settings_server import CheckoutServer, SettingsServer


class ProjectDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.registry = self.directory / "private"
        profile = patch("roleshuttle.local_tasks.profile_home", return_value=self.registry)
        profile.start()
        self.addCleanup(profile.stop)
        self.root = self.directory / "repo"
        self.root.mkdir()
        self.git("init", "--quiet")
        (self.root / "src").mkdir()
        (self.root / "src" / "file.txt").write_text("fixture\n", encoding="utf-8")
        self.git("add", "src")
        self.git("-c", "user.name=Discovery Test", "-c", "user.email=discovery@example.com",
                 "-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "Fixture")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def register(self, project_id="parent", revision="empty", **changes):
        return register_project({
            "id": project_id,
            "repository": str(self.root),
            "allowed_paths": ["src/"],
            "worktrees_allowed": True,
            **changes,
        }, revision)

    def worktree(self):
        root = self.directory / "linked"
        self.git("worktree", "add", "--quiet", "--detach", str(root), "HEAD")
        return root

    def checkout_intent(self):
        return {"title": "Update fixture", "mode": "write", "allowed_paths": ["src/"]}

    def assert_no_tasks(self):
        if not (self.registry / "local-tasks.sqlite3").exists():
            return
        with database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def check_setup_fixture(self):
        self.enterContext(patch.dict(os.environ, {"ROLESHUTTLE_HOME": str(self.registry)}))
        project = self.register()
        task = prepare_task({**self.checkout_intent(), "project_id": project["id"]},
                            panel_input={"prompt": "Test fixture"})
        command = [sys.executable, "-I", "-B", "-c",
                   "from pathlib import Path; assert Path('src/check.txt').read_text().strip() == 'verified'"]
        request = {"task_id": task["task_id"], "revision": task["revision"], "confirmed": True,
                   "checks": [{"id": "verify", "argv": command}]}
        return project, task, request

    def test_missing_checks_block_panel_launch_before_worktree_creation(self):
        _, task, _ = self.check_setup_fixture()
        request = {"task_id": task["task_id"]}
        self.assertEqual(action("check-setup-status", request),
                         {"requires_setup": True, "can_configure": True})
        with patch("roleshuttle.local_panel.create_worktree") as create:
            with self.assertRaisesRegex(ValueError, "No checks are registered"):
                action("launch", request)
        create.assert_not_called()

    def test_task_checks_are_pinned_and_allow_acceptance_without_new_task(self):
        project, task, request = self.check_setup_fixture()
        task_id = task["task_id"]
        configured = action("configure-checks", request)
        self.assertEqual(configured["task_id"], task_id)
        self.assertEqual(configured["contract"], task["contract"])
        self.assertEqual(show_project(project["id"]), project)
        self.assertRegex(configured["task_checks"][0]["executable_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(action("check-setup-status", {"task_id": task_id}),
                         {"requires_setup": False, "can_configure": False})
        with self.assertRaisesRegex(ValueError, "Task revision changed"):
            action("configure-checks", request)
        create_worktree(task_id)
        diff = self.directory / "fixture.diff"
        diff.write_bytes(
            b"diff --git a/src/check.txt b/src/check.txt\nnew file mode 100644\n"
            b"--- /dev/null\n+++ b/src/check.txt\n@@ -0,0 +1 @@\n+verified\n"
        )
        execute_patch(task_id, diff)
        fingerprint = inspect_result(task_id)["fingerprint"]
        checked = check_task(task_id, fingerprint)
        self.assertTrue(checked["check_receipt"]["passed"])
        self.assertEqual(accept_task(task_id, fingerprint)["status"], "accepted")

    def test_task_checks_require_consent_and_current_project_revision(self):
        project, _, request = self.check_setup_fixture()
        with self.assertRaisesRegex(ValueError, "Explicitly confirm"):
            action("configure-checks", {**request, "confirmed": False})
        self.register(revision=project["revision"], worktrees_allowed=False)
        with self.assertRaisesRegex(ValueError, "Project revision changed"):
            action("configure-checks", request)

    def test_linked_registration_confirms_exact_inherited_preview(self):
        parent = self.register(shared_mac_allowed=True, external_code_allowed=True,
                               automatic_cloud=["codex"])
        root = self.worktree()
        registration = ProjectRegistration()
        preview = registration.preview({"path": str(root / "src"), "id": "linked"})
        project = preview["project"]
        self.assertEqual(project["repository"], str(root))
        self.assertEqual(project["id"], "linked")
        self.assertEqual(project["source_project"], {
            "id": parent["id"], "revision": parent["revision"],
        })
        self.assertEqual(project["allowed_paths"], parent["allowed_paths"])
        self.assertIs(project["worktrees_allowed"], False)
        for field in (
            "shared_mac_allowed", "external_code_allowed", "automatic_cloud",
            "extra_usage_allowed", "extra_usage_default",
        ):
            self.assertEqual(project[field], parent[field])
        registered = registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})
        self.assertEqual({key: registered[key] for key in project}, project)
        self.assert_no_tasks()

    def test_linked_registration_rejects_overrides(self):
        self.register()
        root = self.worktree()
        registration = ProjectRegistration()
        request = {"path": str(root), "id": "linked"}
        for field, value in (
            ("allowed_paths", ["src/file.txt"]), ("worktrees_allowed", True),
            ("external_code_allowed", True), ("automatic_cloud", ["codex"]),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                registration.preview({**request, field: value})

    def test_linked_registration_rejects_multiple_authorized_parents(self):
        self.register()
        self.register("another")
        root = self.worktree()
        with self.assertRaises(ValueError):
            ProjectRegistration().preview({"path": str(root), "id": "linked"})

    def test_linked_registration_cannot_confirm_after_parent_revocation(self):
        parent = self.register()
        root = self.worktree()
        registration = ProjectRegistration()
        preview = registration.preview({"path": str(root), "id": "linked"})
        self.register(revision=parent["revision"], worktrees_allowed=False)
        file = self.registry / "local-tasks.sqlite3"
        before = file.read_bytes()
        with self.assertRaises(ValueError):
            registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})
        self.assertEqual(file.read_bytes(), before)

    def test_linked_checkout_requires_confirmation_and_revocation_blocks_start(self):
        parent = self.register()
        root = self.worktree()
        cancel = Mock(return_value={"status": "CANCELLED"})
        result = prepare_for_checkout(self.checkout_intent(), root / "src",
                                      register=cancel)
        self.assertEqual(result, {"status": "CANCELLED"})
        cancel.assert_called_once_with(str(root))
        self.assert_no_tasks()

        def register(checkout):
            self.assertEqual(checkout, str(root))
            self.assert_no_tasks()
            registration = ProjectRegistration()
            preview = registration.preview({"path": checkout, "id": "linked"})
            self.assert_no_tasks()
            return registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})

        task = prepare_for_checkout(self.checkout_intent(), root / "src",
                                    register=register)
        self.assertEqual(task["contract"]["project_id"], "linked")
        self.register(revision=parent["revision"], worktrees_allowed=False)
        file = self.registry / "local-tasks.sqlite3"
        before = file.read_bytes()
        with patch("roleshuttle.local_worktrees.profile_home", return_value=self.registry), \
                patch("roleshuttle.local_worktrees._git") as git:
            with self.assertRaises(ValueError):
                create_worktree(task["task_id"])
        git.assert_not_called()
        self.assertEqual(file.read_bytes(), before)

    def test_parent_revocation_blocks_model_after_task_worktree_exists(self):
        parent = self.register(external_code_allowed=True, automatic_cloud=["codex"])
        root = self.worktree()
        registration = ProjectRegistration()
        preview = registration.preview({"path": str(root), "id": "linked"})
        registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})
        task = prepare_for_checkout(self.checkout_intent(), root)
        with patch("roleshuttle.local_worktrees.profile_home", return_value=self.registry):
            ready = create_worktree(task["task_id"])
        self.assertEqual(ready["phase"], "worktree_ready")
        self.register(revision=parent["revision"], external_code_allowed=False)
        admitted = {"assigned_model": "fixture", "effort": "low", "selected_route": "codex"}
        with (patch("roleshuttle.local_model.profile_home", return_value=self.registry),
              patch("roleshuttle.local_model.advanced_settings.read", return_value={"revision": "empty"}),
              patch("roleshuttle.local_model.codex_route", return_value=admitted),
              patch("roleshuttle.local_model.codex_stage") as stage):
            with self.assertRaisesRegex(ValueError, "current authorized source"):
                propose_patch(task["task_id"], "approved test prompt", {})
        stage.assert_not_called()

    def test_prepare_for_checkout_with_existing_profile_skips_registration(self):
        project = self.register()
        register = Mock()
        task = prepare_for_checkout(self.checkout_intent(), self.root / "src",
                                    register=register)
        self.assertEqual(task["contract"]["project_id"], project["id"])
        register.assert_not_called()

    def test_prepare_for_checkout_registers_before_creating_task_and_replays_request(self):
        values = self.checkout_intent()

        def register(root):
            self.assertEqual(root, str(self.root))
            self.assert_no_tasks()
            return self.register(allowed_paths=["src/"])

        task = prepare_for_checkout(values, self.root / "src", request_id="checkout-request",
                                    register=register)
        contract = task["contract"]
        self.assertEqual(contract["project_id"], "parent")
        self.assertEqual(contract["title"], values["title"])
        self.assertEqual(contract["allowed_paths"], values["allowed_paths"])
        self.assertEqual(contract["mode"], values["mode"])
        self.assertIs(contract["allow_extra_usage"], False)
        replay = prepare_for_checkout(values, self.root / "src", request_id="checkout-request",
                                      register=register)
        self.assertEqual(replay, task)

    def test_prepare_for_checkout_without_confirmation_creates_no_task(self):
        for status in ("CANCELLED", "EXPIRED"):
            with self.subTest(status=status):
                result = prepare_for_checkout(self.checkout_intent(), self.root,
                                              register=Mock(return_value={"status": status}))
                self.assertEqual(result, {"status": status})
                self.assert_no_tasks()

    def test_prepare_for_checkout_rejects_insufficient_registered_scope(self):
        def register(root):
            return self.register(allowed_paths=["src/file.txt"])

        with self.assertRaises(ValueError):
            prepare_for_checkout(self.checkout_intent(), self.root, register=register)
        self.assert_no_tasks()

    def test_prepare_for_checkout_rejects_stale_registration_revision(self):
        def register(root):
            project = self.register()
            self.register(revision=project["revision"], worktrees_allowed=False)
            return project

        with self.assertRaisesRegex(ValueError, "project revision changed"):
            prepare_for_checkout(self.checkout_intent(), self.root, register=register)
        self.assert_no_tasks()

    def test_registration_preview_defaults_and_one_time_confirmation(self):
        registration = ProjectRegistration()
        preview = registration.preview({"path": str(self.root / "src"), "id": "parent"})
        project = preview["project"]
        self.assertEqual(project["repository"], str(self.root))
        self.assertEqual(project["id"], "parent")
        self.assertEqual(project["allowed_paths"], [])
        self.assertEqual(project["automatic_cloud"], [])
        for field in (
            "worktrees_allowed", "shared_mac_allowed", "external_code_allowed",
            "extra_usage_allowed", "extra_usage_default",
        ):
            self.assertIs(project[field], False)
        confirm = {"preview_id": preview["preview_id"], "confirmed": True}
        for request in (
            {**confirm, "confirmed": False},
            {**confirm, "confirmed": 1},
            {**confirm, "allowed_paths": ["src/"]},
        ):
            with self.assertRaises(ValueError):
                registration.confirm(request)
        self.assertFalse(self.registry.exists())

        registered = registration.confirm(confirm)
        self.assertEqual({key: registered[key] for key in project}, project)
        with self.assertRaises(ValueError):
            registration.confirm(confirm)

    def test_registration_preview_preserves_explicit_opt_ins(self):
        registration = ProjectRegistration()
        preview = registration.preview({
            "path": str(self.root), "id": "parent",
            "allowed_paths": ["src/"],
            "worktrees_allowed": True, "shared_mac_allowed": True,
            "external_code_allowed": True, "automatic_cloud": ["codex", "opencode_go"],
        })
        project = preview["project"]
        self.assertEqual(project["allowed_paths"], ["src/"])
        self.assertEqual(project["automatic_cloud"], ["codex", "opencode_go"])
        for field in ("worktrees_allowed", "shared_mac_allowed", "external_code_allowed"):
            self.assertIs(project[field], True)
        self.assertFalse(self.registry.exists())
        registered = registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})
        self.assertEqual({key: registered[key] for key in project}, project)

    def test_registration_rejects_expired_preview(self):
        registration = ProjectRegistration()
        with patch("roleshuttle.project_registration.monotonic", return_value=100) as now:
            preview = registration.preview({"path": str(self.root), "id": "parent"})
            now.return_value += PREVIEW_TTL
            with self.assertRaisesRegex(ValueError, "preview is unavailable"):
                registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})
        self.assertFalse(self.registry.exists())

    def test_registration_rejects_changed_head(self):
        registration = ProjectRegistration()
        preview = registration.preview({"path": str(self.root), "id": "parent"})
        self.git("-c", "user.name=Discovery Test", "-c", "user.email=discovery@example.com",
                 "-c", "commit.gpgSign=false", "commit", "--quiet", "--allow-empty", "-m", "Advance HEAD")
        with self.assertRaisesRegex(ValueError, "HEAD changed"):
            registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})

    def test_registration_rejects_duplicate_checkout(self):
        registration = ProjectRegistration()
        preview = registration.preview({"path": str(self.root), "id": "other"})
        self.register()
        with self.assertRaises(ValueError):
            registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})
        with self.assertRaisesRegex(ValueError, "checkout is already registered"):
            register_project(preview["project"], "empty", unique_checkout=True)

    def test_registration_rejects_duplicate_id(self):
        alternate = self.directory / "alternate"
        self.git("clone", "--quiet", str(self.root), str(alternate))
        registration = ProjectRegistration()
        preview = registration.preview({"path": str(alternate), "id": "parent"})
        self.register()
        with self.assertRaisesRegex(ValueError, "revision changed"):
            registration.confirm({"preview_id": preview["preview_id"], "confirmed": True})

    def test_registration_rejects_linked_and_unborn_checkouts(self):
        registration = ProjectRegistration()
        unborn = self.directory / "unborn"
        unborn.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=unborn, check=True, capture_output=True)
        for path in (self.worktree(), unborn):
            with self.subTest(path=path), self.assertRaises(ValueError):
                registration.preview({"path": str(path), "id": "parent"})
        self.assertFalse(self.registry.exists())

    def test_registration_preview_requires_bearer_and_matching_origin(self):
        with SettingsServer(0, autonomous_tasks=True) as server:
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                for origin, token, allowed in (
                    (server.origin, None, False),
                    ("http://localhost", server.token, False),
                    (server.origin, server.token, True),
                ):
                    with self.subTest(origin=origin, authenticated=bool(token)):
                        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                        try:
                            headers = {"Content-Type": "application/json", "Origin": origin}
                            if token:
                                headers["Authorization"] = f"Bearer {token}"
                            connection.request("POST", "/api/local/project-preview",
                                               json.dumps({"path": str(self.root), "id": "parent"}), headers)
                            response = connection.getresponse()
                            if allowed:
                                self.assertEqual(response.status, 200)
                                self.assertIn("preview_id", json.loads(response.read()))
                            else:
                                self.assertIn(response.status, range(400, 500))
                        finally:
                            connection.close()
            finally:
                server.shutdown()
                worker.join()
        self.assertFalse(self.registry.exists())

    def test_checkout_form_requires_auth_and_cannot_register_another_root(self):
        with CheckoutServer(0) as server:
            server.prefill = {"path": str(self.root), "id": "fixture"}
            server.deadline = monotonic() + 30
            server.result = None
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                def request(method, path, body=None, *, authorized=True, origin=None):
                    headers = {"Content-Type": "application/json", "Origin": origin or server.origin}
                    if authorized:
                        headers["Authorization"] = f"Bearer {server.token}"
                    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    try:
                        connection.request(method, path, None if body is None else json.dumps(body), headers)
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()

                self.assertEqual(request("GET", "/api/checkout", authorized=False)[0], 403)
                self.assertEqual(request("GET", "/api/checkout"), (200, server.prefill))
                with patch("roleshuttle.project_registration._registration_discovery") as probe:
                    self.assertEqual(request("POST", "/api/local/project-preview", {
                        "path": str(self.directory / "other"), "id": "different",
                    })[0], 400)
                    probe.assert_not_called()
                self.assertEqual(request("POST", "/api/checkout/cancel", {}, origin="http://localhost")[0], 403)
                self.assertEqual(request("POST", "/api/checkout/cancel", {}), (200, {"status": "CANCELLED"}))
                self.assertEqual(request("GET", "/api/checkout")[0], 410)
            finally:
                server.shutdown()
                worker.join()
        self.assertFalse(self.registry.exists())

    def test_missing_registry_is_not_created(self):
        self.assertEqual(discover_project(self.directory)["status"], "NOT_GIT_CHECKOUT")
        result = discover_project(self.root / "src")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "PROFILE_MISSING")
        self.assertEqual(result["checkout_root"], str(self.root))
        self.assertEqual(result["next_action"], "local-project register <profile.json>")
        self.assertFalse(self.registry.exists())

    def test_exact_profile_from_subdirectory_leaves_registry_unchanged(self):
        project = self.register()
        file = self.registry / "local-tasks.sqlite3"
        before = file.read_bytes()
        result = discover_project(self.root / "src")
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["resolution"], "exact")
        self.assertEqual(result["profile"], project)
        self.assertEqual(file.read_bytes(), before)

    def test_linked_worktree_without_with_and_between_permitted_parents(self):
        worktree = self.worktree()
        self.assertEqual(discover_project(worktree)["status"], "WORKTREE_SOURCE_MISSING")
        self.assertFalse(self.registry.exists())
        parent = self.register()
        file = self.registry / "local-tasks.sqlite3"
        before = file.read_bytes()
        result = discover_project(worktree / "src")
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["resolution"], "inherited")
        self.assertEqual(result["profile"]["allowed_paths"], parent["allowed_paths"])
        self.assertFalse(result["profile"]["worktrees_allowed"])
        self.assertEqual(result["profile"]["source_project"], {
            "id": parent["id"], "revision": parent["revision"],
        })
        self.assertEqual(file.read_bytes(), before)
        self.register("another")
        before = file.read_bytes()
        result = discover_project(worktree)
        self.assertEqual(result["status"], "PROFILE_AMBIGUOUS")
        self.assertEqual(result["candidates"], ["another", "parent"])
        self.assertEqual(result["next_action"], "select_project_id")
        self.assertEqual(file.read_bytes(), before)

    def test_stale_saved_worktree_profile_is_not_refreshed(self):
        parent = self.register()
        root = self.worktree()
        child = resolve_project(root)
        self.register(revision=parent["revision"], name="Updated parent")
        file = self.registry / "local-tasks.sqlite3"
        before = file.read_bytes()
        result = discover_project(root)
        self.assertEqual(result["status"], "PROFILE_INVALID")
        self.assertEqual(result["project_id"], child["id"])
        self.assertEqual(file.read_bytes(), before)

    def test_clone_of_registered_repository_needs_its_own_profile(self):
        self.register()
        clone = self.directory / "clone" / self.root.name
        clone.parent.mkdir()
        self.git("clone", "--quiet", str(self.root), str(clone))
        self.assertEqual(discover_project(clone)["status"], "PROFILE_MISSING")
