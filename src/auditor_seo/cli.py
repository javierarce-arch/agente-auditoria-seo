"""
cli.py — punto de entrada para la corrida automática (GitHub Actions).

Qué hace:
  1. Arma la lista de URLs a auditar: desde un archivo (por defecto 'urls.txt')
     o, con --crawl, descubriéndolas al crawlear el sitio desde una URL semilla.
  2. Corre el auditor sobre cada una (reutiliza auditor.py sin tocarlo).
  3. Publica el dashboard como página de Confluence (opcional, ver
     confluence.py) y manda las notificaciones por mail (opcional) con el link.
  4. El exit code solo es != 0 ante un error real del pipeline (una excepción
     no manejada) — los hallazgos de indexación NO hacen fallar la corrida;
     esa señal vive en el mail y en el dashboard, no en el semáforo de la Action.

La lógica de auditoría vive en auditor.py; este archivo solo la orquesta.
"""

import argparse
import os
import sys
import time

from auditor_seo import confluence, correo, estado, gsc, multipagina
from auditor_seo.auditor import THIN_CONTENT_DEFAULT, USER_AGENT_DEFAULT, auditar, cargar
from auditor_seo.crawler import (
    DELAY_DEFAULT,
    MAX_PAGINAS_DEFAULT,
    MAX_PROFUNDIDAD_DEFAULT,
    crawlear,
)

# Severidades de INDEXACIÓN que marcan "requiere atención" en el reporte y el
# mail (lo que más preocupa a UTN). Ya NO hacen fallar la Action — ver main().
SEVERIDADES_DE_ATENCION = {"alta", "revisar"}

# Orden de prioridad para el dashboard de Confluence: ALTA primero, BAJA al final.
ORDEN_SEVERIDAD = {"alta": 0, "revisar": 1, "media": 2, "baja": 3}


def agrupar_hallazgos(resultados):
    """
    Junta los hallazgos de indexación y on-page de todas las páginas por
    (campo, severidad, hallazgo) — esa clave ya identifica el mismo problema
    sin importar en qué página aparezca, porque el texto del hallazgo ya
    incluye el valor puntual (el href, el título repetido, etc.). Los
    hallazgos "ok" no son problemas y se excluyen.

    Devuelve una lista de {"campo", "severidad", "hallazgo", "que_significa",
    "urls": [url, ...]}, en el orden de primera aparición de cada grupo.
    """
    grupos = {}
    for r in resultados:
        for clave in ("indexacion", "on_page_tecnico"):
            for h in r["reporte"][clave]:
                if h["severidad"] == "ok":
                    continue
                llave = (h["campo"], h["severidad"], h["hallazgo"])
                if llave not in grupos:
                    grupos[llave] = {
                        "campo": h["campo"], "severidad": h["severidad"],
                        "hallazgo": h["hallazgo"], "que_significa": h["que_significa"],
                        "urls": [],
                    }
                grupos[llave]["urls"].append(r["url"])
    return list(grupos.values())


def agrupar_por_campo(grupos):
    """
    Un nivel más arriba que agrupar_hallazgos(): junta por (campo, severidad),
    para que reglas cuyo mensaje incluye un valor que varía por página (ej.
    "Contenido": 26 palabras en una, 10 en otra) aparezcan una sola vez en el
    dashboard en vez de una tarjeta repetida por cada valor distinto. Cada
    mensaje puntual queda como una "instancia" adentro, con sus propias
    páginas afectadas. `que_significa` es el mismo texto para todas las
    instancias de una regla, así que se toma una sola vez.

    Devuelve una lista de {"campo", "severidad", "que_significa",
    "instancias": [{"hallazgo", "urls"}, ...]}.
    """
    por_campo = {}
    for g in grupos:
        llave = (g["campo"], g["severidad"])
        if llave not in por_campo:
            por_campo[llave] = {"campo": g["campo"], "severidad": g["severidad"],
                                 "que_significa": g["que_significa"], "instancias": []}
        por_campo[llave]["instancias"].append({"hallazgo": g["hallazgo"], "urls": g["urls"]})
    return list(por_campo.values())


