# Auditor SEO — UTN Centro de e-Learning (v1)

Auditor automático de SEO enfocado en **indexación** y errores on-page. Revisa
una lista de páginas, explica cada hallazgo en lenguaje simple (pensado para
equipos que no son de SEO) y genera un reporte listo para volcar a tickets de Jira.

El agente **no ejecuta cambios** en el sitio: solo detecta, explica y propone.

## Qué revisa

Todas las reglas son **deterministas**: se verifican leyendo código, sin IA
(eso queda para una iteración posterior). Cada hallazgo incluye un campo "qué
significa" en criollo.

**Indexación (la prioridad):**
- `noindex` en la página (meta robots y header X-Robots-Tag).
- Canonical: ausente, apuntando a otra URL, **en cadena** (A→B→C), apuntando a
  un destino que no responde 200, o combinado de forma conflictiva con `noindex`.
- Sitemap: se busca primero declarado en `robots.txt` (línea `Sitemap:`, la
  forma más confiable) y recién si no está ahí se prueba `/sitemap.xml`. Si lo
  encuentra, lo parsea y cuenta las URLs (o los sitemaps, si es un índice).
- **Códigos HTTP y redirecciones**: sigue redirecciones manualmente detectando
  cadenas largas y bucles, y marca un hallazgo de acceso si la URL final
  responde 4xx/5xx.
- **robots.txt puntual**: si la URL auditada está bloqueada por una regla
  `Disallow`, se distingue de "no indexable por noindex" — acá ni siquiera se
  puede rastrear.
- Posible bloqueo de Googlebot por el WAF (solo en modo URL; ver más abajo).
- Discrepancia entre lo que el HTML permite y lo que Google indexó de verdad
  (solo con Search Console configurado; ver más abajo).
- **Veredicto de indexabilidad**: combina todas las señales anteriores en un
  estado único por página — `Indexable`, `No indexable (noindex)`,
  `Bloqueada por robots`, `Canonicalizada` o `Error HTTP` (por orden de
  prioridad: si la página ni responde bien, el resto de las señales no importa).

**On-page técnico:**
- Largo del título, meta description faltante, cantidad de H1, imágenes sin
  ALT, URLs con espacios o mayúsculas (solo enlaces **internos** — un link a
  YouTube o Facebook con mayúsculas no es algo que podamos corregir, así que
  no se marca; el mensaje además señala qué palabra puntual tiene la
  mayúscula, ej. `tiene mayúsculas en "Categorias"`).
- **Títulos y meta descriptions duplicados entre páginas** (chequeo cruzado,
  usando todas las URLs auditadas en la corrida — típicamente las que trae el crawler).
- **Thin content**: avisa si el contenido visible de la página está por debajo
  de un umbral configurable de palabras (`--umbral-thin-content`, default 150).
- **Jerarquía de encabezados**: avisa si se saltea un nivel (ej. un `<h3>` sin
  un `<h2>` antes).
- **Contenido mixto**: avisa si una página HTTPS carga recursos (`<script>`,
  `<link>`, `<img>`) por HTTP.

## Estructura

- `src/auditor_seo/auditor.py` — la lógica de auditoría (el corazón). Se puede
  correr sola.
- `src/auditor_seo/crawler.py` — descubre las URLs de un sitio siguiendo
  enlaces internos, como alternativa a mantener `urls.txt` a mano.
- `src/auditor_seo/gsc.py` — contrasta el veredicto por HTML contra el estado
  real de indexación en Google Search Console (opcional, ver más abajo).
- `src/auditor_seo/multipagina.py` — chequeos que necesitan comparar varias
  páginas entre sí (hoy: títulos y meta descriptions duplicados).
- `src/auditor_seo/correo.py` — envío de mail de aviso por Gmail (OAuth2),
  ver "Notificaciones por mail" más abajo.
- `src/auditor_seo/cli.py` — orquesta la corrida sobre varias URLs (de
  `urls.txt` o del crawler), arma los reportes (`report.md`, `report.json`,
  `report.html`), manda las notificaciones por mail y define el resultado de
  la Action (verde/rojo). Es el entrypoint de CI (expuesto como el comando
  `auditor-seo`).
