# 🤖 Bot Auto-Improver (TechBAI)

GitHub Action que revisa un repositorio con **Gemini** (capa gratuita) y abre **una PR pequeña** con mejoras: bugs, tests, seguridad, docs, accesibilidad o funcionalidades. Los commits y la PR siguen **Conventional Commits**. Nunca hace merge: la decisión es siempre tuya.

## Cómo funciona

1. Lee el repo (árbol, código, README y `.auto-improver.md`), prioriza los archivos según su actividad reciente en Git y analiza las PRs anteriores del bot.
2. Pide a Gemini **una** mejora pequeña, en JSON estructurado, con un foco que rota cada semana.
3. Valida la propuesta: cabecera Conventional Commits, rutas permitidas, que cada `find` aparezca exactamente una vez y que JSON y Python compilen. Si falla, se lo devuelve a Gemini una vez; si vuelve a fallar, no hace nada.
4. Aplica los cambios en una rama `tipo/slug-en-kebab-case`, ejecuta los comandos de instalación y tests (se autodetectan si no se especifican) y, si pasan, abre la PR con la etiqueta `auto-improver` e incluye un indicador gráfico de riesgo (Bajo 🟢, Medio 🟡, Alto 🔴).

Si no hay nada que merezca la pena, **no crea PR**.

## Puesta en marcha

### 1. GitHub App (una sola vez)

*Organización → Settings → Developer settings → GitHub Apps → New GitHub App*

- Webhook: desactivado.
- Permisos de repositorio: **Contents**, **Pull requests** e **Issues** en *Read and write*. Issues hace falta para crear la etiqueta `auto-improver` (las etiquetas se gestionan con ese permiso).
- *Where can this app be installed*: **Any account** si vas a usarla en repos de cuentas personales; *Only on this account* si solo la usarás en repos de la organización.
- Anota el **Client ID**, genera una **clave privada** (`.pem`) e **instala la App** en los repos donde quieras el bot.

La App solo sirve para generar un token temporal de 1 hora. Con ella la PR la abre `tu-app[bot]`, el CI del repo **sí se dispara** y tú **puedes aprobarla**.

### 2. Secretos