def ordenar_por_prioridad(grupos):
    """ALTA → a revisar → MEDIA → BAJA. Dentro de una misma severidad se
    mantiene el orden de primera aparición (sorted() es estable)."""
    return sorted(grupos, key=lambda g: ORDEN_SEVERIDAD.get(g["severidad"], 99))


def calcular_metricas(resultados):
    """Cantidad de problemas distintos (ya consolidados) por severidad, más
    cuántas páginas de las auditadas no tienen ningún hallazgo real."""
    grupos = agrupar_hallazgos(resultados)
    conteo = {"alta": 0, "revisar": 0, "media": 0, "baja": 0}
    for g in grupos:
        conteo[g["severidad"]] += 1
    paginas_limpias = sum(
        1 for r in resultados
        if not any(
            h["severidad"] != "ok"
            for clave in ("indexacion", "on_page_tecnico")
            for h in r["reporte"][clave]
        )
    )
    return {**conteo, "paginas_limpias": paginas_limpias, "total_paginas": len(resultados)}


def hay_hallazgos_alta(resultados):
    """True si hay al menos un hallazgo de severidad 'alta' en indexación o
    en on-page técnico, en cualquier página. Distinto de
    SEVERIDADES_DE_ATENCION/atencion (que solo mira indexación y también
    cuenta 'revisar') — esta es la condición para avisarle a Marketing."""
    return any(
        h["severidad"] == "alta"
        for r in resultados
        for clave in ("indexacion", "on_page_tecnico")
        for h in r["reporte"][clave]
    )


def _destinatarios(variable_entorno):
    return [a.strip() for a in os.environ.get(variable_entorno, "").split(",") if a.strip()]


def _cuerpo_mail_it(atencion, metricas, url_dashboard):
    estado = "Requiere atención (rojo)" if atencion else "OK (verde)"
    link = f"Dashboard: {url_dashboard}" if url_dashboard else "No se pudo publicar el dashboard esta corrida."
    return (
        "Resumen de la corrida de auditoría SEO.\n\n"
        f"Estado: {estado}\n"
        f"Páginas auditadas: {metricas['total_paginas']}\n"
        f"Páginas sin hallazgos: {metricas['paginas_limpias']}\n"
        f"ALTA: {metricas['alta']}\n"
        f"A REVISAR: {metricas['revisar']}\n"
        f"MEDIA: {metricas['media']}\n"
        f"BAJA: {metricas['baja']}\n\n"
        f"{link}"
    )


def _cuerpo_mail_marketing(metricas, url_dashboard):
    link = f"Dashboard: {url_dashboard}" if url_dashboard else "No se pudo publicar el dashboard esta corrida."
    return (
        f"La auditoría SEO de hoy encontró {metricas['alta']} problema(s) de prioridad ALTA.\n\n"
        f"{link}"
    )


def _avisar_por_mail(atencion, metricas, alta_presente, url_dashboard):
    """
    IT recibe un mail en cada corrida. Marketing solo si hay ≥1 hallazgo
    ALTA (ver hay_hallazgos_alta). Sin credenciales o sin destinatarios
    configurados, no hace nada — igual que Search Console, esto nunca debe
    tumbar la corrida.

    El dashboard NUNCA se manda como adjunto: muchos clientes de mail no lo
    renderizan (muestran el HTML crudo), así que se linkea la página de
    Confluence publicada por esta misma corrida (url_dashboard, o None si
    la publicación falló o no está configurada — el mail sigue saliendo,
    solo que sin el link).
    """
    if not correo.credenciales_disponibles():
        print("Notificación por mail: deshabilitada (no se encontró el token de Gmail).")
        return

    destinatarios_it = _destinatarios("EMAIL_TO_IT")
    if destinatarios_it:
        try:
            asunto = f"[Auditoría SEO] {'Requiere atención' if atencion else 'OK'}"
            correo.enviar_mail(asunto, _cuerpo_mail_it(atencion, metricas, url_dashboard), destinatarios_it)
            print(f"Mail a IT enviado a: {', '.join(destinatarios_it)}.")
        except Exception as e:
            print(f"No se pudo enviar el mail a IT: {e}")
    else:
        print("Notificación a IT: deshabilitada (no se definió EMAIL_TO_IT).")

    if alta_presente:
        destinatarios_mkt = _destinatarios("EMAIL_TO_MARKETING")
        if destinatarios_mkt:
            try:
                correo.enviar_mail("[Auditoría SEO] Se detectaron problemas de prioridad ALTA",
                                    _cuerpo_mail_marketing(metricas, url_dashboard), destinatarios_mkt)
                print(f"Mail a Marketing enviado a: {', '.join(destinatarios_mkt)}.")
            except Exception as e:
                print(f"No se pudo enviar el mail a Marketing: {e}")


