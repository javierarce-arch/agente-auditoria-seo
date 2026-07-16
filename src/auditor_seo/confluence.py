"""
confluence.py — publica el dashboard de la corrida como una página de
Confluence, para que quede un historial navegable en vez de pisarse en cada
corrida (como pasaba con el dashboard viejo en GitHub Pages).

Mismo patrón que ya usa el agente hermano `agente-reporte-diario-devs`: API
REST v1 de Confluence Cloud, auth básica (email + API token), upsert por
título — una página nueva por día bajo la carpeta configurada; si se corre
dos veces el mismo día, se actualiza en vez de duplicarse.

Autenticación y configuración, todo por variable de entorno (nunca
hardcodeada): CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN,
CONFLUENCE_SPACE_KEY, CONFLUENCE_PARENT_PAGE_ID. Sin estas, la publicación
queda deshabilitada y el resto del auditor sigue funcionando igual (mismo
criterio que gsc.py y correo.py).
"""

import functools
import os
from datetime import datetime, timedelta, timezone
from html import escape

import requests
from requests.auth import HTTPBasicAuth

_VARS_REQUERIDAS = (
    "CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_SPACE_KEY", "CONFLUENCE_PARENT_PAGE_ID",
)

# Argentina no observa horario de verano: offset fijo, sin depender de tzdata.
_ART = timezone(timedelta(hours=-3))

_SEVERIDAD_LABEL = {"alta": "ALTA", "revisar": "A REVISAR", "media": "MEDIA", "baja": "BAJA"}
# Colores del macro "status" de Confluence (Grey/Red/Yellow/Green/Blue/Purple)
# — no son los mismos hex que usaba el viejo report.html, pero cumplen la
# misma función de badge visual rápido de identificar por severidad.
_SEVERIDAD_COLOR_CONFLUENCE = {"alta": "Red", "revisar": "Yellow", "media": "Grey", "baja": "Blue"}
_SEVERIDAD_EXPLICACION = {
    "alta": ("Prioridad máxima, aunque no siempre por el mismo motivo: a veces puede impedir que Google "
             "rastree o indexe la página (bloqueos, errores de acceso, canonical roto); otras veces no "
             "afecta la indexación pero daña mucho cómo se ve en los resultados (sin título, sin meta "
             "description) o es un problema de seguridad (contenido mixto)."),
    "revisar": "No es necesariamente un error — hay que confirmar si es intencional (ej. un 'noindex' a propósito).",
    "media": ("No bloquea la indexación ni es urgente, pero conviene mejorarlo (canonical faltante, "
              "poco contenido, etc.)."),
    "baja": "Detalle prolijo/técnico, de bajo impacto real (ej. URLs con mayúsculas o espacios).",
}


def credenciales_disponibles():
    return all(os.environ.get(v) for v in _VARS_REQUERIDAS)


def _build_session():
    base_url = os.environ["CONFLUENCE_BASE_URL"].rstrip("/")
    session = requests.Session()
    session.auth = HTTPBasicAuth(os.environ["CONFLUENCE_EMAIL"], os.environ["CONFLUENCE_API_TOKEN"])
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    timeout = (15, float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60")))
    session.request = functools.partial(session.request, timeout=timeout)
    return session, base_url


def _find_page(session, base_url, space_key, title):
    resp = session.get(f"{base_url}/wiki/rest/api/content",
                        params={"spaceKey": space_key, "title": title, "expand": "version"})
    resp.raise_for_status()
    resultados = resp.json().get("results", [])
    return resultados[0] if resultados else None


def _upsert_page(session, base_url, space_key, parent_id, title, contenido):
    existente = _find_page(session, base_url, space_key, title)

    if existente:
        payload = {
            "type": "page",
            "title": title,
            "version": {"number": existente["version"]["number"] + 1},
            "body": {"storage": {"value": contenido, "representation": "storage"}},
        }
        resp = session.put(f"{base_url}/wiki/rest/api/content/{existente['id']}", json=payload)
    else:
        payload = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "ancestors": [{"id": int(parent_id)}],
            "body": {"storage": {"value": contenido, "representation": "storage"}},
        }
        resp = session.post(f"{base_url}/wiki/rest/api/content", json=payload)

    resp.raise_for_status()
    return f"{base_url}/wiki{resp.json()['_links']['webui']}"


