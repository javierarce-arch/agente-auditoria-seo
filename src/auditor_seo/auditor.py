"""
Auditor SEO — foco en INDEXACIÓN + on-page.

Pensado para UTN Centro de e-Learning. Dos ideas de diseño:

  1) Prioriza indexación: lo primero que responde es "¿esta página puede
     ser indexada por Google, y si no, por qué?". Combina varias señales
     (noindex, robots.txt, canonical, códigos HTTP) en un único veredicto.
  2) Explica en criollo: como no hay un experto en SEO en el equipo, cada
     hallazgo trae un "qué significa" en lenguaje simple.

El agente NO ejecuta cambios. Produce un reporte cuyos ítems están listos
para convertirse en tickets de Jira (campo, severidad, descripción).

Todas las reglas acá son deterministas (sin IA): se pueden verificar leyendo
el código. La IA queda para una iteración posterior.

Uso:
  python -m auditor_seo.auditor https://utnba.centrodeelearning.com/algun-curso/   (modo URL, real)
  python -m auditor_seo.auditor archivo.html                                       (modo local)
"""

import re
import sys
import xml.etree.ElementTree as ET
from urllib import robotparser
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

try:
    import requests
except ImportError:
    requests = None

# User-Agent honesto: identifica al auditor como lo que es, no se hace pasar por un navegador.
USER_AGENT_DEFAULT = "UTN-SEO-Auditor/1.0 (auditoría interna SEO)"
GOOGLEBOT_USER_AGENT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

THIN_CONTENT_DEFAULT = 150  # palabras mínimas de contenido visible
MAX_SALTOS_REDIRECCION_DEFAULT = 10


def cargar(origen, user_agent=USER_AGENT_DEFAULT):
    """Devuelve (html, headers, es_url). En modo URL trae también los headers HTTP."""
    if origen.startswith("http"):
        if not requests:
            raise SystemExit("Falta 'requests' para el modo URL.")
        r = requests.get(origen, timeout=15, headers={"User-Agent": user_agent})
        return r.text, {k.lower(): v for k, v in r.headers.items()}, True
    with open(origen, encoding="utf-8") as f:
        return f.read(), {}, False


