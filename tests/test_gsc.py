from auditor_seo import gsc
from auditor_seo.gsc import EstadoGSC


def test_credenciales_disponibles_false_sin_env(monkeypatch):
    monkeypatch.delenv("GSC_CREDENTIALS_JSON", raising=False)

    assert gsc.credenciales_disponibles() is False


def test_credenciales_disponibles_true_con_env(monkeypatch):
    monkeypatch.setenv("GSC_CREDENTIALS_JSON", "/ruta/falsa/credenciales.json")

    assert gsc.credenciales_disponibles() is True


def test_credenciales_disponibles_true_con_ruta_explicita(monkeypatch):
    monkeypatch.delenv("GSC_CREDENTIALS_JSON", raising=False)

    assert gsc.credenciales_disponibles("/ruta/explicita.json") is True


def test_construir_inspector_none_sin_credenciales(monkeypatch):
    monkeypatch.delenv("GSC_CREDENTIALS_JSON", raising=False)

    assert gsc.construir_inspector("https://sitio.test/") is None


def test_inspeccionar_parsea_respuesta_cruda():
    def inspector_falso(url):
        return {
            "inspectionResult": {
                "indexStatusResult": {
                    "coverageState": "Crawled - currently not indexed",
                    "lastCrawlTime": "2026-07-01T00:00:00Z",
                }
            }
        }

    estado = gsc.inspeccionar("https://sitio.test/curso/", inspector_falso)

    assert estado.indexada is False
    assert estado.coverage_state == "Crawled - currently not indexed"
    assert estado.ultimo_rastreo == "2026-07-01T00:00:00Z"


def test_inspeccionar_reconoce_indexada():
    def inspector_falso(url):
        return {"inspectionResult": {"indexStatusResult": {"coverageState": "Submitted and indexed"}}}

    estado = gsc.inspeccionar("https://sitio.test/", inspector_falso)

    assert estado.indexada is True


def test_extraer_veredicto_html_indexable():
    rep = {"indexacion": [{"campo": "Indexación", "severidad": "ok", "hallazgo": "", "que_significa": ""}]}

    assert gsc.extraer_veredicto_html(rep) is True


def test_extraer_veredicto_html_noindex():
    rep = {"indexacion": [{"campo": "Indexación", "severidad": "revisar", "hallazgo": "", "que_significa": ""}]}

    assert gsc.extraer_veredicto_html(rep) is False


def test_reconciliar_detecta_discrepancia_html_indexable_google_no_indexada():
    estado_google = EstadoGSC(indexada=False, coverage_state="Crawled - currently not indexed", ultimo_rastreo=None)

    hallazgo = gsc.reconciliar(html_indexable=True, estado_google=estado_google)

    assert hallazgo is not None
    assert hallazgo["severidad"] == "alta"
    assert "no la tiene indexada" in hallazgo["hallazgo"]


def test_reconciliar_detecta_discrepancia_noindex_pero_google_indexada():
    estado_google = EstadoGSC(indexada=True, coverage_state="Submitted and indexed", ultimo_rastreo=None)

    hallazgo = gsc.reconciliar(html_indexable=False, estado_google=estado_google)

    assert hallazgo is not None
    assert hallazgo["severidad"] == "alta"
    assert "SÍ tiene la página indexada" in hallazgo["hallazgo"]


def test_reconciliar_no_dispara_si_coinciden():
    estado_google = EstadoGSC(indexada=True, coverage_state="Submitted and indexed", ultimo_rastreo=None)

    assert gsc.reconciliar(html_indexable=True, estado_google=estado_google) is None


def test_reconciliar_no_dispara_sin_estado_google():
    assert gsc.reconciliar(html_indexable=True, estado_google=None) is None


def test_estado_a_dict():
    estado = EstadoGSC(indexada=True, coverage_state="Submitted and indexed", ultimo_rastreo="2026-07-01T00:00:00Z")

    assert gsc.estado_a_dict(estado) == {
        "indexada": True,
        "coverage_state": "Submitted and indexed",
        "ultimo_rastreo": "2026-07-01T00:00:00Z",
    }


def test_estado_a_dict_none():
    assert gsc.estado_a_dict(None) is None