- `scripts/gmail_auth.py` — genera el `token.json` para las notificaciones
  por mail (se corre una sola vez, a mano).
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

# Con umbral de thin content personalizado (default: 150 palabras):
auditor-seo urls.txt --umbral-thin-content 100

# Tests y lint:
pytest
ruff check .
```

## Dashboard (`report.html`)

Además de `report.md` (para leer línea por línea) y `report.json` (para
procesar), `auditor-seo` genera `report.html`: un dashboard autocontenido
(sin dependencias externas, se abre con doble clic en cualquier navegador)
pensado para revisar de un vistazo qué hay que arreglar primero:

- **Métricas por prioridad** arriba de todo: cantidad de hallazgos ALTA, A
  REVISAR, MEDIA y BAJA, más cuántas páginas de las auditadas no tienen
  ningún hallazgo.
- **Ordenado por prioridad** (ALTA → BAJA), no por página como `report.md`.
- **Consolidado**: si el mismo problema aparece en varias páginas (por
  ejemplo, un link de navegación con mayúsculas presente en las 80 páginas
  del sitio), aparece una sola vez, con la cantidad de páginas afectadas y
  el listado plegable (clickeable) de cuáles son.

Cada corrida de la Action publica este archivo en GitHub Pages:
**https://javierarce-arch.github.io/agente-auditoria-seo/**. El link (no
un adjunto) es lo que se manda en las notificaciones por mail — mandar el
`.html` como adjunto no sirve, porque muchos clientes de mail no lo
renderizan y muestran el código fuente en vez de la página. Por eso el
repo es público: el contenido (mejoras técnicas de SEO) no es sensible, y
así Pages puede publicarlo gratis sin necesitar un plan pago.

## Crawler

En vez de mantener `urls.txt` a mano, `auditor-seo` puede descubrir las páginas
solo, crawleando el sitio desde una URL semilla:

```bash
auditor-seo --crawl https://utnba.centrodeelearning.com/

# Con límites personalizados (los valores de acá son los defaults):
auditor-seo --crawl https://utnba.centrodeelearning.com/ \
  --max-paginas 200 --max-profundidad 3 --delay 1
