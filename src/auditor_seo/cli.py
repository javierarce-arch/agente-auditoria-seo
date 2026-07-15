"""
cli.py — punto de entrada para la corrida automática (GitHub Actions).

Qué hace:
  1. Arma la lista de URLs a auditar: desde un archivo (por defecto 'urls.txt')
     o, con --crawl, descubriéndolas al crawlear el sitio desde una URL semilla.
  2. Corre el auditor sobre cada una (reutiliza auditor.py sin tocarlo).
  3. Escribe tres reportes: report.md (para leer), report.json (para procesar)
     y report.html (dashboard), y manda las notificaciones por mail (opcional).
  4. El exit code solo es != 0 ante un error real del pipeline (una excepción
     no manejada) — los hallazgos de indexación NO hacen fallar la corrida;
     esa señal vive en el mail y en el dashboard, no en el semáforo de la Action.

La lógica de auditoría vive en auditor.py; este archivo solo la orquesta.
"""

import argparse
import json
import os
import sys
import time
from html import escape

from auditor_seo import correo, gsc, multipagina
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

# Orden de prioridad para el dashboard (report.html): ALTA primero, BAJA al final.
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
    link = f"Dashboard: {url_dashboard}\n\n" if url_dashboard else ""
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
        "Se adjunta el reporte detallado (report.md)."
    )


def _cuerpo_mail_marketing(metricas, url_dashboard):
    link = f"Dashboard: {url_dashboard}" if url_dashboard else "Revisá report.html en el artifact de la corrida."
    return (
        f"La auditoría SEO de hoy encontró {metricas['alta']} problema(s) de prioridad ALTA.\n\n"
        f"{link}"
    )


def _avisar_por_mail(atencion, metricas, alta_presente):
    """
    IT recibe un mail en cada corrida. Marketing solo si hay ≥1 hallazgo
    ALTA (ver hay_hallazgos_alta). Sin credenciales o sin destinatarios
    configurados, no hace nada — igual que Search Console, esto nunca debe
    tumbar la corrida.

    El dashboard NUNCA se manda como adjunto: muchos clientes de mail no lo
    renderizan (muestran el HTML crudo), así que se linkea la copia
    publicada en GitHub Pages (variable de entorno REPORT_URL). Sin esa
    variable, el mail sigue funcionando pero sin el link.
    """
    if not correo.credenciales_disponibles():
        print("Notificación por mail: deshabilitada (no se encontró el token de Gmail).")
        return

    url_dashboard = os.environ.get("REPORT_URL")

    destinatarios_it = _destinatarios("EMAIL_TO_IT")
    if destinatarios_it:
        try:
            asunto = f"[Auditoría SEO] {'Requiere atención' if atencion else 'OK'}"
            correo.enviar_mail(asunto, _cuerpo_mail_it(atencion, metricas, url_dashboard), destinatarios_it,
                                adjuntos=["report.md"])
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
    p.add_argument("--crawl", metavar="URL_SEMILLA", default=None,
                    help="En vez de leer urls_file, descubre las páginas crawleando el sitio desde esta URL.")
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

    metricas = calcular_metricas(resultados)

    escribir_json(resultados)
    escribir_md(resultados, atencion)
    escribir_html(resultados, atencion, metricas)
    _avisar_por_mail(atencion, metricas, hay_hallazgos_alta(resultados))

    print(f"Auditadas {len(urls)} URL(s). Ítems de indexación que requieren atención: {atencion}.")
    if atencion:
        print("Hay problemas de indexación: revisá el mail o el dashboard.")
    else:
        print("Sin problemas de indexación. Todo OK.")
    return 0