def _titulo_pagina(fecha=None):
    fecha = fecha or datetime.now(_ART).date()
    return f"Auditoría SEO — {fecha.isoformat()}"


def _badge(severidad):
    etiqueta = _SEVERIDAD_LABEL.get(severidad, severidad.upper())
    color = _SEVERIDAD_COLOR_CONFLUENCE.get(severidad, "Grey")
    return (
        f'<ac:structured-macro ac:name="status">'
        f'<ac:parameter ac:name="colour">{color}</ac:parameter>'
        f'<ac:parameter ac:name="title">{etiqueta}</ac:parameter>'
        f"</ac:structured-macro>"
    )


def _renderizar_instancia(instancia, mostrar_mensaje):
    urls = instancia["urls"]
    filas = "".join(f'<li><a href="{escape(u)}">{escape(u)}</a></li>' for u in urls)
    mensaje = f'<p>{escape(instancia["hallazgo"])}</p>' if mostrar_mensaje else ""
    return f"{mensaje}<p>Afecta {len(urls)} página(s):</p><ul>{filas}</ul>"


def _renderizar_grupo(grupo):
    instancias = grupo["instancias"]
    unico = len(instancias) == 1

    if unico:
        resumen = escape(instancias[0]["hallazgo"])
    else:
        total_paginas = sum(len(i["urls"]) for i in instancias)
        resumen = f"{len(instancias)} casos distintos ({total_paginas} página(s) en total)"

    cuerpo = "".join(_renderizar_instancia(i, mostrar_mensaje=not unico) for i in instancias)

    # <ac:structured-macro name="expand"> es el equivalente en Confluence del
    # <details>/<summary> que usaba el viejo dashboard HTML — ese no es
    # válido en formato storage.
    return (
        f'<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{_SEVERIDAD_LABEL.get(grupo["severidad"], grupo["severidad"].upper())} '
        f'— {escape(grupo["campo"])} — {resumen}</ac:parameter>'
        f"<ac:rich-text-body>"
        f'<p><em>Qué significa:</em> {escape(grupo["que_significa"])}</p>'
        f"{cuerpo}"
        f"</ac:rich-text-body>"
        f"</ac:structured-macro>"
    )


def _construir_contenido(resultados, atencion, metricas):
    # Import diferido: evita el ciclo cli -> confluence -> cli (cli.py importa
    # este módulo a nivel de archivo).
    from auditor_seo.cli import agrupar_hallazgos, agrupar_por_campo, ordenar_por_prioridad

    grupos = ordenar_por_prioridad(agrupar_por_campo(agrupar_hallazgos(resultados)))
    estado = "🔴 Requiere atención" if atencion else "🟢 Sin problemas de indexación"

    # Métricas por prioridad, arriba de todo — mismo propósito que las
    # "tarjetas" del viejo dashboard HTML.
    filas_metricas = "".join(
        f"<tr><td>{_badge(sev)}</td><td>{metricas[sev]}</td></tr>"
        for sev in ("alta", "revisar", "media", "baja")
    )
    # Leyenda: qué significa cada severidad.
    filas_leyenda = "".join(
        f"<li>{_badge(sev)} {escape(_SEVERIDAD_EXPLICACION[sev])}</li>"
        for sev in ("alta", "revisar", "media", "baja")
    )
    # Hallazgos consolidados, ordenados por prioridad (ALTA → BAJA).
    bloques = "".join(_renderizar_grupo(g) for g in grupos) or "<p>Sin hallazgos.</p>"

    return (
        f"<p><strong>{estado}</strong></p>"
        f"<table><tbody><tr><th>Severidad</th><th>Cantidad</th></tr>{filas_metricas}</tbody></table>"
        f'<p>{metricas["paginas_limpias"]} de {metricas["total_paginas"]} páginas sin hallazgos.</p>'
        f"<ul>{filas_leyenda}</ul>"
        f"{bloques}"
    )


def publicar_reporte(resultados, atencion, metricas):
    if not credenciales_disponibles():
        print("Publicación en Confluence: deshabilitada (faltan variables CONFLUENCE_*).")
        return None

    session, base_url = _build_session()
    space_key = os.environ["CONFLUENCE_SPACE_KEY"]
    parent_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]
    titulo = _titulo_pagina()
    contenido = _construir_contenido(resultados, atencion, metricas)

    return _upsert_page(session, base_url, space_key, parent_id, titulo, contenido)
