from pathlib import Path

from bs4 import BeautifulSoup

from auditor_seo import multipagina
from auditor_seo.auditor import (
    _es_enlace_externo,
    _tokens_con_mayuscula,
    calcular_veredicto,
    chequear_bloqueo_robots,
    chequear_canonical_avanzado,
    chequear_contenido_mixto,
    chequear_estado_http,
    chequear_jerarquia_encabezados,
    chequear_thin_content,
    contar_urls_sitemap,
    extraer_sitemaps_de_robots,
    seguir_redirecciones,
)

FIXTURES = Path(__file__).parent / "fixtures" / "reglas"
MINI_SITIO = Path(__file__).parent / "fixtures" / "mini_sitio"


# ============== Regla 1: sitemap declarado en robots.txt ==============

def test_extraer_sitemaps_de_robots():
    texto = (FIXTURES / "robots_con_sitemap.txt").read_text(encoding="utf-8")

    sitemaps = extraer_sitemaps_de_robots(texto)

    assert sitemaps == ["https://sitio.test/sitemap.xml"]


def test_extraer_sitemaps_de_robots_sin_declaracion():
    assert extraer_sitemaps_de_robots("User-agent: *\nDisallow: /admin/\n") == []


def test_contar_urls_sitemap():
    texto_xml = (FIXTURES / "sitemap.xml").read_text(encoding="utf-8")

    assert contar_urls_sitemap(texto_xml) == 3


def test_contar_urls_sitemap_indice():
    indice = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<sitemap><loc>https://sitio.test/sitemap-cursos.xml</loc></sitemap>'
        '<sitemap><loc>https://sitio.test/sitemap-blog.xml</loc></sitemap>'
        '</sitemapindex>'
    )

    assert contar_urls_sitemap(indice) == 2


def test_contar_urls_sitemap_invalido():
    assert contar_urls_sitemap("esto no es XML") is None


# ============== Regla 2: códigos HTTP y cadenas de redirección ==============

def test_redireccion_en_cadena_se_detecta():
    cadena_fake = {
        "https://sitio.test/a": (301, "https://sitio.test/b"),
        "https://sitio.test/b": (301, "https://sitio.test/c"),
        "https://sitio.test/c": (200, None),
    }

    def fetcher(url, user_agent):
        return cadena_fake[url]

    resultado = seguir_redirecciones("https://sitio.test/a", "Bot/1.0", fetcher)

    assert resultado["cadena"] == ["https://sitio.test/a", "https://sitio.test/b", "https://sitio.test/c"]
    assert resultado["status_final"] == 200
    assert resultado["bucle"] is False


def test_bucle_de_redirecciones_se_detecta():
    def fetcher(url, user_agent):
        siguiente = "https://sitio.test/b" if url.endswith("/a") else "https://sitio.test/a"
        return 302, siguiente

    resultado = seguir_redirecciones("https://sitio.test/a", "Bot/1.0", fetcher)

    assert resultado["bucle"] is True


def test_chequear_estado_http_reporta_cadena_larga():
    cadena_fake = {
        "https://sitio.test/a": (301, "https://sitio.test/b"),
        "https://sitio.test/b": (301, "https://sitio.test/c"),
        "https://sitio.test/c": (200, None),
    }

    def fetcher(url, user_agent):
        return cadena_fake[url]

    hallazgos, status_final = chequear_estado_http("https://sitio.test/a", "Bot/1.0", fetcher)

    assert status_final == 200
    assert any(h["campo"] == "Redirecciones" for h in hallazgos)


def test_chequear_estado_http_reporta_error_4xx():
    def fetcher(url, user_agent):
        return 404, None

    hallazgos, status_final = chequear_estado_http("https://sitio.test/no-existe", "Bot/1.0", fetcher)

    assert status_final == 404
    assert any(h["campo"] == "Acceso" and h["severidad"] == "alta" for h in hallazgos)


def test_chequear_estado_http_sin_problemas():
    def fetcher(url, user_agent):
        return 200, None

    hallazgos, status_final = chequear_estado_http("https://sitio.test/", "Bot/1.0", fetcher)

    assert status_final == 200
    assert hallazgos == []


# ============== Regla 3: bloqueo por robots.txt ==============