def leer_urls(ruta):
    with open(ruta, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def construir_parser():
    p = argparse.ArgumentParser(description="Auditor SEO — audita una lista de URLs o un sitio crawleado.")
    p.add_argument("urls_file", nargs="?", default="urls.txt",
                    help="Archivo con URLs a auditar, una por línea (default: urls.txt). Se ignora con --crawl.")
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--crawl", metavar="URL_SEMILLA", default=None,
                       help="En vez de leer urls_file, descubre las páginas crawleando el sitio desde esta URL.")
    modo.add_argument("--desde-estado", action="store_true",
                       help="En vez de leer urls_file o crawlear, audita las URLs que quedaron con "
                            f"severidad alta en la corrida anterior (ver {estado.RUTA_ESTADO_DEFAULT}). "
                            "Si no hay ninguna, no se audita nada.")
    p.add_argument("--max-paginas", type=int, default=MAX_PAGINAS_DEFAULT,
                    help=f"Tope de páginas a crawlear (default: {MAX_PAGINAS_DEFAULT}).")
    p.add_argument("--max-profundidad", type=int, default=MAX_PROFUNDIDAD_DEFAULT,
                    help=f"Tope de profundidad de enlaces a seguir (default: {MAX_PROFUNDIDAD_DEFAULT}).")
    p.add_argument("--delay", type=float, default=DELAY_DEFAULT,
                    help=f"Segundos de espera entre requests del crawler (default: {DELAY_DEFAULT}).")
    p.add_argument("--user-agent", default=USER_AGENT_DEFAULT,
                    help=f"User-Agent a usar en los pedidos HTTP (default: {USER_AGENT_DEFAULT!r}).")
    p.add_argument("--umbral-thin-content", type=int, default=THIN_CONTENT_DEFAULT,
                    help=f"Palabras mínimas de contenido visible antes de avisar 'thin content' "
                         f"(default: {THIN_CONTENT_DEFAULT}).")
    p.add_argument("--gsc-site-url", default=os.environ.get("GSC_SITE_URL"),
                    help="Propiedad de Search Console a consultar (default: variable de entorno "
                         "GSC_SITE_URL). Sin esto (o sin GSC_CREDENTIALS_JSON) se saltea el contraste "
                         "con Search Console.")
    p.add_argument("--gsc-max-inspecciones", type=int, default=gsc.MAX_INSPECCIONES_DEFAULT,
                    help=f"Tope de inspecciones a Search Console por corrida "
                         f"(default: {gsc.MAX_INSPECCIONES_DEFAULT}).")
    p.add_argument("--gsc-delay", type=float, default=gsc.DELAY_DEFAULT,
                    help=f"Segundos de espera entre inspecciones a Search Console (default: {gsc.DELAY_DEFAULT}).")
    return p


