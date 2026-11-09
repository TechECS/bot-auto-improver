# Reglas del proyecto para auto-improver

<!-- Copia este archivo como `.auto-improver.md` en la raíz del repo y adáptalo.
     El bot lo lee en cada ejecución y lo trata como reglas obligatorias. No puede editarlo. -->

## Commits, ramas y PR
- Conventional Commits (https://www.conventionalcommits.org/es/): `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`, `perf:`, `style:`, `build:`, `ci:`.
- Ramas `tipo/descripcion-en-kebab-case`.
- Cambios pequeños y centrados en una sola cosa. Una PR = un objetivo.

## Criterios de código
- (Lenguaje, estilo, frameworks permitidos o prohibidos.)
- (Dependencias: cuándo se pueden añadir.)
- (Seguridad: escapado de datos, secretos, límites de APIs externas.)
- Los tests deben seguir pasando: `<comando de tests>`.

## No tocar
- (Archivos generados, configuración sensible, licencias, despliegue...)

## Prioridades de mejora
- (Ideas o áreas donde quieres que el bot ponga el foco.)
