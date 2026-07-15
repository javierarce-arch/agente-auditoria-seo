from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from auditor_seo.crawler import RespuestaFetch, crawlear, fetcher_http, normalizar

FIXTURES = Path(__file__).parent / "fixtures" / "mini_sitio"
BASE = "https://ejemplo.test"

RUTA_A_ARCHIVO = {
    "/": "index.html",
    "/curso-python/": "curso.html",
    "/contacto/": "contacto.html",
    "/privado/": "privado.html",
    "/archivo.pdf": "archivo.pdf",
    "/robots.txt": "robots.txt",
}

CONTENT_TYPE_POR_SUFIJO = {
    ".html": "text/html; charset=utf-8",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
}


def fetcher_falso(url):
    """Simula requests.get contra el mini-sitio de fixtures, sin red."""
    ruta = urlparse(url).path
    nombre = RUTA_A_ARCHIVO.get(ruta)
    if nombre is None:
        return None
    archivo = FIXTURES / nombre
    texto = archivo.read_text(encoding="utf-8")
    content_type = CONTENT_TYPE_POR_SUFIJO[archivo.suffix]
    return RespuestaFetch(200, {"content-type": content_type}, texto)


def test_crawler_sigue_enlaces_internos_y_deduplica():
    encontradas = crawlear(f"{BASE}/", fetcher=fetcher_falso, delay=0)

    assert encontradas.count(f"{BASE}/") == 1
    assert f"{BASE}/curso-python/" in encontradas
    assert f"{BASE}/contacto/" in encontradas


def test_crawler_no_sale_del_dominio():
    encontradas = crawlear(f"{BASE}/", fetcher=fetcher_falso, delay=0)

    assert not any("externo.test" in u for u in encontradas)


def test_crawler_respeta_robots_txt():
    encontradas = crawlear(f"{BASE}/", fetcher=fetcher_falso, delay=0)

    assert f"{BASE}/privado/" not in encontradas


def test_crawler_saltea_no_html():
    encontradas = crawlear(f"{BASE}/", fetcher=fetcher_falso, delay=0)

    assert not any(u.endswith(".pdf") for u in encontradas)


def test_crawler_respeta_tope_de_paginas():
    encontradas = crawlear(f"{BASE}/", fetcher=fetcher_falso, delay=0, max_paginas=1)

    assert len(encontradas) == 1
    assert encontradas == [f"{BASE}/"]


def test_crawler_respeta_tope_de_profundidad():
    # Con profundidad 0 se audita la semilla pero no se siguen sus enlaces.
    encontradas = crawlear(f"{BASE}/", fetcher=fetcher_falso, delay=0, max_profundidad=0)

    assert encontradas == [f"{BASE}/"]


def test_normalizar_ignora_barra_final_y_tracking():
    assert normalizar(f"{BASE}/curso/") == normalizar(f"{BASE}/curso")
    assert normalizar(f"{BASE}/curso?utm_source=newsletter") == normalizar(f"{BASE}/curso")
    assert normalizar(f"{BASE}/curso?gclid=abc123") == normalizar(f"{BASE}/curso")


def test_normalizar_distingue_paginas_distintas():
    assert normalizar(f"{BASE}/curso/") != normalizar(f"{BASE}/contacto/")


def test_fetcher_http_propaga_user_agent():
    respuesta_falsa = MagicMock(status_code=200, text="<html></html>", headers={"content-type": "text/html"})
    with patch("auditor_seo.crawler.requests.get", return_value=respuesta_falsa) as mock_get:
        fetcher_http(f"{BASE}/", user_agent="MiBot/1.0")

    assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "MiBot/1.0"


def test_crawlear_propaga_user_agent_al_fetcher_por_defecto():
    respuesta_falsa = MagicMock(status_code=404, text="", headers={"content-type": "text/plain"})
    with patch("auditor_seo.crawler.requests.get", return_value=respuesta_falsa) as mock_get:
        # Alcanza con que arranque el crawl (robots.txt + semilla) para verificar el header.
        crawlear(f"{BASE}/", delay=0, user_agent="MiBot/1.0")

    user_agents_usados = {c.kwargs["headers"]["User-Agent"] for c in mock_get.call_args_list}
    assert user_agents_usados == {"MiBot/1.0"}