def main(argv=None):
    args = construir_parser().parse_args(argv)

    if args.crawl:
        urls = crawlear(args.crawl, max_paginas=args.max_paginas,
                         max_profundidad=args.max_profundidad, delay=args.delay,
                         user_agent=args.user_agent)
        print(f"Crawler encontró {len(urls)} página(s) en {args.crawl}.")
    elif args.desde_estado:
        urls = estado.cargar_estado(ruta=estado.RUTA_ESTADO_DEFAULT)
        if not urls:
            print("Sin URLs con severidad ALTA pendiente. No hay nada para auditar hoy.")
            return 0
        print(f"Recheck diario: {len(urls)} URL(s) con ALTA pendiente de la corrida anterior.")
    else:
        urls = leer_urls(args.urls_file)

    inspector_gsc = None
    if args.gsc_site_url and gsc.credenciales_disponibles():
        try:
            inspector_gsc = gsc.construir_inspector(args.gsc_site_url)
            print(f"Search Console: habilitado para {args.gsc_site_url} "
                  f"(tope {args.gsc_max_inspecciones} inspecciones esta corrida).")
        except Exception as e:
            print(f"Search Console: no se pudo inicializar ({e}). El resto de la auditoría corre normal.")
    elif not gsc.credenciales_disponibles():
        print("Search Console: deshabilitado (no se definió GSC_CREDENTIALS_JSON). "
              "El resto de la auditoría corre normal.")
    else:
        print("Search Console: deshabilitado (falta --gsc-site-url / GSC_SITE_URL). "
              "El resto de la auditoría corre normal.")

    resultados = []
    atencion = 0  # cuántos ítems de indexación requieren atención
    inspecciones_hechas = 0

    for origen in urls:
        try:
            html, headers, es_url = cargar(origen, user_agent=args.user_agent)
            rep = auditar(html, headers, origen if es_url else None, es_url, user_agent=args.user_agent,
                          umbral_thin_content=args.umbral_thin_content)
        except Exception as e:  # una URL caída no debe tumbar toda la corrida
            es_url = origen.startswith("http")
            rep = {"indexacion": [{
                "campo": "Acceso", "severidad": "alta",
                "hallazgo": f"No se pudo acceder a la página: {e}",
                "que_significa": "La página no respondió. Puede estar caída, movida o dar error.",
                "como_se_verifica": "Se intentó descargar la URL.",
            }], "on_page_tecnico": []}

        estado_google = None
        if inspector_gsc and es_url and inspecciones_hechas < args.gsc_max_inspecciones:
            try:
                estado_google = gsc.inspeccionar(origen, inspector_gsc)
            except Exception as e:
                print(f"No se pudo consultar Search Console para {origen}: {e}")
            inspecciones_hechas += 1
            if args.gsc_delay:
                time.sleep(args.gsc_delay)

            hallazgo_gsc = gsc.reconciliar(gsc.extraer_veredicto_html(rep), estado_google)
            if hallazgo_gsc:
                rep["indexacion"].append(hallazgo_gsc)

        atencion += sum(1 for h in rep["indexacion"]
                        if h["severidad"] in SEVERIDADES_DE_ATENCION)
        resultados.append({"url": origen, "reporte": rep, "google": gsc.estado_a_dict(estado_google)})

    # Chequeos cruzados (necesitan ver todas las páginas juntas, no una por vez).
    paginas = [{"url": r["url"], **r["reporte"].get("metadatos", {})} for r in resultados]
    duplicados = multipagina.chequear_duplicados(paginas)
    for r in resultados:
        extra = duplicados.get(r["url"])
        if extra:
            r["reporte"]["on_page_tecnico"].extend(extra)

    # Solo --crawl (cubre todo el sitio) y --desde-estado (audita exactamente
    # el conjunto que el estado ya tenía) son corridas representativas del
    # universo que le importa al estado; el modo urls_file es un subconjunto
    # arbitrario (pensado para pruebas puntuales) y no debe pisarlo.
    if args.crawl or args.desde_estado:
        estado.guardar_estado(resultados, ruta=estado.RUTA_ESTADO_DEFAULT)

    metricas = calcular_metricas(resultados)

    confluence_url = None
    try:
        confluence_url = confluence.publicar_reporte(resultados, atencion, metricas)
    except Exception as e:
        print(f"No se pudo publicar en Confluence: {e}")

    _avisar_por_mail(atencion, metricas, hay_hallazgos_alta(resultados), confluence_url)

    print(f"Auditadas {len(urls)} URL(s). Ítems de indexación que requieren atención: {atencion}.")
    if atencion:
        print("Hay problemas de indexación: revisá el mail o el dashboard.")
    else:
        print("Sin problemas de indexación. Todo OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
