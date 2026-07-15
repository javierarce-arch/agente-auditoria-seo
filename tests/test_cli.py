from auditor_seo import correo, gsc
from auditor_seo.auditor import USER_AGENT_DEFAULT
from auditor_seo.cli import (
    _avisar_por_mail,
    agrupar_hallazgos,
    agrupar_por_campo,
    calcular_metricas,
    construir_parser,
    escribir_html,
    hay_hallazgos_alta,
    ordenar_por_prioridad,
)


def test_user_agent_default_es_honesto():
    args = construir_parser().parse_args(["urls.txt"])

    assert args.user_agent == USER_AGENT_DEFAULT
    assert "UTN" in args.user_agent


def test_user_agent_flag_personalizado():
    args = construir_parser().parse_args(["--user-agent", "MiBot/1.0"])

    assert args.user_agent == "MiBot/1.0"


def test_user_agent_se_combina_con_crawl():
    args = construir_parser().parse_args(["--crawl", "https://sitio.test/", "--user-agent", "MiBot/1.0"])

    assert args.crawl == "https://sitio.test/"
    assert args.user_agent == "MiBot/1.0"


def test_gsc_site_url_default_none_sin_env(monkeypatch):
    monkeypatch.delenv("GSC_SITE_URL", raising=False)

    args = construir_parser().parse_args(["urls.txt"])

    assert args.gsc_site_url is None
    assert args.gsc_max_inspecciones == gsc.MAX_INSPECCIONES_DEFAULT
    assert args.gsc_delay == gsc.DELAY_DEFAULT


def test_gsc_site_url_default_toma_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("GSC_SITE_URL", "https://sitio.test/")

    args = construir_parser().parse_args(["urls.txt"])

    assert args.gsc_site_url == "https://sitio.test/"


def test_gsc_flags_explicitos():
    args = construir_parser().parse_args([
        "urls.txt", "--gsc-site-url", "https://otro.test/",
        "--gsc-max-inspecciones", "5", "--gsc-delay", "0.5",
    ])

    assert args.gsc_site_url == "https://otro.test/"
    assert args.gsc_max_inspecciones == 5
    assert args.gsc_delay == 0.5


def test_auditor_corre_sin_credenciales_gsc(monkeypatch):
    # Sin GSC_CREDENTIALS_JSON, gsc queda deshabilitado — el resto del auditor
    # (parseo de argumentos, defaults) no debe fallar por su ausencia.
    monkeypatch.delenv("GSC_CREDENTIALS_JSON", raising=False)

    args = construir_parser().parse_args(["urls.txt"])

    assert gsc.credenciales_disponibles() is False
    assert gsc.construir_inspector(args.gsc_site_url or "https://sitio.test/") is None


def _hallazgo(campo, severidad, hallazgo="mensaje", que_significa="significado"):
    return {"campo": campo, "severidad": severidad, "hallazgo": hallazgo, "que_significa": que_significa}


def _resultado(url, indexacion=None, on_page=None):
    return {"url": url, "reporte": {"indexacion": indexacion or [], "on_page_tecnico": on_page or []},
            "google": None}


# ============== agrupar_hallazgos / ordenar_por_prioridad / calcular_metricas ==============

def test_agrupar_hallazgos_consolida_mismo_hallazgo_en_varias_paginas():
    h = _hallazgo("URL", "baja", 'El enlace "/nav" tiene mayúsculas en "NAV".')
    resultados = [
        _resultado("https://sitio.test/a", on_page=[h]),
        _resultado("https://sitio.test/b", on_page=[dict(h)]),
    ]

    grupos = agrupar_hallazgos(resultados)

    assert len(grupos) == 1
    assert grupos[0]["urls"] == ["https://sitio.test/a", "https://sitio.test/b"]


def test_agrupar_hallazgos_no_consolida_hallazgos_con_texto_distinto():
    resultados = [
        _resultado("https://sitio.test/a",
                   on_page=[_hallazgo("Encabezados", "media", "La página tiene 2 etiquetas <h1>.")]),
        _resultado("https://sitio.test/b",
                   on_page=[_hallazgo("Encabezados", "media", "La página tiene 3 etiquetas <h1>.")]),
    ]

    assert len(agrupar_hallazgos(resultados)) == 2


def test_agrupar_hallazgos_excluye_severidad_ok():
    resultados = [_resultado("https://sitio.test/a", indexacion=[_hallazgo("Indexación", "ok", "todo bien")])]

    assert agrupar_hallazgos(resultados) == []


def test_agrupar_por_campo_junta_mensajes_distintos_del_mismo_campo():
    # Mismo campo/severidad, pero el mensaje varía por página (ej. "Contenido":
    # cantidad de palabras distinta) — deben quedar bajo un solo grupo.
    resultados = [
        _resultado("https://sitio.test/a",
                   on_page=[_hallazgo("Contenido", "media", "La página tiene poco contenido: 26 palabras.")]),
        _resultado("https://sitio.test/b",
                   on_page=[_hallazgo("Contenido", "media", "La página tiene poco contenido: 10 palabras.")]),
    ]

    grupos = agrupar_por_campo(agrupar_hallazgos(resultados))

    assert len(grupos) == 1
    assert grupos[0]["campo"] == "Contenido"
    assert len(grupos[0]["instancias"]) == 2
    mensajes = {i["hallazgo"] for i in grupos[0]["instancias"]}
    assert mensajes == {"La página tiene poco contenido: 26 palabras.", "La página tiene poco contenido: 10 palabras."}