En el repo (o como secretos de la organización, para compartirlos): `APP_CLIENT_ID` (una *variable* del repo, no es secreto), `APP_PRIVATE_KEY` (contenido completo del `.pem`; guárdala como **secreto**, nunca como variable, porque las variables se ven en claro) y `GEMINI_API_KEY` ([Google AI Studio](https://aistudio.google.com/), capa gratuita).

### 3. Workflow

Copia [`ejemplo-workflow.yml`](ejemplo-workflow.yml) en `.github/workflows/auto-improver.yml` del repo a mejorar (ya apunta a `TechECS/bot-auto-improver`). Opcionalmente copia [`ejemplo-reglas.md`](ejemplo-reglas.md) como `.auto-improver.md` con las reglas de tu proyecto.

### 4. Ajuste del repo

Con una App no necesitas tocar nada. Solo si usas el `GITHUB_TOKEN` en lugar de una App, activa en *Settings → Actions → General → Workflow permissions* «Allow GitHub Actions to create and approve pull requests».

### 5. Primera prueba

*Actions → Auto-improver → Run workflow*. Por defecto va en **dry run**: muestra la propuesta en el resumen del job sin crear nada. Cuando te convenza, ejecútalo sin dry run.

## Self-test con la App

El workflow [`self-test.yml`](.github/workflows/self-test.yml) prueba la action sobre este mismo repo (`uses: ./`) y funciona igual que el ejemplo:

- Genera con `actions/create-github-app-token` un token temporal usando la variable `APP_CLIENT_ID` y el secreto `APP_PRIVATE_KEY`, con permisos de **Contents**, **Pull requests** e **Issues** (escritura).
- Ese token se usa en el `checkout` y como `github-token`, y se pasa `app-slug` para que los commits salgan con la identidad de la App.
- El workflow solo tiene `contents: read`: todo lo que escribe lo hace el token de la App. La App debe estar instalada en este repo.
- El *dry run* está **desmarcado por defecto** (crea rama y PR); márcalo si solo quieres ver la propuesta.
- Su `test-command` compila los tres scripts y ejecuta los tests unitarios (`python -m unittest discover`).
- Además de `focus: auto`, puedes elegir `rotar` para que el foco cambie cada semana.

## Identidad en git y en la PR

Con `app-slug` (lo da `actions/create-github-app-token`), **commits y PR salen con la identidad real de la App**: autor `<slug>[bot]` y email `<id>+<slug>[bot]@users.noreply.github.com`, con su avatar enlazado. El nombre visible es el *slug* de la App (por ejemplo `techbai-bot-auto-improver[bot]`), no el nombre con espacios o dos puntos. Sin `app-slug` se usan `git-user-name` y `git-user-email`.

Los commits hechos con `git` desde Actions no salen firmados («Verified»).

## Entradas

| Entrada | Por defecto | Descripción |
|---|---|---|
| `gemini-api-key` | (obligatoria) | API key de Gemini |
| `github-token` | (obligatoria) | Token de la GitHub App (o PAT) |
| `model` | `gemini-flash-latest,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite` | Modelo o lista en orden de preferencia: si uno da 404, límite (429) o servidor saturado (5xx), prueba el siguiente |
| `probe` | `false` | Sondea cada modelo Flash con una petición mínima y publica qué responde (OK, 429, 503, 404) |
| `auto-fallback` | `true` | Si fallan todos los modelos de la lista, prueba hasta 3 modelos Flash más que tu clave tenga disponibles |
| `api-version` | `v1beta` | Versión de la API de Gemini (los alias `*-latest` solo existen en `v1beta`) |
| `language` | `es` | Idioma de commits y PR (`es`, `en`) |
| `focus` | `auto` | `auto`, `bugs`, `tests`, `seguridad`, `docs`, `accesibilidad`, `funcionalidad` |
| `dry-run` | `false` | Solo muestra la propuesta |
| `max-files` | `6` | Máximo de archivos por PR |
| `max-open-prs` | `1` | No crea otra PR si ya hay tantas abiertas |
| `protected` | | Patrones extra de archivos intocables, separados por comas |
| `test-command` | | Comando de tests (se autodetecta si no se indica); si falla, se descarta la PR |
| `install-command` | | Comando de instalación de dependencias (se autodetecta si no se indica) |
| `python-version` | `3.11` | Versión de Python del bot |
| `app-slug` | | Slug de la App: commits con su identidad real (tiene prioridad) |
| `git-user-name` / `git-user-email` | `github-actions[bot]` | Autor de los commits si no hay `app-slug` |

## Seguridad

- **Nunca hace merge** y solo mantiene abierta una PR a la vez.
- Archivos **intocables** por defecto: `.github/`, `data/`, `LICENSE*`, `CNAME`, `.env*`, claves, lockfiles y `.auto-improver.md`.
- Rechaza rutas absolutas o con `..`.
- Los tests del proyecto se ejecutan antes de abrir la PR, **sin** el token ni la API key en el entorno, y el token no queda guardado en `.git/config`.
- Con `GITHUB_TOKEN` en vez de una App, las PRs **no disparan el CI** (regla anti-bucles de GitHub).
- El token de la App dura 1 hora y solo lleva los permisos que pide el workflow (`permission-*`).
- El código del repo se envía a la API de Gemini. En la **capa gratuita de Google AI Studio, los datos pueden usarse para mejorar sus productos**: no lo uses con código privado o sensible sin revisar sus condiciones actuales.

## Si Gemini está saturado

Los errores `503 UNAVAILABLE` («high demand») son del lado de Google y suelen ser temporales, sobre todo con los modelos más nuevos. El bot reintenta una vez (10 s), pasa al siguiente modelo de la lista y, si todos fallan, prueba otros Flash disponibles. Si ninguno responde, termina con un aviso (*warning*) y **sin error**, para no poner en rojo el workflow por un fallo ajeno; la siguiente ejecución lo intentará de nuevo.

## Límites conocidos

- Contexto limitado a unos 250 000 caracteres (con truncado por archivo): en repos grandes verá solo una parte.
- Las ediciones se hacen por «buscar y reemplazar» exacto; si Gemini no copia el fragmento literalmente, la propuesta se descarta.
- La capa gratuita de Gemini tiene límites de uso y puede cambiar. Cada ejecución hace 1-2 llamadas.

## Publicar versiones

Crea un release `v1.0.0` y mantén una etiqueta móvil `v1` que apunte a la última 1.x: así los repos usan `TechECS/bot-auto-improver@v1`. Mientras pruebas, usa `@main`. Si el repo de la Action es privado, los repos de la organización solo podrán usarla si lo permites en *Settings → Actions → General → Access*; para repos de cuentas personales debe ser **público**.

## Contribuir

Commits con [Conventional Commits](https://www.conventionalcommits.org/es/). Cambios pequeños y centrados en una sola cosa.

## Licencia

Añade aquí el archivo `LICENSE` que prefieras (en tus otros proyectos usas AGPL-3.0).