def test_pagina_bloqueada_por_robots():
    texto_robots = (MINI_SITIO / "robots.txt").read_text(encoding="utf-8")

    hallazgo, bloqueada = chequear_bloqueo_robots("https://ejemplo.test/privado/", "Bot/1.0", texto_robots)

    assert bloqueada is True
    assert hallazgo["campo"] == "robots.txt"
    assert hallazgo["severidad"] == "alta"


def test_pagina_no_bloqueada_por_robots():
    texto_robots = (MINI_SITIO / "robots.txt").read_text(encoding="utf-8")

    hallazgo, bloqueada = chequear_bloqueo_robots("https://ejemplo.test/contacto/", "Bot/1.0", texto_robots)

    assert bloqueada is False
    assert hallazgo is None


def test_sin_robots_txt_no_bloquea():
    hallazgo, bloqueada = chequear_bloqueo_robots("https://ejemplo.test/", "Bot/1.0", None)

    assert bloqueada is False
    assert hallazgo is None


# ============== Regla 4: canonical más fino ==============

def test_canonical_en_cadena():
    texto_b = (FIXTURES / "canonical_b.html").read_text(encoding="utf-8")

    def fetcher_estado(url, user_agent):
        return 200

    def fetcher_canonical(url, user_agent):
        soup = BeautifulSoup(texto_b, "html.parser")
        return soup.find("link", attrs={"rel": "canonical"}).get("href")

    hallazgos = chequear_canonical_avanzado(
        "https://sitio.test/pagina-a/", "https://sitio.test/pagina-b/", noindex=False,
        user_agent="Bot/1.0", fetcher_estado=fetcher_estado, fetcher_canonical=fetcher_canonical,
    )

    assert any(h["campo"] == "Canonical" and "cadena" in h["hallazgo"] for h in hallazgos)


def test_canonical_destino_no_responde_200():
    def fetcher_estado(url, user_agent):
        return 404

    hallazgos = chequear_canonical_avanzado(
        "https://sitio.test/pagina-a/", "https://sitio.test/pagina-b/", noindex=False,
        user_agent="Bot/1.0", fetcher_estado=fetcher_estado,
    )

    assert any(h["campo"] == "Canonical" and "no responde 200" in h["hallazgo"] for h in hallazgos)


def test_noindex_mas_canonical_es_conflictivo():
    def fetcher_estado(url, user_agent):
        return 200

    def fetcher_canonical(url, user_agent):
        return None

    hallazgos = chequear_canonical_avanzado(
        "https://sitio.test/pagina-a/", "https://sitio.test/pagina-b/", noindex=True,
        user_agent="Bot/1.0", fetcher_estado=fetcher_estado, fetcher_canonical=fetcher_canonical,
    )

    assert any(h["campo"] == "Indexación" and h["severidad"] == "alta" for h in hallazgos)


def test_sin_canonical_a_otra_no_hay_hallazgos():
    assert chequear_canonical_avanzado("https://sitio.test/", None, noindex=False, user_agent="Bot/1.0") == []


# ============== Regla 5: veredicto de indexabilidad ==============

def test_veredicto_indexable():
    assert calcular_veredicto(noindex=False, bloqueada_por_robots=False,
                              canonical_a_otra=False, status_error=False) == "Indexable"


def test_veredicto_noindex():
    assert calcular_veredicto(noindex=True, bloqueada_por_robots=False,
                              canonical_a_otra=False, status_error=False) == "No indexable (noindex)"


def test_veredicto_bloqueada_por_robots():
    assert calcular_veredicto(noindex=False, bloqueada_por_robots=True,
                              canonical_a_otra=False, status_error=False) == "Bloqueada por robots"


def test_veredicto_canonicalizada():
    assert calcular_veredicto(noindex=False, bloqueada_por_robots=False,
                              canonical_a_otra=True, status_error=False) == "Canonicalizada"


def test_veredicto_error_http_tiene_prioridad():
    # Un error HTTP pesa más que cualquier otra señal: si ni responde, el resto no importa.
    assert calcular_veredicto(noindex=True, bloqueada_por_robots=True,
                              canonical_a_otra=True, status_error=True) == "Error HTTP"


# ============== Regla 6: duplicados entre páginas ==============

def test_titulo_duplicado_entre_paginas():
    paginas = [
        {"url": "https://sitio.test/a/", "title": "Cursos de UTN", "meta_description": "Desc A"},
        {"url": "https://sitio.test/b/", "title": "Cursos de UTN", "meta_description": "Desc B"},
        {"url": "https://sitio.test/c/", "title": "Otro título distinto", "meta_description": "Desc C"},
    ]

    hallazgos = multipagina.chequear_duplicados(paginas)

    assert "https://sitio.test/a/" in hallazgos
    assert "https://sitio.test/b/" in hallazgos
    assert "https://sitio.test/c/" not in hallazgos
    assert hallazgos["https://sitio.test/a/"][0]["campo"] == "Título duplicado"