def test_agrupar_por_campo_no_junta_severidades_distintas():
    resultados = [
        _resultado("https://sitio.test/a", indexacion=[_hallazgo("Canonical", "media", "sin canonical")]),
        _resultado("https://sitio.test/b", indexacion=[_hallazgo("Canonical", "alta", "canonical roto")]),
    ]

    grupos = agrupar_por_campo(agrupar_hallazgos(resultados))

    assert len(grupos) == 2


def test_ordenar_por_prioridad_orden_alta_revisar_media_baja():
    grupos = [
        {"campo": "c", "severidad": "baja", "hallazgo": "1", "que_significa": "x", "urls": []},
        {"campo": "c", "severidad": "media", "hallazgo": "2", "que_significa": "x", "urls": []},
        {"campo": "c", "severidad": "alta", "hallazgo": "3", "que_significa": "x", "urls": []},
        {"campo": "c", "severidad": "revisar", "hallazgo": "4", "que_significa": "x", "urls": []},
    ]

    orden = [g["severidad"] for g in ordenar_por_prioridad(grupos)]

    assert orden == ["alta", "revisar", "media", "baja"]


def test_calcular_metricas_cuenta_grupos_por_severidad_y_paginas_limpias():
    resultados = [
        _resultado("https://sitio.test/a", indexacion=[_hallazgo("Indexación", "ok", "todo bien")]),
        _resultado("https://sitio.test/b", on_page=[_hallazgo("Encabezados", "media", "2 h1")]),
        _resultado("https://sitio.test/c", on_page=[_hallazgo("Título", "alta", "sin title")]),
    ]

    metricas = calcular_metricas(resultados)

    assert metricas["alta"] == 1
    assert metricas["media"] == 1
    assert metricas["baja"] == 0
    assert metricas["revisar"] == 0
    assert metricas["paginas_limpias"] == 1
    assert metricas["total_paginas"] == 3


def test_hay_hallazgos_alta_true_si_hay_alta_en_on_page():
    resultados = [_resultado("https://sitio.test/a", on_page=[_hallazgo("Título", "alta", "sin title")])]

    assert hay_hallazgos_alta(resultados) is True


def test_hay_hallazgos_alta_false_sin_hallazgos_alta():
    resultados = [_resultado("https://sitio.test/a", on_page=[_hallazgo("Encabezados", "media", "2 h1")])]

    assert hay_hallazgos_alta(resultados) is False


# ============== escribir_html ==============

def test_escribir_html_genera_archivo_autocontenido(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resultados = [
        _resultado("https://sitio.test/a", on_page=[_hallazgo("Título", "alta", "<script>alert(1)</script>")]),
    ]

    escribir_html(resultados, atencion=0)

    contenido = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "https://sitio.test/a" in contenido
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in contenido
    assert "<script>alert(1)</script>" not in contenido
    assert "cdn." not in contenido and "googleapis" not in contenido


# ============== _avisar_por_mail ==============

def _metricas(**overrides):
    base = {"alta": 0, "revisar": 0, "media": 0, "baja": 0, "paginas_limpias": 1, "total_paginas": 1}
    return {**base, **overrides}


def test_avisar_por_mail_no_hace_nada_sin_credenciales(monkeypatch):
    monkeypatch.setattr(correo, "credenciales_disponibles", lambda: False)
    llamadas = []
    monkeypatch.setattr(correo, "enviar_mail", lambda *a, **k: llamadas.append(a))

    _avisar_por_mail(0, _metricas(), alta_presente=False)

    assert llamadas == []


def test_avisar_por_mail_a_it_siempre_si_hay_destinatario(monkeypatch):
    monkeypatch.setattr(correo, "credenciales_disponibles", lambda: True)
    monkeypatch.setenv("EMAIL_TO_IT", "it@utn.test")
    monkeypatch.delenv("EMAIL_TO_MARKETING", raising=False)
    llamadas = []
    monkeypatch.setattr(correo, "enviar_mail", lambda *a, **k: llamadas.append(a))

    _avisar_por_mail(0, _metricas(), alta_presente=False)

    assert len(llamadas) == 1
    assert llamadas[0][2] == ["it@utn.test"]


def test_avisar_por_mail_a_marketing_solo_si_hay_alta(monkeypatch):
    monkeypatch.setattr(correo, "credenciales_disponibles", lambda: True)
    monkeypatch.delenv("EMAIL_TO_IT", raising=False)
    monkeypatch.setenv("EMAIL_TO_MARKETING", "mkt@utn.test")
    llamadas = []
    monkeypatch.setattr(correo, "enviar_mail", lambda *a, **k: llamadas.append(a))

    _avisar_por_mail(0, _metricas(), alta_presente=False)
    assert llamadas == []

    _avisar_por_mail(0, _metricas(alta=1), alta_presente=True)
    assert len(llamadas) == 1
    assert llamadas[0][2] == ["mkt@utn.test"]


def test_avisar_por_mail_fallo_en_it_no_impide_marketing(monkeypatch):
    monkeypatch.setattr(correo, "credenciales_disponibles", lambda: True)
    monkeypatch.setenv("EMAIL_TO_IT", "it@utn.test")
    monkeypatch.setenv("EMAIL_TO_MARKETING", "mkt@utn.test")

    llamadas = []

    def enviar_mail_falla_para_it(asunto, cuerpo, destinatarios, adjuntos=None):
        if destinatarios == ["it@utn.test"]:
            raise RuntimeError("smtp caído")
        llamadas.append(destinatarios)

    monkeypatch.setattr(correo, "enviar_mail", enviar_mail_falla_para_it)

    _avisar_por_mail(1, _metricas(alta=1), alta_presente=True)

    assert llamadas == [["mkt@utn.test"]]
