from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from roleshuttle.cli import main
from roleshuttle.recommended import preview, preview_snapshot


NOW = 1794931200
STAMP = datetime.fromtimestamp(NOW, timezone.utc).isoformat()


def snapshot():
    routes = {"codex_engineer": {"worker": "codex", "provider": "codex", "model": "example-codex",
                                 "tier": 3, "pool": "codex", "boundary": "external", "effort": "medium"}}
    return {
        "now": NOW,
        "baseline_routing": {"medium": ["codex_engineer"]},
        "policy": {"schema_version": 2, "extra_usage_supported": True, "activate_saved_resets": False,
                   "snapshot_max_age_seconds": 300, "admission": {"mode": "provider_authoritative"},
                   "routing": {"medium": ["codex_engineer"]}, "routes": routes,
                   "go_model_allowlist": ["kimi-k2.7-code"]},
        "task": {"type": "feature", "risk": "low", "complexity": "medium", "priority": "normal",
                 "required_tools": ["read", "edit"], "external_code_allowed": True, "allow_extra_usage": True},
        "availability": {"observed_at": STAMP, "pools": {
            "codex": {"status": "known", "observed_at": STAMP, "used_percent": 20},
            "opencode_go": {"status": "known", "observed_at": STAMP, "used_percent": 30},
        }, "routes": {
            key: {"available": True, "adapter_available": True, "effort": effort, "model": model,
                  "capabilities": ["read", "edit"], "context_tokens": 16000}
            for key, model, effort in (
                ("codex_engineer", "example-codex", "medium"),
                ("recommended-go/kimi-k2.7-code", "opencode-go/kimi-k2.7-code", None))
        }},
    }


class RecommendedTests(unittest.TestCase):
    def test_quota_ranks_candidates_but_extra_usage_blocks_go_execution(self):
        item = snapshot()
        self.assertEqual(preview_snapshot(item)["selected_route"], "codex_engineer")
        item["availability"]["pools"]["codex"]["used_percent"] = 80
        self.assertEqual(preview_snapshot(item)["assigned_provider"], "opencode-go")
        item["task"]["allow_extra_usage"] = False
        self.assertEqual(preview_snapshot(item)["selected_route"], "codex_engineer")
        item["availability"]["pools"]["codex"]["used_percent"] = 100
        result = preview_snapshot(item)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["execution_admissible"])
        self.assertFalse(result["executed"])
        self.assertIn("explicit Extra Usage permission required", result["reason"])

    def test_stale_quota_and_project_boundary(self):
        item = snapshot()
        item["task"]["external_code_allowed"] = False
        for pool in item["availability"]["pools"].values():
            pool["observed_at"] = "2020-01-01T00:00:00Z"
        result = preview_snapshot(item)
        self.assertIsNone(result["selected_route"])
        self.assertTrue(all(not row["eligible"] for row in result["alternatives"]))
        self.assertIn("project disallows external code transfer", result["alternatives"][0]["reasons"])
        self.assertFalse(result["recommended"]["pools"]["codex"]["known"])

    def test_project_local_route_requires_exact_unchanged_capability_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.txt"
            evidence.write_bytes(b"Verified fixture model and tools")
            project = {"repository": str(root / "checkout"), "external_code_allowed": False,
                       "shared_mac_allowed": True, "extra_usage_allowed": False, "automatic_cloud": [],
                       "local_capability": {"model": "mac-omlx/fixture", "capabilities": ["read", "edit"],
                                            "verified_at": STAMP, "evidence": str(evidence),
                                            "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}}
            item = snapshot()
            item["task"]["shared_mac_allowed"] = True
            item["baseline_routing"]["medium"].append("local_glm")
            item["policy"]["routing"]["medium"].append("local_glm")
            item["policy"]["routes"]["local_glm"] = {"worker": "opencode", "provider": "mac-omlx",
                                                      "model": "mac-omlx/fixture", "tier": 3,
                                                      "pool": None, "boundary": "shared_mac"}
            item["availability"]["routes"]["local_glm"] = {
                "available": True, "adapter_available": True, "model": "mac-omlx/fixture",
                "capabilities": ["read", "edit"], "context_tokens": 16000}

            def decision():
                return preview(item["task"], item["policy"], item["availability"], item["baseline_routing"],
                               now=NOW, project=project)

            self.assertEqual(decision()["selected_route"], "local_glm")
            evidence.write_bytes(b"Changed fixture evidence")
            self.assertIsNone(decision()["selected_route"])
            self.assertTrue(any("intact project capability evidence" in reason
                                for row in decision()["alternatives"] for reason in row["reasons"]))

    def test_narrowed_lane_and_unsupported_attempts(self):
        item = snapshot()
        item["policy"]["routing"]["medium"] = []
        self.assertEqual(preview_snapshot(item)["alternatives"], [])
        item["task"]["attempts"] = [{"route": "codex_engineer", "status": "failed"}]
        with self.assertRaisesRegex(ValueError, "first attempts"):
            preview_snapshot(item)

    def test_cli_prints_plan_only_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot()), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main(["recommend", str(path)]), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["mode"], "plan-only")
        self.assertEqual(result["assigned_model"], "example-codex")
        self.assertFalse(result["executed"])
