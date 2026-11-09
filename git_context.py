"""git_context.py - Priorización de contexto por actividad y recencia en Git.

Inspirado en Continue.dev: analiza commits recientes (churn y recencia)
para dar prioridad a los archivos que están cambiando activamente en el proyecto.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def get_git_churn_scores(repo_root: Path | None = None, max_commits: int = 100) -> dict[str, float]:
    """Calcula una puntuación de actividad para cada archivo basándose en la recencia y frecuencia de commits."""
    root = repo_root or Path.cwd()
    cmd = ["git", "log", f"-n{max_commits}", "--name-only", "--format=COMMIT:%ct"]
    try:
        res = subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}

    scores: dict[str, float] = {}
    current_weight = 1.0
    decay = 0.95

    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT:"):
            current_weight *= decay
            continue
        scores[line] = scores.get(line, 0.0) + current_weight

    if not scores:
        return {}

    max_score = max(scores.values())
    return {path: score / max_score for path, score in scores.items()}


def calculate_file_priority(
    filepath: str,
    git_scores: dict[str, float] | None = None,
) -> float:
    """Combina la prioridad base (tipo de archivo/README) con la puntuación de recencia de git."""
    name = Path(filepath).name.lower()
    if name.startswith(("readme", "contributing")):
        base = 10.0
    elif Path(filepath).suffix.lower() in {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh"}:
        base = 5.0
    else:
        base = 1.0

    churn = (git_scores or {}).get(filepath, 0.0)
    return base + (churn * 4.0)


def rank_files_by_activity(
    files: list[str],
    repo_root: Path | None = None,
    git_scores: dict[str, float] | None = None,
) -> list[str]:
    """Ordena una lista de archivos priorizando los más activos y relevantes."""
    scores = git_scores if git_scores is not None else get_git_churn_scores(repo_root)
    return sorted(files, key=lambda f: calculate_file_priority(f, scores), reverse=True)
