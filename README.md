# Auditor SEO — UTN Centro de e-Learning (v1)

Auditor automático de SEO enfocado en **indexación** y errores on-page. Revisa
una lista de páginas, explica cada hallazgo en lenguaje simple (pensado para
equipos que no son de SEO) y genera un reporte listo para volcar a tickets de Jira.

El agente **no ejecuta cambios** en el sitio: solo detecta, explica y propone.

## Qué revisa

**Indexación (la prioridad):**
- `noindex` en la página (meta robots y header X-Robots-Tag).
- Canonical (ausente, o apuntando a otra URL).
- Presencia de `robots.txt` y `sitemap.xml` (solo en modo URL).

**On-page técnico:**
- Largo del título, meta description faltante, cantidad de H1,
  imágenes sin ALT, URLs con espacios o mayúsculas.

Cada hallazgo incluye un campo "qué significa" en criollo.

## Estructura

- `src/auditor_seo/auditor.py` — la lógica de auditoría (el corazón). Se puede
  correr sola.
- `src/auditor_seo/cli.py` — orquesta la corrida sobre varias URLs, arma el
  reporte y define el resultado de la Action (verde/rojo). Es el entrypoint
  de CI (expuesto como el comando `auditor-seo`).
- `urls.txt` — lista de páginas a auditar (una por línea).
- `.github/workflows/auditoria.yml` — corre la auditoría en automático.
- `.github/workflows/ci.yml` — corre lint (ruff) y tests (pytest) en cada
  push/PR.
- `tests/` — tests de `pytest`, con `tests/fixtures/muestra_*.html` como
  páginas de ejemplo para probar sin tocar el sitio.

## Uso local

```bash
pip install -e ".[dev]"

# Una sola página (URL real o archivo local):
python -m auditor_seo.auditor https://utnba.centrodeelearning.com/algun-curso/
python -m auditor_seo.auditor tests/fixtures/muestra_curso.html

# Varias páginas desde una lista, con reporte y exit code:
auditor-seo urls.txt

# Tests y lint:
pytest
ruff check .
```

## Cómo funciona la Action

Corre sola todos los días (cron) y también se puede disparar a mano desde la
pestaña **Actions**. Al terminar deja el reporte (`report.md` y `report.json`)
como *artifact* de la corrida.

La corrida se marca en **rojo** cuando aparece un problema de **indexación** que
requiere atención (un `noindex` inesperado, un canonical a otra URL, o una página
que no responde). El resto de los hallazgos va al reporte pero no rompe el build.
Ese umbral se ajusta en `SEVERIDADES_QUE_FALLAN`, dentro de `src/auditor_seo/cli.py`.

## Roadmap (próximos pasos)

- **Crawler:** que recorra el sitio siguiendo los enlaces, en vez de una lista fija.
- **Google Search Console:** para confirmar la indexación real de Google, no solo
  la señal del HTML. Las credenciales van en los *secrets* del repo, nunca en el código.
- **Tickets de Jira:** crear el ticket automáticamente a partir de cada hallazgo.