def auditar(html, headers, url, es_url, user_agent=USER_AGENT_DEFAULT,
            fetcher_estado=None, fetcher_redireccion=None, fetcher_canonical=None,
            umbral_thin_content=THIN_CONTENT_DEFAULT,
            max_saltos_redireccion=MAX_SALTOS_REDIRECCION_DEFAULT):
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
    canonical_destino = None
    if canonical and url and es_url:
        destino = canonical.get("href", "")
        if destino and destino.rstrip("/") != url.rstrip("/"):
            canonical_destino = destino
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

    bloqueada_por_robots = False
    status_final = None

    # (modo URL) robots.txt, sitemap, estado HTTP/redirecciones, googlebot, canonical avanzado
    if es_url and requests:
        base = "/".join(url.split("/")[:3])

        texto_robots = None
        try:
            rob = requests.get(base + "/robots.txt", timeout=10, headers={"User-Agent": user_agent})
            if rob.status_code == 200:
                texto_robots = rob.text
            else:
                indexacion.append({
                    "campo": "robots.txt", "severidad": "media",
                    "hallazgo": "No se encontró robots.txt en la raíz del sitio.",
                    "que_significa": ("El robots.txt le dice a los buscadores qué pueden y qué no pueden "
                                      "rastrear. Su ausencia no rompe nada, pero es una buena práctica tenerlo."),
                    "como_se_verifica": f"Se pidió {base}/robots.txt y respondió {rob.status_code}.",
                })
        except requests.RequestException:
            pass

        # Sitemap: primero declarado en robots.txt (más confiable), si no /sitemap.xml
        sitemap_urls = extraer_sitemaps_de_robots(texto_robots) if texto_robots else []
        sitemap_encontrado = False
        if sitemap_urls:
            try:
                sm = requests.get(sitemap_urls[0], timeout=10, headers={"User-Agent": user_agent})
                if sm.status_code == 200:
                    sitemap_encontrado = True
                    n = contar_urls_sitemap(sm.text)
                    detalle = f" ({n} URLs)." if n is not None else "."
                    indexacion.append(_f(
                        "Sitemap", "ok",
                        f"Sitemap declarado en robots.txt: {sitemap_urls[0]}{detalle}",
                        ("El sitemap declarado en robots.txt es la forma más confiable de que Google lo "
                         "encuentre: no depende de adivinar la ubicación por convención."),
                        f"Se leyó la línea 'Sitemap:' de {base}/robots.txt y se pidió esa URL.",
                    ))
            except requests.RequestException:
                pass
        if not sitemap_encontrado:
            try:
                sm = requests.get(base + "/sitemap.xml", timeout=10, headers={"User-Agent": user_agent})
                if sm.status_code == 200:
                    sitemap_encontrado = True
                    n = contar_urls_sitemap(sm.text)
                    detalle = f" ({n} URLs)." if n is not None else "."
                    indexacion.append(_f(
                        "Sitemap", "ok",
                        f"Sitemap encontrado en /sitemap.xml{detalle}",
                        "No estaba declarado en robots.txt, pero existe en la ubicación convencional.",
                        f"Se pidió {base}/sitemap.xml.",
                    ))
            except requests.RequestException:
                pass
        if not sitemap_encontrado:
            indexacion.append(_f(
                "Sitemap", "media",
                "No se encontró sitemap ni declarado en robots.txt ni en /sitemap.xml.",
                ("El sitemap es la lista de páginas que le entregás a Google para que las encuentre a "
                 "todas. Sin él, algunas páginas pueden tardar en indexarse."),
                f"Se buscó la línea 'Sitemap:' en {base}/robots.txt y también se probó {base}/sitemap.xml.",
            ))

        hallazgo_robots, bloqueada_por_robots = chequear_bloqueo_robots(url, user_agent, texto_robots)
        if hallazgo_robots:
            indexacion.append(hallazgo_robots)

        hallazgos_http, status_final = chequear_estado_http(
            url, user_agent, fetcher_redireccion, max_saltos_redireccion)
        indexacion.extend(hallazgos_http)

        hallazgo_googlebot = chequear_bloqueo_googlebot(
            url, user_agent, fetcher_estado or _fetcher_estado_default)
        if hallazgo_googlebot:
            indexacion.append(hallazgo_googlebot)

        hallazgos_canonical = chequear_canonical_avanzado(
            url, canonical_destino, noindex, user_agent, fetcher_estado, fetcher_canonical)
        indexacion.extend(hallazgos_canonical)

    veredicto = calcular_veredicto(
        noindex=noindex,
        bloqueada_por_robots=bloqueada_por_robots,
        canonical_a_otra=bool(canonical_destino),
        status_error=(status_final is not None and status_final >= 400),
    )

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

    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc_texto = meta_desc_tag.get("content", "").strip() if meta_desc_tag else ""
    if not meta_desc_texto:
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
        if _es_enlace_externo(href, url):
            continue
        tokens_mayus = _tokens_con_mayuscula(href)
        probl = [x for x, cond in (("espacios", " " in href), ("mayúsculas", bool(tokens_mayus))) if cond]
        if probl:
            detalle = ""
            if tokens_mayus:
                tokens_citados = ", ".join(f'"{t}"' for t in tokens_mayus)
                detalle = f" en {tokens_citados}"
            tecnico.append(_f("URL", "baja", f'El enlace "{href}" tiene {" y ".join(probl)}{detalle}.',
                              "Las URLs deberían ir en minúscula y con guiones en vez de espacios; así son "
                              "más prolijas y estables para Google.",
                              "Se inspecciona el href del enlace (solo enlaces internos, del mismo dominio "
                              "que la página)."))

    hallazgo_thin = chequear_thin_content(html, umbral_thin_content)
    if hallazgo_thin:
        tecnico.append(hallazgo_thin)

    hallazgo_jerarquia = chequear_jerarquia_encabezados(soup)
    if hallazgo_jerarquia:
        tecnico.append(hallazgo_jerarquia)

    hallazgo_mixto = chequear_contenido_mixto(soup, url)
    if hallazgo_mixto:
        tecnico.append(hallazgo_mixto)

    return {
        "indexacion": indexacion,
        "on_page_tecnico": tecnico,
        "veredicto": veredicto,
        "metadatos": {"title": title, "meta_description": meta_desc_texto or None},
    }


