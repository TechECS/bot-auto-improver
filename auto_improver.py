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
# Única fuente de la lista de modelos por defecto (action.yml y los workflows la dejan vacía).
DEFAULT_MODELS = "gemini-flash-latest,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
MODEL = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODELS
MODELS = [m.strip() for m in MODEL.split(",") if m.strip()]  # cadena de fallback
USED_MODEL = MODELS[0]
AUTO_FALLBACK = os.environ.get("AI_AUTO_FALLBACK", "true").lower() == "true"
PROBE = os.environ.get("AI_PROBE", "false").lower() == "true"
PROBE_PAUSE = float(os.environ.get("AI_PROBE_PAUSE", "6"))
TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_MS", "90000"))
API_VERSION = os.environ.get("GEMINI_API_VERSION", "v1beta")  # los alias *-latest solo existen en v1beta
LANG = os.environ.get("AI_LANGUAGE", "es")
MAX_CHARS = int(os.environ.get("AI_MAX_CONTEXT_CHARS", "250000"))
MAX_FILE_CHARS = int(os.environ.get("AI_MAX_FILE_CHARS", "80000"))
MAX_FILES = int(os.environ.get("AI_MAX_FILES", "8"))
MAX_OPEN_PRS = int(os.environ.get("AI_MAX_OPEN_PRS", "1"))
MAX_COMMITS = 3
TEST_CMD = os.environ.get("AI_TEST_COMMAND", "").strip()
FOCUS = os.environ.get("AI_FOCUS", "auto")  # auto = funcionalidad; "rotar" = rota cada semana
ALLOW_NOTHING = os.environ.get("AI_ALLOW_NOTHING", "false").lower() == "true"  # permitir «nada que hacer»
IDEATE = os.environ.get("AI_IDEATE", "true").lower() == "true"  # fase previa de ideas (modo funcionalidad)
NUM_IDEAS = int(os.environ.get("AI_NUM_IDEAS", "5"))
MAX_IDEA_TRIES = int(os.environ.get("AI_MAX_IDEA_TRIES", "2"))  # ideas que se intentan implementar
EXTRA_CONTEXT = os.environ.get("AI_CONTEXT", "").strip()  # contexto libre del propietario
MAX_OUTPUT_TOKENS = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "32768"))
TEMPERATURE = float(os.environ.get("AI_TEMPERATURE", "0.5"))
FEATURE_FOCUS = "funcionalidad"
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
    "funcionalidad": "una funcionalidad NUEVA y completa que haga el proyecto más potente y útil "
                     "(más opciones, automatización, informes, integraciones, herramientas de uso interno, "
                     "mejor experiencia), inspirada en lo que ofrecen proyectos open source populares del mismo ámbito",
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
    "es": {"inspired": "Inspiración", "ideas": "Otras ideas para próximas PR", "summary": "Resumen", "why": "Por qué", "changes": "Cambios", "test": "Cómo probar",
           "footer": "PR generada automáticamente por auto-improver con Gemini (`{model}`). "
                     "Revísala antes de fusionar."},
    "en": {"inspired": "Inspiration", "ideas": "Other ideas for future PRs", "summary": "Summary", "why": "Why", "changes": "Changes", "test": "How to test",
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


class Idea(BaseModel):
    title: str
    summary: str
    inspiration: str
    value: str
    effort: str


class Ideation(BaseModel):
    project_summary: str
    ideas: list[Idea]


class Proposal(BaseModel):
    nothing_to_do: bool
    reason: str
    inspiration: str
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

def system_prompt(focus_desc: str, feature: bool = False) -> str:
    lang = {"es": "español", "en": "inglés"}.get(LANG, LANG)
    if feature:
        task = (f"Tu tarea: implementar UNA funcionalidad nueva, completa y útil (no un retoque cosmético), "
                f"centrada en: {focus_desc}.")
    else:
        task = f"Tu tarea: proponer UNA mejora pequeña, segura y verificable, centrada en: {focus_desc}."
    if feature and not ALLOW_NOTHING:
        r10 = ("10. nothing_to_do debe ser false: siempre se puede añadir valor. Si la idea es grande, entrega una primera "
               "versión funcional y acotada (MVP) que quepa en los límites. Solo si es materialmente imposible, "
               "devuelve nothing_to_do=true con la razón.")
        r4 = (f"4. Máximo {MAX_FILES} archivos distintos en total. Evita refactors masivos y cambios de estilo gratuitos, "
              "pero SÍ puedes crear módulos o archivos nuevos si la funcionalidad lo necesita.")
    else:
        r10 = ("10. Si no hay nada que merezca de verdad la pena, devuelve nothing_to_do=true con la razón. "
               "Es mejor no hacer nada que generar ruido.")
        r4 = f"4. Máximo {MAX_FILES} archivos distintos en total. Cambios mínimos: nada de refactors masivos ni cambios de estilo gratuitos."
    extra = ""
    if feature:
        extra = ("\n14. El primer commit debe ser de tipo feat. La funcionalidad debe ser compatible hacia atrás: "
                 "opt-in o con valores por defecto que mantengan el comportamiento actual.")
    return f"""Eres un ingeniero de software senior que mantiene este repositorio con mucho cuidado.
{task}

REGLAS ESTRICTAS
1. Conventional Commits (https://www.conventionalcommits.org/es/). type debe ser uno de: {', '.join(TYPES)}. scope es opcional, en minúsculas y kebab-case (módulo o área, p. ej. geocodificacion). Sin '!' ni BREAKING CHANGE.
2. description: en {lang}, modo imperativo, empieza en minúscula, sin punto final. La cabecera completa 'type(scope): description' mide como máximo {HEADER_MAX} caracteres.
3. Un commit = un cambio lógico y autocontenido. Máximo {MAX_COMMITS} commits; el principal va primero. body es opcional (explica el porqué).
{r4}
5. edits: 'find' debe ser un fragmento EXACTO (espacios, tabuladores y saltos de línea idénticos) y ÚNICO en el archivo actual; incluye líneas de contexto suficientes. 'replace' es el texto nuevo. Edita solo partes visibles; nunca archivos [solo lectura] ni zonas [TRUNCADO] omitidas.
6. new_files solo para archivos que no existan (por ejemplo módulos o tests nuevos).
7. Respeta al pie de la letra el README, las reglas de .auto-improver.md y el contexto del propietario. No añadas dependencias externas nuevas (usa la librería estándar o lo que el proyecto ya usa), no toques claves ni secretos, no edites archivos generados.
8. Si cambias comportamiento, añade o ajusta tests cuando el proyecto ya tenga tests o puedas escribirlos con la librería estándar. Los tests existentes deben seguir pasando.
9. No repitas ni solapes las PRs previas de la lista.
{r10}
11. pr_summary, pr_why y pr_how_to_test en {lang}, claros y concretos. Campos sin uso: cadena vacía o lista vacía.
12. branch_slug: 2 a 5 palabras en kebab-case, solo ASCII en minúsculas y números, máximo 40 caracteres.
13. inspiration: proyecto(s) open source REAL(es) o patrón conocido del que sale la idea y qué se toma exactamente. Si es original, escribe 'original'. No inventes proyectos ni funciones.{extra}"""


def ideation_system() -> str:
    lang = {"es": "español", "en": "inglés"}.get(LANG, LANG)
    return f"""Eres un product engineer senior con mucha experiencia en proyectos open source.
Tu tarea: entender a fondo qué hace este repositorio y proponer {NUM_IDEAS} funcionalidades NUEVAS que lo hagan claramente más potente.

CÓMO PENSAR
1. project_summary: qué hace el proyecto, para quién (incluido el uso interno de equipos técnicos si procede), su stack, su estructura y las carencias que ves.
2. Recuerda proyectos open source populares y maduros de la misma categoría o adyacentes y qué ofrecen (funciones, opciones de configuración, estructura, flujos de trabajo, informes, integraciones) que este proyecto no tiene.
3. Propón ideas concretas y distintas entre sí: funciones nuevas, automatizaciones, opciones de configuración, informes o resúmenes, integraciones, mejor observabilidad, herramientas para uso interno.

REGLAS
- Cada idea debe poder implementarse como un MVP funcional en como máximo {MAX_FILES} archivos, sin dependencias externas nuevas y sin romper lo existente.
- Nada de refactors, estilo ni documentación pura: solo funcionalidad real.
- inspiration: nombra proyectos open source REALES y qué se toma de ellos. No inventes proyectos ni funciones: si no estás seguro, escribe 'patrón común en herramientas del sector'.
- No repitas ni solapes las PRs previas ni lo que el proyecto ya hace.
- Ordena de mayor a menor relación valor/esfuerzo: la primera es la recomendada. effort: bajo, medio o alto.
- Respeta las reglas del proyecto (.auto-improver.md) y el contexto del propietario.
- Escribe en {lang}."""


def user_prompt(tree: str, context: str, rules: str, recent: list[dict], errors: list[str],
                extra: str = "", idea: Idea | None = None) -> str:
    prev = "\n".join(f"- [{p['state']}] {p['title']}" for p in recent) or "(ninguna)"
    msg = (f"## Reglas del proyecto (.auto-improver.md)\n{rules or '(no hay)'}\n\n"
           f"## Contexto del propietario\n{extra or '(no hay)'}\n\n"
           f"## PRs previas del bot (no repetir)\n{prev}\n\n"
           f"## Árbol del repositorio\n{tree}\n\n"
           f"## Contenido de los archivos\n{context}\n")
    if idea is not None:
        msg += (f"\n## FUNCIONALIDAD A IMPLEMENTAR (elegida en la fase de ideas)\n"
                f"**{idea.title}**: {idea.summary}\nInspiración: {idea.inspiration}\n"
                f"Valor: {idea.value}\nEsfuerzo estimado: {idea.effort}\n"
                "Impleméntala como MVP funcional dentro de los límites.\n")
    if errors:
        msg += ("\n## ERRORES DE TU PROPUESTA ANTERIOR (corrígelos en la nueva propuesta)\n"
                + "\n".join(f"- {e}" for e in errors))
    return msg


def list_models(client) -> list[str]:
    """Modelos con generateContent disponibles para la clave (para diagnosticar un 404)."""
    names: list[str] = []
    try:
        for m in client.models.list():
            if "generateContent" in (getattr(m, "supported_actions", None) or []):
                names.append(str(m.name).replace("models/", ""))
    except Exception as e:  # noqa: BLE001
        log(f"No se pudo listar los modelos: {e}")
    return sorted(names)


def run_probe(client) -> int:
    """Prueba cada modelo Flash con una petición mínima y publica una tabla de estado."""
    from google.genai import types

    names = list(dict.fromkeys(MODELS + flash_candidates(list_models(client), [])))
    rows = []
    for m in names:
        t0 = time.time()
        try:
            client.models.generate_content(
                model=m, contents="Responde solo con la palabra: ok",
                config=types.GenerateContentConfig(max_output_tokens=64, temperature=0))
            status = "✅ OK"
        except Exception as e:  # noqa: BLE001
            mm = re.search(r"\b([45]\d\d)\b\s+([A-Z_]+)", str(e))
            status = f"❌ {mm.group(1)} {mm.group(2)}" if mm else f"❌ {e.__class__.__name__}"
        rows.append(f"| `{m}` | {status} | {time.time() - t0:.1f}s |")
        log(rows[-1])
        time.sleep(PROBE_PAUSE)
    text = ("### Sondeo de modelos\n\n| Modelo | Estado | Tiempo |\n|---|---|---|\n" + "\n".join(rows)
            + "\n\n✅ responde con tu clave ahora · 429 límite de uso · 503/504 saturado · 404/403 no disponible para tu clave.")
    log(text)
    summary(text)
    return 0


class GeminiBusy(RuntimeError):
    """Ningún modelo pudo responder (saturación, límite de uso o modelos no disponibles)."""


SKIP_ERRORS = ("404", "NOT_FOUND", "403", "PERMISSION_DENIED")
TRANSIENT_ERRORS = ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE")
NOT_TEXT_MODELS = ("image", "tts", "live", "audio", "embedding", "robotics", "computer", "customtools", "exp")


FLASH_RE = re.compile(r"^gemini-(\d+(?:\.\d+)?)-flash(-lite)?(-preview[\w-]*)?$")


def flash_candidates(available: list[str], exclude: list[str]) -> list[str]:
    """Modelos Flash de texto, ordenados: estables antes que preview, normales antes que lite, versión más alta primero."""
    rows = []
    for m in available:
        mm = FLASH_RE.match(m)
        if mm and m not in exclude and not any(x in m for x in NOT_TEXT_MODELS):
            rows.append((bool(mm.group(3)), bool(mm.group(2)), -float(mm.group(1)), m))
    return [r[3] for r in sorted(rows)]


def try_model(client, model: str, system: str, prompt: str, lenient: bool, schema, temperature: float) -> str | None:
    """Devuelve el texto de la respuesta o None si hay que pasar al siguiente modelo."""
    from google.genai import types

    for attempt in (1, 2):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=temperature,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
            return resp.text
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if any(t in msg for t in SKIP_ERRORS):
                log(f"{model}: no disponible o sin permiso ({e.__class__.__name__}: {msg[:200]!r}); pruebo el siguiente.")
                return None
            if not any(t in msg for t in TRANSIENT_ERRORS):
                if lenient:
                    log(f"{model}: {e.__class__.__name__}: {msg[:200]!r}; pruebo el siguiente.")
                    return None
                raise
            kind = "límite de uso (429)" if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) else "servidor saturado (5xx)"
            if attempt == 1:
                log(f"{model}: {kind}; reintento en 10s")
                time.sleep(10)
            else:
                log(f"{model}: {kind} persistente; pruebo el siguiente.")
    return None


