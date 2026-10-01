from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_skill", ROOT / "scripts/package_skill.py")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


class PackagingTests(unittest.TestCase):
    def test_clean_archive_runs_outside_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = pack.package(ROOT, base / "release")
            with zipfile.ZipFile(archive) as zipped:
                self.assertEqual(set(zipped.namelist()), {"shangan-schedule/" + name for name in pack.FILES})
                zipped.extractall(base / "install with spaces")
            skill = base / "install with spaces/shangan-schedule"
            work = base / "work"
            work.mkdir()
            cli = skill / "scripts/shangan_schedule.py"
            for args in (
                ["init", "--year", "2027", "--type", "selected_graduate", "--output", "candidates.json"],
                ["validate", "--input", "candidates.json"],
                ["review", "--candidate", "candidates.json", "--output", "review.md"],
                ["build", "--data", str(skill / "tests/fixtures/published-one-exam.json"), "--sources", str(skill / "tests/fixtures/official-sources.json"), "--output", "site"],
            ):
                result = subprocess.run([sys.executable, str(cli), *args], cwd=work, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((work / "site/index.html").is_file())
            self.assertFalse((skill / "candidates.json").exists())

    def test_scanner_and_symlink_guard(self):
        self.assertIn("service-token", pack.findings("gh" + "p_" + "a" * 30))
        self.assertIn("personal-path", pack.findings("/" + "Users/" + "sample/file"))
        self.assertIn("email", pack.findings("person" + "@" + "example.org"))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / pack.FILES[0]).symlink_to(ROOT / pack.FILES[0])
            with self.assertRaisesRegex(ValueError, "symlink"):
                pack.package(root, root / "release")


if __name__ == "__main__":
    unittest.main()
