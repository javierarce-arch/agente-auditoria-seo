from auditor_seo import confluence

_VARS = {
    "CONFLUENCE_BASE_URL": "https://fepp.atlassian.net",
    "CONFLUENCE_EMAIL": "bot@fepp.test",
    "CONFLUENCE_API_TOKEN": "token123",
    "CONFLUENCE_SPACE_KEY": "~712020",
    "CONFLUENCE_PARENT_PAGE_ID": "3913023489",
}


def _set_env(monkeypatch, **overrides):
    for k, v in {**_VARS, **overrides}.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def test_credenciales_disponibles_false_si_falta_alguna(monkeypatch):
    _set_env(monkeypatch, CONFLUENCE_API_TOKEN=None)

    assert confluence.credenciales_disponibles() is False


def test_credenciales_disponibles_true_con_todas(monkeypatch):
    _set_env(monkeypatch)

    assert confluence.credenciales_disponibles() is True


def test_titulo_pagina_incluye_la_fecha():
    import datetime

    assert confluence._titulo_pagina(datetime.date(2026, 7, 16)) == "Auditoría SEO — 2026-07-16"


def _hallazgo(campo, severidad, hallazgo="mensaje", que_significa="significado"):
    return {"campo": campo, "severidad": severidad, "hallazgo": hallazgo, "que_significa": que_significa}


def _resultado(url, indexacion=None, on_page=None):
    return {"url": url, "reporte": {"indexacion": indexacion or [], "on_page_tecnico": on_page or []},
            "google": None}


def _metricas(**overrides):
    base = {"alta": 0, "revisar": 0, "media": 0, "baja": 0, "paginas_limpias": 1, "total_paginas": 1}
    return {**base, **overrides}


def test_construir_contenido_escapea_html_y_usa_macro_expand():
    resultados = [
        _resultado("https://sitio.test/a", on_page=[_hallazgo("Título", "alta", "<script>alert(1)</script>")]),
    ]

    contenido = confluence._construir_contenido(resultados, atencion=1, metricas=_metricas(alta=1))

    assert "https://sitio.test/a" in contenido
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in contenido
    assert "<script>alert(1)</script>" not in contenido
    assert "<ac:structured-macro" in contenido
    assert "<details" not in contenido


def test_construir_contenido_sin_hallazgos():
    contenido = confluence._construir_contenido([], atencion=0, metricas=_metricas())

    assert "Sin hallazgos." in contenido


class _RespuestaFalsa:
    def __init__(self, status_code=200, cuerpo=None):
        self.status_code = status_code
        self._cuerpo = cuerpo or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._cuerpo


class _SesionFalsa:
    def __init__(self, pagina_existente=None):
        self._pagina_existente = pagina_existente
        self.llamadas = []

    def get(self, url, params=None):
        self.llamadas.append(("GET", url, params))
        resultados = [self._pagina_existente] if self._pagina_existente else []
        return _RespuestaFalsa(cuerpo={"results": resultados})

    def post(self, url, json=None):
        self.llamadas.append(("POST", url, json))
        return _RespuestaFalsa(cuerpo={"_links": {"webui": "/spaces/X/pages/999/Nueva"}})

    def put(self, url, json=None):
        self.llamadas.append(("PUT", url, json))
        return _RespuestaFalsa(cuerpo={"_links": {"webui": "/spaces/X/pages/111/Existente"}})


def test_upsert_page_crea_si_no_existe():
    sesion = _SesionFalsa(pagina_existente=None)

    url = confluence._upsert_page(sesion, "https://fepp.atlassian.net", "~712020", "3913023489",
                                   "Auditoría SEO — 2026-07-16", "<p>contenido</p>")

    assert url == "https://fepp.atlassian.net/wiki/spaces/X/pages/999/Nueva"
    metodo, _, payload = sesion.llamadas[-1]
    assert metodo == "POST"
    assert payload["ancestors"] == [{"id": 3913023489}]


def test_upsert_page_actualiza_si_ya_existe():
    sesion = _SesionFalsa(pagina_existente={"id": "111", "version": {"number": 3}})

    url = confluence._upsert_page(sesion, "https://fepp.atlassian.net", "~712020", "3913023489",
                                   "Auditoría SEO — 2026-07-16", "<p>contenido</p>")

    assert url == "https://fepp.atlassian.net/wiki/spaces/X/pages/111/Existente"
    metodo, endpoint, payload = sesion.llamadas[-1]
    assert metodo == "PUT"
    assert endpoint.endswith("/content/111")
    assert payload["version"]["number"] == 4


def test_publicar_reporte_none_sin_credenciales(monkeypatch):
    _set_env(monkeypatch, CONFLUENCE_BASE_URL=None)

    assert confluence.publicar_reporte([], atencion=0, metricas=_metricas()) is None