def _f(campo, sev, hallazgo, significa, verifica):
    return {"campo": campo, "severidad": sev, "hallazgo": hallazgo,
            "que_significa": significa, "como_se_verifica": verifica}


def _es_enlace_externo(href, url):
    """True si `href` (resuelto contra `url`) apunta a un dominio distinto
    del de la página. Si `url` es None (modo archivo local, sin dominio
    conocido), se asume interno."""
    if not url:
        return False
    destino = urljoin(url, href)
    return urlparse(destino).hostname != urlparse(url).hostname


_TOKEN_URL_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _tokens_con_mayuscula(href):
    """Tokens alfanuméricos de `href` (separados por '/', '-', '[', ']',
    espacios, etc.) que contienen al menos una mayúscula."""
    return [t for t in _TOKEN_URL_RE.findall(href) if any(c.isupper() for c in t)]


def _fetcher_estado_default(url, user_agent):
    """Pide `url` con el User-Agent dado y devuelve el status code (o None si falla)."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": user_agent})
        return r.status_code
    except requests.RequestException:
        return None


def chequear_bloqueo_googlebot(url, user_agent, fetcher):
    """
    Heurística de diagnóstico: pide la misma URL con nuestro User-Agent y con el
    de Googlebot, y compara los códigos de respuesta. Si el WAF deja pasar el
    nuestro pero bloquea el de Googlebot, es una señal (no una prueba) de que
    también podría estar bloqueando al rastreador real de Google.

    `fetcher` recibe (url, user_agent) y devuelve el status code (o None si falló).
    """
    status_propio = fetcher(url, user_agent)
    status_googlebot = fetcher(url, GOOGLEBOT_USER_AGENT)
    if status_propio != 200 or status_googlebot == 200:
        return None
    return _f(
        "Indexación", "alta",
        (f"Con nuestro User-Agent la página respondió 200, pero con el User-Agent "
         f"de Googlebot respondió {status_googlebot}: posible bloqueo de Googlebot por el WAF."),
        ("Esto es una SEÑAL heurística, no una prueba: mandar el User-Agent de Googlebot "
         "no significa que la respuesta venga realmente de Google (Google se verifica por "
         "DNS inverso, cualquiera puede mandar ese header). Pero si un firewall filtra por "
         "User-Agent, es candidato a estar bloqueando también al rastreador real, lo que "
         "impediría indexar la página. Hay que confirmarlo con Google Search Console "
         "(inspección de URL / informe de cobertura) o revisando los logs del servidor."),
        "Se pidió la misma URL con nuestro User-Agent y con el de Googlebot, y se compararon los códigos de respuesta.",
    )


def extraer_sitemaps_de_robots(texto_robots):
    """Devuelve las URLs de sitemap declaradas en robots.txt (líneas 'Sitemap:')."""
    sitemaps = []
    for linea in texto_robots.splitlines():
        linea = linea.strip()
        if linea.lower().startswith("sitemap:"):
            valor = linea.split(":", 1)[1].strip()
            if valor:
                sitemaps.append(valor)
    return sitemaps


def contar_urls_sitemap(texto_xml):
    """Cuenta <url> (sitemap normal) o <sitemap> (índice de sitemaps) en el XML. None si no parsea."""
    try:
        raiz = ET.fromstring(texto_xml)
    except ET.ParseError:
        return None
    urls = [el for el in raiz.iter() if el.tag.rsplit("}", 1)[-1] == "url"]
    if urls:
        return len(urls)
    indices = [el for el in raiz.iter() if el.tag.rsplit("}", 1)[-1] == "sitemap"]
    return len(indices)


def chequear_bloqueo_robots(url, user_agent, texto_robots):
    """
    Compara `url` contra las reglas Disallow del robots.txt (si hay). Devuelve
    (hallazgo_o_None, bloqueada_bool).
    """
    if not texto_robots:
        return None, False
    rp = robotparser.RobotFileParser()
    rp.parse(texto_robots.splitlines())
    if rp.can_fetch(user_agent, url):
        return None, False
    return _f(
        "robots.txt", "alta",
        "La URL está bloqueada para rastreo por una regla Disallow de robots.txt.",
        ("Aunque el HTML esté perfecto, si robots.txt le prohíbe a los buscadores acceder a esta "
         "URL, Google no la va a poder rastrear ni indexar. Es distinto de un 'noindex': acá ni "
         "siquiera puede entrar a ver el contenido."),
        "Se comparó la URL contra las reglas Disallow del robots.txt del sitio.",
    ), True


def _fetcher_redireccion_default(url, user_agent):
    """Pide `url` sin seguir redirecciones. Devuelve (status_code, location) o (None, None) si falla."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": user_agent}, allow_redirects=False)
        return r.status_code, r.headers.get("Location")
    except requests.RequestException:
        return None, None


