#!/usr/bin/env python3
"""
auto_improver.py - Revisa el repositorio con Gemini y abre una PR con Conventional Commits.

Flujo: lee el repo -> pide UNA mejora pequeña a Gemini (JSON estructurado) -> valida commits,
rutas y edits -> aplica -> ejecuta los tests -> crea rama, commits y PR (etiqueta `auto-improver`).
Nunca hace merge. Si no hay nada que merezca la pena, no hace nada.

Se ejecuta desde la raíz del repo que se quiere mejorar (el checkout del consumidor).
Configuración por variables de entorno (ver action.yml). Requiere: google-genai, pydantic y gh.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel

ROOT = Path.cwd()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
LANG = os.environ.get("AI_LANGUAGE", "es")
MAX_CHARS = int(os.environ.get("AI_MAX_CONTEXT_CHARS", "250000"))
MAX_FILE_CHARS = int(os.environ.get("AI_MAX_FILE_CHARS", "80000"))
MAX_FILES = int(os.environ.get("AI_MAX_FILES", "6"))
MAX_OPEN_PRS = int(os.environ.get("AI_MAX_OPEN_PRS", "1"))
MAX_COMMITS = 3
TEST_CMD = os.environ.get("AI_TEST_COMMAND", "").strip()
FOCUS = os.environ.get("AI_FOCUS", "auto")
DRY_RUN = os.environ.get("AI_DRY_RUN", "false").lower() == "true"
LABEL = "auto-improver"

TYPES = ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore"]
HEADER_MAX = 72

FOCOS = {
    "bugs": "errores lógicos, casos límite y excepciones sin controlar",
    "tests": "tests que faltan para código sin cobertura, sin cambiar el comportamiento",
    "seguridad": "escape de datos, validación de entradas, manejo de secretos y límites de peticiones",
    "docs": "documentación desactualizada o que no coincide con el código",
    "accesibilidad": "accesibilidad y rendimiento de la interfaz",
    "funcionalidad": "una mejora pequeña y útil alineada con el propósito del proyecto "
                     "(mira las ideas de colaboración del README si existen)",
}

# Archivos que el bot nunca puede modificar (solo leer).
PROTECTED = [
    ".github/*", "data/*", "LICENSE*", "CNAME", ".env*", "*.env", "*.pem", "*.key",
    "*.min.js", "*.min.css", "package-lock.json", "yarn.lock", "poetry.lock",
    ".auto-improver.md",
] + [p.strip() for p in os.environ.get("AI_PROTECTED", "").split(",") if p.strip()]

TEXT_EXT = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".md",
            ".yml", ".yaml", ".toml", ".txt", ".sh", ".cfg", ".ini", ".xml"}
SKIP_CONTEXT = ["data/*", "*.min.js", "*.min.css", "*lock.json", "*.lock", "sitemap.xml"]

TXT = {
    "es": {"summary": "Resumen", "why": "Por qué", "changes": "Cambios", "test": "Cómo probar",
           "footer": "PR generada automáticamente por auto-improver con Gemini (`{model}`). "
                     "Revísala antes de fusionar."},
    "en": {"summary": "Summary", "why": "Why", "changes": "Changes", "test": "How to test",
           "footer": "PR generated automatically by auto-improver using Gemini (`{model}`). "
                     "Review it before merging."},
}


# ----------------------------- modelos de salida -----------------------------

class Edit(BaseModel):
    path: str
    find: str
    replace: str


class NewFile(BaseModel):
    path: str
    content: str


class CommitPlan(BaseModel):
    type: str
    scope: str
    description: str
    body: str
    edits: list[Edit]
    new_files: list[NewFile]


class Proposal(BaseModel):
    nothing_to_do: bool
    reason: str
    branch_slug: str
    pr_summary: str
    pr_why: str
    pr_how_to_test: str
    commits: list[CommitPlan]


# --------------------------------- utilidades --------------------------------

def log(msg: str) -> None:
    print(msg, flush=True)


def summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")


def sh(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"Falló: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


def out(cmd: list[str]) -> str:
    return sh(cmd).stdout.strip()


def is_protected(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in PROTECTED)


def safe_path(p: str) -> str | None:
    p = p.strip().replace("\\", "/")
    if not p or p.startswith("/") or ".." in Path(p).parts:
        return None
    return p


def read_text(path: str) -> str | None:
    p = ROOT / path
    if not p.is_file():
        return None
    try:
        return p.read_bytes().decode("utf-8")  # conserva saltos de línea originales
    except UnicodeDecodeError:
        return None


def header(c: CommitPlan) -> str:
    scope = f"({c.scope})" if c.scope else ""
    return f"{c.type}{scope}: {c.description}"


# ------------------------------ contexto del repo ----------------------------

def build_context(files: list[str]) -> str:
    def prio(f: str) -> int:
        name = Path(f).name.lower()
        if name.startswith(("readme", "contributing")):
            return 0
        return 1 if Path(f).suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh"} else 2

    cands = []
    for f in files:
        if Path(f).suffix.lower() not in TEXT_EXT:
            continue
        if any(fnmatch.fnmatch(f, s) for s in SKIP_CONTEXT):
            continue
        text = read_text(f)
        if text is not None:
            cands.append((prio(f), len(text), f, text))
    cands.sort()

    parts, used, omitted = [], 0, []
    for _, _, f, text in cands:
        body = text[:MAX_FILE_CHARS]
        if used + len(body) > MAX_CHARS:
            omitted.append(f)
            continue
        tag = " [solo lectura]" if is_protected(f) else ""
        if len(text) > MAX_FILE_CHARS:
            tag += " [TRUNCADO: no propongas ediciones en la parte omitida]"
        parts.append(f"=== ARCHIVO: {f}{tag} ===\n{body}\n")
        used += len(body)
    if omitted:
        parts.append("=== ARCHIVOS OMITIDOS POR TAMAÑO (no los edites) ===\n" + "\n".join(omitted))
    return "\n".join(parts)


def recent_prs() -> list[dict]:
    r = sh(["gh", "pr", "list", "--label", LABEL, "--state", "all", "--limit", "15",
            "--json", "title,state"], check=False)
    if r.returncode != 0:
        log(f"Aviso: no se pudo listar PRs previas: {r.stderr.strip()}")
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


# ------------------------------------ prompt ---------------------------------

def system_prompt(focus_desc: str) -> str:
    lang = {"es": "español", "en": "inglés"}.get(LANG, LANG)
    return f"""Eres un ingeniero de software senior que mantiene este repositorio con mucho cuidado.
