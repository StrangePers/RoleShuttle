from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from roleshuttle.skill import CLIENT_DIRS, install


class ProjectSkillTests(unittest.TestCase):
    def test_each_layout_and_repeated_install(self):
        with TemporaryDirectory() as work:
            root = Path(work).resolve()
            for client, folder in CLIENT_DIRS.items():
                path = install(client, root)
                self.assertEqual(path, root / folder / "skills" / "roleshuttle" / "SKILL.md")
                self.assertIn("name: roleshuttle", path.read_text(encoding="utf8"))
                before = path.stat().st_mtime_ns
                self.assertEqual(install(client, root), path)
                self.assertEqual(path.stat().st_mtime_ns, before)

    def test_existing_customization_survives_reinstall(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            path = install("codex", root)
            path.write_text("My own instructions")
            with self.assertRaisesRegex(ValueError, "Existing skill differs"):
                install("codex", root)
            self.assertEqual(path.read_text(), "My own instructions")
