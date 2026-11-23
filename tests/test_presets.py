import contextlib
import io
import json
import unittest

from roleshuttle.cli import main


class CloudPresets(unittest.TestCase):
    def lookup(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(main(["preset", *args]), 0)
        return json.loads(stream.getvalue())

    def test_role_and_lane_assignments(self):
        self.assertEqual(self.lookup(), ["codex", "opencode-go", "openrouter-free"])
        self.assertEqual(self.lookup("codex", "--role", "reviewer"),
                         {"model": "gpt-6.2-astra", "effort": "medium"})
        self.assertEqual(self.lookup("opencode-go", "--role", "architect"), {"model": "gemini-4-argon"})
        self.assertEqual(self.lookup("opencode-go", "--lane", "architecture"), {"model": "kimi-k3.1-pro"})

    def test_free_policy(self):
        policy = self.lookup("openrouter-free")
        self.assertFalse(policy["paid_fallback"])
        self.assertTrue(policy["require_tools"])
        self.assertTrue(all(model.endswith(":free") for model in policy["preferred_models"]))

    def test_missing_assignments_fail(self):
        for args in (("--role", "reviewer"), ("missing",), ("openrouter-free", "--role", "reviewer"),
                     ("codex", "--lane", "critical")):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["preset", *args])
            self.assertEqual(error.exception.code, 2)
