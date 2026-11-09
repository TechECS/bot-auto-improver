import tempfile
import unittest
from pathlib import Path
from project_detector import detect_install_command, detect_test_command


class TestProjectDetector(unittest.TestCase):
    def test_detect_install_python_requirements(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "requirements.txt").write_text("requests==2.31.0", encoding="utf-8")
            cmd = detect_install_command(root)
            self.assertEqual(cmd, "pip install -r requirements.txt")

    def test_detect_install_npm_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            cmd = detect_install_command(root)
            self.assertEqual(cmd, "npm ci")

    def test_detect_install_cargo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Cargo.toml").write_text("[package]\nname = 'foo'", encoding="utf-8")
            cmd = detect_install_command(root)
            self.assertEqual(cmd, "cargo build")

    def test_detect_python_unittest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "test_sample.py").write_text("import unittest", encoding="utf-8")
            cmd = detect_test_command(root)
            self.assertEqual(cmd, "python -m unittest discover")

    def test_detect_npm_test(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
            cmd = detect_test_command(root)
            self.assertEqual(cmd, "npm test")

    def test_detect_cargo_test(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Cargo.toml").write_text("[package]\nname = 'foo'", encoding="utf-8")
            cmd = detect_test_command(root)
            self.assertEqual(cmd, "cargo test")

    def test_detect_makefile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
            cmd = detect_test_command(root)
            self.assertEqual(cmd, "make test")

    def test_detect_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cmd = detect_test_command(root)
            self.assertIsNone(cmd)


if __name__ == "__main__":
    unittest.main()
