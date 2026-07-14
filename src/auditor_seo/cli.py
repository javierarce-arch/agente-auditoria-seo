"""
cli.py — punto de entrada para la corrida automática (GitHub Actions).

Qué hace:
  1. Lee la lista de URLs a auditar desde un archivo (por defecto 'urls.txt').
  2. Corre el auditor sobre cada una (reutiliza auditor.py sin tocarlo).
  3. Escribe dos reportes: report.md (para leer) y report.json (para procesar).
  4. Devuelve exit code != 0 si hay problemas de INDEXACIÓN que requieren atención,
     para que la corrida se marque en rojo y llegue la notificación.

La lógica de auditoría vive en auditor.py; este archivo solo la orquesta.
"""

import json
import sys

from auditor_seo.auditor import auditar, cargar

# Severidades de INDEXACIÓN que hacen fallar el build (lo que más preocupa a UTN).
# El resto (on-page, canonical faltante, etc.) va al reporte pero no rompe la corrida.
SEVERIDADES_QUE_FALLAN = {"alta", "revisar"}


def leer_urls(ruta):
    with open(ruta, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def main():
    ruta_urls = sys.argv[1] if len(sys.argv) > 1 else "urls.txt"
    urls = leer_urls(ruta_urls)

    resultados = []
    atencion = 0  # cuántos ítems de indexación requieren atención

    for origen in urls:
        try:
            html, headers, es_url = cargar(origen)
            rep = auditar(html, headers, origen if es_url else None, es_url)
        except Exception as e:  # una URL caída no debe tumbar toda la corrida
            rep = {"indexacion": [{
                "campo": "Acceso", "severidad": "alta",
                "hallazgo": f"No se pudo acceder a la página: {e}",
                "que_significa": "La página no respondió. Puede estar caída, movida o dar error.",
                "como_se_verifica": "Se intentó descargar la URL.",
            }], "on_page_tecnico": []}

        atencion += sum(1 for h in rep["indexacion"]
                        if h["severidad"] in SEVERIDADES_QUE_FALLAN)
        resultados.append({"url": origen, "reporte": rep})

    escribir_json(resultados)
    escribir_md(resultados, atencion)

    print(f"Auditadas {len(urls)} URL(s). Ítems de indexación que requieren atención: {atencion}.")
    if atencion:
        print("Hay problemas de indexación: la corrida se marca en rojo.")
        return 1
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
        lineas += [f"## {r['url']}", ""]
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


if __name__ == "__main__":
    sys.exit(main())
