"""
crawler.py — descubre URLs de un sitio siguiendo enlaces internos.

Reemplaza (opcionalmente) la lista fija de urls.txt: parte de una URL semilla,
sigue los <a href> del mismo dominio y devuelve la lista de páginas HTML
encontradas, lista para pasarle a auditor.cargar()/auditar() sin cambios.

El agente NO ejecuta cambios ni escribe nada en el sitio: solo lee páginas.

Diseño para poder testear sin red: el fetcher (la función que trae cada página)
es inyectable. Por defecto usa requests; los tests le pasan uno falso que lee
fixtures locales.
"""

import time
from collections import deque
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from auditor_seo.auditor import USER_AGENT_DEFAULT

try:
    import requests
except ImportError:
    requests = None

MAX_PAGINAS_DEFAULT = 200
MAX_PROFUNDIDAD_DEFAULT = 3
DELAY_DEFAULT = 1.0

# Parámetros de tracking que se ignoran al deduplicar (no cambian el contenido de la página).
TRACKING_PREFIJOS = ("utm_",)
TRACKING_CLAVES = {"gclid", "fbclid", "ref", "mc_cid", "mc_eid"}


@dataclass
class RespuestaFetch:
    status_code: int
    headers: dict  # ya en minúsculas, igual que auditor.cargar()
    texto: str


def fetcher_http(url, user_agent=USER_AGENT_DEFAULT):
    """Fetcher por defecto: trae la URL con requests. Devuelve None si falla."""
    if not requests:
        raise SystemExit("Falta 'requests' para crawlear.")
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": user_agent})
        return RespuestaFetch(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.text)
    except requests.RequestException:
        return None


def _es_tracking(clave):
    clave = clave.lower()
    return clave in TRACKING_CLAVES or any(clave.startswith(p) for p in TRACKING_PREFIJOS)


def normalizar(url):
    """Clave para deduplicar: ignora fragment, barra final y parámetros de tracking."""
    partes = urlparse(url)
    query = sorted((k, v) for k, v in parse_qsl(partes.query) if not _es_tracking(k))
    path = partes.path.rstrip("/") or "/"
    return urlunparse((partes.scheme.lower(), partes.netloc.lower(), path, "", urlencode(query), ""))


def _mismo_dominio(url, dominio):
    return urlparse(url).netloc.lower() == dominio


def _cargar_robots(semilla, fetcher):
    base = "/".join(semilla.split("/")[:3])
    rp = robotparser.RobotFileParser()
    resp = fetcher(base + "/robots.txt")
    if resp and resp.status_code == 200:
        rp.parse(resp.texto.splitlines())
    else:
        rp.parse([])  # sin robots.txt: no hay restricciones
    return rp


def crawlear(semilla, fetcher=None, max_paginas=MAX_PAGINAS_DEFAULT,
             max_profundidad=MAX_PROFUNDIDAD_DEFAULT, delay=DELAY_DEFAULT,
             user_agent=USER_AGENT_DEFAULT):
    """
    Recorre el sitio desde `semilla` siguiendo enlaces internos (mismo dominio),
    respetando robots.txt. Devuelve la lista de URLs únicas de páginas HTML
    encontradas, en el orden en que se visitaron.
    """
    fetcher = fetcher or (lambda url: fetcher_http(url, user_agent=user_agent))
    dominio = urlparse(semilla).netloc.lower()
    robots = _cargar_robots(semilla, fetcher)

    vistas = {normalizar(semilla)}
    cola = deque([(semilla, 0)])
    encontradas = []

    while cola and len(encontradas) < max_paginas:
        url, profundidad = cola.popleft()
        if not robots.can_fetch(user_agent, url):
            continue

        resp = fetcher(url)
        if delay:
            time.sleep(delay)
        if resp is None or resp.status_code != 200:
            continue
        if "html" not in resp.headers.get("content-type", "").lower():
            continue

        encontradas.append(url)

        if profundidad >= max_profundidad:
            continue

        soup = BeautifulSoup(resp.texto, "html.parser")
        for a in soup.find_all("a", href=True):
            destino = urljoin(url, a["href"])
            if not _mismo_dominio(destino, dominio):
                continue
            clave = normalizar(destino)
            if clave in vistas:
                continue
            vistas.add(clave)
            cola.append((destino, profundidad + 1))

    return encontradas
