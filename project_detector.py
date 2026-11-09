"""project_detector.py - Autodetección de comandos de test e instalación.

Inspirado en Dependabot y Renovate: analiza los archivos de configuración
del repositorio para deducir comandos por defecto de ejecución de tests.
"""

from __future__ import annotations

import json
from pathlib import Path


def detect_test_command(repo_root: Path | None = None) -> str | None:
    """Infiere el comando de tests analizando la estructura del repositorio."""
    root = repo_root or Path.cwd()

    makefile = root / "Makefile"
    if makefile.is_file():
        try:
            content = makefile.read_text(encoding="utf-8")
            if "\ntest:" in content or content.startswith("test:"):
                return "make test"
        except OSError:
            pass

    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
            test_script = scripts.get("test", "")
            if test_script and "no test specified" not in test_script.lower():
                if (root / "pnpm-lock.yaml").is_file():
                    return "pnpm test"
                if (root / "yarn.lock").is_file():
                    return "yarn test"
                return "npm test"
        except (json.JSONDecodeError, OSError):
            pass

    if (root / "Cargo.toml").is_file():
        return "cargo test"

    if (root / "go.mod").is_file():
        return "go test ./..."

    pyproject = root / "pyproject.toml"
    has_py_files = any(root.glob("*.py")) or any(root.glob("tests/**/*.py")) or any(root.glob("test_*.py"))

    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if "[tool.pytest.ini_options]" in content or "pytest" in content:
                return "pytest"
        except OSError:
            pass

    if has_py_files:
        has_tests = any(root.glob("test_*.py")) or (root / "tests").is_dir()
        if has_tests:
            return "python -m unittest discover"

    return None
