from pathlib import Path

from auditor_seo.auditor import auditar, cargar

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