def seguir_redirecciones(url, user_agent, fetcher=None, max_saltos=MAX_SALTOS_REDIRECCION_DEFAULT):
    """
    Sigue redirecciones manualmente desde `url`, salto a salto. `fetcher` recibe
    (url, user_agent) y devuelve (status_code, location_o_None).

    Devuelve {"cadena": [urls visitadas en orden], "status_final": int|None,
    "bucle": bool, "excede_tope": bool}.
    """
    fetcher = fetcher or _fetcher_redireccion_default
    cadena = [url]
    actual = url
    status = None

    for _ in range(max_saltos):
        status, location = fetcher(actual, user_agent)
        if status is None or not (300 <= status < 400) or not location:
            return {"cadena": cadena, "status_final": status, "bucle": False, "excede_tope": False}
        siguiente = urljoin(actual, location)
        if siguiente in cadena:
            cadena.append(siguiente)
            return {"cadena": cadena, "status_final": status, "bucle": True, "excede_tope": False}
        cadena.append(siguiente)
        actual = siguiente

    return {"cadena": cadena, "status_final": status, "bucle": False, "excede_tope": True}


def chequear_estado_http(url, user_agent, fetcher=None, max_saltos=MAX_SALTOS_REDIRECCION_DEFAULT):
    """
    Sigue redirecciones desde `url` y genera hallazgos: bucles, cadenas largas,
    y códigos de error final. Devuelve (hallazgos, status_final).
    """
    resultado = seguir_redirecciones(url, user_agent, fetcher, max_saltos)
    hallazgos = []
    saltos = len(resultado["cadena"]) - 1

    if resultado["bucle"]:
        hallazgos.append(_f(
            "Redirecciones", "alta",
            f"Bucle de redirecciones detectado: {' → '.join(resultado['cadena'])}.",
            ("La página nunca llega a un destino final: tanto los buscadores como las personas "
             "quedan dando vueltas entre las mismas URLs sin ver contenido."),
            f"Se siguieron las redirecciones desde {url} hasta encontrar una URL repetida.",
        ))
    elif resultado["excede_tope"]:
        hallazgos.append(_f(
            "Redirecciones", "alta",
            f"Cadena de redirecciones muy larga (más de {max_saltos} saltos) partiendo de {url}.",
            ("Cadenas largas de redirección diluyen la señal de indexación y pueden hacer que "
             "Google abandone el rastreo antes de llegar al destino final."),
            f"Se siguieron redirecciones hasta el tope de {max_saltos} saltos sin llegar a un destino final.",
        ))
    elif saltos > 1:
        hallazgos.append(_f(
            "Redirecciones", "media",
            f"Cadena de {saltos} redirecciones: {' → '.join(resultado['cadena'])}.",
            ("Cada salto de más suma latencia y hace más difícil que los buscadores sigan la "
             "cadena completa. Lo ideal es una sola redirección directa al destino final."),
            f"Se siguieron las redirecciones desde {url}.",
        ))

    status_final = resultado["status_final"]
    if status_final is not None and status_final >= 400:
        sufijo_saltos = f" (después de {saltos} redirección(es))" if saltos else ""
        prefijo_pedido = ", siguiendo redirecciones," if saltos else ""
        hallazgos.append(_f(
            "Acceso", "alta",
            f"La URL respondió {status_final}{sufijo_saltos}.",
            "Un error 4xx/5xx significa que ni las personas ni Google pueden ver el contenido de esta URL.",
            f"Se pidió {url}{prefijo_pedido} y el resultado final fue {status_final}.",
        ))

    return hallazgos, status_final


