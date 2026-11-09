"""commit_validator.py - Validación estricta de Conventional Commits.

Inspirado en Commitlint y semantic-release.
"""

from __future__ import annotations

import re

TYPES = ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore"]
HEADER_MAX = 72
HEADER_RE = re.compile(r"^(" + "|".join(TYPES) + r")(\([a-z0-9._-]+\))?: \S.*$")


def validate_commit_message(header: str, body: str = "") -> list[str]:
    """Valida estrictamente la cabecera y cuerpo de un commit según Conventional Commits."""
    errors: list[str] = []
    header = header.strip()

    if not header:
        errors.append("la cabecera del commit no puede estar vacía")
        return errors

    if len(header) > HEADER_MAX:
        errors.append(f"la cabecera mide {len(header)} caracteres (máximo {HEADER_MAX})")

    if not HEADER_RE.match(header):
        errors.append(f"la cabecera no cumple Conventional Commits: {header!r}")

    match = re.match(r"^([a-z]+)(?:\([^\)]+\))?: (.+)", header)
    if match:
        desc = match.group(2)
        if desc and (desc[0].isupper() or desc.endswith(".") or "\n" in desc):
            errors.append("la descripción debe empezar en minúscula y no acabar en punto")

    if body and len(body.strip()) > 2000:
        errors.append("el cuerpo del commit es demasiado extenso")

    return errors
