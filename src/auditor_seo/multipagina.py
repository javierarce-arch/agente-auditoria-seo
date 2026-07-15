"""
multipagina.py — chequeos que necesitan comparar varias páginas entre sí.

A diferencia de auditor.py (que audita una página aislada), estas reglas solo
tienen sentido mirando el conjunto: por ejemplo, dos páginas no pueden
compararse a sí mismas para saber si su título está duplicado.

El agente NO ejecuta cambios: solo compara y reporta.
"""

from collections import defaultdict

# (clave en los metadatos de cada página, campo del hallazgo, nombre en criollo)
CAMPOS_DUPLICABLES = [
    ("title", "Título duplicado", "título"),
    ("meta_description", "Meta description duplicada", "meta description"),
]


def chequear_duplicados(paginas):
    """
    `paginas`: lista de dicts {"url": str, "title": str|None, "meta_description": str|None}
    (el mismo shape que expone auditor.auditar() en rep["metadatos"], más "url").

    Devuelve un dict {url: [hallazgos]} para las URLs que comparten título o
    meta description con al menos otra página de la lista.
    """
    hallazgos_por_url = defaultdict(list)

    for clave, campo_hallazgo, nombre in CAMPOS_DUPLICABLES:
        agrupadas = defaultdict(list)
        for p in paginas:
            valor = p.get(clave)
            if valor:
                agrupadas[valor].append(p["url"])

        for valor, urls in agrupadas.items():
            if len(urls) < 2:
                continue
            for u in urls:
                otras = ", ".join(x for x in urls if x != u)
                hallazgos_por_url[u].append({
                    "campo": campo_hallazgo,
                    "severidad": "media",
                    "hallazgo": f'El {nombre} "{valor}" se repite en {len(urls)} páginas.',
                    "que_significa": (
                        f"Cuando varias páginas comparten el mismo {nombre}, Google no puede "
                        "diferenciarlas bien en los resultados y puede terminar mostrando la que "
                        "menos conviene (o eligiendo por vos). Cada página debería tener uno propio."
                    ),
                    "como_se_verifica": (
                        f"Se compararon los {nombre}s de todas las páginas auditadas. "
                        f"Coincide con: {otras}."
                    ),
                })

    return dict(hallazgos_por_url)