def _parsed(client, model: str, system: str, prompt: str, lenient: bool, schema, temperature: float):
    """Llama a un modelo y valida la respuesta contra el esquema; None si hay que pasar al siguiente."""
    global USED_MODEL

    text = try_model(client, model, system, prompt, lenient, schema, temperature)
    if text is None:
        return None
    try:
        obj = schema.model_validate_json(text)
    except ValueError as e:  # JSON truncado o fuera de esquema (ValidationError hereda de ValueError)
        log(f"{model}: respuesta no válida ({str(e)[:150]!r}); pruebo el siguiente.")
        return None
    USED_MODEL = model
    log(f"Respuesta obtenida con {model}")
    return obj


def ask_gemini(client, system: str, prompt: str, schema=Proposal, temperature: float | None = None):
    """Prueba los modelos configurados y, si todos fallan, otros Flash que la clave tenga disponibles."""
    temp = TEMPERATURE if temperature is None else temperature
    tried: list[str] = []
    for model in MODELS:
        tried.append(model)
        obj = _parsed(client, model, system, prompt, False, schema, temp)
        if obj is not None:
            return obj

    available = list_models(client)
    if AUTO_FALLBACK:
        extra = flash_candidates(available, tried)[:4]
        if extra:
            log(f"Los modelos configurados fallaron; pruebo otros disponibles: {', '.join(extra)}")
        for model in extra:
            obj = _parsed(client, model, system, prompt, True, schema, temp)
            if obj is not None:
                return obj

    raise GeminiBusy("Ningún modelo respondió. Disponibles para tu clave: "
                     + (", ".join(available[:30]) or "(no se pudo obtener la lista)"))


