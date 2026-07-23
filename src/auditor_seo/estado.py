"""
estado.py — persistencia del único estado que el pipeline guarda entre
corridas: qué URLs quedaron con al menos un hallazgo de severidad "alta".

Lo escribe la auditoría quincenal (--crawl, cubre todo el sitio) y la
reconciliación diaria (--desde-estado, audita exactamente ese conjunto). El
modo por urls_file (el default de auditor-seo, pensado para pruebas puntuales
con un subconjunto arbitrario de URLs) no lo toca — ver cli.py.

El archivo solo contiene la lista de URLs, sin timestamp ni otra metadata:
un timestamp cambiaría en cada corrida aunque el set de URLs sea idéntico,
lo que le rompería la idempotencia al commit condicional de la Action.
"""

import json
import os

RUTA_ESTADO_DEFAULT = "data/estado_alta.json"


def urls_con_alta(resultados):
    """URLs únicas (orden de primera aparición) con al menos un hallazgo de
    severidad 'alta' en indexación u on-page técnico — mismo criterio que
    cli.hay_hallazgos_alta(), pero devolviendo las URLs en vez de un bool."""
    vistas = set()
    urls = []
    for r in resultados:
        if r["url"] in vistas:
            continue
        tiene_alta = any(
            h["severidad"] == "alta"
            for clave in ("indexacion", "on_page_tecnico")
            for h in r["reporte"][clave]
        )
        if tiene_alta:
            vistas.add(r["url"])
            urls.append(r["url"])
    return urls


def cargar_estado(ruta=RUTA_ESTADO_DEFAULT):
    """Lista de URLs con alta pendiente. [] si el archivo no existe o el
    contenido no es un JSON válido — nunca revienta la corrida."""
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    urls = datos.get("urls_alta", []) if isinstance(datos, dict) else []
    return list(urls)


def guardar_estado(resultados, ruta=RUTA_ESTADO_DEFAULT):
    """Escribe la lista (ordenada, sin duplicados) de URLs con alta pendiente.
    Escritura atómica (archivo temporal + rename) para no dejar un JSON a
    medio escribir si la corrida se corta en el medio."""
    directorio = os.path.dirname(ruta)
    if directorio:
        os.makedirs(directorio, exist_ok=True)
    contenido = {"urls_alta": sorted(set(urls_con_alta(resultados)))}
    ruta_tmp = f"{ruta}.tmp"
    with open(ruta_tmp, "w", encoding="utf-8") as f:
        json.dump(contenido, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(ruta_tmp, ruta)