def _fetcher_canonical_default(url, user_agent):
    """Trae `url` y devuelve el href de su <link rel='canonical'>, o None."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": user_agent})
        soup_destino = BeautifulSoup(r.text, "html.parser")
        tag = soup_destino.find("link", attrs={"rel": "canonical"})
        return tag.get("href") if tag else None
    except requests.RequestException:
        return None


def chequear_canonical_avanzado(url, canonical_destino, noindex, user_agent,
                                  fetcher_estado=None, fetcher_canonical=None):
    """
    Chequeos adicionales cuando el canonical apunta a otra URL:
    - combinación conflictiva: 'noindex' + canonical a otra URL.
    - el destino del canonical no responde 200.
    - el destino tiene a su vez su propio canonical a una tercera URL (cadena).
    """
    if not canonical_destino:
        return []

    hallazgos = []

    if noindex:
        hallazgos.append(_f(
            "Indexación", "alta",
            (f"La página tiene 'noindex' Y ADEMÁS declara un canonical a otra URL "
             f"({canonical_destino}): son dos señales de indexación que se pisan entre sí."),
            ("El 'noindex' le dice a Google 'no me indexes a mí'; el canonical le dice 'la versión "
             "oficial es esta otra'. Mandar las dos señales juntas es contradictorio y hace más "
             "difícil que Google entienda qué hacer con la página."),
            "Se comparó la presencia de 'noindex' contra el destino declarado en el canonical.",
        ))

    fetcher_estado = fetcher_estado or _fetcher_estado_default
    status_destino = fetcher_estado(canonical_destino, user_agent)
    if status_destino != 200:
        hallazgos.append(_f(
            "Canonical", "alta",
            f"El canonical apunta a {canonical_destino}, que no responde 200 (status: {status_destino}).",
            ("Si la URL 'oficial' que señala el canonical no existe o da error, Google no tiene "
             "dónde consolidar la indexación: la página corre riesgo de no indexarse en ningún lado."),
            f"Se pidió {canonical_destino} y se revisó el status code.",
        ))
    else:
        fetcher_canonical = fetcher_canonical or _fetcher_canonical_default
        canonical_del_destino = fetcher_canonical(canonical_destino, user_agent)
        if canonical_del_destino and canonical_del_destino.rstrip("/") != canonical_destino.rstrip("/"):
            hallazgos.append(_f(
                "Canonical", "media",
                f"Canonical en cadena: {url} → {canonical_destino} → {canonical_del_destino}.",
                ("El canonical debería apuntar directo a la versión final, no a otra página que a "
                 "su vez apunta a una tercera. Las cadenas diluyen la señal y Google puede no "
                 "seguirla hasta el final."),
                f"Se pidió {canonical_destino} y se revisó su propio <link rel='canonical'>.",
            ))

    return hallazgos


def calcular_veredicto(noindex, bloqueada_por_robots, canonical_a_otra, status_error):
    """
    Combina las señales de indexación en un estado único: Error HTTP > Bloqueada
    por robots > No indexable (noindex) > Canonicalizada > Indexable. El orden
    importa: si la página ni responde bien, el resto de las señales no importa.
    """
    if status_error:
        return "Error HTTP"
    if bloqueada_por_robots:
        return "Bloqueada por robots"
    if noindex:
        return "No indexable (noindex)"
    if canonical_a_otra:
        return "Canonicalizada"
    return "Indexable"


def _contar_palabras_visibles(html):
    copia = BeautifulSoup(html, "html.parser")
    for tag in copia(["script", "style", "noscript"]):
        tag.decompose()
    texto = copia.get_text(separator=" ", strip=True)
    return len(texto.split())


def chequear_thin_content(html, umbral=THIN_CONTENT_DEFAULT):
    palabras = _contar_palabras_visibles(html)
    if palabras >= umbral:
        return None
    return _f(
        "Contenido", "media",
        f"La página tiene poco contenido visible: {palabras} palabra(s) (umbral: {umbral}).",
        ("El 'thin content' (contenido escaso) es una señal que Google asocia con páginas de baja "
         "calidad, y puede hacer que decida no indexarla o la posicione peor. No siempre es un "
         "problema (páginas de categoría o de contacto suelen tener poco texto a propósito), pero "
         "vale la pena revisar si debería tener más desarrollo."),
        f"Se contaron las palabras del texto visible (sin scripts ni estilos): {palabras}.",
    )


def chequear_jerarquia_encabezados(soup):
    encabezados = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    saltos = []
    nivel_maximo_visto = 0
    for h in encabezados:
        nivel = int(h.name[1])
        if nivel > nivel_maximo_visto + 1:
            texto = h.get_text(strip=True) or "(vacío)"
            saltos.append(f'<{h.name}> "{texto}" aparece sin un <h{nivel_maximo_visto + 1}> antes')
        nivel_maximo_visto = max(nivel_maximo_visto, nivel)
    if not saltos:
        return None
    return _f(
        "Jerarquía de encabezados", "media",
        f"La jerarquía de encabezados salta niveles: {'; '.join(saltos)}.",
        ("Los encabezados deberían bajar de a un nivel por vez (H1 → H2 → H3...), como un índice. "
         "Saltearse niveles no rompe la página, pero le hace más difícil a Google — y a quienes "
         "usan lectores de pantalla — entender la estructura del contenido."),
        "Se recorrieron las etiquetas <h1> a <h6> en el orden en que aparecen en el HTML.",
    )


def chequear_contenido_mixto(soup, url):
    if not url or not url.startswith("https://"):
        return None
    recursos = []
    for tag, atributo in (("script", "src"), ("link", "href"), ("img", "src")):
        for el in soup.find_all(tag):
            valor = el.get(atributo, "")
            if valor.startswith("http://"):
                recursos.append(valor)
    if not recursos:
        return None
    return _f(
        "Contenido mixto", "alta",
        f"La página es HTTPS pero carga {len(recursos)} recurso(s) por HTTP: {', '.join(recursos)}.",
        ("El 'contenido mixto' (mixed content) hace que los navegadores bloqueen o muestren "
         "advertencias de seguridad, y es mala práctica de cara a Google. Todos los recursos de "
         "una página HTTPS deberían cargarse también por HTTPS."),
        "Se revisaron los atributos src/href de <script>, <link> e <img> buscando 'http://'.",
    )


def imprimir(rep, origen):
    print("=" * 72)
    print(f"AUDITORÍA — {origen}")
    print(f"Veredicto de indexabilidad: {rep.get('veredicto', '(sin calcular)')}")
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