def test_meta_description_duplicada_entre_paginas():
    paginas = [
        {"url": "https://sitio.test/a/", "title": "Título A", "meta_description": "La misma descripción"},
        {"url": "https://sitio.test/b/", "title": "Título B", "meta_description": "La misma descripción"},
    ]

    hallazgos = multipagina.chequear_duplicados(paginas)

    assert any(h["campo"] == "Meta description duplicada" for h in hallazgos["https://sitio.test/a/"])


def test_sin_duplicados_no_hay_hallazgos():
    paginas = [
        {"url": "https://sitio.test/a/", "title": "Título A", "meta_description": "Desc A"},
        {"url": "https://sitio.test/b/", "title": "Título B", "meta_description": "Desc B"},
    ]

    assert multipagina.chequear_duplicados(paginas) == {}


# ============== Regla 7: thin content ==============

def test_thin_content_se_detecta():
    html = (FIXTURES / "pagina_thin_content.html").read_text(encoding="utf-8")

    hallazgo = chequear_thin_content(html)

    assert hallazgo is not None
    assert hallazgo["severidad"] == "media"
    assert hallazgo["campo"] == "Contenido"


def test_thin_content_no_se_dispara_con_umbral_bajo():
    html = (FIXTURES / "pagina_thin_content.html").read_text(encoding="utf-8")

    assert chequear_thin_content(html, umbral=1) is None


# ============== Regla 8: jerarquía de encabezados ==============

def test_salto_de_encabezados_se_detecta():
    html = (FIXTURES / "pagina_salto_encabezados.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    hallazgo = chequear_jerarquia_encabezados(soup)

    assert hallazgo is not None
    assert hallazgo["campo"] == "Jerarquía de encabezados"
    assert "h3" in hallazgo["hallazgo"]


def test_jerarquia_correcta_no_dispara_hallazgo():
    soup = BeautifulSoup("<h1>Título</h1><h2>Subtítulo</h2><h3>Detalle</h3>", "html.parser")

    assert chequear_jerarquia_encabezados(soup) is None


# ============== Regla 9: contenido mixto (HTTPS con recursos HTTP) ==============

def test_contenido_mixto_se_detecta():
    html = (FIXTURES / "pagina_contenido_mixto.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    hallazgo = chequear_contenido_mixto(soup, "https://sitio.test/pagina/")

    assert hallazgo is not None
    assert hallazgo["severidad"] == "alta"
    assert "http://cdn.ejemplo.test/estilos.css" in hallazgo["hallazgo"]


def test_contenido_mixto_no_aplica_a_paginas_http():
    html = (FIXTURES / "pagina_contenido_mixto.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    assert chequear_contenido_mixto(soup, "http://sitio.test/pagina/") is None


def test_contenido_mixto_no_aplica_sin_url():
    html = (FIXTURES / "pagina_contenido_mixto.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    assert chequear_contenido_mixto(soup, None) is None


# ============== Regla 10: URL con mayúsculas/espacios (solo enlaces internos) ==============

def test_es_enlace_externo_mismo_dominio_no_es_externo():
    assert _es_enlace_externo("/inscripcion", "https://sitio.test/pagina/") is False


def test_es_enlace_externo_otro_dominio_es_externo():
    assert _es_enlace_externo("https://www.youtube.com/video", "https://sitio.test/pagina/") is True


def test_es_enlace_externo_mailto_es_externo_si_hay_dominio_de_pagina():
    assert _es_enlace_externo("mailto:info@sitio.test", "https://sitio.test/pagina/") is True


def test_es_enlace_externo_sin_url_de_pagina_nunca_es_externo():
    assert _es_enlace_externo("https://www.youtube.com/video", None) is False


def test_tokens_con_mayuscula_devuelve_solo_los_tokens_afectados():
    tokens = _tokens_con_mayuscula("/listado/Categorias[administracion-de-empresas]")

    assert tokens == ["Categorias"]


def test_tokens_con_mayuscula_vacio_si_todo_es_minuscula():
    assert _tokens_con_mayuscula("/listado/categorias") == []
