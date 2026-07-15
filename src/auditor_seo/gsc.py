"""
gsc.py — contraste con el estado REAL de indexación en Google Search Console.

El HTML de una página dice si PUEDE indexarse (sin 'noindex', con canonical
propio, etc. — eso ya lo calcula auditor.py). Pero eso no garantiza que Google
la haya indexado de verdad: puede ser percibida como duplicada, de baja
calidad, o no haber sido rastreada todavía. Este módulo consulta la API de
Inspección de URLs de Search Console para saber qué hizo Google realmente, y
compara ese estado contra el veredicto que ya calculamos a partir del HTML.

El agente NO ejecuta cambios: solo consulta y reporta.

Autenticación: service account cuya ruta llega por la variable de entorno
GSC_CREDENTIALS_JSON (nunca hardcodeada). Si no está definida, Search Console
queda deshabilitado y el resto del auditor sigue funcionando igual.

Diseño para poder testear sin red: el "inspector" (la función que consulta la
API) es inyectable — misma idea que el fetcher del crawler. `construir_inspector()`
arma el inspector real (autenticado); los tests le pasan uno falso.
"""

import os
from dataclasses import dataclass

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
except ImportError:
    service_account = None
    build = None

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

MAX_INSPECCIONES_DEFAULT = 100
DELAY_DEFAULT = 1.0


@dataclass
class EstadoGSC:
    indexada: bool  # None si no se pudo determinar a partir del coverageState
    coverage_state: str  # texto tal cual de Google, ej. "Crawled - currently not indexed"
    ultimo_rastreo: str  # None si Google todavía no rastreó la página


def credenciales_disponibles(ruta_credenciales=None):
    """True si hay una ruta de credenciales (explícita o por GSC_CREDENTIALS_JSON)."""
    return bool(ruta_credenciales or os.environ.get("GSC_CREDENTIALS_JSON"))


def _construir_cliente(ruta_credenciales=None):
    """Cliente real de la Search Console API, o None si no hay credenciales."""
    ruta_credenciales = ruta_credenciales or os.environ.get("GSC_CREDENTIALS_JSON")
    if not ruta_credenciales:
        return None
    if not service_account or not build:
        raise SystemExit(
            "GSC_CREDENTIALS_JSON está definida pero faltan las dependencias de Search "
            "Console. Instalá el extra: pip install -e '.[gsc]'"
        )
    creds = service_account.Credentials.from_service_account_file(ruta_credenciales, scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def construir_inspector(site_url, ruta_credenciales=None):
    """
    Arma el inspector inyectable: una función `(url) -> respuesta cruda de la
    API de Inspección de URLs`, ya autenticada contra la propiedad `site_url`.
    Devuelve None si no hay credenciales configuradas (Search Console deshabilitado).
    """
    cliente = _construir_cliente(ruta_credenciales)
    if cliente is None:
        return None

    def inspector(url):
        body = {"inspectionUrl": url, "siteUrl": site_url}
        return cliente.urlInspection().index().inspect(body=body).execute()

    return inspector


def _es_indexada(coverage_state):
    cs = coverage_state.lower()
    return "indexed" in cs and "not indexed" not in cs


def inspeccionar(url, inspector):
    """Consulta el estado real de indexación de `url` vía `inspector` (url) -> respuesta cruda."""
    resp = inspector(url)
    resultado = resp.get("inspectionResult") or {}
    estado_indice = resultado.get("indexStatusResult") or {}
    coverage_state = estado_indice.get("coverageState", "Desconocido")
    return EstadoGSC(
        indexada=_es_indexada(coverage_state),
        coverage_state=coverage_state,
        ultimo_rastreo=estado_indice.get("lastCrawlTime"),
    )


def estado_a_dict(estado):
    """Convierte un EstadoGSC (o None) a dict plano, para volcarlo al reporte."""
    if estado is None:
        return None
    return {
        "indexada": estado.indexada,
        "coverage_state": estado.coverage_state,
        "ultimo_rastreo": estado.ultimo_rastreo,
    }


def extraer_veredicto_html(rep):
    """Lee el veredicto de indexación que auditor.auditar() ya calculó a partir del HTML."""
    for h in rep["indexacion"]:
        if h["campo"] == "Indexación":
            return h["severidad"] == "ok"
    return True


def reconciliar(html_indexable, estado_google):
    """
    Compara lo que el HTML permite (`html_indexable`) contra lo que Google
    realmente hizo (`estado_google.indexada`). Devuelve un hallazgo de
    indexación si hay discrepancia, o None si coinciden (o si no hay estado
    de Google con el que comparar).
    """
    if estado_google is None or estado_google.indexada is None:
        return None
    if html_indexable == estado_google.indexada:
        return None

    if html_indexable:
        hallazgo = (f"El HTML permite indexar la página, pero Google no la tiene indexada "
                    f"(coverageState de Search Console: '{estado_google.coverage_state}').")
        explicacion = ("Puede ser contenido que Google percibe como duplicado o de baja calidad, "
                        "una página muy nueva que todavía no se rastreó a fondo, o algún otro motivo "
                        "que no se ve en el HTML: no es necesariamente un error técnico nuestro. Qué "
                        "hacer: revisar el detalle en Search Console (Inspección de URL) para ver el "
                        "motivo exacto, y si corresponde, pedir ahí una nueva indexación.")
    else:
        hallazgo = (f"El HTML bloquea la indexación ('noindex'), pero Google SÍ tiene la página "
                    f"indexada (coverageState de Search Console: '{estado_google.coverage_state}').")
        explicacion = ("Suele pasar cuando el 'noindex' se agregó después de que Google ya había "
                        "indexado la página, y todavía no la volvió a rastrear para notar el cambio. "
                        "Qué hacer: si el 'noindex' es intencional, pedir en Search Console que Google "
                        "vuelva a rastrear la página para que la saque del índice; si no lo es, sacar "
                        "el 'noindex'.")

    return {
        "campo": "Indexación", "severidad": "alta",
        "hallazgo": hallazgo,
        "que_significa": explicacion,
        "como_se_verifica": ("Se comparó el veredicto de indexación del HTML con el estado real en "
                              "Search Console (Inspección de URL)."),
    }