def escribir_json(resultados):
    with open("report.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)


def escribir_md(resultados, atencion):
    lineas = ["# Reporte de auditoría SEO — indexación y on-page", ""]
    estado = "🔴 Requiere atención" if atencion else "🟢 Sin problemas de indexación"
    lineas += [f"**Estado:** {estado}  ", f"**URLs auditadas:** {len(resultados)}", ""]

    for r in resultados:
        veredicto = r["reporte"].get("veredicto")
        sufijo_veredicto = f" — Veredicto: **{veredicto}**" if veredicto else ""
        lineas += [f"## {r['url']}{sufijo_veredicto}", ""]

        google = r.get("google")
        if google:
            if google["indexada"] is True:
                marca = "✅ Indexada por Google"
            elif google["indexada"] is False:
                marca = "❌ No indexada por Google"
            else:
                marca = "❓ Estado desconocido"
            lineas += ["### Estado en Google (Search Console)", "",
                       f"- **{marca}** — coverageState: `{google['coverage_state']}`",
                       f"  - Último rastreo: {google['ultimo_rastreo'] or 'sin datos'}",
                       ""]

        for titulo, clave in (("Indexación", "indexacion"), ("On-page técnico", "on_page_tecnico")):
            lineas += [f"### {titulo}", ""]
            items = r["reporte"][clave]
            if not items:
                lineas += ["Sin hallazgos.", ""]
                continue
            for h in items:
                lineas += [f"- **[{h['severidad'].upper()}] {h['campo']}** — {h['hallazgo']}",
                           f"  - _Qué significa:_ {h['que_significa']}"]
            lineas.append("")

    with open("report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))


_SEVERIDAD_LABEL = {"alta": "ALTA", "revisar": "A REVISAR", "media": "MEDIA", "baja": "BAJA"}
_SEVERIDAD_COLOR = {"alta": "#dc2626", "revisar": "#ea580c", "media": "#ca8a04", "baja": "#2563eb"}
_SEVERIDAD_EXPLICACION = {
    "alta": ("Prioridad máxima, aunque no siempre por el mismo motivo: a veces puede impedir que Google "
             "rastree o indexe la página (bloqueos, errores de acceso, canonical roto); otras veces no "
             "afecta la indexación pero daña mucho cómo se ve en los resultados (sin título, sin meta "
             "description) o es un problema de seguridad (contenido mixto)."),
    "revisar": "No es necesariamente un error — hay que confirmar si es intencional (ej. un 'noindex' a propósito).",
    "media": ("No bloquea la indexación ni es urgente, pero conviene mejorarlo (canonical faltante, "
              "poco contenido, etc.)."),
    "baja": "Detalle prolijo/técnico, de bajo impacto real (ej. URLs con mayúsculas o espacios).",
}

_CSS_DASHBOARD = """
  body { font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; max-width: 900px;
         margin: 2rem auto; padding: 0 1rem; color: #1f2937; background: #f9fafb; }
  h1 { font-size: 1.4rem; }
  .estado { font-size: 1.1rem; font-weight: 600; }
  .resumen { margin-bottom: 1.5rem; }
  .metricas { display: flex; gap: 0.75rem; flex-wrap: wrap; }
  .metrica { border-left: 4px solid var(--color); background: #fff; padding: 0.5rem 1rem;
             border-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.08); min-width: 90px; }
  .metrica-numero { display: block; font-size: 1.5rem; font-weight: 700; }
  .metrica-etiqueta { display: block; font-size: 0.75rem; color: #6b7280; letter-spacing: 0.03em; }
  .paginas { color: #4b5563; font-size: 0.9rem; }
  .leyenda { list-style: none; margin: 0.75rem 0 0; padding: 0; display: flex; flex-direction: column; gap: 0.3rem; }
  .leyenda li { display: flex; gap: 0.5rem; align-items: baseline; font-size: 0.82rem; color: #4b5563; }
  .leyenda .badge { flex-shrink: 0; }
  .hallazgo { border-left: 4px solid var(--color); background: #fff; border-radius: 4px;
              margin-bottom: 0.6rem; padding: 0.6rem 1rem; box-shadow: 0 1px 2px rgba(0,0,0,0.06); }
  .hallazgo summary { cursor: pointer; display: flex; gap: 0.6rem; align-items: baseline; flex-wrap: wrap; }
  .badge { font-size: 0.7rem; font-weight: 700; color: #fff; background: var(--color);
           border-radius: 3px; padding: 0.1rem 0.4rem; }
  .campo { font-weight: 600; color: #374151; }
  .mensaje { color: #1f2937; }
  .que-significa { color: #4b5563; font-size: 0.9rem; }
  .afecta { font-size: 0.85rem; color: #6b7280; margin-bottom: 0.2rem; }
  .urls { font-size: 0.85rem; margin: 0; padding-left: 1.2rem; max-height: 200px; overflow-y: auto; }
  .instancia { margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid #f3f4f6; }
  .instancia-mensaje { margin: 0 0 0.2rem; font-size: 0.9rem; color: #1f2937; }
"""


def _renderizar_instancia_html(instancia, mostrar_mensaje):
    urls = instancia["urls"]
    lista_urls = "".join(
        f'<li><a href="{escape(u)}" target="_blank" rel="noopener">{escape(u)}</a></li>' for u in urls
    )
    mensaje = f'<p class="instancia-mensaje">{escape(instancia["hallazgo"])}</p>' if mostrar_mensaje else ""
    return (
        f'<div class="instancia">{mensaje}'
        f'<p class="afecta">Afecta {len(urls)} página(s):</p>'
        f'<ul class="urls">{lista_urls}</ul>'
        f"</div>"
    )


def _renderizar_grupo_html(g):
    color = _SEVERIDAD_COLOR.get(g["severidad"], "#6b7280")
    etiqueta = _SEVERIDAD_LABEL.get(g["severidad"], g["severidad"].upper())
    instancias = g["instancias"]
    unico = len(instancias) == 1

    if unico:
        resumen = f'<span class="mensaje">{escape(instancias[0]["hallazgo"])}</span>'
    else:
        total_paginas = sum(len(i["urls"]) for i in instancias)
        resumen = (f'<span class="mensaje">{len(instancias)} casos distintos '
                   f"({total_paginas} página(s) en total)</span>")

    cuerpo = "".join(_renderizar_instancia_html(i, mostrar_mensaje=not unico) for i in instancias)

    return (
        f'<details class="hallazgo" style="--color: {color}">'
        f'<summary><span class="badge">{etiqueta}</span>'
        f'<span class="campo">{escape(g["campo"])}</span>{resumen}</summary>'
        f'<p class="que-significa"><em>Qué significa:</em> {escape(g["que_significa"])}</p>'
        f"{cuerpo}"
        f"</details>"
    )


def escribir_html(resultados, atencion, metricas=None):
    """
    Genera report.html: un dashboard autocontenido (CSS inline, sin JS ni
    dependencias externas) con los hallazgos consolidados y ordenados por
    prioridad, en vez de agrupados por página como report.md.
    """
    metricas = metricas or calcular_metricas(resultados)
    grupos = ordenar_por_prioridad(agrupar_por_campo(agrupar_hallazgos(resultados)))
    estado = "🔴 Requiere atención" if atencion else "🟢 Sin problemas de indexación"

    filas_metricas = "".join(
        f'<div class="metrica" style="--color: {_SEVERIDAD_COLOR[sev]}">'
        f'<span class="metrica-numero">{metricas[sev]}</span>'
        f'<span class="metrica-etiqueta">{_SEVERIDAD_LABEL[sev]}</span></div>'
        for sev in ("alta", "revisar", "media", "baja")
    )
    bloques_hallazgos = "".join(_renderizar_grupo_html(g) for g in grupos) or "<p>Sin hallazgos.</p>"
    filas_leyenda = "".join(
        f'<li><span class="badge" style="--color: {_SEVERIDAD_COLOR[sev]}">{_SEVERIDAD_LABEL[sev]}</span>'
        f"{escape(_SEVERIDAD_EXPLICACION[sev])}</li>"
        for sev in ("alta", "revisar", "media", "baja")
    )

    pagina = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reporte de auditoría SEO</title>
<style>{_CSS_DASHBOARD}</style>
</head>
<body>
<h1>Reporte de auditoría SEO — indexación y on-page</h1>
<p class="estado">{estado}</p>
<div class="resumen">
<div class="metricas">{filas_metricas}</div>
<p class="paginas">{metricas["paginas_limpias"]} de {metricas["total_paginas"]} páginas sin hallazgos.</p>
<ul class="leyenda">{filas_leyenda}</ul>
</div>
<div class="hallazgos">{bloques_hallazgos}</div>
</body>
</html>
"""
    with open("report.html", "w", encoding="utf-8") as f:
        f.write(pagina)


if __name__ == "__main__":
    sys.exit(main())
