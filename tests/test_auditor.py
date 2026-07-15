from pathlib import Path
from unittest.mock import MagicMock, patch

from auditor_seo.auditor import GOOGLEBOT_USER_AGENT, auditar, cargar, chequear_bloqueo_googlebot

FIXTURES = Path(__file__).parent / "fixtures"


def _hallazgos(rep, clave, campo):
    return [h for h in rep[clave] if h["campo"] == campo]


def test_pagina_con_noindex_se_detecta():
    html, headers, es_url = cargar(str(FIXTURES / "muestra_busqueda.html"))
    rep = auditar(html, headers, None, es_url)

    hallazgos = _hallazgos(rep, "indexacion", "Indexación")
    assert len(hallazgos) == 1
    assert hallazgos[0]["severidad"] == "revisar"


def test_pagina_de_curso_hallazgos_on_page():
    html, headers, es_url = cargar(str(FIXTURES / "muestra_curso.html"))
    rep = auditar(html, headers, None, es_url)

    # Sin 'noindex': el bloque de indexación marca "ok".
    indexacion = _hallazgos(rep, "indexacion", "Indexación")
    assert indexacion[0]["severidad"] == "ok"

    # Canonical presente, no genera hallazgo (solo se compara en modo URL).
    assert not _hallazgos(rep, "indexacion", "Canonical")

    # Título de más de 60 caracteres.
    titulo = _hallazgos(rep, "on_page_tecnico", "Título")
    assert len(titulo) == 1
    assert titulo[0]["severidad"] == "alta"

    # Dos <h1>.
    encabezados = _hallazgos(rep, "on_page_tecnico", "Encabezados")
    assert len(encabezados) == 1
    assert "2" in encabezados[0]["hallazgo"]

    # Dos imágenes sin ALT: banner.jpg (sin atributo) y docente.jpg (alt="").
    imagenes = _hallazgos(rep, "on_page_tecnico", "Imágenes")
    assert len(imagenes) == 1
    assert "2 de 3" in imagenes[0]["hallazgo"]

    # Link con mayúsculas y espacios.
    urls = _hallazgos(rep, "on_page_tecnico", "URL")
    assert len(urls) == 1
    assert "espacios" in urls[0]["hallazgo"] and "mayúsculas" in urls[0]["hallazgo"]


def test_url_regla_excluye_enlaces_externos():
    html = ('<html><body>'
            '<a href="/Curso Malo">interno</a>'
            '<a href="https://www.YOUTUBE.com/Video">externo</a>'
            '</body></html>')

    rep = auditar(html, {}, "https://sitio.test/pagina/", False)

    urls = _hallazgos(rep, "on_page_tecnico", "URL")
    assert len(urls) == 1
    assert "/Curso Malo" in urls[0]["hallazgo"]
    assert "YOUTUBE" not in urls[0]["hallazgo"]


def test_url_regla_identifica_token_con_mayuscula():
    html = '<html><body><a href="/listado/Categorias[administracion-de-empresas]">link</a></body></html>'

    rep = auditar(html, {}, "https://sitio.test/pagina/", False)

    urls = _hallazgos(rep, "on_page_tecnico", "URL")
    assert len(urls) == 1
    assert urls[0]["hallazgo"] == (
        'El enlace "/listado/Categorias[administracion-de-empresas]" tiene mayúsculas en "Categorias".'
    )


def test_cargar_propaga_user_agent():
    respuesta_falsa = MagicMock(text="<html></html>", headers={})
    with patch("auditor_seo.auditor.requests.get", return_value=respuesta_falsa) as mock_get:
        cargar("https://sitio.test/", user_agent="MiBot/1.0")

    assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "MiBot/1.0"


def test_chequeo_googlebot_detecta_posible_bloqueo():
    def fetcher_falso(url, user_agent):
        return 200 if user_agent != GOOGLEBOT_USER_AGENT else 403

    hallazgo = chequear_bloqueo_googlebot("https://sitio.test/", "MiBot/1.0", fetcher_falso)

    assert hallazgo is not None
    assert hallazgo["severidad"] == "alta"
    assert "Googlebot" in hallazgo["hallazgo"]
    assert "señal" in hallazgo["que_significa"].lower()


def test_chequeo_googlebot_no_dispara_si_ambos_responden_200():
    def fetcher_falso(url, user_agent):
        return 200

    hallazgo = chequear_bloqueo_googlebot("https://sitio.test/", "MiBot/1.0", fetcher_falso)

    assert hallazgo is None


def test_chequeo_googlebot_no_dispara_si_ya_nos_bloquea_a_nosotros():
    # Si ni siquiera nuestro propio User-Agent puede acceder, no es un problema
    # específico de Googlebot: no corresponde este hallazgo puntual.
    def fetcher_falso(url, user_agent):
        return 403

    hallazgo = chequear_bloqueo_googlebot("https://sitio.test/", "MiBot/1.0", fetcher_falso)

    assert hallazgo is None