# -------------------------------- validación ---------------------------------

HEADER_RE = re.compile(r"^(" + "|".join(TYPES) + r")(\([a-z0-9._-]+\))?: \S.*$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate(p: Proposal, feature: bool = False) -> tuple[list[str], list[dict[str, str]]]:
    """Devuelve (errores, cambios por commit). Simula la aplicación sin tocar el disco."""
    errors: list[str] = []
    if not SLUG_RE.match(p.branch_slug) or len(p.branch_slug) > 40:
        errors.append(f"branch_slug no válido: {p.branch_slug!r}")
    if not (1 <= len(p.commits) <= MAX_COMMITS):
        errors.append(f"debe haber entre 1 y {MAX_COMMITS} commits")
    if not p.pr_summary.strip() or not p.pr_why.strip() or not p.pr_how_to_test.strip():
        errors.append("pr_summary, pr_why y pr_how_to_test no pueden estar vacíos")

    if feature and not any(c.type == "feat" for c in p.commits):
        errors.append("en modo funcionalidad al menos un commit debe ser de tipo feat")

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

def pr_body(p: Proposal, others: list[Idea] | None = None) -> str:
    t = TXT.get(LANG, TXT["en"])
    changes = "\n".join(f"- `{header(c)}`" for c in p.commits)
    inspired = f"## {t['inspired']}\n{p.inspiration.strip()}\n\n" if p.inspiration.strip() else ""
    more = ""
    if others:
        more = f"## {t['ideas']}\n" + "\n".join(f"- **{i.title}**: {i.summary}" for i in others) + "\n\n"
    return (f"## {t['summary']}\n{p.pr_summary.strip()}\n\n"
            f"## {t['why']}\n{p.pr_why.strip()}\n\n"
            f"{inspired}"
            f"## {t['changes']}\n{changes}\n\n"
            f"## {t['test']}\n{p.pr_how_to_test.strip()}\n\n"
            f"{more}"
            f"---\n🤖 {t['footer'].format(model=USED_MODEL)}\n")


def describe_ideas(project_summary: str, ideas: list[Idea]) -> str:
    rows = "\n".join(f"{n}. **{i.title}** (esfuerzo: {i.effort}) — {i.summary}\n"
                     f"   - Inspiración: {i.inspiration}\n   - Valor: {i.value}"
                     for n, i in enumerate(ideas, 1))
    return f"### Análisis del proyecto\n{project_summary}\n\n### Ideas de funcionalidades\n{rows}\n"


def describe_dry_run(p: Proposal, others: list[Idea] | None = None) -> str:
    lines = [f"### Propuesta (dry run)\n**Modelo:** `{USED_MODEL}`\n**PR:** `{header(p.commits[0])}`\n", pr_body(p, others)]
    for c in p.commits:
        lines.append(f"\n#### `{header(c)}`")
        for e in c.edits:
            lines.append(f"`{e.path}`\n```diff\n- {e.find.rstrip()}\n+ {e.replace.rstrip()}\n```")
        for nf in c.new_files:
            lines.append(f"Nuevo archivo `{nf.path}` ({len(nf.content)} caracteres)")
    return "\n".join(lines)


def resolve_focus() -> str:
    if FOCUS in FOCOS:
        return FOCUS
    if FOCUS == "rotar":  # comportamiento antiguo: un foco distinto cada semana
        return list(FOCOS)[dt.date.today().isocalendar()[1] % len(FOCOS)]
    return FEATURE_FOCUS  # auto: siempre funcionalidades nuevas


def propose(client, sysmsg: str, tree: str, context: str, rules: str, recent: list[dict],
            idea: Idea | None, feature: bool) -> tuple[Proposal | None, list[str], list[dict[str, str]], str]:
    """Pide una propuesta (2 intentos, corrigiendo errores). Devuelve (propuesta, errores, cambios, razón_si_nada)."""
    errors: list[str] = []
    proposal, per_commit = None, []
    for attempt in (1, 2):
        proposal = ask_gemini(client, sysmsg, user_prompt(tree, context, rules, recent, errors, EXTRA_CONTEXT, idea))
        if proposal.nothing_to_do:
            if feature and not ALLOW_NOTHING and attempt == 1:
                log("Gemini devolvió nothing_to_do; insisto: en este modo debe entregar un MVP.")
                errors = ["nothing_to_do=true no está permitido: entrega una primera versión (MVP) de la funcionalidad."]
                continue
            return None, [], [], proposal.reason
        errors, per_commit = validate(proposal, feature)
        if not errors:
            return proposal, [], per_commit, ""
        log(f"Intento {attempt}: la propuesta tiene {len(errors)} problema(s):\n- " + "\n- ".join(errors))
    return proposal, errors, per_commit, ""


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        log("::error::Falta el secreto GEMINI_API_KEY")
        return 1
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key, http_options=types.HttpOptions(api_version=API_VERSION, timeout=TIMEOUT_MS))
    if PROBE:
        return run_probe(client)

    base = out(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if not DRY_RUN:
        sh(["gh", "label", "create", LABEL, "--color", "5319e7", "--force",
            "--description", "PR generada por auto-improver"], check=False)

    recent = recent_prs()
    open_prs = [p for p in recent if p["state"] == "OPEN"]
    if len(open_prs) >= MAX_OPEN_PRS and not DRY_RUN:
        log(f"Ya hay {len(open_prs)} PR(s) del bot abierta(s); no se crea otra.")
        return 0

    focus_key = resolve_focus()
    feature = focus_key == FEATURE_FOCUS
    log(f"Modelos (en orden): {', '.join(MODELS)} · foco: {focus_key} · ideas: {feature and IDEATE} · dry_run: {DRY_RUN}")

    files = out(["git", "ls-files"]).splitlines()
    tree = "\n".join(files[:400])
    context = build_context(files)
    rules = read_text(".auto-improver.md") or ""
    sysmsg = system_prompt(FOCOS[focus_key], feature)

    ideas: list[Idea] = []
    try:
        if feature and IDEATE:  # fase 1: entender el proyecto y proponer ideas
            ideation = ask_gemini(client, ideation_system(),
                                  user_prompt(tree, context, rules, recent, [], EXTRA_CONTEXT),
                                  Ideation, temperature=1.0)
            ideas = ideation.ideas[:NUM_IDEAS]
            text = describe_ideas(ideation.project_summary, ideas)
            log(text)
            summary(text)

        # fase 2: implementar la mejor idea (si falla la validación, se prueba la siguiente)
        proposal, errors, per_commit, nothing, chosen = None, [], [], "", None
        for idea in (ideas[:MAX_IDEA_TRIES] or [None]):
            if idea is not None:
                log(f"Implementando idea: {idea.title}")
            proposal, errors, per_commit, nothing = propose(client, sysmsg, tree, context, rules, recent, idea, feature)
            if proposal is not None and not errors:
                chosen = idea
                break
    except GeminiBusy as e:
        log(f"::warning::Gemini no pudo responder ahora: {e}")
        summary(f"Gemini no pudo responder ahora (saturación o límite). No se creó nada.\n\n{e}")
        return 0

    if proposal is None:
        log(f"Gemini no propone cambios: {nothing}")
        summary(f"Sin cambios: {nothing}")
        return 0
    if errors:
        log("::warning::La propuesta de Gemini no pasó la validación; no se crea PR.")
        summary("La propuesta no pasó la validación:\n- " + "\n- ".join(errors))
        return 0

    others = [i for i in ideas if i is not chosen]

    if DRY_RUN:
        text = describe_dry_run(proposal, others)
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
             "--title", header(proposal.commits[0]), "--body", pr_body(proposal, others),
             "--label", LABEL])
    log(pr.stdout.strip())
    summary(f"PR creada: {pr.stdout.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