```

El crawler solo lee páginas, nunca escribe nada en el sitio, y respeta estas
reglas de seguridad:

- Solo sigue enlaces del **mismo dominio** (ignora enlaces externos).
- Respeta `robots.txt`: no entra a lo que esté en `Disallow`.
- Tiene un tope de páginas (`--max-paginas`, default 200) y de profundidad de
  enlaces a seguir (`--max-profundidad`, default 3).
- Espera `--delay` segundos (default 1) entre pedido y pedido, para no
  sobrecargar el servidor.
- Deduplica URLs ignorando la barra final y parámetros de tracking
  (`utm_*`, `gclid`, `fbclid`, etc.).
- Saltea lo que no sea HTML (PDFs, imágenes) y sigue de largo ante errores o
  timeouts en una página puntual, sin cortar la corrida.

Las URLs que descubre se auditan exactamente igual que las de `urls.txt` (mismo
reporte, mismo criterio de verde/rojo). `urls.txt` sigue siendo una opción
válida — el crawler no lo reemplaza, solo evita tener que mantenerlo a mano.

## User-Agent

Todos los pedidos HTTP (auditoría y crawler) usan un User-Agent identificable y
honesto por defecto — **no se hace pasar por un navegador**:

```
UTN-SEO-Auditor/1.0 (auditoría interna SEO)
```

Se puede cambiar con `--user-agent`, por ejemplo si un WAF todavía no tiene el
nuestro en la lista blanca:

```bash
auditor-seo urls.txt --user-agent "OtroNombre/1.0"
auditor-seo --crawl https://sitio/ --user-agent "OtroNombre/1.0"
```

### Chequeo de "posible bloqueo de Googlebot"

En modo URL, por cada página se hace un chequeo extra: se pide la misma URL una
vez con nuestro User-Agent y otra con el de Googlebot, y se comparan los
códigos de respuesta. Si la nuestra pasa (200) pero la de Googlebot no
(403, otro error, o un desafío), aparece un hallazgo de indexación de
severidad **alta**: "posible bloqueo de Googlebot por el WAF".

Importante: esto es una **señal heurística, no una prueba**. Mandar el
User-Agent de Googlebot no significa que la respuesta venga realmente del
rastreador de Google — Google se verifica por DNS inverso, no por el header
User-Agent, así que cualquiera puede mandarlo. El hallazgo lo marca como "a
revisar" y recomienda confirmarlo con Google Search Console (inspección de
URL / informe de cobertura) o con los logs del servidor.

## Google Search Console (opcional)

El HTML de una página dice si **puede** indexarse (sin `noindex`, con
canonical propio, etc. — eso ya lo calculan los chequeos de arriba). Pero no
garantiza que Google la haya indexado de verdad: puede ser percibida como
duplicada, de baja calidad, o no haber sido rastreada todavía. Conectando
Search Console, el auditor trae el estado **real** de indexación (API de
Inspección de URLs) y lo contrasta contra el veredicto del HTML.

Es totalmente opcional: **sin configurarlo, el auditor corre exactamente
igual que hoy** (con un aviso en el log de que Search Console está
deshabilitado).

### Setup

1. Instalar el extra de Search Console:
   ```bash
   pip install -e ".[gsc]"
   ```
2. Crear (o reutilizar) una **service account** de Google Cloud y descargar su
   clave en JSON. Nunca commitear ese archivo — va en `.gitignore` (o como
   *secret* en CI).
3. En Search Console, agregar esa service account como usuario (con permiso
   de lectura alcanza) de la propiedad que se quiera auditar — **Configuración
   → Usuarios y permisos → Agregar usuario**, usando el email de la service
   account (termina en `...gserviceaccount.com`).
4. Definir las variables de entorno:
   ```bash
   export GSC_CREDENTIALS_JSON=/ruta/a/la/clave-service-account.json
   export GSC_SITE_URL="https://utnba.centrodeelearning.com/"   # la propiedad, tal cual está registrada en Search Console
   ```
   `GSC_SITE_URL` también se puede pasar como `--gsc-site-url` en vez de variable de entorno.

### Uso

```bash
auditor-seo urls.txt
# o, con overrides puntuales:
auditor-seo urls.txt --gsc-site-url "https://sitio/" --gsc-max-inspecciones 50 --gsc-delay 2
```

- `--gsc-max-inspecciones` (default 100): tope de páginas a consultar contra
  Search Console **por corrida**, para no pasarse de la cuota de la API
  (~2000 consultas/día, ~600/minuto por propiedad).
- `--gsc-delay` (default 1s): espera entre inspección e inspección.
- Solo se consultan URLs reales (no archivos locales), y solo hasta el tope
  configurado — el resto de las páginas se auditan igual, solo que sin el
  contraste de Search Console.

Cada página auditada suma un bloque "Estado en Google (Search Console)" con el
`coverageState` tal cual lo devuelve Google y la fecha del último rastreo. Si
hay **discrepancia** entre el HTML y Google (el HTML permite indexar pero
Google no la indexó, o al revés), se agrega un hallazgo de indexación de
severidad **alta** explicando el motivo probable y qué revisar en Search
Console. El agente solo lee y reporta — nunca pide indexación ni cambia nada
en Search Console.

## Notificaciones por mail (opcional)

Como no toda la gente que necesita enterarse de un problema tiene acceso a
GitHub Actions, `auditor-seo` puede mandar un mail al terminar la corrida.
Usa Gmail vía OAuth2 (mecanismo XOAUTH2 con `smtplib`, sin contraseña de
aplicación) — el mismo que ya corre en producción en el agente hermano
`agente-reporte-diario-devs`. Es totalmente opcional: sin configurarlo, el
auditor corre exactamente igual que hoy.

Hay dos destinatarios con criterios distintos:

- **IT**: recibe un mail en **cada corrida**, con el resumen de métricas
  (cuenta por prioridad), el link al dashboard publicado en GitHub Pages, y
  `report.md` adjunto (para revisión offline).
- **Marketing**: recibe un mail **solo si hay al menos un hallazgo de
  prioridad ALTA** (de indexación o de on-page — más amplio que el criterio
  que usa la Action para marcarse en rojo, que solo mira indexación), con el
  link al dashboard.

El dashboard nunca se manda como adjunto: se linkea la copia publicada en
GitHub Pages (variable de entorno `REPORT_URL`, que en la Action ya viene
seteada con la URL fija del sitio). Sin `REPORT_URL` (ej. corriendo en
local), el mail se manda igual, solo que sin el link.

### Setup

1. Instalar el extra de mail:
   ```bash
   pip install -e ".[mail]"
   ```
2. Generar el token OAuth (una sola vez, a mano): bajar credenciales OAuth
   tipo "Desktop app" desde Google Cloud Console como `credentials.json` en
   la raíz del repo, y correr:
   ```bash
   python scripts/gmail_auth.py
   ```
   Esto abre el navegador para el login y deja un `token.json` local (nunca
   se commitea). **Si UTN ya tiene un `token.json` vigente para
   `agente-reporte-diario-devs` sobre la misma cuenta de Gmail, se puede
   reutilizar directamente** — usa el mismo scope (`https://mail.google.com/`).