Tu tarea: proponer UNA mejora pequeña, segura y verificable, centrada en: {focus_desc}.

REGLAS ESTRICTAS
1. Conventional Commits (https://www.conventionalcommits.org/es/). type debe ser uno de: {', '.join(TYPES)}. scope es opcional, en minúsculas y kebab-case (módulo o área, p. ej. geocodificacion). Sin '!' ni BREAKING CHANGE.
2. description: en {lang}, modo imperativo, empieza en minúscula, sin punto final. La cabecera completa 'type(scope): description' mide como máximo {HEADER_MAX} caracteres.
3. Un commit = un cambio lógico y autocontenido. Máximo {MAX_COMMITS} commits; el principal va primero. body es opcional (explica el porqué).
4. Máximo {MAX_FILES} archivos distintos en total. Cambios mínimos: nada de refactors masivos ni cambios de estilo gratuitos.
5. edits: 'find' debe ser un fragmento EXACTO (espacios, tabuladores y saltos de línea idénticos) y ÚNICO en el archivo actual; incluye líneas de contexto suficientes. 'replace' es el texto nuevo. Edita solo partes visibles; nunca archivos [solo lectura] ni zonas [TRUNCADO] omitidas.
6. new_files solo para archivos que no existan (por ejemplo tests nuevos).
7. Respeta al pie de la letra el README y las reglas de .auto-improver.md. No añadas dependencias, no toques claves ni secretos, no edites archivos generados.
8. Si cambias comportamiento, añade o ajusta tests. Los tests existentes deben seguir pasando.
9. No repitas ni solapes las PRs previas de la lista.
10. Si no hay nada que merezca de verdad la pena, devuelve nothing_to_do=true con la razón. Es mejor no hacer nada que generar ruido.
11. pr_summary, pr_why y pr_how_to_test en {lang}, claros y concretos. Campos sin uso: cadena vacía o lista vacía.
12. branch_slug: 2 a 5 palabras en kebab-case, solo ASCII en minúsculas y números, máximo 40 caracteres."""


def user_prompt(tree: str, context: str, rules: str, recent: list[dict], errors: list[str]) -> str:
    prev = "\n".join(f"- [{p['state']}] {p['title']}" for p in recent) or "(ninguna)"
    msg = (f"## Reglas del proyecto (.auto-improver.md)\n{rules or '(no hay)'}\n\n"
           f"## PRs previas del bot (no repetir)\n{prev}\n\n"
           f"## Árbol del repositorio\n{tree}\n\n"
           f"## Contenido de los archivos\n{context}\n")
    if errors:
        msg += ("\n## ERRORES DE TU PROPUESTA ANTERIOR (corrígelos en la nueva propuesta)\n"
                + "\n".join(f"- {e}" for e in errors))
    return msg


def ask_gemini(client, system: str, prompt: str) -> Proposal:
    from google.genai import types

    for i in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=Proposal,
                    temperature=0.2,
                ),
            )
            return Proposal.model_validate_json(resp.text)
        except Exception as e:  # noqa: BLE001
            transient = any(t in str(e) for t in ("429", "500", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE"))
            if i == 3 or not transient:
                raise
            wait = 30 * i
            log(f"Gemini no disponible o límite alcanzado ({e.__class__.__name__}); reintento en {wait}s")
            time.sleep(wait)
    raise RuntimeError("inalcanzable")


# -------------------------------- validación ---------------------------------

HEADER_RE = re.compile(r"^(" + "|".join(TYPES) + r")(\([a-z0-9._-]+\))?: \S.*$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate(p: Proposal) -> tuple[list[str], list[dict[str, str]]]:
    """Devuelve (errores, cambios por commit). Simula la aplicación sin tocar el disco."""
    errors: list[str] = []
    if not SLUG_RE.match(p.branch_slug) or len(p.branch_slug) > 40:
        errors.append(f"branch_slug no válido: {p.branch_slug!r}")
    if not (1 <= len(p.commits) <= MAX_COMMITS):
        errors.append(f"debe haber entre 1 y {MAX_COMMITS} commits")
    if not p.pr_summary.strip() or not p.pr_why.strip() or not p.pr_how_to_test.strip():
        errors.append("pr_summary, pr_why y pr_how_to_test no pueden estar vacíos")

    state: dict[str, str] = {}
    per_commit: list[dict[str, str]] = []
    touched: set[str] = set()

    def current(path: str) -> str | None:
        return state[path] if path in state else read_text(path)

    for i, c in enumerate(p.commits, 1):
        h = header(c)
        if c.type not in TYPES or c.scope != c.scope.strip() or not HEADER_RE.match(h):
            errors.append(f"commit {i}: cabecera no cumple Conventional Commits: {h!r}")
        elif len(h) > HEADER_MAX:
            errors.append(f"commit {i}: cabecera de {len(h)} caracteres (máx {HEADER_MAX})")
        elif c.description[0].isupper() or c.description.endswith(".") or "\n" in c.description:
            errors.append(f"commit {i}: la descripción debe empezar en minúscula y no acabar en punto")

        writes: dict[str, str] = {}
        for e in c.edits:
            path = safe_path(e.path)
            if not path or is_protected(path):
                errors.append(f"commit {i}: ruta no permitida {e.path!r}")
                continue
            text = current(path)
            if text is None:
                errors.append(f"commit {i}: {path!r} no existe (usa new_files para crear archivos)")
                continue
            n = text.count(e.find) if e.find else 0
            if n != 1:
                errors.append(f"commit {i}: 'find' aparece {n} veces en {path} (debe ser 1): {e.find[:60]!r}")
                continue
            state[path] = writes[path] = text.replace(e.find, e.replace, 1)
            touched.add(path)
        for nf in c.new_files:
            path = safe_path(nf.path)
            if not path or is_protected(path):
                errors.append(f"commit {i}: ruta no permitida {nf.path!r}")
            elif current(path) is not None:
                errors.append(f"commit {i}: {path!r} ya existe (usa edits)")
            else:
                state[path] = writes[path] = nf.content
                touched.add(path)
        if not writes:
            errors.append(f"commit {i}: no contiene cambios")
        per_commit.append(writes)

    if len(touched) > MAX_FILES:
        errors.append(f"se tocan {len(touched)} archivos (máx {MAX_FILES})")

    for path, content in state.items():  # sintaxis básica
        try:
            if path.endswith(".json"):
                json.loads(content)
            elif path.endswith(".py"):
                compile(content, path, "exec")
        except (ValueError, SyntaxError) as e:
            errors.append(f"{path}: sintaxis inválida tras el cambio ({e})")
    return errors, per_commit


# ----------------------------------- PR --------------------------------------

def pr_body(p: Proposal) -> str:
    t = TXT.get(LANG, TXT["en"])
    changes = "\n".join(f"- `{header(c)}`" for c in p.commits)
    return (f"## {t['summary']}\n{p.pr_summary.strip()}\n\n"
            f"## {t['why']}\n{p.pr_why.strip()}\n\n"
            f"## {t['changes']}\n{changes}\n\n"
            f"## {t['test']}\n{p.pr_how_to_test.strip()}\n\n"
            f"---\n🤖 {t['footer'].format(model=MODEL)}\n")


def describe_dry_run(p: Proposal) -> str:
    lines = [f"### Propuesta (dry run)\n**PR:** `{header(p.commits[0])}`\n", pr_body(p)]
    for c in p.commits:
        lines.append(f"\n#### `{header(c)}`")
        for e in c.edits:
            lines.append(f"`{e.path}`\n```diff\n- {e.find.rstrip()}\n+ {e.replace.rstrip()}\n```")
        for nf in c.new_files:
            lines.append(f"Nuevo archivo `{nf.path}` ({len(nf.content)} caracteres)")
    return "\n".join(lines)


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        log("::error::Falta el secreto GEMINI_API_KEY")
        return 1
    from google import genai

    base = out(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if not DRY_RUN:
        sh(["gh", "label", "create", LABEL, "--color", "5319e7", "--force",
            "--description", "PR generada por auto-improver"], check=False)

    recent = recent_prs()
    open_prs = [p for p in recent if p["state"] == "OPEN"]
    if len(open_prs) >= MAX_OPEN_PRS and not DRY_RUN:
        log(f"Ya hay {len(open_prs)} PR(s) del bot abierta(s); no se crea otra.")
        return 0

    focus_key = FOCUS if FOCUS in FOCOS else list(FOCOS)[dt.date.today().isocalendar()[1] % len(FOCOS)]
    log(f"Modelo: {MODEL} · foco: {focus_key} · dry_run: {DRY_RUN}")

    files = out(["git", "ls-files"]).splitlines()
    tree = "\n".join(files[:400])
    context = build_context(files)
    rules = read_text(".auto-improver.md") or ""
    client = genai.Client(api_key=key)

    proposal, errors, per_commit = None, [], []
    for attempt in (1, 2):
        proposal = ask_gemini(client, system_prompt(FOCOS[focus_key]),
                              user_prompt(tree, context, rules, recent, errors))
        if proposal.nothing_to_do:
            log(f"Gemini no propone cambios: {proposal.reason}")
            summary(f"Sin cambios: {proposal.reason}")
            return 0
        errors, per_commit = validate(proposal)
        if not errors:
            break
        log(f"Intento {attempt}: la propuesta tiene {len(errors)} problema(s):\n- " + "\n- ".join(errors))
    if errors:
        log("::warning::La propuesta de Gemini no pasó la validación; no se crea PR.")
        summary("La propuesta no pasó la validación:\n- " + "\n- ".join(errors))
        return 0

    if DRY_RUN:
        text = describe_dry_run(proposal)
        log(text)
        summary(text)
        return 0

    branch = f"{proposal.commits[0].type}/{proposal.branch_slug}"
    if sh(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], check=False).returncode == 0:
        branch += "-" + dt.date.today().strftime("%Y%m%d")
    sh(["git", "checkout", "-b", branch])

    for c, writes in zip(proposal.commits, per_commit):
        for path, content in writes.items():
            target = ROOT / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        sh(["git", "add", "--", *writes])
        msg = ["git", "commit", "-m", header(c)]
        if c.body.strip():
            msg += ["-m", c.body.strip()]
        sh(msg)

    if TEST_CMD:
        log(f"Ejecutando tests: {TEST_CMD}")
        safe_env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GEMINI_API_KEY")}
        r = subprocess.run(TEST_CMD, shell=True, cwd=ROOT, env=safe_env)
        if r.returncode != 0:
            log("::warning::Los tests fallan con los cambios propuestos; se descarta la PR.")
            summary("Los tests fallaron con los cambios propuestos; no se creó la PR.")
            sh(["git", "checkout", "-f", base])
            sh(["git", "branch", "-D", branch])
            return 0

    sh(["git", "push", "-u", "origin", branch])
    pr = sh(["gh", "pr", "create", "--base", base, "--head", branch,
             "--title", header(proposal.commits[0]), "--body", pr_body(proposal),
             "--label", LABEL])
    log(pr.stdout.strip())
    summary(f"PR creada: {pr.stdout.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
