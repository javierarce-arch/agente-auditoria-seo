import json
import os

from auditor_seo.estado import cargar_estado, guardar_estado, urls_con_alta


def _hallazgo(severidad, hallazgo="mensaje"):
    return {"campo": "campo", "severidad": severidad, "hallazgo": hallazgo, "que_significa": "x"}


def _resultado(url, indexacion=None, on_page=None):
    return {"url": url, "reporte": {"indexacion": indexacion or [], "on_page_tecnico": on_page or []},
            "google": None}


# ============== urls_con_alta ==============

def test_urls_con_alta_incluye_solo_paginas_con_severidad_alta():
    resultados = [
        _resultado("https://sitio.test/a", indexacion=[_hallazgo("alta")]),
        _resultado("https://sitio.test/b", on_page=[_hallazgo("media")]),
    ]

    assert urls_con_alta(resultados) == ["https://sitio.test/a"]


def test_urls_con_alta_detecta_alta_en_on_page_tecnico():
    resultados = [_resultado("https://sitio.test/a", on_page=[_hallazgo("alta")])]

    assert urls_con_alta(resultados) == ["https://sitio.test/a"]


def test_urls_con_alta_sin_hallazgos_alta_devuelve_vacio():
    resultados = [_resultado("https://sitio.test/a", indexacion=[_hallazgo("media")])]

    assert urls_con_alta(resultados) == []


def test_urls_con_alta_no_duplica_la_misma_url():
    resultados = [
        _resultado("https://sitio.test/a", indexacion=[_hallazgo("alta", "problema 1")]),
        _resultado("https://sitio.test/a", indexacion=[_hallazgo("alta", "problema 2")]),
    ]

    assert urls_con_alta(resultados) == ["https://sitio.test/a"]


def test_urls_con_alta_mantiene_orden_de_primera_aparicion():
    resultados = [
        _resultado("https://sitio.test/b", indexacion=[_hallazgo("alta")]),
        _resultado("https://sitio.test/a", indexacion=[_hallazgo("alta")]),
    ]

    assert urls_con_alta(resultados) == ["https://sitio.test/b", "https://sitio.test/a"]


# ============== guardar_estado / cargar_estado ==============

def test_guardar_y_cargar_estado_hacen_round_trip(tmp_path):
    ruta = tmp_path / "estado.json"
    resultados = [_resultado("https://sitio.test/a", indexacion=[_hallazgo("alta")])]

    guardar_estado(resultados, ruta=str(ruta))

    assert cargar_estado(ruta=str(ruta)) == ["https://sitio.test/a"]


def test_guardar_estado_escribe_lista_ordenada_sin_metadata(tmp_path):
    ruta = tmp_path / "estado.json"
    resultados = [
        _resultado("https://sitio.test/b", indexacion=[_hallazgo("alta")]),
        _resultado("https://sitio.test/a", indexacion=[_hallazgo("alta")]),
    ]

    guardar_estado(resultados, ruta=str(ruta))

    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    assert datos == {"urls_alta": ["https://sitio.test/a", "https://sitio.test/b"]}


def test_guardar_estado_no_deja_archivo_temporal_residual(tmp_path):
    ruta = tmp_path / "estado.json"
    guardar_estado([_resultado("https://sitio.test/a", indexacion=[_hallazgo("alta")])], ruta=str(ruta))

    assert not os.path.exists(f"{ruta}.tmp")


def test_guardar_estado_crea_el_directorio_si_no_existe(tmp_path):
    ruta = tmp_path / "sub" / "estado.json"

    guardar_estado([_resultado("https://sitio.test/a", indexacion=[_hallazgo("alta")])], ruta=str(ruta))

    assert cargar_estado(ruta=str(ruta)) == ["https://sitio.test/a"]


def test_cargar_estado_sin_archivo_devuelve_vacio(tmp_path):
    assert cargar_estado(ruta=str(tmp_path / "no-existe.json")) == []


def test_cargar_estado_con_json_corrupto_devuelve_vacio(tmp_path):
    ruta = tmp_path / "estado.json"
    ruta.write_text("{esto no es json", encoding="utf-8")

    assert cargar_estado(ruta=str(ruta)) == []