3. Definir las variables de entorno (local) o secrets (CI):
   ```bash
   export SMTP_USER="reportes@utn.edu.ar"        # cuenta de Gmail que envía
   export SMTP_FROM="reportes@utn.edu.ar"         # default: igual a SMTP_USER
   export GMAIL_TOKEN_FILE="token.json"           # default: token.json
   export EMAIL_TO_IT="it@utn.edu.ar"             # separar con comas si son varios
   export EMAIL_TO_MARKETING="marketing@utn.edu.ar"
   ```

### Uso

```bash
auditor-seo urls.txt
```

Sin `token.json` (o sin `EMAIL_TO_IT`/`EMAIL_TO_MARKETING` seteadas), el
auditor imprime un aviso y sigue corriendo normal — igual que con Search
Console. Un fallo al mandar un mail puntual (ej. SMTP caído) tampoco rompe
la corrida ni impide el otro mail.

En CI, los mismos valores se cargan como **secrets** del repo (Settings →
Secrets and variables → Actions):

| Secret | Contenido |
|--------|-----------|
| `GMAIL_TOKEN_JSON` | El contenido completo de `token.json`. |
| `SMTP_USER` / `SMTP_FROM` | La cuenta de Gmail que envía. |
| `EMAIL_TO_IT` | Direcciones de IT (separadas por coma). |
| `EMAIL_TO_MARKETING` | Direcciones de Marketing (separadas por coma). |

El workflow reescribe `token.json` en disco al inicio de cada corrida a
partir de `GMAIL_TOKEN_JSON`. Si el token se revoca, hay que regenerarlo con
`python scripts/gmail_auth.py` localmente y actualizar el secret.

## Cómo funciona la Action

Corre sola todos los días (cron) y también se puede disparar a mano desde la
pestaña **Actions**. Al terminar deja el reporte (`report.md`, `report.json`
y `report.html`) como *artifact* de la corrida, y dispara las notificaciones
por mail descriptas arriba (si están configuradas).

La corrida se marca en **rojo** cuando aparece un problema de **indexación** que
requiere atención (un `noindex` inesperado, un canonical a otra URL, o una página
que no responde). El resto de los hallazgos va al reporte pero no rompe el build.
Ese umbral se ajusta en `SEVERIDADES_QUE_FALLAN`, dentro de `src/auditor_seo/cli.py`.
Es un criterio distinto y más angosto que el del mail a Marketing (que mira
ALTA en cualquier categoría, no solo indexación) — a propósito: lo que hace
fallar la Action es "esto puede sacar la página de Google", y lo que le
llega a Marketing es "esto amerita revisión", que es un universo más amplio.

## Roadmap (próximos pasos)

- **Search Analytics de Search Console:** clics, impresiones y posición por página
  (esta primera integración solo trae indexación, vía Inspección de URLs).
- **Tickets de Jira:** crear el ticket automáticamente a partir de cada hallazgo.
