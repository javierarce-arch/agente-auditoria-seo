"""
Auditor SEO v1 — foco en INDEXACIÓN + on-page básico.

Pensado para UTN Centro de e-Learning. Dos ideas de diseño:

  1) Prioriza indexación: lo primero que responde es "¿esta página puede
     ser indexada por Google, y si no, por qué?".
  2) Explica en criollo: como no hay un experto en SEO en el equipo, cada
     hallazgo trae un "qué significa" en lenguaje simple.

El agente NO ejecuta cambios. Produce un reporte cuyos ítems están listos
para convertirse en tickets de Jira (campo, severidad, descripción).

Uso:
  python -m auditor_seo.auditor https://utnba.centrodeelearning.com/algun-curso/   (modo URL, real)
  python -m auditor_seo.auditor archivo.html                                       (modo local)
"""

import sys

from bs4 import BeautifulSoup

try:
    import requests
except ImportError:
    requests = None


def cargar(origen):
    """Devuelve (html, headers, es_url). En modo URL trae también los headers HTTP."""
    if origen.startswith("http"):
        if not requests:
            raise SystemExit("Falta 'requests' para el modo URL.")
        r = requests.get(origen, timeout=15, headers={"User-Agent": "AuditorUTN/1.0"})
        return r.text, {k.lower(): v for k, v in r.headers.items()}, True
    with open(origen, encoding="utf-8") as f:
        return f.read(), {}, False


