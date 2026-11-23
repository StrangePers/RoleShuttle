import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class OrdersFixtureTests(unittest.TestCase):
    def test_new_repositories_agree_on_base_and_checks_detect_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / name for name in ("direct", "routed")]
            command = [sys.executable, "-B", str(EXAMPLES / "prepare_orders.py")]
            bases = [subprocess.check_output([*command, roots[0]], text=True).strip(),
                     subprocess.check_output([*command, roots[1], "--with-local-profile"], text=True).strip()]
            self.assertEqual(bases[0], bases[1])
            profile = Path(".roleshuttle/roles.json")
            self.assertEqual((roots[1] / profile).read_bytes(), (EXAMPLES / "local-roles.json").read_bytes())
            subprocess.run(["git", "check-ignore", "-q", str(profile)], cwd=roots[1], check=True)
            root = roots[0]
            check = [sys.executable, "-B", "checks.py", "export"]
            self.assertNotEqual(subprocess.run(check, cwd=root, capture_output=True).returncode, 0)
            options = root / "options.json"
            config = json.loads(options.read_text())
            config.update(scope="selected", encoding="csv")
            options.write_text(json.dumps(config))
            subprocess.run(check, cwd=root, check=True, capture_output=True)
            self.assertNotEqual(subprocess.run([*command, root], capture_output=True).returncode, 0)
            self.assertEqual(json.loads(options.read_text()), config)