def auditar(html, headers, url, es_url):
    soup = BeautifulSoup(html, "html.parser")
    indexacion, tecnico = [], []

    # ================= INDEXACIÓN (la prioridad) =================

    # meta robots noindex
    meta_robots = soup.find("meta", attrs={"name": "robots"})
    contenido_robots = (meta_robots.get("content", "") if meta_robots else "").lower()
    x_robots = headers.get("x-robots-tag", "").lower()
    noindex = "noindex" in contenido_robots or "noindex" in x_robots

    if noindex:
        indexacion.append({
            "campo": "Indexación", "severidad": "revisar",
            "hallazgo": "La página tiene 'noindex': le pide a Google que NO la muestre en los resultados.",
            "que_significa": ("Esta página está bloqueada para el buscador a propósito. Puede estar "
                              "bien (buscadores internos, páginas de gracias, filtros duplicados) o mal "
                              "(si es una página importante que sí querés que aparezca). Hay que confirmar "
                              "si es intencional."),
            "como_se_verifica": "Se lee la etiqueta <meta name='robots'> y el header X-Robots-Tag.",
        })
    else:
        indexacion.append({
            "campo": "Indexación", "severidad": "ok",
            "hallazgo": "La página está abierta para ser indexada (sin 'noindex').",
            "que_significa": "Google puede mostrarla en los resultados. Es lo esperable en una página de curso.",
            "como_se_verifica": "No se encontró 'noindex' ni en el HTML ni en los headers.",
        })

    # canonical
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical and url and es_url:
        destino = canonical.get("href", "")
        if destino and destino.rstrip("/") != url.rstrip("/"):
            indexacion.append({
                "campo": "Canonical", "severidad": "revisar",
                "hallazgo": f"El canonical apunta a otra URL: {destino}",
                "que_significa": ("La página le dice a Google 'la versión oficial es esta otra, indexá "
                                  "esa en vez de mí'. Si las dos son en realidad la misma cosa, está bien. "
                                  "Si son distintas, esta página nunca va a posicionar por su cuenta."),
                "como_se_verifica": "Se compara el href del <link rel='canonical'> con la URL de la página.",
            })
    elif not canonical:
        indexacion.append({
            "campo": "Canonical", "severidad": "media",
            "hallazgo": "La página no declara canonical.",
            "que_significa": ("El canonical le indica a Google cuál es la versión 'oficial' de la página. "
                              "Sin él, si existen URLs parecidas, Google puede confundirse sobre cuál mostrar."),
            "como_se_verifica": "Se busca <link rel='canonical'> en el HTML.",
        })

    # (modo URL) robots.txt y sitemap
    if es_url and requests:
        base = "/".join(url.split("/")[:3])
        try:
            rob = requests.get(base + "/robots.txt", timeout=10)
            if rob.status_code != 200:
                indexacion.append({
                    "campo": "robots.txt", "severidad": "media",
                    "hallazgo": "No se encontró robots.txt en la raíz del sitio.",
                    "que_significa": ("El robots.txt le dice a los buscadores qué pueden y qué no pueden "
                                      "rastrear. Su ausencia no rompe nada, pero es una buena práctica tenerlo."),
                    "como_se_verifica": f"Se pidió {base}/robots.txt y respondió {rob.status_code}.",
                })
            sm = requests.get(base + "/sitemap.xml", timeout=10)
            if sm.status_code != 200:
                indexacion.append({
                    "campo": "Sitemap", "severidad": "media",
                    "hallazgo": "No se encontró sitemap.xml en la raíz del sitio.",
                    "que_significa": ("El sitemap es la lista de páginas que le entregás a Google para que "
                                      "las encuentre a todas. Sin él, algunas páginas pueden tardar en indexarse."),
                    "como_se_verifica": f"Se pidió {base}/sitemap.xml y respondió {sm.status_code}.",
                })
        except requests.RequestException:
            pass

    # ================= ON-PAGE TÉCNICO =================

    title = soup.title.get_text(strip=True) if soup.title else None
    if not title:
        tecnico.append(_f("Título", "alta", "La página no tiene etiqueta <title>.",
                          "El título es lo que se ve como link azul en Google. Es imprescindible.",
                          "Se busca la etiqueta <title>."))
    elif len(title) > 60:
        tecnico.append(_f("Título", "alta", f"El título tiene {len(title)} caracteres; Google corta cerca de 60.",
                          "El texto que sobra no se ve en el resultado de Google, se corta con '...'.",
                          "Se cuenta el largo del <title>."))

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not meta_desc.get("content", "").strip():
        tecnico.append(_f("Meta description", "alta", "La página no tiene meta description.",
                          "Es el textito gris bajo el título en Google. Sin él, Google inventa uno, "
                          "que suele ser peor y baja los clics.",
                          "Se busca <meta name='description'> con contenido."))

    h1s = soup.find_all("h1")
    if len(h1s) != 1:
        etiquetas = ", ".join(h.get_text(strip=True) for h in h1s) or "ninguno"
        tecnico.append(_f("Encabezados", "media", f"La página tiene {len(h1s)} etiquetas <h1> ({etiquetas}).",
                          "El H1 es el título principal de la página. Tiene que haber uno solo, que resuma "
                          "de qué trata; varios confunden a Google.",
                          "Se cuentan las etiquetas <h1>."))

    imgs = soup.find_all("img")
    sin_alt = [i.get("src", "(sin src)") for i in imgs if not i.get("alt", "").strip()]
    if sin_alt:
        tecnico.append(_f("Imágenes", "media", f"{len(sin_alt)} de {len(imgs)} imágenes sin ALT: {', '.join(sin_alt)}.",
                          "El ALT describe la imagen. Ayuda a Google a entenderla y a las personas que usan "
                          "lectores de pantalla. Falta ponerlo.",
                          "Se revisa el atributo alt de cada <img>."))

    for a in soup.find_all("a", href=True):
        href = a["href"]
        probl = [x for x, cond in (("espacios", " " in href), ("mayúsculas", any(c.isupper() for c in href))) if cond]
        if probl:
            tecnico.append(_f("URL", "baja", f'El enlace "{href}" tiene {" y ".join(probl)}.',
                              "Las URLs deberían ir en minúscula y con guiones en vez de espacios; así son "
                              "más prolijas y estables para Google.",
                              "Se inspecciona el href del enlace."))

    return {"indexacion": indexacion, "on_page_tecnico": tecnico}


def _f(campo, sev, hallazgo, significa, verifica):
    return {"campo": campo, "severidad": sev, "hallazgo": hallazgo,
            "que_significa": significa, "como_se_verifica": verifica}


def imprimir(rep, origen):
    print("=" * 72)
    print(f"AUDITORÍA v1 — {origen}")
    print("=" * 72)
    for titulo, clave in (("INDEXACIÓN (prioridad)", "indexacion"),
                          ("ON-PAGE TÉCNICO", "on_page_tecnico")):
        print(f"\n>> {titulo}")
        print("-" * 72)
        if not rep[clave]:
            print("   Sin hallazgos.")
        for i, h in enumerate(rep[clave], 1):
            print(f"\n{i}. [{h['severidad'].upper()}] {h['campo']}")
            print(f"   Qué pasa:      {h['hallazgo']}")
            print(f"   Qué significa: {h['que_significa']}")
    print()


if __name__ == "__main__":
    origen = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/muestra_curso.html"
    html, headers, es_url = cargar(origen)
    rep = auditar(html, headers, origen if es_url else None, es_url)
    imprimir(rep, origen)
